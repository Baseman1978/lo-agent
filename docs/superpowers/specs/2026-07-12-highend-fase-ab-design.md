# LO high-end roadmap — fase A + B (design)

**Datum:** 2026-07-12 (herzien na WhatsApp-onderzoek + Bas' vier expliciete keuzes)
**Status:** ontwerp — door Bas in de brainstorm goedgekeurd; wacht op spec-review
**Scope-besluit:** fase A hard gecommit. Fase B: de drie grote sprongen (B1 spraak-pijplijn, B3 taak-motor, B5 PWA) zijn door Bas **expliciet gecommit** — de A→B-telemetrie stuurt daar de *volgorde en tuning*, niet meer de go/no-go. De overige B-blokken blijven bewijs-gepoort.
**Bas' vier keuzes (brainstorm 12-07):** (1) stem = ElevenLabs-streaming, bewust US-compromis, mét A/B-luistertest; (2) spraak fase B = volle pijplijn; (3) taak-motor = volledig in fase B; (4) bereik = PWA in fase B. Plus: **WhatsApp-kanaal** in fase A (chat + spraakmemo's), bellen via WhatsApp als fase-C-oogst.

## Doel

LO doorontwikkelen tot een high-end agent op drie assen die Bas expliciet heeft benoemd:

1. **Vloeiend spraakgesprek** — praten met LO voelt als een telefoongesprek, niet als een walkie-talkie.
2. **1001 taken uitvoeren** — taken die minuten tot dagen lopen, herstart overleven, en nooit stilletjes falen.
3. **Historisch goed geheugen** — LO onthoudt wat er wanneer gezegd is en kan er tijd-bewust op terugvallen.

## Kernprincipe: ruggengraat vóór structuur

De centrale ontwerpbeslissing: **elk fase-A-blok is de omkeerbare "ruggengraat"-versie van een structureel fase-B-blok.** Fase A levert ~80% van het voelbare resultaat tegen ~20% van het risico met bijna uitsluitend omkeerbare wijzigingen (config-schakelaars, telemetrie, retries, indexen). Fase B bevat de structurele sprongen die innovatie-tokens kosten (nieuwe services, schema-migraties, nieuwe client). Voor de gepoorte blokken (B2/B4) geldt: pas bouwen als de telemetrie bewijst dat de ruggengraat niet volstond. Voor de gecommitte blokken (B1/B3/B5) geldt hetzelfde principe zachter: de telemetrie bepaalt niet óf ze komen, wel in welke volgorde en met welke invulling.

| Pijler | Fase A (ruggengraat, omkeerbaar) | Fase B (structureel) |
|---|---|---|
| Spraak-uit | A2 — ElevenLabs-WS *goed-genoeg* | B2 — ElevenLabs productie-hardening *(gepoort)* |
| Spraak-in | A1-switch — STT → GPU-Whisper (batch) | B1 — streaming-STT + Smart Turn v3, transport-agnostisch *(gecommit)* |
| Taken | A3 — vangnet (retries / eerlijke uitkomst / Telegram-push) | B3 — DBOS durable engine *(gecommit)* |
| Geheugen | A4 — onderhoud (indexen / health / degraded-mode) | B4 — bi-temporeel + tijd-recall + TEI-embeddings *(gepoort)* |
| Bereik | A5 — mobiel-spraakfixes + Telegram-voice · A6 — WhatsApp chat + spraakmemo's | B5 — PWA *(gecommit)* + WhatsApp-proactief |

## De meetlat (§1) — telemetrie-first

**A1 is de fundering en gaat strikt eerst, alleen.** Zonder de gesegmenteerde meetlat is de A→B-poort blind en verwatert "bewijs-gepoort" tot onderbuik.

A1 meet elke conversatie-beurt in aparte segmenten:

- `stt_ms` — spraak→tekst latency
- `llm_ms` — eerste-token en totale generatie
- `tts_ms` — tekst→eerste-audio latency
- `tool_ms` — tool-executie (per tool)
- `turn_total_ms` — end-to-end
- `outcome` — succes / fout / degraded, met foutklasse

Opslag: lichte append-only log (JSONL) + aggregatie-endpoint. Geen nieuwe service; draait in de bestaande FastAPI-server. De poort-beslissingen lezen deze aggregaten.

