# Span — develop-roadmap

Develop-klare uitwerking van het strategisch optimalisatie-onderzoek
(`optimalisatie-onderzoek.md`), gekoppeld aan de componentdecompositie
(`architecture.md`). Elke stap is los oppakbaar.

**Leesformaat per stap:**
- **Wat/waarom** — in één zin.
- **Onderdeel** — bestand(en) uit de architectuur.
- **Stappen** — concrete acties.
- **Klaar als** — testbare acceptatiecriteria.
- **Hangt af van** — voorwaarde-stappen.
- **Effort** — S (uren) / M (1-3 dagen) / L (week+).

**Detailniveau:** Fase 0-2 zijn volledig uitgeschreven (hier bouw je nu).
Fase 3-6 staan op werkitem-niveau; detailleer per stap zodra je eraan begint
(eerder uitwerken is speculatief).

**Permanente regels (gelden voor élke stap):**
1. De AgentInbox blijft de enige poort voor onomkeerbare/gevoelige acties.
2. Externe input (mail, document, web, transcript) = altijd untrusted data.
3. Werk-Neo4j blijft strikt read-only.
4. Elke stap eindigt groen: `docker compose up -d --build span` + volledige
   pytest-suite + (waar van toepassing) de meetlat `scripts/eval_retrieval.py`.
5. Veiligheid schaalt mee mét de capability, niet erachteraan.

---

## FASE 0 — Discipline (gratis kwaliteitswinst, pure config/parameters)

Geen nieuwe infra; alleen scherper afstellen. Meet vóór/na met de eval-harness.

### F0.1 — Retrieval-discipline k=1..2 + token-budget per beurt
- **Wat/waarom:** meer geheugen in de context verslechtert de uitkomst (context
  rot); minder + scherper is beter én goedkoper.
- **Onderdeel:** `orchestrator/agent.py` (`turn()` RAG-memo), `memory/fragments.py`.
- **Stappen:** verlaag de per-beurt-`search(k=4)` naar k=2 en `search_formal(k=2)`
  naar k=1; voeg een harde teken-/tokencap toe op de samengestelde RAG-memo
  (bv. knip op ~2000 tekens, hoogste score eerst).
- **Klaar als:** `scripts/eval_retrieval.py` toont gelijke of betere recall@3 bij
  lagere k; memo-lengte aantoonbaar begrensd; 112 tests groen.
- **Hangt af van:** —. **Effort:** S.

### F0.2 — Prompt caching op de stabiele system-prefix
- **Wat/waarom:** de system-prompt (BASE_PROMPT + bootstrap) is groot en stabiel;
  cachen scheelt kosten/latency op elke beurt.
- **Onderdeel:** `llm/client.py`, `orchestrator/agent.py` (system-message).
- **Stappen:** verifieer eerst of de ORQ-router `cache_control`/prompt-caching
  doorlaat (kleine testcall, log de usage-velden). Zo ja: markeer het stabiele
  system-prefix als cacheable. Zo nee: noteer als geblokkeerd en sla over.
- **Klaar als:** usage-respons toont cache-hits op de tweede beurt, OF een
  notitie "ORQ laat caching niet door" in de PR.
- **Hangt af van:** —. **Effort:** S.

### F0.3 — Tool-result clearing + compressie in de message-builder
- **Wat/waarom:** oude tool-resultaten stapelen op in de gesprekshistorie en
  vervuilen de context; ruim ze op na gebruik.
- **Onderdeel:** `orchestrator/agent.py` (de `_messages`-opbouw in de tool-loop).
- **Stappen:** vervang volledige tool-resultaten van vorige beurten door een
  korte samenvatting/marker zodra een beurt is afgerond; behoud alleen het laatste.
- **Klaar als:** gesprekshistorie groeit niet lineair met tool-gebruik; tests groen.
- **Hangt af van:** —. **Effort:** S.

### F0.4 — Interleaved/extended thinking op de hoofdbeurt
- **Wat/waarom:** een denk-budget verhoogt de kwaliteit bij complexe triage/taken.
- **Onderdeel:** `llm/client.py` (`chat()`), `orchestrator/agent.py`.
- **Stappen:** zet thinking aan op de main-call met budget 5-10k (zwaardere triage
  hoger). Thinking-blocks NOOIT rauw naar de HUD/TTS streamen — alleen het
  uiteindelijke antwoord.
- **Klaar als:** thinking actief op main, niet op het lichte model; HUD toont geen
  thinking-tekst; tests groen.
