# Span — componentdecompositie

Doel van dit document: de codebase opdelen in **los ontwikkelbare componenten**
met een duidelijk contract, zodat aan onderdelen parallel gewerkt kan worden
zonder elkaar te breken. Afgeleid uit de echte import-graaf (13-6-2026).

## Afhankelijkheidsregel

De code is gelaagd. **Afhankelijkheden wijzen alleen naar beneden** (een laag
mag lagen onder zich gebruiken, nooit boven zich). Er zijn geen
module-load-cycles. Houd deze richting aan bij elke wijziging — het is wat
parallel ontwikkelen veilig maakt.

```
Laag 5  Interfaces      cli.py · server/{app,routes,state,stt}.py · frontend (static/*)
Laag 4  Orchestratie    orchestrator/{agent,tools,tool_specs}.py
Laag 3  Proactief       jarvis/{daily,ambient,briefing,crons,meetings,documents}.py
Laag 2  Integraties     integrations/{o365,asana,fireflies,weather,telegram,http}.py
Laag 1  Geheugen-kern   memory/{fragments,bootstrap}.py · evaluation/reflect.py
Laag 0  Fundament       config.py · clock.py · llm/client.py · db/{brain,schema,work}.py
```

---

## Laag 0 — Fundament (stabiele kern; raak je zelden, iedereen leunt erop)

### `config` — `src/span/config.py`
- **Doel:** alle configuratie uit `.env` op de systeemgrens valideren.
- **Contract:** `load_settings() -> Settings`; dataclasses `Settings`, `JarvisConfig`, `WorkDbConfig`. Eén bron voor alle integratie-tokens, modellen, embed-dims, `decay_mode`.
- **Deps:** geen. **Los ontwikkelbaar:** ja, volledig geïsoleerd. Test: `tests/test_config.py`.

### `clock` — `src/span/clock.py`
- **Doel:** één tijdbron (Europe/Amsterdam) voor alle planners. `now_local()`, `today_local()`, `TZ`.
- **Deps:** geen. **Los:** ja. Wijzig hier nooit naar `datetime.now()` zonder tz.

### `llm/client` — `src/span/llm/client.py`
- **Doel:** ORQ.AI-gateway (OpenAI-compatible). `chat()`, `chat_json()`, `embed_one()`, `list_models()`.
- **Deps:** config. **Los:** ja, te mocken via MagicMock (zie tests). Wisselen van gateway = alleen dit bestand.

### `db/brain` — `src/span/db/brain.py`
- **Doel:** Neo4j-client voor het eigen brein. `run()` (read+write), `run_read()` (READ_ACCESS, afgedwongen alleen-lezen), `run_system()`, `vector_search()`, `ensure_database()`, `verify()`, `close()`.
- **Deps:** config. **Los:** ja. Dit is hét data-contract; wijzig de methodes voorzichtig (veel afnemers).

### `db/schema` — `src/span/db/schema.py`
- **Doel:** constraints, vector-indexen (mf + formeel), seed (identity/protocollen), embedding-drift-guard. `init_schema()` (idempotent).
- **Deps:** config, db.brain. **Los:** ja, maar wijzigingen raken alle node-types — coördineer met Laag 1.

### `db/work` — `src/span/db/work.py`
- **Doel:** optionele **alleen-lezen** koppeling naar productiedata. `assert_read_only()`, `ReadOnlyViolation`.
- **Deps:** config. **Los:** ja. Regel: productiedata blijft strikt read-only.

---

## Laag 1 — Geheugen-kern (het hart; stabiel contract, hoge impact)

### `memory/fragments` — `src/span/memory/fragments.py`
- **Doel:** informeel geheugen (MemoryFragment) + retrieval. `FragmentStore(brain, llm, decay_mode)`: `write()`, `search()`, `search_formal()`, `recent()`, `embed()`, `session_fragments()`, `count()`. Zacht verval-algoritme (`_decay_factor`).
- **Deps:** db.brain, llm.client. **Los:** ja — dé plek voor retrieval-experimenten. Meet met `scripts/eval_retrieval.py`. Contract `search()/search_formal()` wordt gebruikt door agent, server, jarvis.

### `memory/bootstrap` — `src/span/memory/bootstrap.py`
- **Doel:** sessiestart-context opbouwen (identity, protocollen, quests, recente kennis, insights/lessen, vector-match). `start_session()`, `end_session()`, `load_bootstrap()`, `render_bootstrap()`, `BootstrapContext`.
- **Deps:** db.brain, memory.fragments. **Los:** ja.

