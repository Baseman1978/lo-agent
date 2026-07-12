# LO high-end roadmap — fase A + B (design)

**Datum:** 2026-07-12
**Status:** ontwerp (goedgekeurd na brainstorm + eng-review + CEO-review; wacht op Bas' spec-review)
**Scope-besluit:** bewijs-gepoort fase B — fase A wordt hard gecommit en gebouwd; fase B wordt volledig ontworpen maar per pijler gepoort op telemetrie/eval-bewijs bij de A→B-grens.

## Doel

LO doorontwikkelen tot een high-end agent op drie assen die Bas expliciet heeft benoemd:

1. **Vloeiend spraakgesprek** — praten met LO voelt als een telefoongesprek, niet als een walkie-talkie.
2. **1001 taken uitvoeren** — taken die minuten tot dagen lopen, herstart overleven, en nooit stilletjes falen.
3. **Historisch goed geheugen** — LO onthoudt wat er wanneer gezegd is en kan er tijd-bewust op terugvallen.

## Kernprincipe: ruggengraat vóór structuur

De centrale ontwerpbeslissing: **elk fase-A-blok is de omkeerbare "ruggengraat"-versie van een structureel fase-B-blok.** Fase A levert ~80% van het voelbare resultaat tegen ~20% van het risico met bijna uitsluitend omkeerbare wijzigingen (config-schakelaars, telemetrie, retries, indexen). Fase B bevat de structurele sprongen die innovatie-tokens kosten (nieuwe services, schema-migraties, nieuwe client). Elke fase-B-sprong wordt pas definitief gemaakt als de telemetrie bewijst dat de ruggengraat niet volstond.

| Pijler | Fase A (ruggengraat, omkeerbaar) | Fase B (structureel, gepoort) |
|---|---|---|
| Spraak-uit | A2 — ElevenLabs-WS *goed-genoeg* | B2 — ElevenLabs productie-hardening |
| Spraak-in | A1-switch — STT → GPU-Whisper (batch) | B1 — streaming-STT + Smart Turn v3 |
| Taken | A3 — vangnet (retries / eerlijke uitkomst / Telegram-push) | B3 — DBOS durable engine (+Postgres) |
| Geheugen | A4 — onderhoud (indexen / health / degraded-mode) | B4 — bi-temporeel + tijd-recall + TEI-embeddings |
| Bereik | A5 — mobiel-spraakfixes + Telegram-voice | B5 — PWA |

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

**Poort-regel:** een fase-B-blok wordt alleen gebouwd als de fase-A-telemetrie aantoont dat de bijbehorende ruggengraat de bottleneck was. Voorbeeld: B1 (streaming-STT) alleen als `stt_ms` na de A1-switch nog steeds de dominante term in `turn_total_ms` is; niet als het model (`llm_ms`) de traagheid veroorzaakt.

## Fase A — de ruggengraat (hard gecommit)

Sequentieel gebouwd, elk op een eigen branch. Geen parallel werk op gedeelde core-bestanden (anders vervuilt A1's meetlat).

### A1 — telemetrie + twee schakelaars
- Gesegmenteerde beurt-telemetrie (zie §1).
- Schakelaar 1: streaming-TTS aan op de bestaande pijplijn (feature-flag).
- Schakelaar 2: `SPAN_STT_URL` naar de ongebruikte GPU-Whisper-container op z390 (:9000).
- **Meetpunt:** baseline-latency vastleggen vóór, en na elke schakelaar.

### A2 — ElevenLabs-WS streaming (goed-genoeg)
- ElevenLabs WebSocket-streaming voor spraak-uit, versie "goed-genoeg" (niet productie-gehard).
- Micro-bevestigingen (LO geeft hoorbaar aan dat hij bezig is bij lange tool-calls).
- Prewarm van de TTS-verbinding om cold-start te vermijden.
- **Poort:** pas aanzetten nadat A1 bevestigt dat TTS-latency de bottleneck is. ElevenLabs-key is tijdelijk; EU-residency is Enterprise-only → AVG-besluit hangt (buiten scope van dit blok, zie §5).

### A3 — taak-vangnet
- Retries met backoff op falende tool-calls.
- **Eerlijke uitkomsten:** een taak die faalt meldt dat expliciet (geen stille fallback, geen verzonnen succes).
- Telegram-push wanneer een langlopende taak klaar is of definitief faalt.
- **Meetpunt:** hoeveel taken hadden retries nodig; hoeveel liepen > enkele minuten; hoeveel moesten herstart overleven (bepaalt of B3 nodig is).

### A4 — geheugen-onderhoud
- Ontbrekende/suboptimale HNSW-indexen herstellen; index-gezondheid controleren.
- Quest-limiet (begrens geheugen-groei per categorie).
- Health-check op de Neo4j-brain + degraded-mode (agent blijft werken als de brain traag/onbereikbaar is).
- **Meetpunt:** recall-kwaliteit + brain-latency (bepaalt of B4 nodig is).

### A5 — bereik + duurzaamheid
- Mobiele spraak-fixes (bekende iOS/Safari mic-quirks).
- Telegram-voice (spraakberichten in/uit via de bestaande Telegram-bridge).
- Backup-drill: verifieer dat de nova-backup-restore daadwerkelijk werkt (restic + rsync-laag).

## Fase B — de structurele sprongen (volledig ontworpen, gepoort)

Elk blok heeft een expliciete **poort-vraag** die bij de A→B-grens met telemetrie/eval-bewijs beantwoord wordt. "Nee" = blok schrappen of uitstellen.

### B1 — volledige spraak-in-pijplijn
- Streaming-STT (partial transcripts tijdens spreken) + Smart Turn v3 voor natuurlijke beurtwisseling.
- **Poort:** was `stt_ms` na de A1-switch nog de dominante latency-term? Zo niet → schrappen.

### B2 — ElevenLabs productie
- Hardening van A2: reconnect-logica, foutafhandeling, stem-selectie definitief, kosten-monitoring.
- **Poort:** heeft A2 zich bewezen als de juiste TTS én is het AVG-besluit rond (EU-residency)? Zo niet → uitstellen.

### B3 — DBOS durable task engine
- Volwaardige durable-execution-engine (DBOS + Postgres): taken overleven herstart, exact-once-semantiek, workflow-observability.
- **Poort:** faalde A3 op taken die uren/dagen lopen of herstart moeten overleven? Zo niet → schrappen (A3's vangnet volstaat; heel service+Postgres-oppervlak bespaard). *Dit is het duurste token en het meest waarschijnlijk overbodig.*

### B4 — bi-temporeel geheugen
- Bi-temporeel schema (event-tijd + ingest-tijd), tijd-bewuste recall ("wat wist je op datum X"), TEI-embeddings productie.
- **Poort:** liet A4 zien dat tijd-blindheid echte recall-fouten veroorzaakte? Zo niet → uitstellen.
- **Risico-eis:** hoogste migratie-risico van het hele plan (schema-retrofit + her-indexeren op de live brain). MOET laatste blok zijn, achter dual-write/shadow-migratie, nooit big-bang.

### B5 — PWA
- Progressive Web App zodat LO op mobiel als geïnstalleerde app draait (offline-shell, push, home-screen).
- **Poort:** bleken de A5-mobielfixes onvoldoende voor dagelijks mobiel gebruik? Zo niet → uitstellen.

### B6 — nachtelijke eval + observability
- Nachtelijke eval-suite (regressie op spraak-latency, taak-succes, recall-kwaliteit) + observability-dashboard bovenop de A1-telemetrie.
- **Geen poort:** dit is de meetlat-verankering die de andere B-poorten voedt; bouwen zodra fase B start.

## Afhankelijkheden (§4)

- A1 → alles (meetlat eerst).
- A2 poort-afhankelijk van A1-telemetrie.
- B1/B2/B3/B4/B5 elk gepoort op hun fase-A-tegenhanger.
- B4 laatste (migratie-risico) en na B6 (eval vangt regressie in de migratie).
- Sequentiële build op eigen branches; gedeelde core-bestanden = geen parallel.

## Veiligheidskleppen (§5)

- **CI-gate:** merge alleen op groene CI (zowel push- als PR-run). Nooit mergen op rood (les uit PR #110).
- **Geheimen:** nooit printen (alleen presence/length-checks); `.env` chmod 600; ElevenLabs- en Tavily-keys buiten git.
- **AVG:** ElevenLabs EU-residency (Enterprise) + DPA's zijn Bas' eigen open items; B2 is geblokkeerd tot dat besluit rond is.
- **Degraded-mode overal:** brain onbereikbaar, TTS down, tool-fout → LO blijft functioneel en eerlijk over wat niet lukte.
- **Omkeerbaarheid:** elke fase-A-wijziging achter een feature-flag; B4-migratie achter shadow/dual-write.
- **Package-naam:** `span` niet hernoemen.

## Open items van Bas (buiten build-scope)

- nova-backup-key in password manager zetten.
- ElevenLabs-stem kiezen.
- AVG-docs naar privacy-officer; DPA's tekenen.

## Niet in scope (fase C, apart besluit)

Belkanaal (echt telefoonnummer), HA-satellites (spraak door het huis), code-mode. Niet bouwen zonder expliciete go.
