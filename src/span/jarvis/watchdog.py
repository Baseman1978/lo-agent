# src/span/jarvis/watchdog.py
"""A3 — cron-toets: bewaakt dat de geplande dagtaken (dagafsluiting,
consolidatie, weekreview) daadwerkelijk liepen.

De scheduler stempelt c.last_<key> op de runtime-Config-node pas NA succes
(daily.py _mark_run); deze watchdog toetst achteraf of die stempel er voor de
laatst verplichte dag staat. Een gat (server een dag plat, scheduler-coroutine
dood) = één melding via Agent Inbox + Telegram, met een gemeld-stempel
(c.watchdog_<key>) zodat dezelfde misser nooit spamt. Vandaag telt bewust niet
mee: de run van vandaag kan nog komen. Best-effort, achter SPAN_CRON_WATCHDOG
(default aan)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from span.jarvis.daily import now_local

# key -> mensvriendelijk label; keys matchen c.last_<key> uit daily.py
WATCHED = {
    "evening": "dagafsluiting (dagelijks 17:15)",
    "consolidate": "consolidatie (dagelijks 03:30)",
    "weekreview": "weekreview (vrijdag 16:30)",
}


def watchdog_enabled() -> bool:
    """Feature-flag SPAN_CRON_WATCHDOG (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_CRON_WATCHDOG", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def expected_date(key: str, now: datetime) -> str:
    """Meest recente dag vóór vandaag waarop de taak af had moeten zijn."""
    d = now.date() - timedelta(days=1)
    if key == "weekreview":
        while d.weekday() != 4:  # terug naar de laatste vrijdag
            d -= timedelta(days=1)
    return d.isoformat()


def check_missed_runs(brain: Any, now: datetime) -> list[dict[str, str]]:
    """Pure toets: welke bewaakte taken misten hun laatst verplichte run?
    Datum-strings (YYYY-MM-DD) vergelijken lexicografisch = chronologisch.
    Een lege last ('nooit gelopen') telt ook als misser — een scheduler die
    nooit één keer slaagde mag niet onzichtbaar blijven."""
    missed: list[dict[str, str]] = []
    for key, label in WATCHED.items():
        expected = expected_date(key, now)
        rows = brain.run(
            f"MATCH (c:Config {{id:'runtime'}}) "
            f"RETURN c.last_{key} AS last, c.watchdog_{key} AS reported"
        )
        row = rows[0] if rows else {}
        last = row.get("last") or ""
        reported = row.get("reported") or ""
        if last >= expected or reported >= expected:
            continue  # gelopen, of deze misser is al gemeld
        missed.append({"key": key, "label": label,
                       "expected": expected, "last": last})
    return missed


def _mark_reported(brain: Any, key: str, expected: str) -> None:
    brain.run(
        f"MERGE (c:Config {{id:'runtime'}}) SET c.watchdog_{key} = $d",
        d=expected,
    )


def watchdog_tick(state: dict[str, Any]) -> int:
    """Meld gemiste runs (inbox + Telegram, best-effort) en stempel ze als
    gemeld. Geeft het aantal meldingen terug; mag een tick nooit breken."""
    if not watchdog_enabled():
        return 0
    brain = state["brain"]
    try:
        missed = check_missed_runs(brain, now_local())
    except Exception as exc:
        print(f"[watchdog] toets mislukt: {type(exc).__name__}: {exc}", flush=True)
        return 0
    for m in missed:
        titel = f"Geplande taak niet gelopen: {m['label']}"
        detail = (f"Verwacht op {m['expected']}; laatste geslaagde run: "
                  f"{m['last'] or 'nooit'}. Check de serverlog van die dag.")
        inbox = state.get("inbox")
        if inbox is not None:
            inbox.add(kind="notify", title=titel, detail=detail, urgency="high")
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            try:
                from span.jarvis.daily import send_respecting_quiet
                send_respecting_quiet(tg, f"⚠️ {titel}\n{detail}", brain)
            except Exception:
                print(f"[watchdog] telegram-push mislukt voor {m['key']}", flush=True)
        try:
            from span import telemetry
            telemetry.record("cron_missed", 0.0,
                             {"key": m["key"], "expected": m["expected"]})
        except Exception:
            pass
        try:
            _mark_reported(brain, m["key"], m["expected"])
        except Exception as exc:
            print(f"[watchdog] stempel mislukt voor {m['key']}: {exc}", flush=True)
    return len(missed)