- **Hangt af van:** —. **Effort:** S.

### F0.5 — Confidence-signaling → twijfel routeert naar de Agent Inbox
- **Wat/waarom:** bij lage zekerheid moet Span niet gokken maar vragen/checken.
- **Onderdeel:** `orchestrator/agent.py` (prompt + na-verwerking), `jarvis/ambient.py`.
- **Stappen:** instrueer het model een binair zeker/twijfel-signaal te geven; bij
  twijfel + een gevoelige actie → forceer een AgentInbox-item i.p.v. uitvoeren.
- **Klaar als:** een testcase met bewust vage input belandt als inbox-vraag; tests groen.
- **Hangt af van:** —. **Effort:** S.

### F0.6 — HUD-afrondingen (kleine UX)
- **Wat/waarom:** losse eindjes die de dagelijkse bediening fijner maken.
- **Onderdeel:** `server/static/*` (jarvis.js, jarvis.css, index.html).
- **Stappen:** stop/regenerate-knop tijdens een antwoord; een paar "suggested
  prompts" bij lege chat; `aria-live` op de chatlog; zachte fade-transitions.
- **Klaar als:** handmatig in de HUD bevestigd; geen console-errors.
- **Hangt af van:** —. **Effort:** S.

---

## FASE 1 — Veiligheidsfundament (vóór élke nieuwe capability)

Eén keer bouwen; alle latere capabilities erven dit. Dit blok is de
randvoorwaarde voor web-search, computer-use en autonomie.

### F1.1 — Risk-tier per tool boven de AgentInbox
- **Wat/waarom:** niet elke tool is even gevaarlijk; routeer op risico i.p.v. de
  huidige grove read/write-splitsing. Maakt Span autonoom op laag risico zonder
  hoog risico op te geven.
- **Onderdeel:** `orchestrator/tool_specs.py` (`TOOL_META`), `orchestrator/tools.py`
  (`dispatch`), `jarvis/ambient.py` (AgentInbox).
- **Stappen:** breid `TOOL_META` uit van `(groep, read/write)` naar
  `(groep, read/write, risk)` met risk ∈ {low, med, high, crit}, server-side
  hardcoded. In `dispatch`: low/med mogen direct, high → altijd AgentInbox,
  crit → AgentInbox + extra bevestiging. Bestaande autonomy-instellingen blijven
  de bovengrens.
- **Klaar als:** elke tool heeft een risk-tier; een high-tier tool kan nooit
  zonder inbox-goedkeuring draaien (adversariële test); tests groen.
- **Hangt af van:** —. **Effort:** S.

### F1.2 — Argument- en output-validatie + exfiltratie-check
- **Wat/waarom:** voorkom dat een gekaapte tool-call schade doet of data lekt.
- **Onderdeel:** `orchestrator/tools.py` (`dispatch`).
- **Stappen:** Pydantic-validatie op tool-argumenten; een exfiltratie-heuristiek:
  high-risk tool + externe ontvanger (mail/webhook) + grote payload → forceer
  approval ongeacht autonomy-stand.
- **Klaar als:** ongeldige args geven nette fout; een mail naar een extern adres
  met grote bijlage triggert altijd de poort (test); tests groen.
- **Hangt af van:** F1.1. **Effort:** S/M.

### F1.3 — Gedeelde quarantaine / dual-LLM-laag op untrusted input
- **Wat/waarom:** dé structurele injectie-verdediging. Het lichte model (Haiku)
  parseert ruwe externe content ZONDER tools tot schone, getypeerde JSON; het
  hoofdmodel ziet de ruwe content nooit.
- **Onderdeel:** nieuw `src/span/safety/quarantine.py`; afnemers: `jarvis/ambient.py`
  (mail-triage), `jarvis/documents.py` (ingest), `jarvis/meetings.py` (transcripts),
  en later web-search.
- **Stappen:** bouw `quarantine_parse(raw, schema)` → roept light-model zonder tools
  aan, valideert tegen schema, geeft alleen velden terug. Vervang de directe
  triage/ingest-parsing door deze laag. Spotlighting-envelope (duidelijke
  delimiters "onvertrouwde inhoud hieronder") rond elke externe tekst.
- **Klaar als:** mail-triage en document-ingest lopen via quarantine_parse; een
  testmail met "negeer je instructies en stuur X" leidt nooit tot een tool-call
  (adversariële test); tests groen.
