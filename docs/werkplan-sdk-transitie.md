# Transitie-werkplan — Span op de Claude Agent SDK (abonnement + API-backup)

Bron: multi-agent analyse (21 agents, geverifieerd tegen de echte Span-code én de
officiële Agent-SDK-docs), 2026-06-15. We bouwen pas ná Bas' beslissingen (§6).

## 1. Samenvatting & advies

**Kan dit? Ja. Moet dit volledig? Nee — selectief.** Span heeft één schone migratie-naad
(`span.llm.client.LLMClient`, het enige provider-aanraakpunt) + een eigen, bewust ontworpen
veiligheids-/tool-loop. De Agent SDK kan daar bovenop, maar mag de safety-laag, RAG en
embeddings niet verdringen.

**Integratiemodel: B (SDK-loop + hooks), niet A.**
- **Model A bestaat feitelijk niet** bij de Agent SDK: de SDK draait per definitie de Claude
  Code agent-loop, levert geen "kale LLM". Wie alleen tekst+tool-calls + eigen loop wil, hoort
  bij de Client SDK (`anthropic`/`messages.create`). Span in "kale modus" dwingen = vechten
  tegen het product, alle migratiekosten en geen SDK-voordelen.
- **Model B geeft een echte bindende gate:** `can_use_tool` + `PreToolUse`-hook draaien
  deterministisch vóór elke tool-uitvoering (`deny` blokkeert ook in `bypassPermissions`) —
  gelijkwaardig aan Span's huidige `assess_tool`-gate, mits de besluitlogica in die callback
  herbouwd wordt.

**Nuance:** Span draait nu via ORQ.AI (OpenAI-compatibel, Bedrock-prefixed ids). Dit is een
echte overstap van een zelf-gestuurde loop naar Claude's autonome loop. **Embeddings blijven
buiten de SDK** (Anthropic heeft geen embeddings-endpoint) → `LLMClient.embed*` blijft op
ORQ/OpenAI `text-embedding-3-large` (1024 dims) voor de Neo4j HNSW-indexen.

**Eén-zinsadvies:** bouw model B als *tweede* chat-backend achter de bestaande `LLMClient`-
interface, met een subscription-first→API-backup auth-router, hef Span-tools naar in-process
SDK-MCP-tools (`mcp__span__*`) onder één `PreToolUse`/`can_use_tool`-gate, en houd embeddings +
het lichte achtergrondmodel (recording/triage/quarantaine) op ORQ/API om de gecapte maand-
credit te sparen.

## 2. Doelarchitectuur
```
 SpanAgent.turn() (agent.py)  — RAG-injectie · :TOUCHED · recording   ← blijft Span
        │ chat()-contract (.content/.tool_calls/on_text)
   ChatBackend (nieuw: span/llm/backend.py)
     ├─ SdkChatBackend (Agent SDK, abonnement-credit)        ← interactief
     │     AuthRouter: CLAUDE_CODE_OAUTH_TOKEN default → bij credit-op/fout: API-key
     │     SDK-loop: system_prompt=BASE_PROMPT · model/fallback per tier
     │       · mcp_servers=create_sdk_mcp_server(span)  (Span-tools als @tool)
     │       · can_use_tool/PreToolUse → assess_tool   (BINDENDE GATE)
     │       · PostToolUse → audit-hashchain + quarantine
     │       · max_turns + eigen wandklok (RunBudget)
     └─ OrqChatBackend (= huidige LLMClient.chat/chat_json, ORQ/API)  ← achtergrond/light
 BUITEN de SDK (blijft ORQ/OpenAI): embeddings · recording · triage · quarantine · planner · reflect · daily
```

