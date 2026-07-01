# Een integratie toevoegen aan LO

LO's integraties lopen via de **Integration Broker** (`src/span/integrations/broker/`).
Een integratie toevoegen = meestal **één `Connector` in het register**. De broker
levert automatisch de catalogus, het koppelen en het uitvoeren van acties — alles
onder LO's bestaande governance: `assess_tool` → **Agent Inbox** (goedkeuring bij
schrijven) → **egress-allowlist** → **audit**, per gebruiker geïsoleerd.

## De lagen

```
HUD (Integraties-tab) ─► /api/integrations/* ─► IntegrationBroker ─► Adapter ─► extern
                                                       │
                                         connectors.py (register)
```

- **Register** — `connectors.py`: declaratieve `Connector`/`Action` + `SEED`.
- **Broker** — `broker.py`: catalogus, `preview`, `run` (read = direct + audit;
  write = Agent Inbox → `run_approved` ná goedkeuring).
- **Adapters** — `adapters/`: `mock`, `native`, `mcp`, `nango`.

## De connector-spec

```python
Connector(
    id="slack",                 # uniek, kebab/snake
    name="Slack",
    provider="mcp",             # mock | native | mcp | nango
    category="chat",            # email|calendar|files|chat|crm|project|database|analytics|meetings|automation|dev|other
    auth="mcp_oauth",           # none|api_key|oauth2|mcp_oauth|graph
    capabilities=["read","write"],
    risk="medium",              # low|medium|high
    scopes=["chat:write"],
    docs_url="https://…",
    status="available",         # available|needs_config|beta|planned
    summary="korte omschrijving voor de catalogus",
    # provider-config:
    mcp_url="https://…/mcp",    # provider=mcp
    base_url="https://api…",    # provider=native (HTTP, later fase)
    nango_key="slack",          # provider=nango (provider-config-key in Nango)
    actions=[ Action(...) ],
)
```

```python
Action(
    id="post_message",
    name="Bericht posten",
    capability="write",         # read|write|sync|webhook|workflow
    approval="on_write",        # never | on_write | always
    risk="medium",
    input_schema={...},         # JSON Schema
    # binding (native):
    tool="o365_teams_search",   # hergebruik een bestaande LO-tool …
    method="GET", path="user/repos",  # … of een HTTP-call (native/nango)
)
```

**Approval-beleid** (`needs_approval`): `never` nooit, `always` altijd,
`on_write` bij `capability in {write, workflow}`. Onbekend → fail-closed (approval).

## Per adaptertype

### 1) `mcp` — externe MCP-server met OAuth-login (aanrader waar beschikbaar)
Zet alleen `provider="mcp"` + `mcp_url`. Koppelen = de knop **Koppelen (login)** in
de catalogus: LO registreert de server, doorloopt OAuth (DCR + PKCE) en de gebruiker
logt in. De acties komen dan van de MCP-server zelf.

> Let op — **redirect-URI-beleid**: sommige MCP-servers (bv. **Asana**) accepteren
> bij dynamische registratie alléén `localhost`-redirects (lokale clients). Een
> gehoste app als LO wordt dan geweigerd (`invalid_redirect_uri`, nette 422). Zulke
> providers koppel je via een eigen OAuth-app of API-sleutel (native), niet via MCP.
> **Notion** en **Fireflies** accepteren onze publieke redirect wél.

### 2) `native` — hergebruik een bestaande LO-tool
Geef de actie een `tool="..."`. De broker voert die uit via LO's normale
`dispatch` → dezelfde risico-poort/Agent Inbox/audit gelden. Zo wordt bestaande
functionaliteit (Graph/Teams/…) zonder nieuwe attack surface een catalogus-actie.
`is_connected` voor `auth="graph"` = de gebruiker is met Microsoft ingelogd.

Declaratieve HTTP-native (`base_url` + `method`/`path` met eigen OAuth-token) is
voorzien maar nog niet bedraad — gebruik voorlopig een tool-binding of `nango`.

### 3) `nango` — breedte via een self-hosted Nango-instance
De privacy-veilige breedte-optie: **Nango self-host** houdt tokens én data op je
eigen (EU-)infra (geen sub-processor). Zet `provider="nango"` + `nango_key`
(de provider-config-key in Nango) + acties met `method`/`path` (de externe
endpoint die Nango proxyt).

Activeren:
```bash
# in de .env (env_file injecteert dit in de container):
NANGO_HOST=http://nango:3003         # of https://nango.famspaan.nl
NANGO_SECRET_KEY=<nango secret>
```
Zonder deze twee is de nango-adapter uit (nette melding). LO→Nango is
vertrouwd-intern verkeer (eigen service) en gaat NIET door de egress-poort; de
externe call gebeurt server-side in Nango. Koppelen gebruikt **Nango Connect**
(sessie-token → Connect-UI); dat frontend-stuk is een vervolg.

### 4) `mock` — voor de catalogus zonder keys + tests
`provider="mock"`, geen credentials. Handig om de broker-lus (catalogus → run →
resultaat) te demonstreren en te testen.

## Stap voor stap

1. Voeg een `Connector` toe aan `SEED` in `connectors.py` (import valideert 'm hard).
2. Kies het adaptertype en vul de provider-config + `actions`.
3. Draai de tests: `PYTHONPATH=src pytest tests/test_broker.py -q`.
4. Deploy (static/back-end): `scp` de gewijzigde bestanden → `docker compose up -d --build span`.
5. Controleer in **Instellingen → Integraties** en/of via
   `GET /api/integrations/catalog`.

## Governance (geldt automatisch)

- **Schrijfacties** → Agent Inbox, per gebruiker geïsoleerd (`owner` + `approvable_by`).
- **Native** loopt via bestaande, al-gegatete tools.
- **Externe output** wordt als *data* omkaderd (geen opdracht) — prompt-injectie-vangnet.
- **Tokens** komen nooit in de prompt of de frontend.