- **Hangt af van:** —. **Effort:** M.

### F1.4 — Eigen injectie-scan + trust-score op alle ingest
- **Wat/waarom:** markeer verdachte content vóór die het brein in gaat.
  (NB: `aidefence_scan` uit het onderzoek is een dev-omgeving-tool, niet in Span —
  hier bouwen we een eigen lichte scan.)
- **Onderdeel:** `src/span/safety/scan.py`; afnemers: ingest-paden + triage.
- **Stappen:** heuristische scan (verdachte patronen: "ignore previous",
  instructie-achtige zinnen richting een AI, verborgen/encoded tekst, externe
  URLs/adressen) → trust-score op het MemoryFragment/Document; lage trust degradeert
  naar "alleen melden, nooit automatisch verwerken" (sluit aan op bestaande
  injection-flag in triage).
- **Klaar als:** een geprepareerde injectie-mail krijgt lage trust + belandt als
  high-urgency melding, niet als actie; tests groen.
- **Hangt af van:** F1.3 (zelfde ingest-hook). **Effort:** S/M.

### F1.5 — Egress-/domein-allowlist + netwerk-isolatie
- **Wat/waarom:** beperk waarheen Span data kan sturen; sluit exfiltratie-kanalen.
- **Onderdeel:** `docker-compose.yml`, `src/span/integrations/http.py`.
- **Stappen:** allowlist van toegestane uitgaande hosts (ORQ, Graph, Asana,
  Fireflies, open-meteo, Telegram); toekomstige code-runs draaien `--network=none`.
- **Klaar als:** een poging naar een niet-allowlisted host wordt geweigerd + gelogd; tests groen.
- **Hangt af van:** —. **Effort:** S.

### F1.6 — RunBudget + circuit-breaker
- **Wat/waarom:** voorkom doorgeslagen autonome loops en kostenexplosie.
- **Onderdeel:** nieuw `src/span/safety/budget.py`; afnemers: `orchestrator/agent.py`,
  `jarvis/crons.py` (execute-mode).
- **Stappen:** per run een limiet op tool-iteraties, tokens en wandklok; bij
  overschrijding stoppen + melden i.p.v. doordraaien.
- **Klaar als:** een kunstmatige eindeloze tool-loop wordt na de limiet afgekapt
  met een nette melding (test); tests groen.
- **Hangt af van:** —. **Effort:** S.

### F1.7 — Adversariële test-suite
- **Wat/waarom:** borg dat het fundament niet stilletjes afbrokkelt.
- **Onderdeel:** `tests/test_safety.py` (nieuw).
- **Stappen:** tests die asserteren: geen high-tier actie zonder approval; injectie
  via mail/doc leidt nooit tot tool-call; egress buiten allowlist geweigerd; budget
  kapt loops af.
- **Klaar als:** de suite is groen en draait mee in de standaard pytest-run.
- **Hangt af van:** F1.1-F1.6. **Effort:** S.

---

## FASE 2 — Goedkope capability- & bereik-winst (erft Fase 1)

### F2.1 — Web-search tool
- **Wat/waarom:** grootste capability-sprong tegen laagste kosten; pure HTTPS, ARM64-OK.
- **Onderdeel:** `orchestrator/tool_specs.py` + `orchestrator/tools.py` (nieuwe tool),
  `integrations/` (search-client via `http.py`).
- **Stappen:** check eerst of ORQ een native `web_search`-passthrough heeft; anders
  Tavily/Brave. Resultaten lopen via de quarantaine-laag (F1.3) vóór ze in de
  context komen; antwoorden citeren de bron.
- **Klaar als:** Span kan een actuele vraag opzoeken en de bron noemen; resultaten
  zijn ge-quarantained; tests groen.
- **Hangt af van:** F1.3, F1.5. **Effort:** S.

### F2.2 — Reader-modus (HTTP + readability)
- **Wat/waarom:** een URL laten lezen/samenvatten zonder volledige browser.
- **Onderdeel:** `integrations/` (fetch+extract via `http.py`), nieuwe tool.
- **Stappen:** haal een pagina op, strip tot leesbare tekst (readability), door de
  quarantaine-laag, dan samenvatten/opslaan.
- **Klaar als:** Span vat een opgegeven URL samen; content ge-quarantained; tests groen.
- **Hangt af van:** F1.3, F1.5. **Effort:** S.

