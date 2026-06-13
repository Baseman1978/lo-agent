# Span-optimalisatie — deep research (13-6-2026)

Bron: multi-agent deep research, 5 gebieden x 5 internet-research-agents +
synthese per gebied + eindconclusie (31 agents, ~1,6M tokens). Strategische
laag boven de feature-roadmap. Permanente rode draad: de AgentInbox blijft de
enige poort voor onomkeerbare/gevoelige acties, ongeacht welke capability erbij
komt; externe input is altijd untrusted data.


Hieronder de eindconclusie. Geen tools nodig: de vijf syntheses zijn compleet en consistent; dit is pure cross-gebied-prioritering.

---

# EINDCONCLUSIE — Span optimaliseren (voor Bas)

Dwars door alle vijf gebieden loopt dezelfde rode draad: **bouw één gedeelde veiligheidsruggengraat (untrusted-quarantaine + AgentInbox-poort + provenance + egress-allowlist) en laat élke nieuwe capability daar doorheen erven.** De grootste winst zit niet in nieuwe features maar in (a) goedkope discipline-keuzes die je vandaag al kunt zetten en (b) dat fundament, vóórdat je capabilities openzet. Hieronder eerlijk en concreet.

---

## 1. TOP-10 AANBEVELINGEN (over alle gebieden, impact/effort geprioriteerd)

| # | Aanbeveling | Gebied · Onderdeel | Effort | Waarom bovenaan |
|---|-------------|--------------------|--------|-----------------|
| 1 | **Risico-tier (LOW/MED/HIGH/CRIT) als routeringslaag boven AgentInbox**, server-side hardcoded per tool | autonomie · risk-tier | S | Fundament waar half de roadmap op leunt; maakt Span autonomer op LOW/MED zonder HIGH op te geven |
| 2 | **Gedeelde quarantaine/dual-LLM-laag op álle untrusted input** (Haiku zonder tools parseert → schone JSON; Sonnet ziet nooit ruwe content) | capabilities · quarantaine (= autonomie E / integraties 2) | M | Dé structurele injectie-verdediging; bouw één keer, erf overal |
| 3 | **k=1..2 retrieval-discipline** + **harde token-budget per beurt** | geheugen · retrieval/context-rot | S | Pure parameter; meer memory verslechtert uitkomst (49.7%→44.4%); directe kwaliteits- én tokenwinst |
| 4 | **Prompt caching op stabiele system-prefix + Tool Search Tool / defer_loading** | geheugen · caching + capabilities · tool-loading | S/M | ~30 schemas upfront = 25-75k tokens/prompt; samen forse kosten/latency-daling op de ORQ-route |
| 5 | **aidefence_scan + spotlighting-envelope + trust-score op alle ingest** (mail/docs/transcripts/web) | geheugen 7 + capabilities 3 + integraties 10 | S/M | Tools heb je al; sluit de meest waarschijnlijke aanvalsroute (mail) vóór hij het brein in gaat |
| 6 | **Web-search tool** (ORQ native `web_search` passthrough checken, anders Tavily) | capabilities · web-search | S | Grootste capability-sprong tegen laagste kosten; pure HTTPS, ARM64-OK |
| 7 | **Episode-nodes + provenance (`DERIVED_FROM`)** als geheugenruggengraat | geheugen · sprong A | M | Maakt "waarom denkt Span dit?" beantwoordbaar; randvoorwaarde voor write-gate, ABAC, reconciliatie én de UX-provenance-graph |
| 8 | **Write-gate met contradictie-check (Haiku/NLI)** vóór een feit het langetermijngeheugen in mag | geheugen · sprong B | M | Kerndefensie tegen memory-poisoning (90-98% injectie-succes met ~5 teksten); zonder dit is zelf-evoluerend geheugen onveilig |
| 9 | **Telegram + ntfy als mobiel kanaal** (inline-keyboard AgentInbox + voice→server-Whisper) | integraties · mobiel/voice | S | Goedkoopste weg naar "Span overal", omzeilt proxy-blokkade Web Speech, geen WhatsApp-risico |
| 10 | **Graph delta-query polling** voor mail/agenda/Teams + **8u-herlogin als nette AgentInbox-melding** | integraties 7 + autonomie B + ux 6 | M | Juiste pull-alternatief op een lokale stack; lost de dagelijkse CA-frustratie op i.p.v. stil falen |

---

## 2. QUICK-WIN-LIJST (klein, hoog rendement, meteen oppakken)

**Deze week — pure parameters/config, nul nieuwe infra:**
- k=1..2 retrieval + token-budget-cap per beurt *(geheugen)*
- Prompt caching op system-prefix (eerst verifiëren dat ORQ `cache_control` doorlaat) *(geheugen)*
- Interleaved/extended thinking op de main-call (budget 5-10k, ~32k voor zware triage; thinking-blocks nooit rauw naar HUD) *(geheugen)*
- Tool-result clearing + compressie-cascade in de message-builder *(geheugen)*
- Binaire confidence-signaling ("zeker" / "twijfel→check") → lage confidence routeert naar AgentInbox *(ux)*
- Stop/regenerate-knop, suggested prompts, aria-live, fade-transitions *(ux)*

**Korte sprint — kleine code, hoog fundament-rendement:**
- Risk-tier per tool + arg-/output-validatie (Pydantic) + exfiltratie-check (HIGH + externe recipient + grote payload → forceer approval) *(autonomie)*
- aidefence_scan + spotlighting-delimiters als pre-processing hook *(meerdere)*
- Egress-/domein-allowlist op containers; code-runs `--network=none` *(capabilities)*
- Resource-/kosten-limieten + circuit-breaker + RunBudget (één `budget.py`-module) *(autonomie)*
- Adversariële pytest-suite (assert: geen HIGH zonder approval) *(autonomie)*
- Telegram inline-keyboard AgentInbox + voice-handler + ntfy-push *(integraties)*
- Whisper large-v3-turbo int8 + Silero-VAD (meet latency op ARM64 vóór default) *(integraties/ux)*
- Web-search tool + reader-modus (HTTP+readability) *(capabilities)*
- Entity-resolution/dedup-pass + Weibull-decay-formule + `t_invalid`-filter verifiëren *(geheugen)*
- Read-only "kijk-modus" host-bridge (screenshots/UIA, géén muis/toets) *(capabilities)*
- iCal-feed endpoint + HMAC-harden van `/api/inbound` + Cloudflare Tunnel (alleen mét HMAC/Access) *(integraties/autonomie)*
- Lichtgewicht metrics-dashboard via Neo4j-aggregatie (géén Langfuse/ClickHouse nu) *(ux)*

---

## 3. GROTE SPRONGEN (ambitieus — waarde vs. veiligheid/haalbaarheid)

**A. Event-driven brein i.p.v. polling — Graph webhooks (BASIC) + lifecycle + delta-reconciliatie.**
*Waarde:* latency van poll-interval → <1-2 min; echt "levend" brein. *Afweging:* vereist Cloudflare Tunnel (alleen `/webhooks/graph` exposen, persoonlijk account, niet Lomans-tenant) + webhook-handshake-hardening. Notificatie NOOIT als instructie — alleen trigger → read-only Graph-pull → scan → redeneren. **Eerlijk:** start met delta-polling (quick win); webhooks pas als de tunnel veilig staat. Werk-tenant blijft read-only = laag risico.

**B. Plan-Execute-Verify kern-loop + plan-immutability + dual-LLM/provenance (autonomie C+D+E samen).**
*Waarde:* tilt Span van reactieve assistent naar betrouwbare meerstaps-uitvoerder. *Afweging:* Planner/Validator krijgen NOOIT tools → klein injectie-oppervlak; bevroren plan betekent dat een geïnjecteerde mail de stappenlijst niet kan kapen. Hogere effort, alleen voor meerstaps-Quests — niet voor enkele-tool-antwoorden. Durable checkpointing in Neo4j (overleeft Docker-restart + 8u-herlogin) erbovenop.

