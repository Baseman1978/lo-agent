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


def build_briefing(
    brain: BrainDB,
    o365: O365Client | None = None,
    asana: AsanaClient | None = None,
    owner: str = "Bas",
) -> dict[str, Any]:
    """Alles voor 'JARVIS, geef me mijn briefing' — en voor de HUD-panelen."""
    now = datetime.now(TZ)
    briefing: dict[str, Any] = {
        "greeting": f"{_greeting(now)}, {owner}.",
        "timestamp": now.isoformat(timespec="seconds"),
        "errors": {},
    }

    if o365 is not None:
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