**Poort-regel (voor de gepoorte blokken B2/B4):** zo'n blok wordt alleen gebouwd als de fase-A-telemetrie aantoont dat de bijbehorende ruggengraat de bottleneck was. Voor de **gecommitte** blokken (B1/B3/B5) verandert de telemetrie niets aan het óf, wel aan het hoe: zij bepaalt de volgorde, de tuning en waar de eerste winst zit (voorbeeld: als `llm_ms` domineert, begint B1 met fast-lane-routering in plaats van streaming-STT).

## Fase A — de ruggengraat (hard gecommit)

Sequentieel gebouwd, elk op een eigen branch. Geen parallel werk op gedeelde core-bestanden (anders vervuilt A1's meetlat).

### A1 — telemetrie + twee schakelaars
- Gesegmenteerde beurt-telemetrie (zie §1).
- Schakelaar 1: streaming-TTS aan op de bestaande pijplijn (feature-flag).
- Schakelaar 2: `SPAN_STT_URL` naar de ongebruikte GPU-Whisper-container op z390 (:9000).
- **Meetpunt:** baseline-latency vastleggen vóór, en na elke schakelaar.

### A2 — ElevenLabs-WS streaming (goed-genoeg)
- ElevenLabs WebSocket-streaming voor spraak-uit, versie "goed-genoeg" (niet productie-gehard).
- **A/B-luistertest** (Bas' keuze 1): Flash v2.5 vs Multilingual v2 op Nederlandse zinnen; Bas kiest op oor, latencyverschil komt uit de A1-telemetrie.
- Micro-bevestigingen (LO geeft hoorbaar aan dat hij bezig is bij lange tool-calls).
- Prewarm van de TTS-verbinding om cold-start te vermijden.
- **Poort:** pas aanzetten nadat A1 bevestigt dat TTS-latency de bottleneck is. ElevenLabs-key is tijdelijk; EU-residency is Enterprise-only → AVG-besluit hangt (buiten scope van dit blok, zie §5).

### A3 — taak-vangnet
- Retries met backoff op falende tool-calls (alleen transiente fouten; muterende tools nooit blind herhalen).
- **Eerlijke uitkomsten:** een taak die faalt meldt dat expliciet (geen stille fallback, geen verzonnen succes).
- Cron-toets: controleer dat geplande taken (dagafsluiting, consolidatie, weekreview) daadwerkelijk liepen; gemiste run = melding.
- Telegram-push wanneer een langlopende taak klaar is of definitief faalt.
- **Meetpunt:** hoeveel taken hadden retries nodig; hoeveel liepen > enkele minuten; hoeveel moesten herstart overleven (stuurt de B3-inrichting, zie B3).

### A4 — geheugen-onderhoud
- Ontbrekende/suboptimale HNSW-indexen herstellen; index-gezondheid controleren.
- Quest-limiet (begrens geheugen-groei per categorie).
- Health-check op de Neo4j-brain + degraded-mode (agent blijft werken als de brain traag/onbereikbaar is).
- **Meetpunt:** recall-kwaliteit + brain-latency (bepaalt of B4 nodig is).

**A4 GEBOUWD + LIVE (PR #118, 2026-07-13).** 8 range-indexen + `entity_name`-constraint geverifieerd ONLINE op `span-brain`; nachtelijke brain-health-taak draait (`ok=True`); embed-guard in `turn()` en degraded-bootstrap in `begin()` achter `SPAN_DEGRADED_MODE`; quest-limiet actief.

Baseline brain-latency (z390, 75 records na deploy):

| op | n | p50 | p95 | max |
|---|---|---|---|---|
| run | 74 | 5,3 ms | 32,8 ms | 81,9 ms |
| healthcheck | 1 | 4,5 ms | 4,5 ms | 4,5 ms |

Het brein is snel (p50 ~5 ms); geen aanwijzing dat brain-latency de bottleneck is. `read`/`vector`-segmenten vullen zich zodra er RAG-verkeer is.

**B4-poortconclusie (voorlopig):** brain-latency geeft géén reden voor B4. De tijd-blindheidsvraag ("verwachte node bestond, maar oude/verkeerde versie kwam boven") is nog niet beantwoord — dat vergt de recall-baseline, die op de **A7**-gouden set wacht (`eval_retrieval_set.json` staat nog niet op de server). **Openstaand:** (1) recall-baseline draaien zodra A7 de set levert; (2) degraded-drill (Neo4j gecontroleerd stoppen) op een rustig moment met Bas.

### A5 — bereik + duurzaamheid
- Mobiele spraak-fixes (bekende iOS/Safari mic-quirks; QR-flow moet naar HTTPS wijzen).
- Telegram-voice (spraakberichten in/uit via de bestaande Telegram-bridge, `sendVoice`).
- Backup-drill: verifieer dat de nova-backup-restore daadwerkelijk werkt (restic + rsync-laag).
- `verify_chain` als nachttaak: dagelijkse integriteitscontrole op de brain, afwijking = melding.

### A6 — WhatsApp-kanaal (laag 1 + 2)
- **Route:** Meta WhatsApp Cloud API, direct (geen BSP-tussenlaag). Onofficiële routes (whatsmeow/Baileys e.d.) zijn verboden — ban-risico, en nooit op Bas' eigen nummer.
- **Apart nummer voor LO** (prepaid-SIM of VoIP-nummer; eenmalige OTP-verificatie, daarna is de SIM niet meer nodig).
- Laag 1 — chat: webhook → bestaande agent-loop → antwoord via Cloud API. Zelfde guard/risk-governance als Telegram; WhatsApp-inhoud is untrusted input.
- Laag 2 — spraakmemo's: inkomende voice-note → bestaande STT; antwoord optioneel als voice-note terug via TTS (Opus/OGG).
- **Meta business-verificatie nu starten** (doorlooptijd weken; vereist voor hogere messaging-tiers en later voor Calling). Actie ligt bij Bas; de LO-kant is bouwbaar zodra het testnummer werkt.
- **Beleidsklep:** EU/Meta-beleid rond AI-assistenten op WhatsApp is in beweging (EC schorste Meta's assistentenverbod EER-breed, zaak AT.41034). Kanaal wegneembaar bouwen: dunne adapter op de bestaande loop, geen kernlogica in de WhatsApp-laag.

### A7 — eval-set v1
- 20 representatieve taak-scenario's + 50 Nederlandse geheugenvragen met verwachte antwoorden.
- Handmatig draaibaar script; uitkomsten in dezelfde telemetrie-log als A1.
- Dit is de meetlat waarmee de gepoorte B-blokken (B2/B4) beslist en de gecommitte (B1/B3/B5) getuned worden; B6 automatiseert hem 's nachts.

## Fase B — de structurele sprongen

B1, B3 en B5 zijn door Bas **gecommit**; hun telemetrie-vraag stuurt volgorde en invulling. B2 en B4 houden een echte **poort-vraag**: "nee" = uitstellen.

### B1 — volledige spraak-pijplijn (gecommit)
- Streaming-STT (partial transcripts tijdens spreken, kandidaat WhisperLiveKit) + Smart Turn v3 voor natuurlijke beurtwisseling — definitieve keuze na een NL-benchmark op de eval-set.
- **Transport-agnostisch:** de spraakloop (STT → agent → TTS) wordt losgetrokken van de browser, zodat dezelfde loop later WhatsApp Calling (fase C) en eventueel HA-satellites kan bedienen.
- Fast-lane-routering: korte/sociale beurten via een sneller pad, op basis van A1-bewijs over waar de latency zit.
- **Telemetrie-vraag (volgorde, geen go/no-go):** domineert `stt_ms` of `llm_ms`? Dat bepaalt of streaming-STT of fast-lane eerst komt.

### B2 — ElevenLabs productie
- Hardening van A2: reconnect-logica, foutafhandeling, stem-selectie definitief, kosten-monitoring.
- **Poort:** heeft A2 zich bewezen als de juiste TTS én is het AVG-besluit rond (EU-residency)? Zo niet → uitstellen.

### B3 — DBOS durable task engine (gecommit)
- Volwaardige durable-execution-motor via **DBOS-als-library** (in-process in de bestaande FastAPI-app; system-DB op SQLite of Postgres — geen aparte orkestratie-service): taken overleven herstart, workflow-observability, approval-resume (taak pauzeert op akkoord-vraag en hervat na Bas' klik).
- Idempotency-keys + dry-run-modus op muterende tools, zodat een hervatte workflow nooit dubbel muteert.
- Push-status: langlopende taken melden voortgang via het bestaande announce/Telegram-kanaal.
- **Telemetrie-vraag (invulling, geen go/no-go):** A3-meetpunten bepalen welke taaktypen het eerst op de motor gaan. *Dit blijft het duurste token — daarom als library, niet als service.*

### B4 — bi-temporeel geheugen
- Bi-temporeel schema (event-tijd + ingest-tijd), tijd-bewuste recall ("wat wist je op datum X"), TEI-embeddings productie.
- **Poort:** liet A4 zien dat tijd-blindheid echte recall-fouten veroorzaakte? Zo niet → uitstellen.
- **Risico-eis:** hoogste migratie-risico van het hele plan (schema-retrofit + her-indexeren op de live brain). MOET laatste blok zijn, achter dual-write/shadow-migratie, nooit big-bang.

### B5 — PWA + WhatsApp-proactief (gecommit)
- Progressive Web App zodat LO op mobiel als geïnstalleerde app draait (offline-shell, push, home-screen).
- WhatsApp-proactief: LO mag zelf berichten sturen (dagafsluiting, urgente melding) — venster-bewust: binnen het 24-uurs service-venster vrij, daarbuiten alleen via goedgekeurde templates.
- **Telemetrie-vraag (volgorde):** A5/A6-gebruik bepaalt wat eerst komt — PWA of WhatsApp-proactief.

### B6 — nachtelijke eval + observability
- Nachtelijke eval-suite (regressie op spraak-latency, taak-succes, recall-kwaliteit) + observability-dashboard bovenop de A1-telemetrie.
- **Geen poort:** dit is de meetlat-verankering die de andere B-poorten voedt; bouwen zodra fase B start.

## Afhankelijkheden (§4)

- A1 → alles (meetlat eerst).
- A2 poort-afhankelijk van A1-telemetrie.
- A6 kan parallel aan A2–A5 (raakt geen gedeelde core-bestanden; wacht wel op Meta-testnummer). Meta business-verificatie start op dag 1 (doorlooptijd weken).
- A7 vóór de A→B-grens (zonder eval-set geen poort-besluit en geen B-tuning).
- B2/B4 gepoort op hun fase-A-tegenhanger; B1/B3/B5 gecommit, volgorde uit telemetrie.
- B4 laatste (migratie-risico) en na B6 (eval vangt regressie in de migratie).
- WhatsApp Calling (fase C) hangt af van B1's transport-agnostische loop + Meta-verificatie/tier.
- Sequentiële build op eigen branches; gedeelde core-bestanden = geen parallel.

## Veiligheidskleppen (§5)

- **CI-gate:** merge alleen op groene CI (zowel push- als PR-run). Nooit mergen op rood (les uit PR #110).
- **Geheimen:** nooit printen (alleen presence/length-checks); `.env` chmod 600; ElevenLabs- en Tavily-keys buiten git.
- **AVG:** ElevenLabs EU-residency (Enterprise) + DPA's zijn Bas' eigen open items; B2 is geblokkeerd tot dat besluit rond is.
- **Degraded-mode overal:** brain onbereikbaar, TTS down, tool-fout → LO blijft functioneel en eerlijk over wat niet lukte.
- **Omkeerbaarheid:** elke fase-A-wijziging achter een feature-flag; B4-migratie achter shadow/dual-write; WhatsApp als dunne, wegneembare adapter.
- **WhatsApp:** uitsluitend de officiële Cloud API; nooit onofficiële bibliotheken; nooit Bas' privénummer als botnummer; webhook-inhoud behandelen als untrusted input onder de bestaande guard.
- **Package-naam:** `span` niet hernoemen.

## Open items van Bas (buiten build-scope)

- nova-backup-key in password manager zetten.
- ElevenLabs-stem kiezen.
- AVG-docs naar privacy-officer; DPA's tekenen.
- Prepaid-SIM of VoIP-nummer voor LO's WhatsApp regelen.
- Meta business-verificatie doorlopen (documenten Lomans/eigen bedrijf aanleveren in de Meta Business Manager).

## Niet in scope (fase C, apart besluit)

- **Belkanaal = WhatsApp Calling** (vervangt het eerdere WebRTC-belkanaal-idee): echt bellen met LO via WhatsApp. Vereist messaging-tier ≥ 2000 (dus afgeronde business-verificatie) én B1's transport-agnostische spraakloop (server-side, Pipecat-achtig). Go/no-go aan het einde van fase B.
- HA-satellites (spraak door het huis), code-mode. Niet bouwen zonder expliciete go.
