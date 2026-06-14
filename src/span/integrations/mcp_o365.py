"""MCP→briefing-adapter: vul de HUD-panelen (agenda + mail) via een gekoppelde
MCP-server met M365-tools, als de directe O365-koppeling er niet is.

De MCP-tools geven het ruwe Microsoft Graph-formaat terug (JSON met 'value').
We parsen dat naar dezelfde paneel-structuur die de directe O365Client levert,
zodat de HUD (jarvis.js) niets hoeft te weten van de bron.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any


def _find(mcp: Any, suffix: str) -> str | None:
    for n in mcp.tool_names():
        if n.endswith("__" + suffix):
            return n
    return None


def _value(mcp: Any, tool: str, args: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Geef (items, fout) terug. Een lege lijst zónder fout = écht leeg;
    een fout (bv. 'Rate limit overschreden') laat de HUD het verschil tonen
    tussen 'niets nieuws' en 'server beperkt — even geduld'."""
    res = mcp.call(tool, args)
    if isinstance(res, dict) and res.get("error"):
        return [], str(res["error"])
    try:
        data = json.loads(res.get("text", "") or "{}")
    except (json.JSONDecodeError, AttributeError):
        return [], "kon antwoord niet lezen"
    return (data.get("value", []) if isinstance(data, dict) else []), ""


def mcp_mail(mcp: Any, top: int = 8) -> tuple[list[dict[str, Any]], str]:
    tool = _find(mcp, "m365_mail_list")
    if not tool:
        return [], "geen mail-tool op de MCP-server"
    items, err = _value(mcp, tool, {"top": top})
    out = []
    for m in items[:top]:
        frm = ((m.get("from") or {}).get("emailAddress") or {})
        out.append({
            "graph_id": m.get("id"),
            "subject": m.get("subject") or "(zonder onderwerp)",
            "from": frm.get("name") or frm.get("address") or "",
            "preview": m.get("bodyPreview", ""),
            "unread": not m.get("isRead", True),
            "link": m.get("webLink"),
        })
    return out, err


def mcp_calendar(mcp: Any, now: Any) -> tuple[list[dict[str, Any]], str]:
    tool = _find(mcp, "m365_calendar_view")
    if not tool:
        return [], "geen agenda-tool op de MCP-server"
    # Graph calendarView verwacht ISO-datetime in startDateTime/endDateTime
    args = {"startDateTime": now.isoformat(),
            "endDateTime": (now + timedelta(days=1)).isoformat()}
    items, err = _value(mcp, tool, args)
    out = []
    for e in items:
        out.append({
            "subject": e.get("subject") or "(zonder titel)",
            "start": ((e.get("start") or {}).get("dateTime") or "")[:19],
            "end": ((e.get("end") or {}).get("dateTime") or "")[:19],
            "location": (e.get("location") or {}).get("displayName", ""),
            "all_day": e.get("isAllDay", False),
        })
    return out, err