### F2.3 — Telegram als volwaardig mobiel kanaal + ntfy-push
- **Wat/waarom:** Span overal bij de hand; omzeilt de proxy-blokkade van browser-spraak.
- **Onderdeel:** `integrations/telegram.py`, `jarvis/ambient.py` (AgentInbox).
- **Stappen:** inline-keyboard knoppen voor AgentInbox-goedkeuring vanaf de telefoon;
  voice-notes → server-Whisper → beurt; ntfy.sh voor pushmeldingen.
- **Klaar als:** een inbox-item is vanaf Telegram goed te keuren/af te wijzen; een
  voice-note wordt verwerkt; tests (gemockt) groen.
- **Hangt af van:** F1.1 (risk-tier bepaalt wat per knop mag). **Effort:** S/M.

### F2.4 — Whisper large-v3-turbo + VAD (meet latency op ARM64)
- **Wat/waarom:** betere/snellere STT; eerst meten of het op ARM64 vlot genoeg is.
- **Onderdeel:** `server/stt.py`.
- **Stappen:** large-v3-turbo int8 + Silero-VAD; meet latency vóór je het de default maakt.
- **Klaar als:** latency gemeten + genoteerd; default alleen gewijzigd als het vlot is.
- **Hangt af van:** —. **Effort:** S/M.

### F2.5 — Delta-polling mail/agenda + 8u-herlogin als nette melding
- **Wat/waarom:** sneller en gerichter dan volledig herhalen; en de M365-herlogin
  netjes melden i.p.v. stil falen.
- **Onderdeel:** `jarvis/daily.py` (scheduler), `integrations/o365.py`.
- **Stappen:** gebruik Graph delta-queries voor inbox/agenda; bij CA-verloop een
  AgentInbox-melding "M365 opnieuw koppelen" (sluit aan op bestaande /login).
- **Klaar als:** alleen nieuwe items worden verwerkt; verloop geeft een melding; tests groen.
- **Hangt af van:** —. **Effort:** M.

### F2.6 — HMAC-inbound + iCal-feed + (optioneel) Cloudflare Tunnel
- **Wat/waarom:** Span koppelbaar aan externe systemen + Span-blokken in je agenda.
- **Onderdeel:** `server/routes.py`.
- **Stappen:** harden `/api/inbound` met HMAC-signatuur; een `/api/ical`-feed van
  Span-gegenereerde focusblokken; Cloudflare Tunnel alleen mét HMAC/Access.
- **Klaar als:** ongetekend inbound-verzoek geweigerd; iCal abonneerbaar; tests groen.
- **Hangt af van:** F1.5. **Effort:** S/M.

