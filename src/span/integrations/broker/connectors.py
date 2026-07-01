"""Declaratief connector-register — de 'makkelijk toevoegen'-laag.

Een integratie = één `Connector` met een paar `Action`s. De broker leest dit
register; adapters (mock/native/mcp/nango) voeren de acties uit. Toevoegen van
een integratie is dus: een entry aan SEED toevoegen (en zo nodig de adapter
voor die provider uitbreiden).

Velden bewust plat + serialiseerbaar (asdict) zodat de HUD/API ze direct krijgt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# providers = welke adapter de connector afhandelt
PROVIDERS = ("mock", "native", "mcp", "nango")
# capabilities per actie
CAPABILITIES = ("read", "write", "sync", "webhook", "workflow")
# risico-niveaus (sturen approval + zichtbaarheid)
RISKS = ("low", "medium", "high")
# approval-beleid per actie
APPROVALS = ("never", "on_write", "always")
CATEGORIES = ("email", "calendar", "files", "chat", "crm", "project",
              "database", "analytics", "meetings", "automation", "dev", "other")


@dataclass
class Action:
    id: str
    name: str
    description: str = ""
    capability: str = "read"
    approval: str = "on_write"        # never | on_write | always
    risk: str = "low"
    input_schema: dict[str, Any] = field(default_factory=dict)
    # binding voor de native-adapter: hergebruik een bestaande LO-tool …
    tool: str = ""
    # … of een declaratieve HTTP-call (native-adapter, later fase)
    method: str = ""
    path: str = ""


@dataclass
class Connector:
    id: str
    name: str
    provider: str                     # mock | native | mcp | nango
    category: str = "other"
    auth: str = "none"                # none | api_key | oauth2 | mcp_oauth | graph
    capabilities: list[str] = field(default_factory=list)
    risk: str = "low"
    scopes: list[str] = field(default_factory=list)
    docs_url: str = ""
    # provider-config
    base_url: str = ""                # native (HTTP)
    mcp_url: str = ""                 # mcp
    nango_key: str = ""               # nango: provider-config-key van de integratie
    actions: list[Action] = field(default_factory=list)
    # status in de catalogus
    status: str = "available"         # available | needs_config | beta | planned
    summary: str = ""


def _validate(c: Connector) -> None:
    if c.provider not in PROVIDERS:
        raise ValueError(f"connector {c.id}: onbekende provider {c.provider!r}")
    if c.category not in CATEGORIES:
        raise ValueError(f"connector {c.id}: onbekende categorie {c.category!r}")
    for a in c.actions:
        if a.capability not in CAPABILITIES:
            raise ValueError(f"{c.id}.{a.id}: capability {a.capability!r}")
        if a.approval not in APPROVALS:
            raise ValueError(f"{c.id}.{a.id}: approval {a.approval!r}")
        if a.risk not in RISKS:
            raise ValueError(f"{c.id}.{a.id}: risk {a.risk!r}")


# ---------------------------------------------------------------------------
# SEED — het register. Eén entry per integratie. DIT is wat je uitbreidt.
# ---------------------------------------------------------------------------
SEED: list[Connector] = [
    # Demo/mock: geen externe credentials nodig -> de catalogus + de
    # approval->uitvoer-lus zijn zo lokaal en in tests te draaien.
    Connector(
        id="demo", name="Demo (mock)", provider="mock", category="other",
        auth="none", capabilities=["read", "write"], risk="low",
        status="available", summary="Test-connector zonder externe koppeling.",
        actions=[
            Action(id="echo", name="Echo", capability="read", approval="never",
                   risk="low", description="Geeft de invoer terug.",
                   input_schema={"type": "object",
                                 "properties": {"text": {"type": "string"}}}),
            Action(id="create_note", name="Notitie maken", capability="write",
                   approval="on_write", risk="low",
                   description="Doet alsof het een notitie aanmaakt (mock).",
                   input_schema={"type": "object",
                                 "properties": {"title": {"type": "string"}}}),
        ],
    ),
    # Microsoft Teams via het bestaande Graph-token (native-adapter, hergebruik
    # bestaande o365-tools). Zoeken werkt met de huidige scopes; posten volgt.
    Connector(
        id="ms_teams", name="Microsoft Teams", provider="native", category="chat",
        auth="graph", capabilities=["read", "write"], risk="medium",
        status="available", docs_url="https://learn.microsoft.com/graph/",
        summary="Teams-chats doorzoeken (bestaand Graph-token).",
        actions=[
            Action(id="search", name="Chats doorzoeken", capability="read",
                   approval="never", risk="low", tool="o365_teams_search",
                   input_schema={"type": "object",
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}),
        ],
    ),
    # Notion via z'n gehoste MCP-server (mcp-adapter + LO's OAuth-login).
    Connector(
        id="notion", name="Notion", provider="mcp", category="database",
        auth="mcp_oauth", capabilities=["read", "write"], risk="medium",
        mcp_url="https://mcp.notion.com/mcp", status="needs_config",
        docs_url="https://developers.notion.com/", summary="Notion via MCP (login).",
    ),
    # Google Calendar + Gmail: nieuw OAuth-domein (Google) -> eigen OAuth-app nodig.
    Connector(
        id="google_calendar", name="Google Agenda", provider="native",
        category="calendar", auth="oauth2", capabilities=["read", "write"],
        risk="medium", status="needs_config",
        scopes=["https://www.googleapis.com/auth/calendar"],
        docs_url="https://developers.google.com/calendar",
        summary="Vereist een Google-OAuth-app (Fase 4).",
    ),
    Connector(
        id="gmail", name="Gmail", provider="native", category="email",
        auth="oauth2", capabilities=["read", "write"], risk="high",
        status="needs_config",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        docs_url="https://developers.google.com/gmail/api",
        summary="Vereist een Google-OAuth-app (Fase 4).",
    ),
    # Power BI via het bestaande Microsoft-token (extra Power BI-scopes nodig).
    Connector(
        id="power_bi", name="Power BI", provider="native", category="analytics",
        auth="graph", capabilities=["read"], risk="low", status="available",
        scopes=["https://analysis.windows.net/powerbi/api/.default"],
        docs_url="https://learn.microsoft.com/rest/api/power-bi/",
        summary="Rapporten, dashboards en datasets lezen (bestaand Microsoft-login).",
        actions=[
            Action(id="reports", name="Rapporten", capability="read", approval="never",
                   risk="low", tool="o365_powerbi_reports",
                   description="Power BI-rapporten opsommen.",
                   input_schema={"type": "object",
                                 "properties": {"top": {"type": "integer"}}}),
            Action(id="dashboards", name="Dashboards", capability="read", approval="never",
                   risk="low", tool="o365_powerbi_dashboards",
                   description="Power BI-dashboards opsommen.",
                   input_schema={"type": "object",
                                 "properties": {"top": {"type": "integer"}}}),
            Action(id="datasets", name="Datasets", capability="read", approval="never",
                   risk="low", tool="o365_powerbi_datasets",
                   description="Power BI-datasets opsommen.",
                   input_schema={"type": "object",
                                 "properties": {"top": {"type": "integer"}}}),
            Action(id="tables", name="Tabellen/kolommen", capability="read", approval="never",
                   risk="low", tool="o365_powerbi_tables",
                   description="Schema van een dataset (tabellen + kolommen).",
                   input_schema={"type": "object",
                                 "properties": {"dataset_id": {"type": "string"}},
                                 "required": ["dataset_id"]}),
            Action(id="query", name="DAX-query", capability="read", approval="never",
                   risk="low", tool="o365_powerbi_query",
                   description="Echte cijfers uit een dataset via DAX (alleen-lezen).",
                   input_schema={"type": "object",
                                 "properties": {"dataset_id": {"type": "string"},
                                                "dax": {"type": "string"},
                                                "top": {"type": "integer"}},
                                 "required": ["dataset_id", "dax"]}),
        ],
    ),
    # Fireflies: login-only via z'n MCP-server (DCR met publieke redirect = OK).
    Connector(
        id="fireflies", name="Fireflies", provider="mcp", category="meetings",
        auth="mcp_oauth", capabilities=["read"], risk="medium",
        mcp_url="https://api.fireflies.ai/mcp", status="available",
        docs_url="https://docs.fireflies.ai/", summary="Meeting-notulen via MCP (login)."),
    # Asana: z'n MCP-server accepteert bij DCR alléén localhost-redirects (lokale
    # clients) -> niet koppelbaar aan een gehoste LO via MCP-login. Route =
    # eigen Asana-OAuth-app of een API-sleutel (Fase 4).
    Connector(
        id="asana", name="Asana", provider="native", category="project",
        auth="oauth2", capabilities=["read", "write"], risk="medium",
        status="needs_config", docs_url="https://developers.asana.com/",
        summary="MCP staat alleen lokale clients toe → eigen OAuth-app of API-sleutel nodig."),
    # Voorbeeld van een nango-connector: breedte via een self-host Nango-instance
    # (OAuth-lifecycle + proxy op eigen EU-infra). Werkt zodra NANGO_HOST +
    # NANGO_SECRET_KEY gezet zijn en de 'github'-integratie in Nango staat.
    Connector(
        id="github", name="GitHub", provider="nango", category="dev",
        auth="oauth2", capabilities=["read", "write"], risk="medium",
        nango_key="github", status="needs_config",
        docs_url="https://docs.nango.dev/integrations/all/github",
        summary="Via Nango self-host (OAuth). Vereist een draaiende Nango-instance.",
        actions=[
            Action(id="list_repos", name="Repos opsommen", capability="read",
                   approval="never", risk="low", method="GET", path="user/repos",
                   description="Repositories van de gekoppelde gebruiker.",
                   input_schema={"type": "object"}),
        ]),
]

# valideer de seed bij import (faalt hard bij een fout in een entry)
for _c in SEED:
    _validate(_c)

_BY_ID: dict[str, Connector] = {c.id: c for c in SEED}


def connector_dict(c: Connector) -> dict[str, Any]:
    return asdict(c)


def list_connectors(category: str | None = None, capability: str | None = None,
                    query: str | None = None) -> list[Connector]:
    out = list(SEED)
    if category:
        out = [c for c in out if c.category == category]
    if capability:
        out = [c for c in out if capability in c.capabilities]
    if query:
        q = query.lower().strip()
        out = [c for c in out if q in c.name.lower() or q in c.id.lower()
               or q in (c.summary or "").lower()]
    return out


def get_connector(cid: str) -> Connector | None:
    return _BY_ID.get(cid)


def get_action(cid: str, aid: str) -> tuple[Connector, Action] | None:
    c = _BY_ID.get(cid)
    if c is None:
        return None
    a = next((x for x in c.actions if x.id == aid), None)
    return (c, a) if a is not None else None


def needs_approval(action: Action) -> bool:
    """Beleid: 'always' altijd; 'on_write' bij schrijf-achtige capability;
    'never' nooit. Onbekend -> fail-closed (approval)."""
    if action.approval == "never":
        return False
    if action.approval == "always":
        return True
    if action.approval == "on_write":
        return action.capability in ("write", "workflow")
    return True