### `evaluation/reflect` — `src/span/evaluation/reflect.py`
- **Doel:** de zelflerende cirkel sluiten: fragmenten → Insight/Mistake/Idea/Quest/Skill. Grounding-eis (geen bron-loze Insight/Mistake). `reflect_session()`.
- **Deps:** config, db.brain, llm, bootstrap, fragments. **Los:** ja, met gemockte brain/llm (zie tests).

---

## Laag 2 — Integraties (elk volledig los; achter één bouwfunctie)

### `integrations/http` — `src/span/integrations/http.py`
- **Doel:** gedeelde weerbaarheid: `request_with_retry()` (429/503 + Retry-After + backoff). Gebruik dit in elke nieuwe externe client.
- **Deps:** geen. **Los:** ja.

### `integrations/{o365,asana,fireflies,weather}`
- **Doel:** externe API-clients (Microsoft Graph, Asana, Fireflies, open-meteo). Elk een zelfstandige client-klasse.
- **Deps:** integrations.http (de eerste drie). **Los:** ja — elk apart te ontwikkelen/mocken; verschijnen alleen als geconfigureerd.
- **Bouw-contract:** `integrations/__init__.py` → `build_integrations(settings) -> (o365, asana, fireflies)`. Voeg een nieuwe stateless integratie hier toe.

### `integrations/telegram` — `src/span/integrations/telegram.py`
- **Doel:** Telegram-bridge (chat op zak, `/koppel`, `/login`, dagstart-push). `TelegramBridge(token, state)`.
- **Deps:** ⚠️ **agent + reflect + bootstrap + fragments** (lazy imports) — hangt aan de volledige orchestratie omdat het een echte agent-sessie draait. Niet stateless; daarom niet in `build_integrations` maar los in `server/app.py` opgezet via `settings.jarvis.telegram_bot_token`.
- **Los:** deels — de bridge zelf wel, maar test met een gemockte agent.

---

## Laag 3 — Proactieve laag (jarvis/ambient)

### `jarvis/briefing` — `briefing.py`
- **Doel:** agenda/mail/taken/quests in één briefing-object. `build_briefing()`, `_overlaps()`.
- **Deps:** clock, db.brain, integrations.asana, jarvis.crons. **Los:** ja (bronnen falen zacht).

### `jarvis/daily` — `daily.py`
- **Doel:** scheduler + dagstart/avond/weekreview/consolidatie + de klok-re-export. `daily_scheduler()`, `generate_daily()`, `consolidate_memory()`, `reflect_orphan_sessions()`.
- **Deps:** clock, db.brain, reflect, integrations, briefing, crons, meetings, llm, fragments (spil van Laag 3). **Los:** matig — raakt veel; wijzig per functie.

### `jarvis/ambient` — `ambient.py`
- **Doel:** AgentInbox (goedkeuringswachtrij) + watcher (mail-triage, meeting-prep, O365-verloop-detectie). `AgentInbox` (`add/claim/release/resolve/snapshot/open_count`), `ambient_watcher()`, `execute_approval()`, `triage_message()`.
- **Deps:** daily, fragments. **Los:** ja — `AgentInbox` is een schoon, los te testen contract (de goedkeuringspoort).

### `jarvis/crons` — `crons.py`
- **Doel:** door Span zelf geplande taken (remind/execute). `create_cron()`, `list_crons()`, `run_due_crons()`.
- **Deps:** ⚠️ daily, bootstrap, **orchestrator.agent** (execute draait een agent-beurt). **Los:** deels.

### `jarvis/meetings` — `meetings.py`
- **Doel:** Fireflies-transcripties → brein + actiepunten → inbox (deelnemer-filter). `sync_meetings()`.
- **Deps:** bootstrap, fragments. **Los:** ja.

### `jarvis/documents` — `documents.py`
- **Doel:** document-ingest (MarkItDown → chunks → Document-node + entities). `ingest_document()`.
- **Deps:** config, bootstrap, fragments. **Los:** ja.

---

## Laag 4 — Orchestratie

### `orchestrator/tool_specs` — `tool_specs.py`
- **Doel:** declaratieve tool-schema's + `TOOL_META`-permissieregistry (pure data). `TOOL_SPECS`, `TOOL_META`, `O365_TOOLS`, `ASANA_TOOLS`.
- **Deps:** memory.fragments (alleen `MF_TYPES`-enum). **Los:** ja — nieuwe tool toevoegen = hier het schema, in `tools.py` de handler.

### `orchestrator/tools` — `tools.py`
- **Doel:** `ToolBox` — tool-dispatch, permissie-handhaving, inbox-gating, read-only-borging. `specs()`, `dispatch()`, `_tool_*`-handlers.
- **Deps:** db.brain, db.work, integrations, jarvis.*, fragments, tool_specs. **Los:** matig — voegt veel samen; per handler los te wijzigen.

