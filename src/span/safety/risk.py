"""Risico-tier per tool (F1.1).

Server-side hardcoded — niet door de agent of via instellingen te wijzigen.
low/med mogen (afhankelijk van autonomy) direct; high/crit horen via de
AgentInbox-goedkeuringspoort. De tier voedt ook de exfiltratie-check (F1.2),
de audit en de risico-weergave in HUD/Telegram.
"""

from __future__ import annotations

# Expliciet per tool. Niet-genoemde tools vallen terug op _default_tier().
TOOL_RISK: dict[str, str] = {
    # brein / lezen — laag
    "brain_search": "low", "brain_cypher": "low", "work_cypher": "low",
    "weather": "low", "jarvis_briefing": "low",
    "web_search": "low", "web_read": "low",   # lezen; output is untrusted -> quarantaine
    "o365_mail_inbox": "low", "o365_thread_summary": "low", "o365_calendar": "low",
    "o365_mail_search": "low", "o365_mail_folders": "low", "o365_calendar_search": "low",
    "o365_files_search": "low", "o365_file_read": "low", "o365_sharepoint_search": "low",
    "o365_teams_search": "low", "o365_people_search": "low",
    "o365_todo_list": "low", "asana_my_tasks": "low", "asana_search": "low",
    "asana_projects": "low", "inbox_open": "low", "fireflies_meetings": "low",
    "cron_list": "low", "triage_rules_get": "low",
    # eigen brein schrijven — laag (alleen-eigen, omkeerbaar)
    "remember": "low", "quest_upsert": "low", "triage_rules_set": "low",
    "plan_goal": "med",             # maakt een plan (Quest); voert niets uit
    "mcp_propose_server": "med",    # queue't zelf in de inbox; voegt niets direct toe
    "propose_share": "low",         # queue't deel-voorstel; share gebeurt pas na Bas' akkoord
    "mail_archive_folder": "med",   # leest mail -> eigen brein (omkeerbaar)
    # eigen taken / concepten — midden
    "o365_draft_reply": "med", "o365_todo_create": "med", "o365_todo_complete": "med",
    "asana_task_create": "med", "asana_task_complete": "med",
    "fireflies_sync": "med",
    # naar buiten / onomkeerbaar / voert namens Bas uit — hoog
    "o365_mail_send": "high", "o365_event_create": "high",
    # cron_create: med — plannen is op zich omkeerbaar; een execute-cron draait
    # later via een volwaardige agent-beurt mét eigen Agent Inbox-poort, dus de
    # gevoelige actie wordt dáár alsnog afgevangen. high zou ook onschuldige
    # herinner-crons blokkeren (kernfeature).
    "cron_create": "med",
    # inbox_approve: med — heeft de origin-vangrail (agent kan z'n eigen
    # gequeuede acties niet zelf goedkeuren) als primaire bescherming, en
    # bedient alleen reeds-gequeuede items; stembediening is een kernfeature.
    "inbox_approve": "med",
    "inbox_reject": "med",
    "cron_delete": "med",
}

VALID_TIERS = ("low", "med", "high", "crit")


def _default_tier(name: str) -> str:
    """Onbekende tool fail-closed: naar-buiten/onomkeerbaar -> high, andere
    schrijf-achtige -> med, rest -> med (niet low; een onbekende tool die
    namens Bas handelt verdient geen vrijbrief)."""
    n = name.lower()
    if any(w in n for w in ("send", "forward", "share", "publish", "post", "grant")):
        return "high"
    return "med"


# MCP-toolnamen met deze werkwoorden doen iets onomkeerbaars / naar buiten en
# horen via de goedkeuringspoort, ook al draaien ze op een vertrouwde server.
_MCP_WRITE_VERBS = ("send", "forward", "reply", "delete", "remove", "move",
                    "create", "update", "add", "post", "write", "flag",
                    "complete", "share")


def risk_for(name: str) -> str:
    # MCP-tools: de externe server doet zijn eigen autorisatie (bv. O365-scopes)
    # en Bas heeft hem bewust gekoppeld; Span quarantained de output. LEES-tools
    # = 'med' (mogen draaien, uit te vinken). SCHRIJF-tools (mail sturen/wissen,
    # bestanden wijzigen) = 'high' -> via de Agent Inbox-poort, nooit ongezien.
    if name.startswith("mcp__"):
        tail = name.split("__")[-1].lower()
        return "high" if any(v in tail for v in _MCP_WRITE_VERBS) else "med"
    return TOOL_RISK.get(name) or _default_tier(name)