**C. Computer-use / web-surfen (de door Bas genoemde richting) — getrapt, niet in één keer.**
*Waarde:* hoogst zichtbare JARVIS-sprong. *Eerlijke afweging:* dit is het **grootste nieuwe aanvalsoppervlak van alles**. Daarom strikt gefaseerd:
1. **Read-only kijk-modus** (screenshots/UIA-tree, geen acties) — nu al veilig en nuttig.
2. **Playwright read-only extractie** (kies Playwright, niet Puppeteer: ARM64-Chromium; accessibility-snapshot ~200-4k tokens i.p.v. ~50k screenshot). Eerst quarantaine + allowlist + kill-switch.
3. **Autonoom browsen + desktop-besturing** (browser-use / host-bridge daemon onder Bas' user, 127.0.0.1+token) — pas áchter de tiered AgentInbox, élke betekenisvolle actie ter goedkeuring.
**Bewust NIET:** volledig autonome Operator-stijl agent (cloud-locked, ~1 op 3-5 taken faalt nog, onbewaakt + mailtoegang = onaanvaardbaar), Claude-for-Chrome-extensie (zero-click-CVE jan 2026), lokale vision-modellen (geen GPU). M365 nooit via browser (8u CA-herlogin) — via Graph/M365-MCP.

**D. Provenance-graph + citaties + agentic transparantie-laag (ux B+C+D).**
*Waarde:* "waarom weet je dit?" als klikbaar pad, inline-citaties, live tool-status + why-cards + plan-preview. Tegelijk vertrouwens- én **zichtbare injectie-verdediging** (een Insight die terugleidt naar een verdachte mail valt op). *Afweging:* read-only weergave op data die er al is (bi-temporeel) → laag risico, hoge effort-verdeling over meerdere M-stukken. Bouwt direct op sprong A (Episode-nodes).

**E. WASM/Pyodide code-interpreter + CodeAct.**
*Waarde:* tools componeren in één codeblok (~20pp hogere succesratio, ~30% minder stappen); ideaal voor meeting-prep-aggregatie. *Afweging:* WASM omzeilt de binary-wheel-constraint volledig en is de sterkste lokale isolatiegrens op deze stack. **Harde regel:** gevoelige tools binnen de sandbox plaatsen een AgentInbox-verzoek, voeren de actie niet zelf uit. Pin op gepatchte Pyodide (CVE-2026-5752).

---

## 4. BEWUST NIET DOEN (valkuilen & over-engineering op 1-user-schaal)

**Onhaalbaar op Windows ARM64 / geen GPU / ORQ-managed:**
- Fine-tuning / LoRA op eigen ervaring; zelf-gehoste GPU-embeddings, neural reranking, lokale VLM's (UI-TARS, SmolVLM, Qwen2.5-VL); lokale KV-cache-compressie/eigen inference. Blijf non-parametrisch + ORQ-remote.
- Firecracker/gVisor/Kata microVM (geen geneste virtualisatie); Fast-Downward GOAP (C++-compilatie).
- Zware RL-trainingspipelines (GRPO/ProActor) — single-user = te weinig data. Neem de *ideeën* (reward-dimensies als heuristieken, opportunity-windows), niet de trainingsmachinerie.

**Onverstandig / vermengt privé met Lomans / black-box:**
- Azure Event Grid/Event Hubs, rich Graph-notifications (cert-beheer), tenant-brede Teams `getAllMessages` (metered billing). → delta-polling + BASIC.
- Cloud speech-to-speech (Realtime/Gemini Live/Nova Sonic): omzeilt ORQ-router + AgentInbox-poort → tool-calls ontsnappen aan goedkeuring. Houd de chained STT→ORQ→TTS-pipeline.
- WhatsApp Cloud API (per-bericht-pricing, 24u-window) en unofficial WAHA/whatsmeow (ban-risico). → Telegram + ntfy. Eigen Zapier/IFTTT-catalogus in Span's code → breedte hoort in n8n.
- Managed cloud-sandbox/hosted-browser als default (data verlaat de host); Parallel/Perplexity deep-research als basis-search (black-box, breekt grounding/quarantaine).

**Over-engineering op deze schaal — parkeren, niet schrappen:**
- Volledige CaMeL (policy-onderhoud > opbrengst → rubber-stamping = nétto minder veilig); neem alleen dual-LLM + provenance-vlag.
- Tree/Graph-of-Thought breed in de interactieve laag (5-10× kosten/latency, slecht voor stem/HUD); reversible reconciliation (SSGM) en drie-tier Letta-hierarchie pas ná Episode-nodes+write-gate.
- Neo4j native VECTOR-migratie, DIAL-KG schema-evolutie, GraphRAG community-summaries → wachtkamer tot de graaf gegroeid is en het fundament staat.
- Langfuse/ClickHouse-tracing, zwaar 3D/WebGL-hologram, VR/AR + token-level provenance, companion-persona (sycophantie ondermijnt oordeel — bouw juist een lichte anti-sycophancy-instructie).
- A2A-protocol, WebMCP, MCP Sampling (gedeprecieerd) — te vroeg / dood spoor.

**Eén design-regel boven alles:** vertrouw nooit op een groter context-venster i.p.v. curatie. Context rot maakt "alles erin proppen" contraproductief — investeer in retrieval/compactie/geheugen.

---

## 5. VOORGESTELDE VOLGORDE / ROADMAP-FASERING

**Fase 0 — Discipline (deze week, gratis kwaliteitswinst).**
k=1..2 retrieval + token-budget + prompt caching + interleaved thinking + tool-result clearing + confidence-signaling + HUD-afrondingen. Puur config/parameters.

**Fase 1 — Veiligheidsfundament (vóór élke capability).**
Risk-tier boven AgentInbox → arg/output-validatie + exfiltratie-check → gedeelde quarantaine/dual-LLM-laag → aidefence_scan + spotlighting-envelope → egress-allowlist → budget/circuit-breaker → adversariële pytest-suite. **Dit blok bouw je één keer; alle latere capabilities erven de bescherming.**

**Fase 2 — Goedkope capability- & bereik-winst.**
Web-search + reader-modus + read-only kijk-modus *(capabilities)*; Telegram+ntfy mobiel + voice→Whisper+VAD *(integraties)*; HMAC-inbound + iCal-feed + Cloudflare Tunnel *(integraties)*; delta-polling mail/agenda + 8u-herlogin-melding *(integraties/autonomie)*; entity-dedup + Weibull-decay + `t_invalid`-filter *(geheugen)*.

**Fase 3 — Geheugen-ruggengraat.**
Episode-nodes + provenance (A) → write-gate + contradictie-check (B) → bi-temporeel compleet (C) → ABAC/scope-tags (F). Hierna: provenance-graph + citaties + transparantie-laag in de HUD *(ux B/C/D)* — die erven Episode-nodes.

**Fase 4 — Autonomie-kern.**
Plan-Execute-Verify + plan-immutability + dual-LLM/provenance (C+D+E samen) → durable checkpointing (F) → ambient HITL-triage + acceptance-feedback (G) → pre-meeting context-bundeling (H) → tamper-evident audit-trail (I). Event-webhooks (BASIC) vervangen hier de polling zodra de tunnel veilig staat.

**Fase 5 — Zware sprongen, getrapt.**
ReasoningBank/Reflexion (leren uit mislukkingen, D) + hybrid retrieval/importance/routing (E) *(geheugen)*; WASM/Pyodide-sandbox → CodeAct *(capabilities)*; Playwright read-only → autonoom browsen → host-bridge desktop-besturing — elk áchter de tiered AgentInbox. n8n als integratie-hub voor breedte.

**Fase 6 — Wachtkamer.**
SSGM-reconciliation, Letta-drie-tier, Neo4j VECTOR-migratie, GraphRAG/DIAL-KG, branche-data-API's (2BA/Ketenstandaard/NEN), Copilot Retrieval (alleen als Lomans-licentie geverifieerd), mail-archief-bulkimport. Oppakken zodra de graaf gegroeid is en fundament + autonomie bewezen draaien.

**Permanente rode draad:** AgentInbox blijft de enige poort voor onomkeerbare/gevoelige acties — ongeacht voice, vision, confidence of autonomie-niveau. Autonomie-escalatie mag de injectie-verdediging nooit omzeilen. Externe input = altijd untrusted data.


---

# Gebied-syntheses (detail)


## Gebied: geheugen

Geen dubbele MCP-tools nodig; dit is een pure synthese-taak. Hieronder de geprioriteerde synthese.

---

# Synthese — Geheugen van Span

De vijf deelonderzoeken convergeren sterk: drie pijlers keren overal terug — (1) **bi-temporeel + provenance** als ruggengraat, (2) **write-gate / poisoning-verdediging** als randvoorwaarde zodra geheugen zelf-evolueert, en (3) **retrieval-discipline** (weinig, scherp, gefilterd) als grootste kwaliteitswinst. Hieronder ontdubbeld en geprioriteerd.

---

## QUICK WINS (laag-hangend, S/M, hoge impact)

**1. k=1..2 retrieval-discipline.** ReasoningBank's harde empirische les: méér opgehaalde memory-items verslechtert de uitkomst (49.7% → 44.4% van k=1 naar k=4). Zet de retrieval-stap die Insights/Strategies in de SpanAgent-prompt injecteert default op k=1..2, gerankt op relevantie. Pure rank+limit-parameter, geen nieuwe infra. Direct kwaliteits- én tokenwinst, en kleiner injectie-oppervlak. (effort S, impact high)

**2. Prompt caching op de stabiele system-prefix.** Markeer system-prompt + Span-persona + de ~30 vaste ToolBox-definities als cache-breakpoint. Cache-read ≈10% van input; bij een 50-turn scheduler-run enorme besparing. Eerst verifiëren dat de ORQ.AI-router de Anthropic `cache_control`-header doorlaat. Gevoelige fragments ná de breakpoint plaatsen. (effort S, impact high)

**3. Interleaved/adaptive extended thinking op de main-call.** Goedkoopste, meest impactvolle redeneer-upgrade: Claude redeneert tussen tool-calls door. Budget ~5–10k normaal, ~32k voor zware triage/meeting-prep; Haiku-light krijgt geen/laag budget. Let op: thinking-blocks ongewijzigd terugsturen (signature behouden), `display:omitted` voor HUD-latency, nooit rauw naar HUD-log (PII). (effort S, impact high)

**4. Tool-result clearing + compressie-cascade.** Ruim oude tool-output (mailbodies, agenda-dumps, Fireflies-transcripts, MarkItDown-ingest) uit de message-history zodra Span erop handelde — behoud de tool-CALL-beslissing én de bron-id (zodat just-in-time herladen kan i.p.v. hallucineren). Geordende cascade in de message-builder: tool-output trimmen → sliding window → LLM-samenvatting als laatste redmiddel. Deterministisch, weinig regressierisico. (effort S/M, impact high)

**5. Harde token-budget per beurt (context rot).** Boven ~50% gevuld venster favoriseert het model recente tokens en zakt de rest weg — erger bij complexe planningstaken. Cap working-context op een vast deel van het window en cureer i.p.v. dumpen. Ontwerp-regel + meetpunt in orchestratie. (effort S, impact high)

**6. Entity-resolution / dedup-pass bij ingest.** Voorkomt dubbele nodes ('Lomans' / 'Lomans Installatietechniek' / 'Lomans B.V.'). Recept: nieuwe entiteit → embedding (text-embedding-3-large@1024 die je al hebt) → HNSW-nearest-neighbor + fuzzy, score = 0.7·embedding + 0.3·fuzzy, **type-constrained** (PERSON matcht niet ORG). Auto-merge ≥0.95, 0.88–0.95 → `:SAME_AS` voor review in AgentInbox, lager → nieuwe node. Conservatieve drempel: bij twijfel NIET mergen maar flaggen (verkeerde merge lekt context tussen personen). (effort M, impact high)

**7. aidefence-scan + trust-score op ingest.** Scan binnenkomende mail/docs/transcripts (untrusted) met `aidefence_scan`/`is_safe`/`has_pii` vóór ze het brein in gaan; geef elke node een trust-score (eigen invoer hoog, externe mail laag). Lage-trust feiten wegen minder en vereisen contradictie-check. Directe verdediging tegen de meest waarschijnlijke aanvalsroute (mail). De tools heb je al beschikbaar. (effort M, impact high)

**8. Weibull-decay als formule voor het bestaande zachte verval.** Vervang ad-hoc verval door `w(dt)=exp(-(dt/eta)^kappa)`, met eta/kappa per node-type (meeting-actiepunt vervalt sneller dan kernfeit over Bas). Weeg op retrieval, wis niet — history blijft voor audit/rollback. (effort S, impact medium)

---

## GROTE SPRONGEN (ambitieuzer, echt niveau-verhogend)

**A. Episode-nodes + provenance als fundament (M, high).** Laat document-ingest, Fireflies en mail-triage eerst een immutable **Episode-node** neerzetten; koppel elke afgeleide Insight/Idea met een `DERIVED_FROM`-edge. Dit is de ruggengraat onder álle veiligheids- en hygiene-items hieronder (reconciliation, write-gate, ABAC, contradictie-resolutie). *Afweging:* matige effort, maar het maakt het brein controleerbaar ("waarom denkt Span dit?") en is randvoorwaarde voor de rest — daarom als eerste grote sprong.

**B. Write-gate met contradictie-check (NLI/Haiku) (M, high) — randvoorwaarde, geen optie.** Vóór een afgeleid feit het langetermijngeheugen in mag: top-k dichtstbijzijnde bestaande feiten ophalen, Haiku/NLI laat entailment/neutral/contradiction-oordeel vellen. Bij contradiction → niet stil overschrijven maar `SUPERSEDES`-edge óf via AgentInbox aan Bas ("Span dacht X, nieuwe info zegt Y"). *Afweging:* dit is dé kerndefensie tegen memory-poisoning (onderzoek toont 90–98% injectie-succes met ~5 vergiftigde teksten; klassieke filters falen). Sluit naadloos aan op Span's bestaande prompt-injectie-strategie via AgentInbox. Zonder dit is zelf-evoluerend geheugen onveilig.

**C. Bi-temporeel edge-model volledig per fact (M, high).** Span is al bi-temporeel — maak het compleet: vier timestamps (`t_valid`, `t_invalid`, `t_created`, `t_expired`) op elke Insight/Mistake/Idea-relatie; conflicterend feit → oude edge **invalideren, niet verwijderen**; retrieval filtert standaard op geldige feiten ("as-of"-query) maar history blijft voor "wat dacht ik toen?". *Afweging:* tegelijk veiligheidslaag (append-only = een poisoning-write kan echte feiten niet stil wissen) én forensisch waardevol. **Verifieer eerst** dat de huidige retrieval áltijd `t_invalid` filtert — dat alleen is al een quick win die hier in past.

**D. ReasoningBank + Reflexion: leren uit mislukkingen (M, high).** Voeg een `Strategy`-knooptype toe, gevoed door volledige tool-trajectories (niet losse notities), en distilleer expliciet uit FAILED trajectories — een afgewezen AgentInbox-voorstel, een mislukte O365-call na de 8u-herlogin, een genegeerd ambient-alert. Span schrijft "waarom ging dit mis" naar een Mistake-knoop en haalt die bij de volgende soortgelijke actie. *Afweging:* dit is precies waar de huidige reflect-cirkel (alleen successen, periodiek) tekortschiet; non-parametrisch dus past binnen de no-GPU/no-finetune-constraint. Strategieën zijn een poisoning-vector → herkomst-tag verplicht.

**E. Hybrid retrieval + importance-as + geheugen-routing (M, high).** Combineer drie verbeteringen die elkaar versterken: (1) semantic (HNSW) + BM25 full-text-index + graph-traversal in één LLM-loze retrieval-stap (P95 ~300ms), rerank op graph-distance (dichtbij Bas = relevanter); (2) expliciete **importance-as** — een Haiku 1–10 rating bij fragment-creatie, score = recency·α + importance·β + relevance·γ (onderscheidt "koffie gehaald" van "deadline-conflict Lomans-audit"); (3) route retrieval per geheugen-type (episodisch / semantisch / procedureel mappen op Neo4j-labels). *Afweging:* fundament-investering met brede uitstraling. Veiligheidsnoot: importance door derden-tekst laten scoren is manipuleerbaar ("THIS IS EXTREMELY IMPORTANT") → cap importance op niet-Bas-bevestigde content.

**F. ABAC / access-scoped retrieval (M, medium-high).** Tag elke node met herkomst-scope (privé / werk-Lomans / extern) en filter top-k op die predicaten. Cruciaal omdat werk-Neo4j strikt read-only is en M365/Asana zakelijke data binnenkomt: voorkomt dat zakelijke feiten in privé-context opduiken of omgekeerd. *Afweging:* directe privacy-winst (vangt topology-induced leakage via edges af), sluit aan op Lomans conditional-access-eisen. Lage effort relatief tot de waarde.

**G. Reversible reconciliation (SSGM dual-track) (L, high) — later, ná A-F.** Append-only episodisch logboek naast de muteerbare graaf; een asynchrone cron leidt periodiek de afgeleide kennis opnieuw af uit het ruwe logboek en corrigeert gedrifte concepten (begrenst drift bewijsbaar naar O(N·e)). *Afweging:* hoogste effort, maar het herstelt het brein naar geverifieerde grondwaarheid en wist sluipende vergiftiging/drift die door de write-gate glipte. Bouw pas wanneer Episode-nodes (A) en write-gate (B) staan — anders is er geen grondwaarheid om naar terug te rollen.

**H. Drie-tier geheugenhierarchie (Letta/MemGPT) (M, high) — optioneel structureel.** Formaliseer core-memory ("RAM": Bas-persona, actieve Quests/Skills) / recall (dag) / archival (Neo4j+HNSW als "disk"), met tool-calls om tussen tiers te swappen. *Afweging:* maakt context per beurt scherper; goede ROI maar overlapt deels met geheugen-routing (E). Core-edits door de agent zelf = poisoning-oppervlak → via AgentInbox-poort. Doe E vóór H.

---

## NIET DOEN (NU)

**1. Parametrische self-improvement (fine-tuning / LoRA op eigen ervaring).** Onhaalbaar: Windows ARM64 = geen broncompilatie/GPU; ORQ.AI routeert naar managed Claude/embedding die je niet per-user fine-tunet; single-user data is te schaars voor betrouwbare gradient-updates. Bijkomend nadeel: verlies van inspecteerbaarheid/verwijderbaarheid (recht-op-vergeten, poisoning-rollback). Blijf bewust non-parametrisch — precies waarom Reflexion/Voyager/ReasoningBank passen.

**2. Zelf-gehoste GPU-embeddings / lokale neural reranking / GDS-broncompilatie.** Op deze host (geen GPU, alleen binary pip-wheels) weegt de winst niet op tegen bouw-/compatibiliteitspijn. Blijf bij ORQ.AI text-embedding-3-large (remote) + cloud-LLM-reranking. *Let op de afgeleide eis:* remote embedding betekent dat fragmenttekst naar ORQ/OpenAI gaat — daarom moet de PII/ABAC-scan (quick win 7 / sprong F) ervóór zitten.

**3. Lokale KV-cache-compressie / eigen long-context inference.** Vereist GPU/low-level inference-controle die managed Claude via ORQ niet biedt. Blijf bij managed prompt-caching + applicatie-niveau compressie.

**4. Tree-of-Thought / Graph-of-Thought breed in de interactieve laag.** 5–10× token/latency-kosten, slecht voor de real-time stem/HUD-ervaring, lost geen probleem op dat Bas vandaag heeft (loont bij puzzels/zoekproblemen, niet bij een persoonlijke assistent). Houd het bij interleaved thinking + graph-grounding + gerichte self-verification; bewaar echte boomzoek hooguit voor een offline "diep nadenken"-cron buiten de interactieve flow.

**5. Vertrouwen op een groter context-venster i.p.v. curatie.** Context rot maakt "alles erin proppen" contraproductief, juist bij Span's complexere planningstaken. Investeer in retrieval/compactie/geheugen, niet in window-grootte. (Ontwerp-regel, geen feature.)

**6. Neo4j native VECTOR-datatype migratie — nog niet nodig.** Low-effort en nuttig op termijn (lagere opslag, snellere HNSW, Cypher SEARCH-clause vanaf 2026.01), maar op huidige schaal geen prioriteit. Plan het mee bij een volgende Neo4j-versie-bump; controleer dan arm64-Docker-compatibiliteit. Niet als losse actie nu.

**7. Schema-vrije ontologie-evolutie (DIAL-KG) & GraphRAG community-summaries — wachtkamer, niet "niet doen".** Beide L/M-effort en aantrekkelijk, maar pas zinvol als de graaf gegroeid is én A–C staan; autonome schema-groei zonder goedkeuringspoort maakt de graaf onauditbaar, en GDS-community-detectie heeft een arm64-compatibele build nodig. Niet schrappen, wel parkeren tot na de fundament-sprongen.

---

**Aanbevolen volgorde:** quick wins 1–5 (deze week, pure parameters/config) → quick wins 6–8 + sprong C-verificatie → sprong A (Episode/provenance) → B (write-gate) + F (ABAC) → D + E → daarna G/H en de wachtkamer-items.


## Gebied: capabilities

Synthese gereed. Hieronder de geprioriteerde aanbevelingen voor het gebied "capabilities" van Span.

---

# SYNTHESE — CAPABILITIES (Span)

Vijf deelonderzoeken (computer-use, browser-automatisering, live web-toegang, code-sandboxes, MCP/A2A) wijzen allemaal naar dezelfde rode draad: **Span kan veel meer dan nu, mits elke nieuwe capability door één gedeelde veiligheidsruggengraat loopt** (AgentInbox-poort + quarantaine-laag + egress-allowlist + audit). De grootste dubbeling tussen alle vijf: het **dual-LLM/CaMeL-quarantaine-patroon** en **aidefence_scan op untrusted input**. Bouw die één keer, hergebruik overal.

---

## QUICK WINS (laag-hangend, S/M, hoge impact)

1. **Web-search tool (Tavily of Claude native web_search via ORQ)** — S, hoog. De grootste capability-sprong tegen de laagste kosten. Tavily SDK is pure HTTPS (geen native deps → werkt op Windows ARM64), geeft geranked snippets mét bron-URLs. Eerst checken of ORQ.AI de server-side `web_search`-tool passthrough't (nul Python-werk); zo niet → Tavily-route. *Onderdeel:* nieuwe ToolBox-tool + system-prompt-instructie "altijd [bron: titel+URL]".

2. **Gedeelde quarantaine/dual-LLM laag op alle untrusted input** — M, hoog. De belangrijkste structurele verdediging (genoemd in 3 van de 5 onderzoeken). Hergebruik Span's bestaande Haiku-tier als "quarantined parser": webinhoud/screenshot-tekst/mail → schone JSON; SpanAgent (Sonnet, privileged, met tools) ziet nooit ruwe content. Bouw dit vóór elke web/desktop-tool live gaat. *Onderdeel:* quarantine-laag tussen fetch/scrape en SpanAgent.

3. **aidefence_scan + spotlighting als pre-processing hook** — S, midden/hoog. Span heeft `aidefence_scan/is_safe/has_pii` al via ruflo. Draai alle gescrapete tekst, OCR/screenshot-tekst en mail-body erdoorheen vóór de redeneerloop; `has_pii` voorkomt dat PII naar embeddings/LLM lekt. Defense-in-depth bovenop de quarantaine. *Onderdeel:* input-filter in ambient-watcher + nieuwe tools.

4. **Egress-/domein-allowlist op de browser-/code-container** — S, hoog. Docker network policy die uitgaand verkeer beperkt tot een whitelist; interne/Lomans-domeinen expliciet OFF. Zelfs als injectie slaagt, kan er niets geëxfiltreerd worden. Code-runs default `--network=none`. *Onderdeel:* config-laag (fundament).

5. **Tool Search Tool + defer_loading** — M, hoog. Span laadt nu ~30 tool-schemas upfront (25-75k tokens/prompt). Anthropic's Tool Search Tool (GA feb 2026) laadt on-demand → ~85% token-reductie. Houd een kerncirkel (geheugen, agenda, mail-triage) altijd geladen, defer de rest. Verlaagt kosten/latency direct op de ORQ-route. *Let op:* defer NOOIT de AgentInbox-classificatie zelf.

6. **Reader-modus voor pure info-ophaal** — S, midden. HTTP+readability voor statische pagina's, Playwright-snapshot alleen voor JS-zware sites. Dekt ~80% van de behoefte ("koers/openingstijd/feit opzoeken") zonder dure agentic loop en met klein aanvalsoppervlak. Default; escaleer bewust.

7. **Kill-switch + action-budget + audit-trail** — M, hoog. Globale noodstop, per-sessie max-N-acties/max-tijd tegen runaway-loops, onveranderlijke log met before/after. Belangrijkste vangnet tegen "agent op hol" en salami-slicing. *Onderdeel:* bridge/runner-supervisor + log naar eigen (write) Neo4j, niet de read-only werk-graph.

8. **Read-only "kijk-modus" host-bridge** — S, midden. Eerste veilige stap richting computer-use: alleen screenshots + UIA-tree leveren, géén muis/toets. Span kan "wat staat er op mijn scherm / vat dit venster samen" beantwoorden met nul actie-risico — direct nuttig voor de JARVIS-HUD/stem. *Wel:* `has_pii`-check, lokale opslag met verval.

9. **Ambient nieuws-/onderwerp-monitoring via Tavily news** — S, midden. Zodra de search-tool er is: dag-scheduler draait `topic=news, days=1` op Bas-relevante onderwerpen → gebriefde samenvatting in HUD/Telegram. Read-only, laag risico (wel door de quarantaine-samenvatter).

10. **fastapi-mcp: read-only endpoints als MCP-tools** — S, midden. Span draait al op FastAPI; `fastapi-mcp` mount met 1 regel een `/mcp`-endpoint. Whitelist alleen read-only/idempotente routes, bind op loopback. Goedkoop ecosysteem-voordeel.

---

## GROTE SPRONGEN (ambitieuzer, hoge impact)

1. **Playwright als browser-tool (read-only snapshot → later autonoom via browser-use)** — M→L, hoog.
   *Afweging:* Playwright heeft officiële ARM64-Chromium-binaries (Puppeteer NIET → kies Playwright); accessibility-snapshot is ~200-4k tokens vs ~50k voor screenshots en haalt ~92% betrouwbaarheid tegen ~$0.02-0.10/taak. **Begin read-only/extractie** (lezen/samenvatten), schaal pas op naar autonoom (`browser-use`, ~89% WebVoyager) áchter AgentInbox. Hoogste injectie-risico van alle items → vereist quick-wins #2-#4 + #7 eerst. M365 niet via browser automatiseren (8u conditional-access herlogin) — gebruik de bestaande Graph/M365-MCP.

2. **Host-bridge daemon (Windows-companion) + hybride UIA-first desktop-besturing** — L, hoog.
   *Afweging:* De verplichte enabler voor álle echte pc-besturing (de container kan de host niet zien). UIA accessibility-tree first (deterministisch, auditbaar, ~gratis), Claude computer-use als vision-fallback (~$0.20-0.50/taak) alleen voor canvas/legacy. Krachtig maar het grootste nieuwe privilege-oppervlak: draai onder Bas' user (geen admin), bind op 127.0.0.1 met token, élke betekenisvolle actie via AgentInbox. Bouw ná de read-only kijk-modus (#8).

3. **AgentInbox → risico-getrapte approval-engine (tiered HITL)** — M, hoog.
   *Afweging:* Het centrale handhavingspunt voor álle nieuwe capabilities. Laag-risico read-only auto; medium gelogd voor async review; hoog (verzenden/betalen/akkoord/installeren/verwijderen, domein-wissel) synchrone goedkeuring vooraf met leesbare samenvatting (intent, doel-element, blast-radius, rollback). Tel cumulatief risico per sessie mee (anti-salami-slicing). MCP Elicitation als gestandaardiseerd confirm-kanaal. Dit raakt alles → vroeg in de roadmap.

4. **Provenance/taint-tracking light (CaMeL-geïnspireerd)** — L, hoog.
   *Afweging:* De sterkste structurele verdediging die bestaat: tag tekst uit screenshots/scraped pages als "untrusted" en blokkeer dat zulke tekst direct een gevoelige tool-parameter wordt zonder Bas' bevestiging. Volledige CaMeL is zwaar; de taint+capability-light variant past op AgentInbox + de fragments-laag. Combineert met de quarantaine-laag (#2 quick-wins). Geeft "provable-ish" garanties tegen geld/data-exfiltratie via injectie.

5. **Code-interpreter: Pyodide-in-Deno sandbox** — M, hoog.
   *Afweging:* `run_python(code, packages, stateful)` in Pyodide (CPython→WASM) binnen een Deno-subprocess met deny-by-default permissies. **WASM is platformneutraal → omzeilt de binary-wheel-constraint volledig** en is op deze stack de sterkste lokale isolatiegrens. Let op: pin op gepatchte versie (CVE-2026-5752 sandbox-escape), behandel output als untrusted. Voor zwaardere runs: gehardende ephemeral sibling-Docker (`--network=none`, cap-drop, seccomp, `--rm`).

6. **CodeAct + code-execution-with-MCP (tools als bestanden, progressive disclosure)** — L, hoog.
   *Afweging:* Eén codeblok dat meerdere tools componeert i.p.v. ronde-per-ronde JSON-calls (~20pp hogere succesratio, ~30% minder stappen); tools als filesystem van code-API's on-demand geladen (Anthropic meldt tot 98,7% token-reductie). Ideaal voor meeting-prep (agenda+transcript+mail aggregeren zonder alles door de context). **Vereist de sandbox (#5) + harde regel: gevoelige tools binnen de sandbox-namespace plaatsen een AgentInbox-verzoek, ze voeren de actie niet zelf uit.**

7. **Span's ToolBox als MCP-server (FastMCP) + officiële Neo4j MCP read-only server** — M, hoog.
   *Afweging:* FastMCP exporteert de ~30 tools zodat Claude Desktop/Code/Cursor en latere agents ze kunnen gebruiken zonder herimplementatie. De officiële `mcp-neo4j-cypher` read-only server **dwingt de read-only-constraint op de werk-Neo4j af op protocolniveau** (weigert writes/admin) i.p.v. via afspraak — aparte write-instance voor de persoonlijke graph-brain. Scoped OAuth 2.1-credentials per integratie (O365/Asana/Fireflies) tegen credential-aggregatie; MCP-servers gecontaineriseerd, `.env` nooit mounten.

8. **Sessie-record/replay van geslaagde desktop-flows als deterministische Skills** — M, midden/hoog.
   *Afweging:* Leg geslaagde UIA-actiesequenties (element-selectors, geen ruwe coördinaten) vast als herbruikbare "desktop-macro" Skill — sneller, goedkoper, minder injectie-oppervlak. Sluit aan op Span's bestaande zelflerende Skill-cirkel. Macro's blijven door de AgentInbox-tier lopen; invalideer bij selector-mismatch (niet blind doorklikken).

---

## NIET DOEN (NU)

1. **Lokale vision-modellen (UI-TARS 7B/72B)** — onhaalbaar. Vereist 8-40GB+ VRAM; Span draait Windows ARM64 zónder GPU, alleen binary-wheels. Niet forceren via broncompilatie. Vision uitsluitend via ORQ/Claude. *Heroverwegen bij andere hardware.*

2. **Volledig autonome computer-use agent (OpenAI Operator/CUA-stijl, eigen VM, zonder mens)** — ongewenst én deels onhaalbaar. CUA is cloud-only/OpenAI-locked (niet Span's ORQ/Claude-stack); OSWorld toont ~1 op 3-5 taken faalt nog. Onbewaakte autonomie + injectie + toegang tot Bas' mail/agenda = onaanvaardbaar risico op een single-user setup. Span blijft een door-Bas-gestuurde assistent.

3. **Firecracker/gVisor/Kata microVM-isolatie lokaal** — onhaalbaar. Vereist KVM/hardware-virtualisatie; Docker Desktop draait al in een VM (WSL2/Hyper-V) zonder geneste virtualisatie → geen KVM. gVisor op ARM64 slechts preliminary. Gebruik WASM/Pyodide (default) + gehardende Docker; delegeer echte microVM-isolatie eventueel naar cloud-sandbox (E2B) — maar alleen voor niet-gevoelige data.

4. **Claude-for-Chrome browser-extensie** — niet geschikt. Consument-extensie die de échte Chrome bestuurt; past niet in Span's headless Docker/FastAPI-architectuur en kreeg jan 2026 een zero-click injectie-CVE. Server-side Playwright geeft méér controle.

5. **Managed cloud-sandbox (E2B/Daytona) of hosted browser-API (Steel/Browserbase) als default** — onverstandig als standaard. Persoonlijke/Lomans-data verlaat dan de host naar een derde partij; voor een privé graph-as-brain ongewenst. Alleen voor incidentele niet-gevoelige rekenintensieve taken; self-hosted (kale Playwright / Steel open-source) sterk te verkiezen.

6. **Parallel/Perplexity deep-research API als basis-search-backend** — overkill + duur + black-box. Levert afgewerkte antwoorden i.p.v. bouwstenen, verbergt de bron-keten (moeilijker te quarantineren/grounden) en botst met Span's eigen redeneer/geheugen-cirkel. Bewaar voor een latere expliciete "diep onderzoek"-tool.

7. **RestrictedPython als enige sandbox** — onveilig. Eigen docs: "is not a sandbox system". In-proces → escape = volledige compromittering van Span's geheugen/credentials. Hooguit extra laag bovenop WASM/Docker, nooit standalone.

8. **MCP Sampling adopteren** — dood spoor. Gedeprecieerd per spec 2026-07-28; direct op ORQ.AI/Claude blijven is exact het aanbevolen pad.

9. **WebMCP (W3C navigator.modelContext) nu bouwen** — te vroeg. Pre-productie (Chrome 146 achter flag), nauwelijks sites die het ondersteunen, geen volwassen security-model. *Heroverwegen over 6-12 maanden.*

10. **A2A (Agent2Agent) protocol nu bouwen** — overkill voor single-user. De waarde zit in MCP (tool-toegang), niet in agent-federatie. Op de radar houden voor eventuele latere koppeling met Lomans-platform-agents.

---

**Aanbevolen volgorde:** eerst de gedeelde veiligheidsruggengraat (quick-wins #2, #3, #4, #7 + grote sprong #3 tiered AgentInbox), dán de goedkope capability-winst (#1 web-search, #6 reader, #8 kijk-modus), dán de zwaardere sprongen (Playwright → sandbox → host-bridge → CodeAct). Bouw het quarantaine/taint-patroon en de allowlist één keer; alle latere capabilities erven de bescherming.


## Gebied: autonomie

QUICK WINS (laag-hangend, S/M effort, hoge impact — direct doen)

1. Cloudflare Tunnel als vaste HTTPS-ingress (cloudflared sidecar) — S. Dé enabler voor de hele event-driven laag. Expose ALLEEN /webhooks/graph, niet HUD/CLI-API. Persoonlijke account-tunnel, niet via Lomans-tenant. Voeg Cloudflare Access of geheim pad-token toe.

2. Webhook-handshake hardening in de FastAPI-route — S. validationToken binnen 10s echoën, clientState verifiëren op elke notificatie, direct 202 + async queue. Zonder dit markeert Graph je endpoint "slow/drop" en verlies je stil notificaties. clientState door Bas in .env laten zetten (agent bewerkt .env niet).

3. Risico-classificatie (LOW/MEDIUM/HIGH/CRITICAL) als routeringslaag boven AgentInbox — S. Voeg `risk_tier` toe aan elke ToolBox-tool; AgentInbox routeert op tier i.p.v. vaste lijst. Server-side hard-coded (nooit door LLM). Maakt Span autonomer op LOW/MEDIUM zonder HIGH op te geven. Fundament waar veel andere items op leunen.

4. Output-/argument-validatie + exfiltratie-detectie in ToolBox — S. Pydantic-schema per tool-call (heb je al via FastAPI) + check: HIGH-actie met externe recipient + grote geheugen-payload = forceer approval. Allowlist vaste recipients. Vangt de klassieke "injectie exfiltreert je notities"-aanval.

5. Adversariële pytest-suite (prompt-override / tool-misuse / memory-poisoning) — S. Assert dat geen HIGH-actie zonder approval afgaat en provenance onvertrouwd blijft. Draai bij elke wijziging aan ToolBox/AgentInbox. Goedkoop, beschermt de guardrails tegen regressie.

6. Resource-/kostenlimieten + circuit-breaker — S. Tool-call-rate, recursiediepte van self-scheduling crons, token/kosten-cap per sessie. Cruciaal omdat crons + ambient watcher + ORQ een onzichtbare betaalde loop kunnen vormen. Bij overschrijding pauzeren en Bas pingen.

7. Budget-envelope per autonome run (RunBudget-dataclass) — M/laag-hangend. max_steps, max_replans, max_tool_calls, wall-clock, cost-ceiling; soft-limit 70% → Haiku/truncate. Overlapt sterk met #6 — bouw ze als één budget/guardrail-module (`span/orchestration/budget.py`).

8. Two-tier "wakeAgent=false" / changed?-gate vóór dure Sonnet-calls — S. Goedkope Python/Haiku-check of de toestand écht veranderde; alleen dán Sonnet wekken. Plus debounce/coalescing van event-bursts (30-60s) op dezelfde thread/afspraak. Bespaart tokens én verkleint injectie-oppervlak per event.

9. Confidence-thresholding + silence-preservation op elke proactieve trigger — S. Onder drempel: Span doet NIETS (geen ping). Conservatieve defaults, do-not-disturb 's avonds. Voorkomt dat een geïnjecteerde mail een vals-positieve actie uitlokt.

10. Notificatie-batching + dag-fasen (ochtend-briefing / dag-coalescing / avond-reflectie) — S. Niet-urgente items → digest; alleen urgentie breekt direct door. Tegen notification-fatigue én approval-moeheid (batch MEDIUM, escaleer HIGH individueel). Opportunity-windows (earliest_ok/latest_ok/urgency) i.p.v. exacte due_at.

11. Subscription-renewal scheduler (PATCH vóór expiry) — S. Mail/event ~7d, todo ~3d. Opportunistisch verlengen tijdens webhook + 6-12u cron als vangnet. Subscription-id's + expiry in Neo4j, niet in code.

12. Realtime WebSocket-push naar de HUD bij elk event — S. Infra staat er al; brein-hologram licht live op. Alleen niet-gevoelige samenvattingen over de socket.

13. APScheduler als scheduling-fundament (geen Celery) — S. Pure-Python wheel (voldoet aan ARM64 binary-wheel-constraint), geen broker, draait in het FastAPI-proces. Pin een stabiele versie (4.x is jong).

GROTE SPRONGEN (ambitieuzer, tillen Span naar JARVIS-niveau)

A. Volledige Graph change-notification webhooks voor mail + agenda + To Do (vervang polling) — M, impact high. Latency van poll-interval → <1-2 min. Gebruik BASIC notificaties (alleen resource-id) als trigger + delta-query als source-of-truth — vermijdt encryptiecertificaten van rich notifications. Afweging: notificatie NOOIT direct als LLM-instructie; alleen trigger → read-only Graph ophalen → aidefence_scan → redeneerlaag. Werk-tenant blijft read-only (veilig). Vereist #1+#2 als fundament.

B. Lifecycle notifications + scheduled delta-reconciliatie (anti-gap vangnet) — M, high. Vang reauthorizationRequired/subscriptionRemoved + draai 6-12u volledige delta-sync. Sluit reauthorizationRequired naadloos aan op de M365 8u-herlogin: "herlogin nodig" naar AgentInbox i.p.v. stil falen. Onmisbaar omdat Graph-webhooks aantoonbaar intermitterend uitvallen.

C. Plan-Execute-Verify (PEV) kern-loop in SpanAgent — L, high. Planner (Haiku) → Executor (Sonnet, 1 stap) → Validator (Haiku, 0-1 score) → deterministische Python-router (proceed/retry/replan/fail). Alléén voor meerstaps-quests, niet voor enkele-tool antwoorden. Afweging: hoge effort, maar Planner/Validator krijgen NOOIT tools → kleiner injectie-oppervlak. Nieuw bestand `span/orchestration/pev_loop.py` dat de bestaande ToolBox wrapt.

D. Plan-immutability + control-plane/data-plane scheiding — M, high. Bevroren plan (append-only audit); tool-output is data-plane en mag het plan NIET herschrijven; alleen de Router (op validatie-scores, niet vrije tekst) wijzigt het plan. Kern-mitigatie tegen indirecte injectie: een geïnjecteerde mail-instructie kan de stappenlijst niet kapen. Bouw samen met C.

E. CaMeL-light: dual-LLM rolscheiding + provenance-vlag — L, high. Je ORQ-router heeft al Sonnet (privileged/plant tool-calls) + Haiku (quarantined/verwerkt onvertrouwde data). Markeer elke fragment/tool-output met `trusted: bool`; HIGH-actie met onvertrouwde args → verplicht AgentInbox. Haiku mag NOOIT tool-calling krijgen — dát is de bescherming. Sterkste structurele injectie-verdediging. Neem ALLEEN de twee goedkope kernideeën, niet de volledige CaMeL-machinerie (zie Niet Doen).

F. Durable/resumable execution via Neo4j-checkpointing — L, high. Sla PEV-state (plan, past_steps, current_idx, scores) per quest-thread op; overleef Docker-herstart én de 8u M365-herlogin. Hergebruik de bi-temporele geheugen-kern (`checkpointer.py`). Bij hervatten na HITL-pauze: hervalideer wereld-state (afspraak intussen verzet?). Maakt lange autonome taken betrouwbaar.

G. Ambient HITL-triage + acceptance-rate feedback-loop — M, high. AgentInbox upgraden naar drie modi (Notify/Question/Review-Approve) én een first-class pauzeer/hervat plan-node. Log accept/ignore/reject + context als Fragments/Mistake → triggerdrempels leren. Dit voedt de zelflerende cirkel met proactiviteit zelf. Gevoelige acties (mail/afspraak) blijven hardcoded op REVIEW ongeacht trust-niveau.

H. Pre-meeting context-bundeling op opportuun moment — M, high. X min vóór een call automatisch mails + Fireflies-notulen + open Asana-taken + deelnemer-context uit de graph bundelen en als NOTIFY pushen, getriggerd door de agenda. Onderscheidende JARVIS-ervaring; read-only, alleen aan Bas.

I. Tamper-evident audit-trail (SHA-256 hash-chain in Neo4j) — M, medium-high. `:AuditEvent`-keten met actie, geredigeerde args, risk_tier, approval-uitkomst, provenance, prev_hash. Forensisch nuttig bij vermoeden van injectie; complementair aan de zelflerende cirkel (WAT Span deed, met welke goedkeuring). Redigeer PII vóór loggen.

OVERWEEG LATER / SECUNDAIR (haalbaar maar niet eerst)

- Event-naar-graph correlatie (events als episodische fragments) — M, medium. Mooi voor dagbriefings/meeting-prep, maar zet eerst de event-pijplijn (A/B) en privacy (aidefence_has_pii + verval) goed neer.
- Least-privilege task-scoped tool-gating per plan-stap — M, medium. Logisch ná C/D; tool-allowlist per PlanStep.
- ReWOO-modus voor token-zuinige read-only fan-out (dagbriefing) — M, medium. Naast PEV; alleen voor idempotente read-only ketens.
- Graduated/adaptieve autonomie per integratie/categorie — M, medium. Pas zinvol nadat audit-trail (I) track-record levert; upgrade altijd expliciet door Bas bevestigd.
- Reisduur/"tijd om te vertrekken"-trigger — M, medium. Vaste home/work-coördinaten in config (geen live GPS); geen agenda-titels naar route-API.
- Replan met failure-context (i.p.v. blinde retry) + append-only run-trace met step-scores — S/M. Bouw mee met C/F.
- Undo via compensating actions (saga) — M, medium. Eerlijk in de UI over wat écht omkeerbaar is (verzonden mail = verzonden).
- Memory-write validatie tegen poisoning — M, medium. Lagere trust/confidence op Insights uit onvertrouwde bronnen; herkomst-veld per node. Sluit aan op bestaand bi-temporeel verval.
- Next-action suggesties uit temporele graph-patronen — L, medium. Begin regel-gebaseerd (frequency/sequence-mining); GNN/RNN is overkill op huidige schaal.
- OpenTelemetry GenAI-tracing — M, low. Let op naamsbotsing met product "Span"; collector strikt lokaal. Nuttig maar geen prioriteit.

NIET DOEN (NU) — onhaalbaar of onverstandig op deze stack/constraints

1. Azure Event Grid / Event Hubs als delivery-kanaal. Vereist Azure-abonnement, extra cloud-infra, kosten én koppeling aan Lomans-tenant-governance/conditional-access. Voor single-user is webhook+tunnel+delta eenvoudiger, gratis en volledig zelf-gehost. Vermeng Span's persoonlijke infra niet met Lomans-cloud.

2. Rich notifications (met resource-inhoud in de payload). Vereisen encryptiecertificaten + private-key-beheer en hebben een kortere 1-dag-lifetime. BASIC + delta-query geeft hetzelfde resultaat zonder secret-management; alleen het resource-id reist via de tunnel.

3. Volledige CaMeL (custom Python-interpreter + formele capability-policies). Auteurs noemen zelf het policy-onderhoud als hoofdbeperking; lost maar ~77% op met provable security. Onderhoudslast > opbrengst voor single-user. Over-engineering leidt tot rubber-stamping = nétto minder veilig. Neem alleen E's twee kernideeën.

4. Lokale Quarantined-LLM op deze host. Windows ARM64 zonder GPU + alleen binary wheels maakt serieuze lokale inference onpraktisch/traag. De quarantaine-bescherming zit in "geen tool-access", niet in "lokaal draaien" — blijf Haiku via ORQ draaien (no-tools).

5. Zware RL-trainingspipeline / GRPO voor timing (ProActor, PPP/UserVille). Single-user = te weinig data voor RL-convergentie; GPU-loze ARM64 = geen training; ORQ-router is inference-only. Neem de ideeën over (reward-dimensies als heuristieken, opportunity-windows, feedback-logging) maar niet de trainingsmachinerie. Bespaart maanden zonder rendement.

6. Fast-Downward voor GOAP-planning. Vereist C++-compilatie — botst met de binary-wheel-constraint op Windows ARM64. Als je GOAP wilt: begin met pure-Python pyperplan (haalbaar) — maar de hele GOAP-laag is sowieso secundair t.o.v. PEV en pas te overwegen ná bewezen PEV + budget-envelope.

7. Continue egocentrische sensing (smart glasses / always-on audio-video). ARM64 zonder GPU maakt on-device vision/continue audio onpraktisch, de Lomans-proxy blokkeert al Web Speech, en continue AV in een werkcontext is een privacy-ramp. Digitale signalen (agenda/mail/taken/coördinaten/weer) leveren ~90% van de waarde zonder de risico's.

8. Reason-Plan-ReAct hybride (strateeg boven uitvoerder). Meer LLM-calls, hogere kosten, groter aanvalsoppervlak — waarschijnlijk overkill voor single-user. Pas overwegen áls PEV in de praktijk te rigide blijkt; niet nu.

KERNVOLGORDE (rode draad)
Fundament eerst: #1 tunnel → #2 webhook-hardening → #3 risk-tier + #4 arg-validatie + #6/#7 budget/circuit-breaker → #5 adversariële tests. Dan event-pijplijn A+B. Dan autonomie C+D+E samen (PEV + plan-immutability + dual-LLM/provenance horen bij elkaar), met F (durable) en G (ambient HITL + leren) erbovenop. H (meeting-prep) en I (audit-trail) als zichtbare JARVIS-waarde. Alle gevoelige acties blijven permanent achter REVIEW in AgentInbox — autonomie-escalatie mag de injectie-verdediging nooit omzeilen.


## Gebied: integraties

# SYNTHESE — Gebied "Integraties" voor Span

Single-user persoonlijke agent (Bas / Lomans). Stack: lokaal Docker op Windows ARM64, FastAPI + WebSocket, Neo4j (bi-temporeel, HNSW), ORQ.AI-router + Claude, Telegram + server-side Whisper, AgentInbox-goedkeuringspoort, ambient watcher, dag-scheduler/self-scheduling crons, MarkItDown-ingest. Harde constraints: read-only werk-tenant, 8u conditional-access herlogin, geen publieke inbound, ARM64 binary-wheels-only, files <500 regels.

---

## QUICK WINS (laag-hangend fruit, S/M, hoge impact)

1. **ntfy self-hosted push-laag** (S, hoog) — extra arm64 Docker-container + `integrations/ntfy.py` + notify-tool in de ToolBox. Koppel aan AgentInbox-verzoeken, dag-briefings, ambient-signalen. Token-auth aan; nooit PII in de pushbody (alleen titel + "open Span"). Dit is de snelste weg naar "Span overal bij de hand".

2. **iCal-feed endpoint `/calendar/{token}.ics`** (S, hoog) — read-only FastAPI-route met `icalendar` (pure-Python wheel, ARM64-OK). Publiceert crons/Quest-deadlines/AgentInbox-deadlines als abonneerbare kalender (webcal:// in Outlook/Apple). Lange niet-raadbare, roteerbare token in het pad; "privé = bezet/geen titel"-modus tegen datalek.

3. **`/api/inbound` harden met HMAC-SHA256 + timestamp/nonce** (S, hoog) — randvoorwaarde vóór elk nieuw inbound-kanaal. Constant-time compare, replay-window ~5 min, per-bron secrets in `.env`. Let op: geldige HMAC = herkomst, niet onschadelijke inhoud — payload blijft untrusted data, via quarantaine-route + AgentInbox.

4. **Telegram voice-handler → server-Whisper** (S, hoog) — hergebruikt de bestaande STT-pijplijn; ffmpeg-binary (ARM64-OK). Omzeilt de proxy-blokkade van de browser Web Speech API volledig want STT is server-side. Praten tegen Span onderweg zonder telefonie-stack.

5. **Telegram inline-keyboards als mobiele AgentInbox-poort** (S/M, hoog) — Goedkeuren/Afwijzen-knoppen (callback_data) op de bestaande poort. Whitelist Bas' chat_id; toon exact WAT wordt goedgekeurd (ontvanger + onderwerp) tegen blind tappen. Maakt Span proactief-op-afstand. Combineer met slash-commando's (`/brief`, `/quests`, `/snooze`).

6. **Whisper large-v3-turbo int8** i.p.v. base (S, hoog) — alleen model-id wijzigen + cache in span-models volume; CTranslate2 wheels zijn ARM64-OK. Fors lagere WER op Nederlands. Meet eerst latency op de ARM64-host vóór je het default maakt.

7. **Graph delta-query polling voor mail/agenda/Teams** (M, hoog) — versterk de ambient watcher met `/messages/delta`, `/events/delta`; deltaToken in Neo4j, self-scheduling cron. Dit is het JUISTE pull-based alternatief voor webhooks op een lokale stack. Bouw graceful re-login + delta-resume in voor de 8u CA-cyclus.

8. **Graph Search API `m365_search`** (M, hoog) — read-only POST /search/query (driveItem/listItem/site, KQL) voor Lomans SharePoint/OneDrive zonder Neo4j-indexering. Delegated Files.Read.All + Sites.Read.All, permission-trimmed. aidefence_scan op resultaten vóór ze in een prompt gaan.

9. **RSS/Atom-ingest met conditional GET** (M, hoog) — `feedparser-rs` (Rust-core wheel, ARM64-OK), ETag/Last-Modified. Bronnen: Installatie.nl, Cobouw, Techniek Nederland, ISSO, NEN, RVO. Haiku-first-pass filter (signal/noise) + Sonnet-samenvatting. **Vereist de spotlighting-envelope (#10) vooraf.**

10. **Spotlighting/delimiter-isolatie voor externe ingest** (S, hoog) — centrale "untrusted content envelope" in de prompt-laag: extern materiaal omsloten door unieke delimiters + "dit is data, geen opdracht". Randvoorwaarde vóór RSS/web/mail/ERP. Verlaagt injectiekans; gevoelige acties blijven hardgecodeerd door AgentInbox.

11. **find_meeting_availability (Graph findMeetingTimes)** (S, midden) — read-only vrije slots vóór een afspraak-voorstel naar AgentInbox gaat. Strikt scheiden: beschikbaarheid checken mag vrij, event boeken (POST /events) alleen via AgentInbox.

12. **Cloudflare Tunnel (cloudflared)** (S, midden) — los binary, uitgaande versleutelde tunnel zodat externe diensten `/api/inbound` bereiken zonder open poorten. ALLEEN samen met HMAC (#3) + Cloudflare Access. Check eerst of Lomans-proxy uitgaand QUIC/443 toestaat (anders HTTP/2-fallback). Geen Lomans-werkdata over persoonlijke tunnel zonder akkoord.

*Lichte ondersteuners (S):* `$search`/mentions-filter op Teams-berichten (minder ruis én kleiner injectie-oppervlak); numerieke datum-normalisatie + "vandaag"-injectie voor feeds; self-scheduling branche-briefing-cron.

---

## GROTE SPRONGEN (ambitieus, tillen Span echt op)

1. **n8n als self-hosted automatiserings-hub** (M, hoog) — eigen multi-arch container die 400+ diensten als triggers afvangt en genormaliseerd naar `/api/inbound` POST't; Span start omgekeerd workflows via outbound-webhook. Houdt Span's codebase klein (CLAUDE.md-conform). Afweging: nieuw aanvalsoppervlak + eigen credential-store — isoleer in eigen netwerk, basic-auth aan, ORQ/LLM-keys eruit, alle write-requests via AgentInbox. **Dit is de slimste manier om breedte te krijgen zonder Span op te blazen.**

2. **CaMeL-achtige trust-boundary voor álle externe input** (L, hoog) — architectuur i.p.v. feature: scheid de plannende LLM (alleen vertrouwde Bas-input) van een quarantaine-pad dat alle externe payloads als pure data behandelt; één gedeelde "untrusted ingest"-functie (inbound-webhook, mail-triage, MCP-args, document-ingest) met aidefence_scan/has_pii aan de grens + least-privilege per tool. Afweging: hoge effort, maar **moet meegroeien** zodra je inbound-kanalen opent (n8n, MCP, WhatsApp). Versterkt AgentInbox (invoer-kant) i.p.v. te vervangen (uitvoer-kant). Dit is dé verdediging tegen indirecte prompt-injectie.

3. **Streaming STT (WebSocket) + Silero-VAD + ElevenLabs/Cartesia streaming TTS** (M elk, hoog) — vervang segment-POST door doorlopende PCM16-WS-stream (words-as-you-speak); Silero-VAD i.p.v. vaste 1.2s-stilte (geen afgekapte zinnen); natuurlijke streaming-TTS over Span's eigen WS (omzeilt proxy). Samen tillen ze het voice-first JARVIS-gevoel echt op. Afweging: TTS-tekst gaat naar US-vendor → per-bericht "geen cloud-TTS"-vlag voor gevoelige antwoorden; overweeg Kokoro-82M ONNX lokaal als privacy-fallback (verifieer NL-support eerst).

4. **PWA + Web Push (VAPID)** (M, hoog/midden) — manifest + service worker op de web-HUD ("Add to Home Screen") + pywebpush (pure-Python). Volwaardige mobiele toegang zonder native app. Afweging: iOS levert Web Push alléén vanaf geïnstalleerde PWA — daarom is ntfy (#1 quick win) op iOS robuuster/lager-drempel. Doe PWA voor de HUD, laat push voorlopig via ntfy lopen.

5. **Mail-archief bulk-import als episodisch geheugen** (L, hoog) — historische Outlook-mail → thread-aware RAG, embed met text-embedding-3-large@1024, Fragments met bi-temporele timestamps. Geeft Span echt langetermijngeheugen ("wie-zei-wat-wanneer"). Afweging: groot PII-volume → versleutelde archief-store, uitsluiten van uitgaande externe LLM-context behalve geanonimiseerd, respecteer 8u CA bij de export-fase.

6. **Span als remote MCP-server (read-subset)** (M, midden) — Streamable HTTP-transport met `search_memory`/`get_today_brief`/`list_open_quests`, zodat Claude Desktop / andere agents / ORQ Span's brein kunnen bevragen via de gestandaardiseerde MCP-laag. Afweging: v1 UITSLUITEND read-only/idempotent; MCP-args zijn untrusted (aidefence_scan aan de protocolgrens).

7. **Copilot Retrieval API als grounding-bron** (M, hoog) — semantische chunks uit SharePoint/OneDrive zonder eigen RAG-pijplijn, ideaal voor de ORQ-router bij Lomans-vragen. **Harde blocker: vereist M365 Copilot-licentie of PAYG — eerst verifiëren of Lomans die heeft.** Zo niet → val terug op Graph Search (#8 quick win), die geen licentie nodig heeft.

*Branche-data (verifieer licentie/credentials eerst):* 2BA productdata (ETIM, L), Ketenstandaard Milieudata REST API (M, schone eerste "echte API"), NEN-normen-ingest via MarkItDown (M — let op: géén publieke API, gemachtigde download binnen licentie; ISSO blijft betaald/niet scrapen), Lomans-ERP read-only via MCP (L, hard read-only op connector-niveau).

---

## NIET DOEN (NU) — onhaalbaar/onverstandig op deze stack

1. **Graph change-notification webhooks (push)** — EISEN publiek bereikbaar HTTPS-endpoint dat <3s valideert; Span draait lokaal achter NAT/proxy. Een tunnel exposeert het brein aan internet = onaanvaardbaar voor een single-user persoonlijke agent. Tenant-brede Teams-webhooks vereisen bovendien app-permissies + cert + metered billing. → **Gebruik delta-query polling (quick win #7).**

2. **Tenant-brede Teams `getAllMessages` (application-permissies)** — valt onder Microsoft's metered Teams-data-export-billing + app-only consent, botst met de read-only werk-tenant. → **Blijf bij delegated per-chat/per-channel reads.**

3. **Volledige speech-to-speech (gpt-realtime / Nova Sonic / Gemini Live)** — omzeilt de ORQ-router + Claude, je verliest LLM-keuze, ToolBox- en AgentInbox-logica; ondoorzichtige reasoning ondermijnt de goedkeuringspoort en injectie-verdediging; $5-6/15min; lagere TTS-kwaliteit dan losse vendors. → **Houd de chained pipeline STT→ORQ/Claude→TTS.**

4. **Officiële WhatsApp Cloud API (nu)** — per-bericht-pricing sinds 1 juli 2025, geverifieerd Business-account vereist, 24u-window blokkeert proactieve pushes. Te zwaar/kostend voor een privé single-user JARVIS. → **Telegram + ntfy eerst; Cloud API alleen als ToS-compliance ooit hard vereist wordt.**

5. **Evolution API als unified messaging-gateway** — zwaardere container (queues, dashboards), meer attack-surface; overkill voor één persoonlijk nummer. → **Kale WAHA volstaat áls je überhaupt WhatsApp wilt.**

6. **Eigen Zapier/IFTTT-connector-catalogus in Span bouwen** — botst met "niets meer dan gevraagd / files <500 regels", vergroot attack-surface en credential-opslag. → **Connectiviteit-breedte bij n8n; Span houdt alleen één inbound-route + één outbound-emitter + de iCal-feed.**

**Twijfelgeval — bewust apart:** *self-hosted WhatsApp (WAHA/whatsmeow)* is technisch haalbaar (losse Go/Node-container, geen ARM64-pip-issue) en hoog-impact, maar draagt een reëel **ban-risico** (unofficial libs schenden WhatsApp-ToS). Niet bij de quick wins. Overweeg alleen later, op een **apart/secundair nummer**, low-volume, met afzender-whitelist + AgentInbox + versleutelde sessietokens. Voor nu dekken Telegram + ntfy de mobiele behoefte zonder dit risico.

---

## RODE DRAAD

Eerst de **veiligheidsfundering** leggen (HMAC-inbound #3 + spotlighting-envelope #10, later opschalend naar de CaMeL trust-boundary), dán pas inbound-kanalen openzetten. Mobiel bereik komt het goedkoopst via **Telegram + ntfy** (niet WhatsApp). Voice wint via de **chained pipeline** (geen S2S). M365 gaat via **delta-polling + Graph Search** (geen webhooks, geen Copilot-licentie nodig tenzij geverifieerd aanwezig). Breedte aan integraties hoort in **n8n**, niet in Span's eigen code. Constante regel door alles heen: externe input = untrusted data, gevoelige acties = altijd via AgentInbox.

Relevante codelocaties uit de bevindingen: `src/span/integrations/` (telegram.py, nieuwe ntfy.py/m365/rss-adapters, telefonie-bridge), de interfaces-laag (iCal-route), de proactieve/orchestratie-laag (`/api/inbound`, AgentInbox, event-emitter), `voice.js` + nieuw WS-STT-endpoint, en `pyproject` [stt]-extra + span-models volume.


## Gebied: ux

Hieronder de geprioriteerde synthese voor het gebied "ux" van Span. Filter op dubbelingen toegepast (confidence-signaling, citaties/grounding, progressive delegation en provenance-tagging kwamen meerdere keren terug — samengevoegd).

# UX-SYNTHESE SPAN — geprioriteerd

## QUICK WINS (laag-hangend, hoge ROI)

**1. Bento-grid HUD-layout (M, high)**
Herstructureer de losse panelen naar één CSS-grid met ongelijk-grote tegels (Agenda/Mail-triage groot in F/Z-patroon, secundair kleiner). 3-4 kol desktop → 2 tablet → 1 mobiel, herstapelt vanzelf. Raakt HUD-template + CSS. Geen privacy-impact. Dit is het fundament waar de meeste andere HUD-items op landen — eerst doen.

**2. Server-side audiopad: AudioWorklet → PCM16 binair over WebSocket (M, high)**
Vervang elke browser-Web-Speech-afhankelijkheid (proxy blokkeert die) door AudioWorklet → 16kHz PCM16 → binaire WS-frames → bestaande server-side Whisper. Binair i.p.v. base64 scheelt ~33% bandbreedte. Dit is het fundament voor álle voice-features — zonder dit werkt voice niet op deze stack. WS achter auth-token, frame-grootte valideren aan FastAPI-grens.

**3. Silero VAD als eerste audiofilter (S, high)**
ONNX, paar ms/frame op CPU, ARM64-wheel beschikbaar. Alleen spraakframes naar Whisper → Span reageert niet op stilte/ruis en bespaart STT-compute. Basislaag voor turn-detection en barge-in. Volledig lokaal.

**4. Telegram-foto's als eerste oog (S, high)**
`update.message.photo[-1]` → `get_file` → bytes als image-block naar Claude via ORQ. Open, mobiel kanaal dat de geblokkeerde Web Speech API omzeilt. Vereist wel item #11 (multimodale input-pipeline) als basis. Beeld-afkomstige tekst = untrusted → acties altijd via AgentInbox.

**5. Binaire confidence-signaling (S, high)**
"Ik ben hier zeker van" / "Ik twijfel — check dit even" i.p.v. percentages of kleurschalen (getest: sneller beslissen). Lage confidence routeert verplicht naar AgentInbox. Klein in SpanAgent, groot in vertrouwen.

**6. Error/recovery-states, m.n. M365 8u-herlogin (S, high)**
Detecteer 401/expired en toon expliciete "Herlog M365"-actie i.p.v. stille gefaalde stream. Vriendelijke foutkaart + retry. Herlogin start alleen de OAuth-flow, vraagt nooit credentials in de UI. Dit lost een concrete dagelijkse frustratie op deze stack op.

**7. Proactieve notificaties met verplichte "waarom + 3 knoppen" (S, high)**
Elke push (Telegram/HUD): korte reden + bevestig/verzet/negeer. Zonder rationale voelt proactiviteit als surveillance. Combineer met **notificatie-suppressie** (hysteresis + dag-digest om 8:00 via bestaande scheduler) en **next-best-action inline** (1 actieknop per melding). Samen verlagen ze alert-fatigue meteen.

**8. "Op deze dag" / resurfacing-widget (S, high)**
Gebruik de bestaande verval-score omgekeerd: hoge-salience items die wegzakken komen terug via dag-scheduler. Houdt het brein levend i.p.v. archief. Alleen informatief (Telegram/HUD), geen auto-acties.

**9. Lichtgewicht metrics-dashboard zonder nieuwe infra (S, medium)**
FastAPI-endpoint dat bestaande Neo4j-data aggregeert (geheugengroei per node-type/dag, top-entiteiten, reflectie-runs, AgentInbox-goedkeuringsstatistiek, verval-distributie) + Recharts/uPlot. Nul nieuwe containers. Geeft direct inzicht; doe dit i.p.v. Langfuse (zie Grote Sprongen).

**10. Goedkope laag-hangende afrondingen (S elk):**
- **Stop/regenerate-knop** tijdens streamen (bespaart ORQ/Claude-tokens; cancel mag geen half-uitgevoerde gevoelige actie achterlaten).
- **Suggested prompts** tegen lege-veld-effect (afgeleid van agenda/taken, read-only).
- **Subtiele fade-transitions** bij WS-updates i.p.v. full reloads (houdt hologram performant op ARM64).
- **Toegankelijkheid:** aria-live=polite op streaming, WCAG AA-contrast, status via icoon+vorm naast kleur.
- **Conversationele onboarding** (3-5 vragen → profile-node) + **bewerkbaar tone-profiel** (config-blok in system-prompt, buiten .env).
- **MarkItDown-OCR plugin** (pure-Python, geen binaries) maakt afbeeldingen in Office-docs leesbaar; tag output als document-afkomstig/untrusted.
- **Provenance-depth property** op nodes (één extra veld tijdens consolidatie) als zwak/sterk-signaal.

## GROTE SPRONGEN (ambitieus, tillen Span echt op)

**A. Volledige natuurlijke voice-pipeline (Smart Turn v3 + barge-in + streaming TTS)**
Bouwt op #2/#3. Smart Turn v3 (8M, ONNX int8 8MB, ~12ms CPU, NL/EN) bepaalt of Bas écht klaar is met praten i.p.v. vaste stilte-drempel — dit is het verschil tussen walkietalkie en echt gesprek. Plus barge-in (TTS server-side cancellen + buffer flushen + echo-cancellation) en streaming Kokoro-TTS (82M, CPU, ~550ms tot eerste audio, sentence-splitter tussen LLM-stream en TTS).
*Afweging:* effort M-L gestapeld, impact zeer hoog, ARM64-haalbaar (ONNX-wheels). Veiligheid: een half-uitgesproken "ja" mag NOOIT gelden als goedkeuring — gevoelige acties blijven uitsluitend in AgentInbox. Wake word "Hey Span" (openWakeWord) en Pipecat-orkestratie zijn optionele uitbreidingen hierna, niet eerst.

**B. Provenance-graph: "waarom weet je dit?" als klikbaar pad (M, high)**
Expliciete edges (DERIVED_FROM/SUPPORTS/CONTRADICT/INVALIDATE) in de persoonlijke Neo4j tussen bron-fragment → reflectie → Insight/Mistake. HUD toont een backward-trace per geheugen-item. Dit is tegelijk dé zichtbare injectie-verdediging: een Insight die terugleidt naar een verdachte mail valt op. Combineer met **conflict-highlighting** (rode superseded-link) en de **bi-temporele time-slider** (vis-timeline: "wat wist Span op datum X", valid-time vs ingest-time — data is er al, puur visualisatielaag).
*Afweging:* hoge vertrouwens- én veiligheidswinst, raakt geheugen-kern + HUD, read-only weergave dus laag risico.

**C. Citaties/grounding bij geheugen-antwoorden (M, high)**
Retrieval stuurt node-IDs mee; antwoord toont genummerde inline-referenties die uitklappen naar bron-kaarten (welke Fragment/Insight, datum/freshness uit bi-temporeel geheugen). "Citation UI is het belangrijkste vertrouwensmechanisme." Maakt hallucinaties zichtbaar. Sluit direct aan op B.

**D. Agentic transparantie-laag: status + why-cards + plan-preview + activity panel**
Vier samenhangende stukken die het black-box-gevoel breken:
- **Live agent-status-strook** (ASP) — welke tool Span nu draait, gestreamd over WS; toon toolnamen, redigeer PII/tokens.
- **Why-card per tool-call** — 1-2 zin rationale ("waarom roep ik dit aan"); vult de transparantie-kloof (systemen loggen DAT, niet WAAROM).
- **Plan-and-execute preview** voor multi-step taken — heel plan eerst goedkeuren/aanpassen, stappen krijgen vinkje/pulse tijdens uitvoering. Zelf een injectie-verdediging (onverwachte stap valt op).
- **Activity panel gescheiden van chat** — autonoom werk in aparte timeline die page-reload overleeft; + **day-recap** als mens-leesbare audit-trail.
*Afweging:* effort M per stuk, samen transformatief voor vertrouwen; geen extra data-uitgang.

**E. Intent Preview + AgentInbox-versterking**
Vóór mail/afspraak: compacte voorvertoning van de exacte payload (ontvanger, onderwerp, kernzin) als één kaart met Goedkeuren/Wijzig/Weiger. Toon de exacte payload, niet geparafraseerd — zo worden geïnjecteerde instructies zichtbaar. Dit is het laatste menselijke checkpoint en de kern-verdediging tegen prompt-injectie. Koppel aan **confidence-signaling** (#5) en **provenance-tagging gateway** (alle inkomende mail/docs/transcripts expliciet "untrusted" met zichtbare herkomst-tag).

**F. Mens-leesbaar, bewerkbaar geheugen-overzicht (M, high)**
Pagina/CLI om te zien wat Span onthoudt (Insights/Mistakes/Ideas/Skills/Quests) en items te bewerken/"vergeten". Vergeten = soft-delete/valid-time afsluiten, géén hard delete (bi-temporele audit blijft intact). Werk-Neo4j blijft read-only; raakt alleen persoonlijke graph.

**G. Native PDF/document-vision-pad (M, high)**
Tweede ingest-pad naast MarkItDown: visueel-rijke docs (installatieschema's, gescande facturen, presentaties) als native document-block naar Claude via Files API (file_id, niet base64). Sterk relevant voor Information Manager bij installatietechniek. Kies per doc: MarkItDown voor pure tekst (goedkoop), vision voor layout/diagrammen. Limiteer pagina's (~1.5-3k tokens/pagina). Vereist multimodale input-pipeline (#11/#4-basis) en de **visuele-prompt-injectie-verdediging** (aidefence_scan op geëxtraheerde tekst, alles via AgentInbox).

**H. Diagram-as-code (Mermaid/Matplotlib) (M, medium)**
Span genereert Mermaid (sub-graph/relaties visualiseren) client-side gerenderd, of Matplotlib voor trends (mail-volume, agenda-belasting). Betrouwbaarder/goedkoper dan pixel-generatie, dekt ~90% van de reële visuele behoefte. Matplotlib-code in een data-only sandbox draaien, geen vrije eval. **Pixel-image-generatie (Nano Banana) als optionele tool met lage prioriteit** — nice-to-have, niet kern.

**I. Progressive delegation + interrupt-as-first-class (M-L)**
Zichtbare autonomie-schakelaar per integratie/actietype (observeer → stel-voor → handel-met-goedkeuring), die meegroeit met Bas' goedkeurings-historie. Plus globale pauze voor de proactieve laag en undo/rollback op reversibele acties. **Harde grens:** mail-verzenden/afspraken blijven hardgecodeerd achter AgentInbox ongeacht schakelaar of track record — anders verdwijnt de injectie-verdediging. Onomkeerbare acties kunnen niet via rollback achteraf, dus blijven vooraf gegated.

## NIET DOEN (NU)

**1. Cloud speech-to-speech (OpenAI Realtime / Gemini Live).** Omzeilt ORQ's router én de AgentInbox-poort (tool-calls ontsnappen aan goedkeuring + injectie-verdediging), stuurt Bas' mail/agenda als audio naar externe clouds (privacy + Lomans-data), en de proxy/conditional-access maakt persistente realtime-verbindingen fragiel. Het STT→ORQ→TTS chained-pad houdt governance en privacy intact. Bewaar als bewuste "later"-optie voor puur persoonlijke chit-chat.

**2. Zwaar 3D/WebGL-hologram met shaders/particles.** Windows ARM64-host heeft geen GPU-broncompilatie; zware WebGL kost framerate. Houd het hologram licht en state-driven (CSS/lichte canvas).

**3. Lokaal VLM op ARM64 (SmolVLM/Moondream/Qwen2.5-VL via llama.cpp).** llama.cpp-CPU werkt, maar de Python-VLM-stack (transformers/torch ARM-wheels) + mmproj-conversie zijn broos en traag, en kwaliteit ligt lager dan cloud-Claude. Alleen overwegen bij een concrete privacy-eis voor beelden die de machine niet uit mogen; anders is cloud-Claude achter AgentInbox superieur en simpeler.

**4. VR/AR brein-hologram + token-level provenance.** ForceGraphVR/AR levert weinig op zonder VR-hardware tegen veel UX-complexiteit. Token-level evidence-tracing blaast de Neo4j-opslag op en vergroot opgeslagen ruwe (gevoelige) tekst — claim-level is de pragmatische én privacy-vriendelijke korrel.

**5. Langfuse + ClickHouse self-hosted tracing (nu nog niet).** ClickHouse heeft ARM64-images dus technisch haalbaar, maar de extra container-overhead weegt nu niet op tegen het lichtgewicht Neo4j-aggregatie-dashboard (Quick Win #9). Heroverweeg pas als je echte per-turn OTel-traces/kostenattributie nodig hebt.

**6. Emotioneel-hechtende "companion"-persona.** Optimaliseren richting intimiteit/vleierij geeft risico op manipulatie en ongewenste gehechtheid; sycophantische output ondermijnt kritisch oordeel. Voor een dagelijkse werk-assistent is een eerlijke, beknopte, soms-tegensprekende toon waardevoller — bouw een lichte anti-sycophancy-instructie in de persona.

## Volgorde-advies
Fundamenten eerst: **#1 (bento-grid)** en **#2+#3 (audiopad+VAD)** ontgrendelen respectievelijk de HUD- en voice-laag. Dan de goedkope vertrouwens-quick-wins (#5/#6/#7). Daarna de grote sprongen in deze volgorde: **B+C (provenance+citaties)** → **D (transparantie-laag)** → **A (natuurlijke voice)** → **E/F/G**. Veiligheidsrode draad door alles heen: AgentInbox blijft de enige poort voor onomkeerbare/gevoelige acties, ongeacht voice, vision, confidence of autonomie-niveau.