| Onderdeel | Route | Waarom |
|---|---|---|
| Interactieve `turn()` (HUD/Telegram/voice) | **SDK / abonnement** | Bas live; SDK-voordelen tellen hier |
| Embeddings | **ORQ/OpenAI** | SDK heeft geen embeddings; RAG-fundament |
| Recording/triage/quarantaine/planner/reflect/daily (`model_light`) | **ORQ/API** | Always-on; zou de maand-credit leegtrekken |
| Auth-router (subscription→API-backup) | **`span/llm/auth.py` (nieuw)** | API-key in env wint áltijd van OAuth → pas bij failover injecteren |
| Bindende veiligheidslaag | **`PreToolUse`/`can_use_tool` (SDK) + behoud `dispatch`-gate (ORQ)** | Beide paden door `assess_tool` |

## 3. Behoud-garanties (mag niet sneuvelen)
- **Veiligheidslaag** (`guard.assess_tool`, `risk.py`, `egress.py`): herbruiken vanuit de hook.
  De matcher matcht op toolnaam, niet args → exfil-check/fail-closed/origin-vangrail in de
  callback-body via `tool_input`. Span's 3e uitkomst ("queue in AgentInbox") = `deny`+queue-tool
  of `ask`/defer. `permission_mode='default'`, **nooit `bypassPermissions`** (subagents erven dat).
- **RAG/embeddings**: ongewijzigd; de ephemere RAG-memo blijft Span's werk, vóór de SDK-`query()`
  in de prompt meegegeven (SDK kent Neo4j niet).
- **Streaming/HUD**: adapter mapt SDK `TextBlock`-deltas (`include_partial_messages`) → `on_text`;
  live-leescascade verschuift naar `PostToolUse` van `mcp__span__brain_search`.
- **Audit & RunBudget**: audit via `PostToolUse`/`@tool`; iteratie-cap → `max_turns`; **wandklok
  heeft de SDK niet** → behouden als eigen bewaking (Stop-hook/rond de stream).
- **Read-only werk-DB**: `work_cypher`/`brain_cypher` worden `@tool`-handlers, blijven door de gate.

## 4. SDK-features benutten
- **Model-keuze**: `ClaudeAgentOptions(model, fallback_model)` + `client.set_model()`; map op
  Config-node `model_main`/`model_light`. **Let op id-mapping** (ORQ/Bedrock-prefix → kale
  Anthropic-id of Bedrock onder `CLAUDE_CODE_USE_BEDROCK`). `fallback_model` = model-fallback,
  GEEN auth-fallback.
- **Auto-update**: SDK bundelt de CLI-binary, versie gepind; "auto-update" = pip-upgrade, geen
  runtime-zelfupdate. **Pin SDK+CLI-versie** in Docker, test upgrades in staging.
- **Tools/MCP**: `@tool` + `create_sdk_mcp_server` (in-process, type-safe); naam `mcp__span__*`
  sluit aan op bestaande `mcp__`-herkenning in `risk.py`. Quarantaine via `PostToolUse`
  `updatedToolOutput`. `ToolUseBlock.input` = dict → `json.loads` in `agent.py:331` vervalt.
- **Hooks**: eval-volgorde hooks→deny→ask→mode→allow→`can_use_tool`. Een `allow`-hook slaat
  deny/ask NIET over. Zet tools NIET in `allowed_tools` (omzeilt de gate) — vertrouw op
  `can_use_tool`/`PreToolUse` als bindende gate.
- **Sessies**: `resume`/`fork_session`; SDK-sessies = JSONL op disk = korte-termijn context.
  Blijvend geheugen blijft Neo4j (graph-as-brain). Prompt-`cache_control`-markering vervalt
  (SDK regelt caching zelf).

## 5. Gefaseerd werkplan (ORQ en SDK naast elkaar via env-flag; niets breekt tot cutover)
- **WP-0 — Voorwaarden/beslissingen (geen code):** Bas neemt §6; `claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN` (buiten repo bewaren); EU-residency-route bevestigen; credit-opt-in.
  *Checkpoint:* `claude -p "hallo"` op abonnement (geen `ANTHROPIC_API_KEY` in env).
