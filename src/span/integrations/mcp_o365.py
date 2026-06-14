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


def _value(mcp: Any, tool: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    res = mcp.call(tool, args)
    try:
        data = json.loads(res.get("text", "") or "{}")
    except (json.JSONDecodeError, AttributeError):
        return []
    return data.get("value", []) if isinstance(data, dict) else []


def mcp_mail(mcp: Any, top: int = 8) -> list[dict[str, Any]]:
    tool = _find(mcp, "m365_mail_list")
    if not tool:
        return []
    out = []
    for m in _value(mcp, tool, {"top": top})[:top]:
        frm = ((m.get("from") or {}).get("emailAddress") or {})
        out.append({
            "graph_id": m.get("id"),
            "subject": m.get("subject") or "(zonder onderwerp)",
            "from": frm.get("name") or frm.get("address") or "",
            "preview": m.get("bodyPreview", ""),
            "unread": not m.get("isRead", True),
            "link": m.get("webLink"),
        })
    return out


def mcp_calendar(mcp: Any, now: Any) -> list[dict[str, Any]]:
    tool = _find(mcp, "m365_calendar_view")
    if not tool:
        return []
    # Graph calendarView verwacht ISO-datetime in startDateTime/endDateTime
    args = {"startDateTime": now.isoformat(),
            "endDateTime": (now + timedelta(days=1)).isoformat()}
    out = []
    for e in _value(mcp, tool, args):
        out.append({
            "subject": e.get("subject") or "(zonder titel)",
            "start": ((e.get("start") or {}).get("dateTime") or "")[:19],
            "end": ((e.get("end") or {}).get("dateTime") or "")[:19],
            "location": (e.get("location") or {}).get("displayName", ""),
            "all_day": e.get("isAllDay", False),
        })
    return out
