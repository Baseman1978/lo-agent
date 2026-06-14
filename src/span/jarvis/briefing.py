"""Proactieve briefing — agenda, mail, taken en quests in één overzicht.

Elke bron is optioneel en faalt zacht: een briefing met drie van de vier
panelen is beter dan geen briefing. Fouten komen als string mee zodat de
HUD (en de agent) kunnen melden wát er ontbreekt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from span.clock import TZ
from span.db.brain import BrainDB
from span.integrations.asana import AsanaClient
from span.integrations.o365 import O365Client


def _greeting(now: datetime) -> str:
    if now.hour < 6:
        return "Goedenacht"
    if now.hour < 12:
        return "Goedemorgen"
    if now.hour < 18:
        return "Goedemiddag"
    return "Goedenavond"


def _safe(fn, fallback: Any = None) -> tuple[Any, str | None]:
    try:
        return fn(), None
    except Exception as exc:
        return fallback, f"{type(exc).__name__}: {exc}"


# laatst-bekende-goede MCP-data, zodat een tijdelijke hapering (rate limit,
# netwerk) het paneel niet leegklapt — we blijven het vorige tonen
_LAST_GOOD: dict[str, list[Any]] = {}


def _cache_or_fresh(key: str, items: list[Any], err: str) -> list[Any]:
    """Bij een fout: val terug op het laatst bekende resultaat. Bij succes met
    inhoud: ververs de cache. Een echt-lege succesvolle ophaal wist niets, maar
    overschrijft ook niet (zodat 'leeg door fout' niet als 'leeg' blijft hangen
    zodra de server weer praat — een lege succes-ophaal laat de cache staan)."""
    if err:
        return _LAST_GOOD.get(key, items)
    if items:
        _LAST_GOOD[key] = items
    return items


def _panel_status(err: str | None) -> dict[str, str] | None:
    """Vertaal een MCP-fout naar een korte status voor de HUD."""
    if not err:
        return None
    low = err.lower()
    if "rate limit" in low or "429" in low or "too many" in low:
        return {"kind": "rate_limited",
                "message": "MCP-server beperkt het verkeer even — laatste stand getoond, ververst zo vanzelf."}
    if "401" in low or "token" in low or "unauthorized" in low:
        return {"kind": "auth",
                "message": "MCP-aanmelding verlopen — verbind de server opnieuw in instellingen."}
    return {"kind": "error", "message": "MCP-bron tijdelijk niet bereikbaar — laatste stand getoond."}


def build_briefing(
    brain: BrainDB,
    o365: O365Client | None = None,
    asana: AsanaClient | None = None,
    owner: str = "Bas",
    mcp: Any = None,
) -> dict[str, Any]:
    """Alles voor 'JARVIS, geef me mijn briefing' — en voor de HUD-panelen."""
    now = datetime.now(TZ)
    briefing: dict[str, Any] = {
        "greeting": f"{_greeting(now)}, {owner}.",
        "timestamp": now.isoformat(timespec="seconds"),
        "errors": {},
    }

    o365_ok = False
    if o365 is not None:
        o365_ok = _safe(lambda: o365.is_authenticated(), False)[0]
    if o365 is not None and o365_ok:
        agenda, err = _safe(lambda: o365.calendar(days=1), [])
        briefing["calendar"] = agenda
        if err:
            briefing["errors"]["calendar"] = err
        briefing["conflicts"] = _overlaps(agenda or [])
        mail, err = _safe(lambda: o365.inbox(top=8), [])
        briefing["mail"] = mail
        briefing["unread_mail"] = [m for m in mail if m.get("unread")]
        if err:
            briefing["errors"]["mail"] = err
        todo, err = _safe(lambda: o365.todo_tasks(top=10), [])
        briefing["todo"] = todo
        if err:
            briefing["errors"]["todo"] = err
    elif mcp is not None:
        # directe O365 niet ingelogd -> agenda + mail via de MCP-server.
        # De MCP-server kan tijdelijk rate-limiten; dan tonen we het laatst
        # bekende resultaat + een statusvlag i.p.v. een misleidend lege inbox.
        from span.integrations.mcp_o365 import mcp_calendar, mcp_mail
        (agenda, cal_err) = _safe(lambda: mcp_calendar(mcp, now), ([], ""))[0]
        (mail, mail_err) = _safe(lambda: mcp_mail(mcp, 8), ([], ""))[0]
        agenda = _cache_or_fresh("calendar", agenda, cal_err)
        mail = _cache_or_fresh("mail", mail, mail_err)
        briefing["calendar"] = agenda
        briefing["conflicts"] = _overlaps(agenda or [])
        briefing["mail"] = mail
        briefing["unread_mail"] = [m for m in mail if m.get("unread")]
        briefing["source"] = "mcp"
        status = _panel_status(mail_err) or _panel_status(cal_err)
        if status:
            briefing["mcp_status"] = status

    if asana is not None:
        tasks, err = _safe(lambda: asana.my_tasks(top=10), [])
        briefing["asana"] = tasks
        if err:
            briefing["errors"]["asana"] = err

    quests, err = _safe(
        lambda: brain.run(
            "MATCH (q:Quest) WHERE q.status IN ['open','active'] "
            "RETURN q.id AS id, q.title AS title, q.status AS status, "
            "       coalesce(q.updated, q.created, datetime()) < datetime() - duration('P7D') AS stale "
            "ORDER BY q.status, q.updated DESC LIMIT 10"
        ),
        [],
    )
    briefing["quests"] = quests
    if err:
        briefing["errors"]["quests"] = err

    from span.jarvis.crons import list_crons
    crons, err = _safe(lambda: list_crons(brain), [])
    briefing["crons"] = crons
    if err:
        briefing["errors"]["crons"] = err

    return briefing


def _overlaps(events: list[dict[str, Any]]) -> list[str]:
    """Overlappende afspraken — benoemd in de dagstart."""
    conflicts = []
    timed = [e for e in events if not e.get("all_day") and e.get("start") and e.get("end")]
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if a["start"] < b["end"] and b["start"] < a["end"]:
                conflicts.append(f"{a['subject']} overlapt met {b['subject']}")
    return conflicts[:5]