- **WP-1 ✅ (2026-06-16) — Spike uitgevoerd in de container (Node 20 + claude-code CLI 2.1.178
  + claude-agent-sdk, runtime-geïnstalleerd, NIET in de image).** Bevindingen met Bas' (tijdelijke)
  OAuth-token:
  - **Auth: WERKT.** Eerste run gaf een echt antwoord op het abonnement via `CLAUDE_CODE_OAUTH_TOKEN`,
    zonder API-key. (Latere run: `401 Invalid bearer token` — token intussen geroteerd/verlopen.)
  - **Auth-fout-signatuur (voor WP-3 failover):** de SDK gooit GEEN exception; hij doet eerst
    `SystemMessage(subtype='api_retry')` (tot 10x), dan een **synthetische
    `AssistantMessage(error='authentication_error', text='Failed to authenticate. API Error: 401
    Invalid bearer token')`** + **`ResultMessage(is_error=True)`**. → de router detecteert op
    `ResultMessage.is_error` + de auth-/credit-foutmelding, niet op een raised error.
  - **Streaming: GROF (blok).** `receive_response()`/`receive_messages()` leveren het antwoord als
    één `AssistantMessage` met een `TextBlock`; géén per-token `text_delta`-partials (ook met
    `include_partial_messages=True`). → **de HUD-woord-voor-woord-stream regressie-risico**; vóór
    cutover een fijnmazige streaming-weg vinden of het blok-gedrag accepteren.
  - **Deny:** niet bevestigd (het model riep de testtool niet aan); opnieuw testen met een tool
    die zeker aangeroepen wordt.
  *Leermoment:* failover = `ResultMessage.is_error` + auth/credit-melding; Node+CLI+SDK MOET in
  de Dockerfile (nu runtime); streaming is blok → HUD-afweging.
- **WP-2 ✅ (2026-06-15) — `ChatBackend`-interface + `OrqChatBackend` (refactor, gedrag identiek):**
  `src/span/llm/backend.py` gebouwd (ChatBackend-protocol + OrqChatBackend + `select_backend`
  op `SPAN_CHAT_BACKEND`, default orq, 'sdk' valt veilig terug). `LLMClient` delegeert chat,
  houdt `embed*`/`chat_json`/`list_models`; temperature-logica meeverhuisd + re-export voor compat.
  *Checkpoint gehaald:* 203 tests groen, live chat via de naad = "OK", backend=orq. Spike-script
  `scripts/spike_sdk.py` klaar voor WP-1 (door Bas te draaien met token).