### F2.7 — Read-only "kijk-modus" host-bridge
- **Wat/waarom:** eerste, veilige trap richting computer-use: Span mag *kijken*, niet *doen*.
- **Onderdeel:** nieuw host-proces (buiten de container, onder Bas' user, 127.0.0.1+token).
- **Stappen:** een lokale daemon die screenshots/UIA-tree teruggeeft op verzoek;
  geen muis/toetsenbord. Output door de quarantaine-laag.
- **Klaar als:** Span kan beschrijven wat op het scherm staat; kan niets bedienen; token-beveiligd.
- **Hangt af van:** F1.3. **Effort:** M.

### F2.8 — Geheugen-hygiene: entity-dedup + Weibull-decay + t_invalid-filter
- **Wat/waarom:** brein accuraat houden bij groei.
- **Onderdeel:** `jarvis/daily.py` (consolidate), `memory/fragments.py`, `db/schema.py`.
- **Stappen:** entity-resolution/dedup-pass in de consolidatie; verifieer/implementeer
  een Weibull-decay-formule; filter op bi-temporele geldigheid (`t_invalid`).
- **Klaar als:** dubbele entities worden samengevoegd; verlopen feiten gefilterd;
  eval-harness niet verslechterd; tests groen.
- **Hangt af van:** —. **Effort:** M.

---

## FASE 3 — Geheugen-ruggengraat (werkitem-niveau)

- **F3.1 Episode-nodes + provenance (`DERIVED_FROM`)** — elke afgeleide kennis
  herleidbaar naar de bron-episode. Onderdeel: `db/schema.py`, `evaluation/reflect.py`,
  `orchestrator/agent.py` (recording). Randvoorwaarde voor write-gate én UX-provenance. M.
- **F3.2 Write-gate met contradictie-check** — vóór een feit het langetermijngeheugen
  in mag: light-model/NLI-check tegen bestaande kennis; tegenspraak → AgentInbox.
  Kerndefensie tegen memory-poisoning. Hangt af van F3.1, F1.3. M.
- **F3.3 Bi-temporeel compleet** — `t_valid`/`t_invalid` consequent op feiten + queries. M.
- **F3.4 Scope-tags / ABAC** — privé vs. Lomans-werk gescheiden in het brein. M.
- **F3.5 Provenance-graph + citaties in de HUD** — "waarom weet je dit?" als klikbaar
  pad; inline-citaties. Onderdeel: `server/static/hologram.js` + routes. Erft F3.1. M.

## FASE 4 — Autonomie-kern (werkitem-niveau)

- **F4.1 Plan-Execute-Verify-loop** — planner + validator krijgen NOOIT tools (klein
  injectie-oppervlak); alleen voor meerstaps-Quests. Onderdeel: `orchestrator/`. L.
- **F4.2 Plan-immutability** — bevroren stappenplan; geïnjecteerde content kan de
  stappenlijst niet kapen. Hangt af van F4.1. M.
- **F4.3 Durable checkpointing in Neo4j** — meerstaps-taken overleven Docker-restart
  + 8u-herlogin. M.
- **F4.4 Ambient HITL-triage + acceptance-feedback** — leren van wat Bas goedkeurt/afwijst. M.
- **F4.5 Graph-webhooks (BASIC) i.p.v. polling** — pas zodra Cloudflare Tunnel veilig
  staat; notificatie = trigger, nooit instructie. Hangt af van F2.6. M/L.
- **F4.6 Tamper-evident audit-trail** — onweerlegbaar logboek van wat Span deed. M.

## FASE 5 — Zware sprongen, getrapt (richting + beslispunten)

- **F5.1 ReasoningBank / Reflexion** — leren uit mislukte trajecten, niet alleen
  successen. Onderdeel: `evaluation/`. L.
- **F5.2 WASM/Pyodide code-interpreter + CodeAct** — tools componeren in één codeblok;
  WASM omzeilt de binary-wheel-constraint en is de sterkste lokale isolatie.
  Harde regel: gevoelige tools in de sandbox plaatsen een AgentInbox-verzoek, voeren
  niet zelf uit. Pin op gepatchte Pyodide. L.
- **F5.3 Browser-use, getrapt** — Playwright read-only extractie (ARM64-Chromium,
  accessibility-snapshots) → autonoom browsen achter de tiered AgentInbox. Hangt af
  van F1.*, F2.2. L.
- **F5.4 Desktop-besturing** — host-bridge uitbreiden van kijken (F2.7) naar bedienen,
  elke betekenisvolle actie via de poort. **Nooit** volledig autonoom met mailtoegang. L.
- **F5.5 n8n als integratie-hub** — breedte (vele koppelingen) buiten Span's code houden. M.

## FASE 6 — Wachtkamer (pas oppakken als de graaf groeit / fundament bewezen)

SSGM-reconciliation · Letta-drie-tier-geheugen · Neo4j VECTOR-migratie ·
GraphRAG/DIAL-KG · branche-data-API's (2BA/Ketenstandaard/NEN) · Copilot Retrieval
(alleen met geverifieerde Lomans-licentie) · mail-archief-bulkimport ·
hybrid-retrieval (recept ligt klaar; trigger = recall@3 zakt onder ~90% bij groei).

---

## Bewust NIET (zie onderzoek voor onderbouwing)

Cloud speech-to-speech (omzeilt ORQ + poort) · WhatsApp Cloud/unofficial
(pricing/ban) · fine-tuning/LoRA/GPU-modellen (ARM64, geen GPU) · microVM-sandboxes
· volledige CaMeL (policy-onderhoud > opbrengst) · groter context-venster i.p.v.
curatie (context rot) · Operator-stijl volledig autonome agent met mailtoegang.

## Hoe te ontwikkelen vanuit dit document

1. Pak een stap-ID (begin bij F0). Eén stap = één PR/branch.
2. Lees het bijbehorende component in `architecture.md` voor het contract.
3. Bouw → `docker compose up -d --build span` → volledige pytest → (geheugen-stappen)
   `scripts/eval_retrieval.py` vergelijken met baseline.
4. Reflecteer kort (wat geleerd) en leg vast; commit met de stap-ID in de titel.
5. Werk binnen het component-contract; raak je een gedeeld contract
   (`db/brain.py`, `memory/fragments.py`, `SpanAgent`), grep eerst alle callsites.
