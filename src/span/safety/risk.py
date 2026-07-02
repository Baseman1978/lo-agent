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
    "o365_mail_attachments": "low", "o365_attachment_read": "low",
    "o365_archive_folder": "low", "o365_excel_sheets": "low", "o365_excel_read": "low",
    "o365_unanswered_sent": "low", "o365_enrich_archive": "low",
    "o365_mail_mark_read": "low", "o365_mail_flag": "low",
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
    "o365_mail_move": "med", "o365_mail_delete": "med",
    "o365_mail_forward_draft": "med", "o365_mail_reply_all_draft": "med",
    "o365_excel_write": "med", "o365_file_create": "med", "o365_event_respond": "med",
    "o365_doc_generate": "med",
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


# Read-only-ALLOWLIST voor MCP-tools: een tool telt alleen als 'lezen' als de naam
# duidelijk een lees-werkwoord bevat. Al het andere = schrijven -> goedkeuringspoort.
# Fail-closed: een onbekend/ambigu werkwoord (bv. merge/archive/revoke/purge/deploy)
# wordt NOOIT stilzwijgend als lezen behandeld. (audit C2/B1)
_MCP_READ_VERBS = ("search", "get", "list", "read", "find", "query", "fetch",
                   "lookup", "describe", "count", "view", "show", "summar",
                   "recent", "history", "retrieve", "preview")
# Sterke schrijf-indicatoren die, ook als er een lees-woord in de naam staat,
# altijd naar schrijven moeten kantelen (bv. 'get_and_delete').
_MCP_WRITE_VERBS = ("send", "forward", "reply", "delete", "remove", "move",
                    "create", "update", "add", "post", "write", "flag", "complete",
                    "share", "archive", "merge", "close", "cancel", "revoke",
                    "resolve", "assign", "decline", "restore", "rename", "duplicate",
                    "set", "trash", "purge", "empty", "deploy", "publish", "approve",
                    "disable", "enable", "block", "grant", "transfer", "execute",
                    "run", "trigger", "upload", "import", "invite", "reopen", "lock")


def mcp_capability(name: str) -> str:
    """'read' of 'write' voor een MCP-toolnaam (of z'n staart). Eén bron voor de
    risico-poort én de UI/skill-labels. Fail-closed: schrijf-verb of onbekend = write."""
    tail = name.split("__")[-1].lower()
    if any(w in tail for w in _MCP_WRITE_VERBS):
        return "write"
    if any(r in tail for r in _MCP_READ_VERBS):
        return "read"
    return "write"   # onbekend/ambigu -> behandel als schrijven (approval)


def risk_for(name: str) -> str:
    # MCP-tools: de externe server doet zijn eigen autorisatie en Bas heeft hem
    # bewust gekoppeld; Span quarantined de output. LEES-tools = 'med' (mogen
    # draaien, uit te vinken). Al het andere (schrijven/onbekend) = 'high' ->
    # via de Agent Inbox-poort, nooit ongezien.
    if name.startswith("mcp__"):
        return "med" if mcp_capability(name) == "read" else "high"
    return TOOL_RISK.get(name) or _default_tier(name)