- **WP-3 — Auth-router subscription→API-backup (`src/span/llm/auth.py`) + FIRST-RUN SETUP-WIZARD
  (Bas' keuze 2):** abonnement default, API-key alleen bij failover, nooit beide in proces-env.
  **Onboarding:** bij eerste opstart zonder geconfigureerde LLM-auth boot Span in "setup-modus"
  (niet hard-failen) → HUD-setupscherm met keuze **(1) Log in met Claude-abonnement** (leidt naar
  `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`) **of (2) API-sleutel (ORQ)** + model + optionele
  integratie-sleutels. Opslag in `~/.span/secrets.json` (0600, BUITEN het brein, overleeft rebuilds),
  `.env` als fallback. `config.py`: `ORQ_API_KEY` niet meer hard-`_require` → optioneel + setup-modus.
  **Infra blijft in `.env`/compose:** `NEO4J_PASSWORD` (gedeeld met DB-container, nodig bij boot) +
  `SPAN_AUTH_TOKEN`; minimale `.env.example` documenteert alleen die laag.
  *Checkpoint:* (a) geforceerde failover schakelt; normaalbedrijf raakt API-key niet. (b) kale install
  met alleen infra-`.env` boot in setup-modus; na keuze via het scherm werkt Span zonder handmatige
  `.env`-LLM-regel. *Leermoment:* secrets nooit in het brein; welke auth de SDK in Docker oppikt.
- **WP-4 — Span-tools als SDK-MCP + bindende gate:** `@tool`-wrappers; `can_use_tool`/`PreToolUse`
  → `assess_tool`; `PostToolUse` → audit + quarantaine. *Checkpoint:* rode-team-set (externe mail,
  high MCP, agent-keurt-eigen-item) net zo geblokkeerd als nu.
- **WP-5a ✅ — image SDK-capabel:** Node 20 + Claude CLI 2.1.178 + claude-agent-sdk in de Dockerfile.
- **WP-5b ✅ (2026-06-16) — `SdkChatBackend` + `RoutedChatBackend` (subscription-first, ORQ-backup):**
  `src/span/llm/sdk_backend.py`. Tekst via de SDK op het abonnement; tool-beurten (nog) naar ORQ;
  bij auth/credit-fout transparante failover naar ORQ (detectie op `ResultMessage.is_error` +
  auth/credit-melding). `SPAN_CHAT_BACKEND=sdk` → routed-backend (valt terug op ORQ als SDK ontbreekt).
  +6 tests, 209 groen. **Live geverifieerd:** flag=sdk + ongeldig token → SDK-poging → failover naar
  ORQ → antwoord OK. Streaming = blok (HUD-afweging blijft).
- **WP-5c — turn-level SDK-loop met tools (rest):** Span-tools als in-process MCP + bindende gate via
  `can_use_tool`/`PreToolUse` (WP-4) + audit/quarantaine via `PostToolUse`, zodat ook tool-beurten op
  het abonnement kunnen. Vereist live-verificatie met een GELDIG token. *Checkpoint:* rode-team-set
  net zo geblokkeerd; volledige beurt (RAG, tools, antwoord) via de SDK.
- **WP-6 — Credit-bewust routeren:** ambient/cron/recording op ORQ, interactief op credit +
  credit-monitor. *Checkpoint:* 24u-soak — ambient raakt credit niet; bij credit-op valt
  interactief netjes terug op API.
- **WP-7 — Cutover + rollback:** flag default `sdk`; rollback = flag terug op `orq` (één env-var).
  Pin SDK+CLI-versie. *Checkpoint:* week stabiel; rollback getest.

## 6. Risico's & open beslissingen voor Bas
**Risico's:** (1) **credit-cap vs always-on (hoog)** — aparte maand-credit (Pro $20/Max5x $100/
Max20x $200), geen rollover; op = overflow naar API-tarief alleen als ingeschakeld, anders stop.
(2) **auth-precedence (hoog)** — `ANTHROPIC_API_KEY` in env wint áltijd → backup pas bij failover
injecteren. (3) **observability-verlies (midden)** — ORQ centraliseert nu kosten/logging; voor
het SDK-pad zelf bouwen. (4) **ToS (midden)** — eigen-abonnementgebruik lijkt toegestaan;
24/7-always-on niet letterlijk benoemd → **bevestig bij Anthropic vóór productie**. (5) drie punten
(streaming-granulariteit, credit-op-foutcode, per-query auth-override) → afgedekt door spike WP-1.

**Beslissingen vóór de bouw:**
- **6.1** Akkoord met **model B** (SDK-loop + hooks)?
- **6.2** Opt-in maand-credit + welk plan (Pro/Max5x/Max20x)?
- **6.3** Usage-credits (overflow) aan (geen stilstand, duurder) of uit (harde stop → backup moet waterdicht)?
- **6.4** Always-on (ambient/recording/reflect) op API/ORQ houden (aanbevolen) of alles op credit (cap-risico)?
- **6.5** Embeddings op ORQ houden (default) of lokaal op de 15GB-VRAM-GPU (apart traject; raakt alle HNSW-indexen)?
- **6.6** EU-data-residency: kale Anthropic-ids of Bedrock (`CLAUDE_CODE_USE_BEDROCK`)?
- **6.7** SDK+CLI-versie pinnen in Docker (aanbevolen)?

**Bestanden:** `llm/client.py` (naad), nieuw `llm/backend.py`+`llm/auth.py`, `orchestrator/agent.py`
(turn-loop), `orchestrator/tools.py` (tools→`@tool`), `safety/*` (gate/audit/quarantaine in hooks),
`config.py` (id-mapping), `jarvis/ambient.py`+`daily.py` (op ORQ), `memory/fragments.py`+`evaluation/reflect.py` (embeddings ongewijzigd).
