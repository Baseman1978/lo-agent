# src/span/jarvis/task_push.py
"""A3 — taak-push: melding wanneer een langlopende achtergrondtaak klaar is
of definitief faalt. Maakt de belofte van spawn_task ("ik meld het als 'ie
klaar is") eindelijk waar.

Regels: definitief mislukt -> altijd melden (urgent + Agent Inbox); klaar ->
alleen als de taak lang liep (LONG_PUSH_SECS); geannuleerd/onderbroken -> stil
(dat deed Bas zelf of een herstart). Telegram is alléén Bas' kanaal, dus
taken van andere web-login-gebruikers pushen nooit. Best-effort, achter
SPAN_TASK_PUSH (default aan)."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

LONG_PUSH_SECS = 120.0  # 'langlopend': pas boven deze duur een klaar-ping


def push_enabled() -> bool:
    """Feature-flag SPAN_TASK_PUSH (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_TASK_PUSH", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def _duration_s(item: dict[str, Any]) -> float:
    try:
        a = datetime.fromisoformat(item.get("created") or "")
        b = datetime.fromisoformat(item.get("updated") or "")
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return 0.0


def _mine(item: dict[str, Any]) -> bool:
    """Alleen systeem-/owner-taken; nooit die van een andere gebruiker."""
    owner = (item.get("owner") or "").strip()
    return owner in ("", os.environ.get("SPAN_OWNER_OID", "").strip())


def should_push(item: dict[str, Any]) -> bool:
    if not _mine(item):
        return False
    status = item.get("status")
    if status == "error":
        return True  # definitief mislukt -> altijd eerlijk melden
    return status == "done" and _duration_s(item) >= LONG_PUSH_SECS


def make_task_push(state: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    """on_done-callback voor de TaskManager; closure over de server-state
    (telegram/inbox/brain) — wiring in app.py."""

    def on_done(item: dict[str, Any]) -> None:
        if not push_enabled() or not should_push(item):
            return
        status = item.get("status")
        titel = ((item.get("title") or item.get("goal") or "").strip())[:60]
        if status == "error":
            kop = "❌ Achtergrondtaak mislukt"
            inbox = state.get("inbox")
            if inbox is not None:
                inbox.add(kind="notify", title=f"Taak mislukt: {titel}",
                          detail=(item.get("result") or "")[:240], urgency="high")
        else:
            kop = "✅ Achtergrondtaak klaar"
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            try:
                from span.jarvis.daily import send_respecting_quiet
                send_respecting_quiet(
                    tg, f"{kop}: {titel}\n\n{(item.get('result') or '')[:500]}",
                    state["brain"], urgent=(status == "error"))
            except Exception:
                print(f"[task-push] telegram mislukt voor taak {item.get('id')}",
                      flush=True)

    return on_done
