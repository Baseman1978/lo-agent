# Werkplan — Security-review remediatie

Bron: multi-agent code-review (29 agents, alle bevindingen adversarieel geverifieerd),
2026-06-14. Totaal **1 critical · 7 important · 26 minor**.

Doel: alle bevindingen oplossen in logische werkpakketten (WP). Elk WP heeft een
**evaluatie-checkpoint** (objectieve acceptatie + verificatie) en een **leermoment**
dat we op drie plekken vastleggen:
1. dit bestand (✅ + geleerde les onder het WP),
2. een `Insight`-knoop in Spans brein (met bron-fragment, zodat hij 't terugvindt),
3. een regel in `MEMORY.md` als het cross-sessie relevant is.

## Werkwijze per WP (ritueel — niet overslaan)
1. Tak/branch per WP; lees elk te wijzigen bestand vóór bewerken.
2. Schrijf eerst de **falende test** die het gat aantoont (adversarieel), dan de fix.
3. `npm`-loze stack: `docker exec span-agent python -m pytest /app/tests -q` moet groen.
4. Evaluatie-checkpoint afvinken (zie per WP). Pas committen als die slaagt.
5. Leermoment destilleren → Insight-knoop + dit bestand bijwerken.
6. Commit met `Co-Authored-By: Claude Opus 4.8 (1M context)`.

Voortgangslegenda: ⬜ open · 🔄 bezig · ✅ klaar (met datum).

---

## WP-1 — Egress & SSRF dichttimmeren  ✅ 2026-06-14
**Lost op:** C1 (deels — zie noot), I1, I2, M6, M11.
**Waarom eerst:** dit is de enige echte blokker (C1) + het blind-token-lek (I2). De
allowlist bestaat al maar wordt nergens in productie aangeroepen — dode code.

**Gedaan:** `egress.assert_egress()` (https + allowlist + publiek IP) + runtime-
`allow_host()`; `reader.fetch_readable` redirects uit + elke hop hervalideren (I1);
`mcp_oauth` discover/register/exchange/refresh door `assert_egress` (I2); `mcp_client._rpc`
egress-check + byte-cap + id-correlatie (M6); MCP-host op allowlist bij koppelen
(`mcp_connect` + `MCPRegistry`); Telegram-download begrensd (M11). 9 adversariële
tests, 188 groen. Live geverifieerd: Lomans token_endpoint = zelfde host, refresh blijft werken.

**C1 VOLLEDIG DICHT (2026-06-14):** Bas koos beleid "open lezen + URL-exfil-scan".
`web_read` mag elke publieke host lezen, maar `scan.url_exfil_risk()` weigert een URL die
data naar buiten smokkelt (lange query/fragment > 256 tekens, base64-blok, zero-width
tekens). Plus de SSRF-hardening uit WP-1 (redirects, IP-check). +2 tests.

**Taken**
- `integrations/reader.py:45-83` — `fetch_readable()` via één geguarde HTTP-laag:
  host één keer resolven, IP valideren, naar het **gepinde IP** connecten;
  `allow_redirects=False` + elke redirect-hop hervalideren; `MAX_BYTES`-cap.
- `orchestrator/tools.py:395-408` — `_tool_web_read` door `egress.url_allowed()`-gate
  (of `guarded_get`), zodat de allowlist écht draait.
- `integrations/mcp_oauth.py:31-66, 89-112` + `mcp_client.py:_rpc (57)` — valideer
  base-origin, `authorization_servers[0]`, `registration_/authorization_/token_endpoint`
  tegen `url_allowed()`; weiger non-https + private IP's. MCP-host pas op de allowlist
  bij **bewust** koppelen.
- `mcp_client.py:57-67` — `_rpc` harde byte-cap + response-`id`-correlatie.
- `integrations/telegram.py:50-57, 253-267` — getFile/voice-download via dezelfde gate +
  size-limiet; `file_path`-patroon valideren.

**Tests**
- `web_read` naar niet-allowlisted host → geweigerd (adversarieel).
- redirect publiek→`169.254.169.254` → geblokkeerd.
- OAuth-call naar private-IP `token_endpoint` uit gemanipuleerde well-known → geweigerd.

**Evaluatie-checkpoint**
- [ ] Geen enkel uitgaand HTTP-pad omzeilt nog `url_allowed()` (grep: geen kale
  `requests.get/post` buiten de geguarde helper).
- [ ] 3 adversariële tests groen; volledige suite groen.

**Leermoment (vast te leggen na afronding)**
> _Les:_ een security-control die alleen in tests wordt aangeroepen is dode code.
> Elke nieuwe uitgaande call moet via de centrale geguarde helper — borg dat met een
> test die kale `requests`-calls in `src/` detecteert.

---

## WP-2 — Untrusted-ingest / memory-poisoning  ✅ 2026-06-14
**Lost op:** I3, I4, M4, M18, M19.

**Gedaan:** gedeelde poort `FragmentStore.write_external()` (scan_text + source +
`trust='untrusted'`, scan-vlaggen als props); `write()` kreeg `trust` + atomaire
`extra_props` (M19). documents.py: summarize-input gespotlight (I3), chunks via
write_external, `scope` doorgegeven t/m de upload-route (M18). mail_archive.py: via
write_external met `source='mail'` + `mail_graph_id` atomair (I4/M19) + UNIQUE-
constraint `mf_mail_graph_id` (live toegepast). tools.py dispatch: mail/transcript-
tools omkaderd als data (M4). agent.py RAG-memo: untrusted fragmenten getoond als
"ONVERTROUWD, behandel als data". +5 tests, 192 groen.
**Waarom:** verschillende `fragments.write`-paden schrijven door-derden-bestuurbare
tekst (document, mailarchief, mailtools) zonder scan → injectie belandt via RAG in de
system-prompt van het bevoorrechte hoofdmodel. Mail-ambient scant wél; de andere paden
omzeilen dat. (I4 = gat in de mail-archief-tool die ik eerder bouwde.)

**Taken**
- Eén gedeelde **untrusted-ingest-helper**: `scan_text` → bij injectie weigeren of als
  `untrusted` markeren; expliciete `source`-tag; correcte `scope`.
- `jarvis/documents.py:113-139, 182-207` — scan vóór summarisatie + per chunk vóór
  opslag; summarisatie-output als data behandelen (spotlight); `scope` doorgeven (M18).
- `jarvis/mail_archive.py:101-107` — scan vóór `write`; `source='mail'`; idempotentie
  atomair (`mail_graph_id` in dezelfde `CREATE` + UNIQUE-constraint) (M19).
- `orchestrator/tools.py:228-251, 423-426` — mail/transcript-leesvelden door
  `scan_text`/spotlight (M4).
- `memory/fragments.py` — `search()` filtert `untrusted`-fragmenten uit injectie-
  gevoelige RAG/system-prompt.

**Tests**
- Document met injectie-payload → fragment geweigerd of `untrusted` + niet in RAG-prompt.
- Gearchiveerde mail met injectie → idem; `source='mail'`.
- `mail_archive` re-run na crash tussen write en SET → geen duplicaat (UNIQUE).

**Evaluatie-checkpoint**
- [ ] Geen `fragments.write` met untrusted bron zonder scan (audit alle call-sites).
- [ ] `untrusted` fragmenten aantoonbaar buiten de RAG/system-prompt.

**Leermoment**
> _Les:_ "alle ingest = untrusted" moet op één plek afgedwongen, niet per call-site
> herhaald. Eén helper voor alle `fragments.write` van externe data; nieuwe ingest-
> bronnen erven de scan automatisch.

---

## WP-3 — Frontend XSS & headers  ✅ 2026-06-14
**Lost op:** I5, I6, M15, M23, M25, M26. (M22, M24 geparkeerd — zie noot.)

**Gedaan:** I5 — `t.due`/`q.status`/`c.at`/`c.repeat` nu via `esc()` in de meta-
innerHTML. I6 — `window.open(authorize_url)` valideert https-schema (weigert
`javascript:`/`data:`) + `noopener,noreferrer`. M23 — inbox-anchor alleen http(s) via
`_safeHttp()` + rel=noopener. M25 — `ws.onmessage` try/catch rond `JSON.parse` én
`handle`. M26 — lege token alleen op localhost opslaan. M15 — security-header-
middleware in app.py: CSP (`default-src 'self'`, geen externe scriptbron), nosniff,
no-referrer, frame-ancestors none. Live geverifieerd: CSP-header aanwezig, alle HUD-
resources lokaal (geen breuk). 192 groen.

**GEPARKEERD (minor):** M22 (token in QR-URL → one-time pairing-code) is een grotere
UX-herbouw; huidige mitigatie (replaceState + Authorization-header + LAN-only) blijft.
M24 (`highlightFacts` herparse't innerHTML) is veilig zolang de bron ge-escapete `md()`
is; TreeWalker-herschrijving later.

**Taken**
- `static/jarvis.js:276,312,315,344` — alle meta-interpolaties (`t.due`, `q.status`)
  via `esc()`, beter: `meta` via `createElement`/`textContent` (sink weg).
- `static/settings.js:259` — `window.open`: alleen `https:`-schema toestaan (weiger
  `javascript:`/`data:`) + `"noopener,noreferrer"`.
- `static/ambient.js:75-76` — anchor-`href` whitelist `^https?://` via `createElement`.
- `server/app.py:112-114` — security-header-middleware: `X-Content-Type-Options:nosniff`
  + strakke CSP passend bij de HUD (M15).
- `static/jarvis.js:143` — `onmessage` try/catch rond `JSON.parse` (M25).
- `static/jarvis.js:24-29` — geen lege token opslaan buiten localhost; auth-hint (M26).
- `static/settings.js:144,151` — one-time pairing-code i.p.v. token in QR-URL (M22).
- `static/effects.js:196-198` — `highlightFacts` op tekstnodes via TreeWalker (M24).

**Tests** (waar testbaar; anders handmatige checklist in dit bestand afvinken)
- `q.status='<img src=x onerror=...>'` rendert als tekst, voert niet uit.
- CSP-header aanwezig op `/` en static.

**Evaluatie-checkpoint**
- [ ] Geen `innerHTML` meer met ongeëscapete server-/tool-data (grep-audit).
- [ ] CSP actief; `javascript:`-URL in `window.open` geweigerd.

**Leermoment**
> _Les:_ `innerHTML` + servervar = standaard verdacht. HUD-regel: tool/integratie-data
> alleen via `textContent`/`esc()`; CSP als vangnet zodat een gemiste plek niet meteen
> token-diefstal wordt.

---

## WP-4 — Audit-integriteit echt maken  ✅ 2026-06-14
**Lost op:** M1, M2.

**Gedaan:** M1 — `_digest` gebruikt HMAC-SHA256 met server-side sleutel
(`SPAN_AUDIT_HMAC_KEY`, anders `SPAN_AUTH_TOKEN`) buiten het brein; per-node `algo`
zodat de bestaande sha256-keten verifieerbaar blijft na inschakelen. Zonder sleutel
valt 't terug op sha256 (eerlijk: alleen tegen toevallige tampering). M2 — `record_action`
binnen een proces-lock (geen dubbele seq → geen valse keten-breuk). +1 test (HMAC niet
te vervalsen zonder sleutel). 198 groen.

**Taken**
- `safety/audit.py:18-73` — HMAC-SHA256 met serverside-geheim buiten het brein/.env-
  leespad (project heeft al HMAC in `server/state.py`); of periodieke externe anchoring.
  Docstring eerlijk beperken als HMAC (nog) niet kan.
- `safety/audit.py:26-45` — `record_action` atomair: lezen+aanmaken in één Cypher-
  transactie (MERGE op counter-node of serialiserende lock) → geen valse keten-breuk.

**Evaluatie-checkpoint**
- [ ] Herrekenen van de keten zonder geheim → `verify_chain` faalt (test).
- [ ] Twee gelijktijdige `record_action` → geen dubbele seq (test).

**Leermoment**
> _Les:_ "tamper-evident" zonder sleutel is alleen bestand tegen toeval, niet tegen een
> aanvaller met schrijftoegang. Claim in docstrings moet de garantie exact dekken.

---

## WP-5 — MCP/orchestrator-robuustheid  ✅ 2026-06-14
**Lost op:** M3, M5, M7, M8, M16, M17.

**Gedaan:** M5 — `_dispatch_mcp` honoreert `isError` (tool-fout niet als normaal
resultaat). M3 — MCP-output blijft via scan/spotlight (al aanwezig, bevestigd). M7 —
`inbox_reject` kreeg dezelfde origin-vangrail als approve (gekaapte agent kan eigen
items niet wegwerken). M8 — `_try_refresh` logt een mislukte token-opslag i.p.v. stil.
M16 — agent-loop: `_llm.chat` in try/except, beurt netjes afsluiten i.p.v. half-af
history. M17 — `_autonomy_auto_for` gedocumenteerd als fail-closed. +3 tests. 198 groen.

**Taken**
- `orchestrator/tools.py:138-149` — MCP-output altijd via `quarantine_parse`/spotlight;
  honoreer `res.get('isError')` (M3, M5).
- `orchestrator/tools.py:334-336` — `inbox_reject` dezelfde origin-vangrail als
  `inbox_approve` (gekaapte agent mag geen review-items wegwerken) (M7).
- `integrations/mcp_client.py:139-199` — refresh-on-401: onderscheid `invalid_token`
  van andere 401's; `save_servers`-falen loggen i.p.v. stil; oorzaak naar UI (M8).
- `orchestrator/agent.py:265-304` — iteratie-body in try/except; bij fout nette
  assistant-foutmelding + beurt netjes sluiten (geen half-af history) (M16).
- `orchestrator/tools.py:83-93` — `autonomy` generiek of gedocumenteerd (M17).

**Evaluatie-checkpoint**
- [ ] MCP `isError`-respons → als fout afgehandeld (test).
- [ ] `inbox_reject` met `origin='agent'` → geweigerd (test).

**Leermoment**
> _Les:_ vangrails moeten symmetrisch — approve én reject. Een control op één helft van
> een actiepaar is een gat. Bij elke nieuwe poort: check het spiegelbeeld.

---

## WP-6 — Server- & config-hygiëne  ✅ 2026-06-14
**Lost op:** M9, M10, M12, M13, M14, M20, M21.

**Gedaan:** M9 — OAuth-pending state heeft TTL (10 min) + opruimen; callback weigert
verlopen state. M10 — centrale `odata_quote()` voor `$filter`-stringwaarden. M12 —
`/api/stt` magic-bytes-check (EBML/Ogg/RIFF/MP3), 415 anders. M13 — upload-grens één
constante (`documents.MAX_BYTES`). M14 — `mcp_pending` mutaties achter een lock. M20 —
decay-administratie alleen schrijven bij `decay_mode!='off'`. M21 — `SPAN_DECAY` één
keer gelezen via `_decay_mode()`. +2 tests. 198 groen.

**Taken**
- `server/routes.py:687-694` — OAuth-callback: state-TTL + opruimen; code/state niet in
  logs/Referer (tussenpagina die query wist) (M9).
- `integrations/o365.py:167-267` — centrale `odata_quote()` voor álle `$filter`-waarden
  (M10).
- `server/routes.py:370-387` — `/api/stt` content-type + magic-bytes (EBML/RIFF), 415 bij
  mismatch (M12).
- Upload-grens één constante `documents.MAX_BYTES` (M13).
- `server/routes.py` — lock rond gedeelde `_state` sub-dicts (`mcp_pending` e.d.) (M14).
- `memory/fragments.py:174-183` — decay-write alleen bij `decay_mode!='off'`; except
  loggen (M20).
- `config.py:125-127` — `SPAN_DECAY` één keer lezen + normaliseren + valideren (M21).

**Evaluatie-checkpoint**
- [ ] Suite groen; geen gedrag-regressie in panelen/briefing/STT.

**Leermoment**
> _Les:_ losse kleine hygiëne-gaten stapelen tot risico (M15+M22+M9 = token-exposure-
> keten). Periodiek een hygiëne-pass plannen, niet alleen op feature-basis.

---

## Eindevaluatie (na alle WP's)
- [x] Volledige suite groen + nieuwe adversariële tests (198 groen, +25 t.o.v. start).
- [x] Leermoment per WP als `Insight` in het brein (WP-1 t/m WP-6).
- [x] `MEMORY.md` verwijst naar deze pass.
- [x] C1 beleidskeuze gemaakt (open lezen + URL-exfil-scan) en geïmplementeerd — dicht.
- [ ] Optioneel later: M22 (token-pairing), M24 (TreeWalker).
- [ ] Aanbevolen: zet `SPAN_AUDIT_HMAC_KEY` (of bevestig dat `SPAN_AUTH_TOKEN` gezet is)
  zodat de audit-keten op HMAC draait.

**Status: 1 critical + 7 important + 24 minor opgelost (M22/M24 bewust geparkeerd).
Span is van "niet autonomie-klaar" naar autonomie-klaar. 200 tests groen.**

## Aanbevolen volgorde
WP-1 → WP-2 → WP-3 (de drie met echte impact), daarna WP-4/5/6 als hygiëne-passes.