### `orchestrator/agent` — `agent.py`
- **Doel:** `SpanAgent` — de gespreksbeurt: bootstrap, RAG-injectie (incl. formele kennis per beurt), tool-loop, continuous recording, traces. `begin()`, `turn()`, `set_location()`, `flush_recording()`.
- **Deps:** config, db.brain, db.work, integrations.asana, llm, bootstrap, fragments, tools. **Los:** matig — kern van de interactie; veel afnemers (server-WS, telegram, crons-execute).

---

## Laag 5 — Interfaces (entrypoints; los van elkaar via HTTP/WS-contract)

### `server/state` — `server/state.py`
- **Doel:** gedeelde `_state`-dict + helpers (auth, `_effective_settings`, `_audit`, `_tools_overview`). Geen route-logica.
- **Deps:** config, orchestrator.tools. **Los:** ja — gedeeld fundament voor app + routes.

### `server/routes` — `server/routes.py`
- **Doel:** alle REST-endpoints (`APIRouter`). Status, settings, graph, inbox, backup, documents, stt, fireflies, daily, briefing, o365-auth.
- **Deps:** state + Laag 1-4. **Los:** ja — endpoint toevoegen = hier, raakt app.py niet.

### `server/app` — `server/app.py`
- **Doel:** wiring: lifespan (achtergrondtaken + state), WebSocket-chat, mount, `include_router`. Geen REST-logica.
- **Deps:** state, routes, Laag 1-4. **Los:** ja — alleen aanraken voor lifespan/WS.

### `server/stt` — `server/stt.py`
- **Doel:** server-side Whisper (faster-whisper). `available()`, `transcribe()`. **Los:** ja, optioneel.

### `cli` — `src/span/cli.py`
- **Doel:** Typer-CLI: init, chat (met inbox-gating), status, memory, reflect, o365-login/logout, serve. **Deps:** breed. **Los:** ja — eigen entrypoint.

### Frontend — `src/span/server/static/*.js|*.css`
- **Doel:** JARVIS-HUD. Modules met gedeelde `window.SPAN`-namespace: `jarvis.js` (core/WS/panelen), `fx.js` (visuals), `voice.js` (spraak), `hologram.js` (3D-graph), `settings.js`, `ambient.js` (inbox/toasts), `effects.js`.
- **Contract:** praat met de server **uitsluitend via HTTP (`/api/*`) + WebSocket (`/ws/chat`)**. Volledig los ontwikkelbaar: zolang het API-contract gelijk blijft, raakt frontend-werk de Python niet en omgekeerd.

---

## Werk-eenheden voor parallel ontwikkelen

Onderdelen die je **gelijktijdig en onafhankelijk** kunt oppakken, met de grens
waarop je niet over elkaar heen komt:

| Werk-eenheid | Bestanden | Grens / contract |
|--------------|-----------|------------------|
| Retrieval & geheugen | `memory/*`, `evaluation/reflect.py` | `FragmentStore`-methodes; meet met `scripts/eval_retrieval.py` |
| Een externe integratie | `integrations/<naam>.py` | `build_integrations` + `request_with_retry`; verschijnt alleen indien geconfigureerd |
| Tools / agent-gedrag | `orchestrator/*` | `ToolBox.dispatch` + `TOOL_SPECS/TOOL_META`; tool toevoegen = schema + handler |
| Proactieve features | `jarvis/<naam>.py` | scheduler-haak in `daily.py` of `AgentInbox`-contract |
| REST-API | `server/routes.py` | nieuwe `@router`-route; `_require_rest_auth` + `_state` |
| HUD / UX | `static/*` | alleen `/api/*` + `/ws/chat`; geen Python-koppeling |
| CLI | `cli.py` | eigen entrypoint |

## Let op (de dikkere koppelingen)

- **`telegram.py` en `crons.py` (execute-mode) hangen aan `orchestrator.agent`** — wie de `SpanAgent`-constructor of `turn()`-signatuur wijzigt, moet deze twee + `server/app.py` (WS) + `cli.py` meenemen. Dit zijn de enige plekken die de volledige agent opbouwen.
- **`db/brain.py` en `memory/fragments.py` zijn de meest-afgenomen contracten.** Wijzig hun publieke methodes alleen bewust; een signatuurwijziging raakt vrijwel elke laag erboven (grep eerst de callsites — zie de fase-104/103 reflecties in het brein).
- **Frontend ↔ backend is volledig ontkoppeld via het HTTP/WS-contract.** Verander je een `/api/*`-respons, werk dan de bijbehorende `static/*.js` mee bij (en omgekeerd).
