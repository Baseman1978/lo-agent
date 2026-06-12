# Span — een AI die zichzelf onthoudt

AI-kennispartner van **Bas Spaan**, gebouwd naar het graph-as-brain principe:
een Neo4j knowledge graph is het brein, ORQ.AI de LLM-gateway, en een
orchestrator spreekt de juiste laag op het juiste moment aan.

> *"Treat this graph as my brain, my memory, my intelligence."*

De naam: vier letters uit **Spaan**. *Span* = verbinden, overspannen — bruggen
tussen sessies. En een *span* is een duo dat samenwerkt.

## Architectuur

| Laag | Invulling |
|------|-----------|
| Lange-termijn extern geheugen | dit repo (code, protocollen in seed) |
| Formeel geheugen | Neo4j: Insight, Mistake, Idea, Quest, Skill, Protocol |
| Informeel geheugen | Neo4j: MemoryFragments (continuous recording) |
| Reasoning + taal | ORQ.AI router → Claude Sonnet 4.5 (EU) |
| Licht model | Claude Haiku 4.5 — recording/JSON-taken |
| Embeddings | text-embedding-3-large (1024 dims) via ORQ |
| Orchestrator | `src/span/orchestrator/` — RAG, tool-loop, recording |
| Zelflerend | `src/span/evaluation/reflect.py` — de cirkel rond |
| Productiedata | optionele tweede Neo4j, **strikt alleen-lezen** |

### De cirkel

1. **Ervaring → MemoryFragment** — continuous recording tijdens elk gesprek
2. **Evaluatie → formele knopen** — bij `/end`: Insight / Mistake / Idea / Quest
3. **Bij herhaling → Skill**, bij schema-gat → uitbreidingsvoorstel
4. **Volgende sessie haalt op via bootstrap.** Cirkel rond.

## Setup

### 1. Neo4j Desktop

1. Installeer [Neo4j Desktop](https://neo4j.com/download/), maak een DBMS aan
   (versie 5.x), kies een wachtwoord en start hem.
2. Meer hoeft niet — `span init` maakt de database `span-brain` zelf aan.

### 2. ORQ.AI

API-key aanmaken in het [ORQ dashboard](https://my.orq.ai) (workspace → API keys).

### 3. Installeren

```powershell
cd "C:\DEV\AI agent"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
# vul in .env: ORQ_API_KEY en NEO4J_PASSWORD
```

### 4. Initialiseren en praten

```powershell
span init      # database, schema, identity, kernprotocollen
span chat      # gesprek; /end sluit af mét evaluatie
span status    # staat van het brein
span memory "ssh agent fix"   # semantisch zoeken in het geheugen
span reflect session-...      # evaluatie alsnog draaien na /quit
```

## JARVIS-modus: O365 en Asana (optioneel)

Span wordt een volwaardige JARVIS zodra je integraties koppelt. Voeg toe aan `.env`:

```ini
# Asana
ASANA_TOKEN=<personal access token>
ASANA_WORKSPACE=             # leeg = eerste workspace van je account

# Microsoft 365 — werkt out-of-the-box (device code flow via Microsofts
# publieke "Graph Command Line Tools"-client). Alleen nodig als je een
# eigen app-registratie wilt gebruiken of je tenant de publieke blokkeert:
# MS_CLIENT_ID=<eigen client id>
# MS_TENANT_ID=common
```

**O365 inloggen** (eenmalig): knop "verbind Microsoft 365" in de web-UI of
`span o365-login` → link + code → inloggen. Token-cache in
`~/.span/msal_cache.json` (in Docker: volume `span-msal`).

**Asana:** [developer console](https://app.asana.com/0/developer-console) →
Personal access token → in `.env`.

De agent krijgt dan tools voor inbox, mail versturen (na bevestiging), agenda
lezen/afspraken maken, Asana-taken (lijst/aanmaken/afvinken/zoeken) en
`jarvis_briefing` — zeg "geef me mijn briefing" of "wat staat er vandaag".

**Web-UI (`span serve`)**: JARVIS HUD met boot sequence, arc reactor, klok,
live panelen (agenda, taken, inbox, quests, brein), spraak in/uit en wake word
— zet **◉ WAKE** aan en zeg *"Jarvis, …"*.

## Productiedata koppelen (optioneel)

Vul `WORK_NEO4J_URI` (+ user/password/db) in `.env`. De agent krijgt dan de
tool `work_cypher` — alleen-lezen, dubbel geborgd (cypher-guard + READ access
mode). Schrijven op productie kan niet, by design.

## Tests

```powershell
pytest
```

## Identiteit hernoemen

De naam is data, geen code-constante in de graph:

```cypher
MATCH (i:Identity {name: 'Span'}) SET i.name = 'NieuweNaam'
```

(plus `AGENT_NAME` in `src/span/__init__.py` en de seeds in `src/span/db/schema.py`).
