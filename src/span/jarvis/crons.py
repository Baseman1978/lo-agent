"""Geplande taken — door Span zelf te beheren via tools.

Twee soorten:
- remind:  op het moment een melding (Agent Inbox + Telegram)
- execute: Span voert de opdracht zelf uit als agent en levert het
           resultaat af (inbox + Telegram)

Schema's: once (met datum), daily, weekdays (ma-vr), weekly (met weekdag).
Opslag in het brein (Cron-nodes), dus herstart-bestendig.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from span.jarvis.daily import now_local, today_local

REPEATS = {"once", "daily", "weekdays", "weekly"}
WEEKDAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]


def create_cron(brain, text: str, at: str, repeat: str = "once",
                run_date: str = "", weekday: int | None = None,
                mode: str = "remind") -> dict[str, Any]:
    datetime.strptime(at, "%H:%M")  # valideert
    if repeat not in REPEATS:
        raise ValueError(f"repeat moet één van {sorted(REPEATS)} zijn")
    if repeat == "once" and not run_date:
        run_date = today_local()
    if repeat == "once":
        datetime.strptime(run_date, "%Y-%m-%d")
    if repeat == "weekly" and (weekday is None or not 0 <= int(weekday) <= 6):
        raise ValueError("weekly vereist weekday 0 (maandag) t/m 6 (zondag)")
    if mode not in {"remind", "execute", "task"}:
        raise ValueError("mode moet remind, execute of task zijn")
    from uuid import uuid4
    cron_id = f"cron-{uuid4().hex[:10]}"
    brain.run(
        """
        CREATE (:Cron {
          id: $id, text: $text, at: $at, repeat: $repeat,
          run_date: $run_date, weekday: $weekday, mode: $mode,
          created: datetime(), last_run: ''
        })
        """,
        id=cron_id, text=text.strip()[:500], at=at, repeat=repeat,
        run_date=run_date, weekday=int(weekday) if weekday is not None else -1,
        mode=mode,
    )
    return {"created": True, "id": cron_id, "at": at, "repeat": repeat, "mode": mode}


def list_crons(brain) -> list[dict[str, Any]]:
    return brain.run(
        "MATCH (c:Cron) RETURN c.id AS id, c.text AS text, c.at AS at, "
        "c.repeat AS repeat, c.run_date AS run_date, c.weekday AS weekday, "
        "c.mode AS mode, c.last_run AS last_run ORDER BY c.at"
    )


def delete_cron(brain, cron_id: str) -> bool:
    rows = brain.run(
        "MATCH (c:Cron {id: $id}) DETACH DELETE c RETURN count(*) AS n", id=cron_id
    )
    return bool(rows and rows[0]["n"])


MAX_CRON_ATTEMPTS = 3


def _is_due(cron: dict[str, Any], now: datetime) -> bool:
    today = now.date().isoformat()
    if cron["last_run"] == today:
        return False
    if now.strftime("%H:%M") < cron["at"]:
        return False
    repeat = cron["repeat"]
    if repeat == "once":
        # ook achterstallig (server lag eruit op de run-dag) alsnog uitvoeren
        return bool(cron["run_date"]) and cron["run_date"] <= today
    if repeat == "daily":
        return True
    if repeat == "weekdays":
        return now.weekday() < 5
    if repeat == "weekly":
        return now.weekday() == cron["weekday"]
    return False


def run_due_crons(state: dict[str, Any]) -> int:
    """Draait in de minuut-scheduler. Markeert pas NA succes; bij een fout
    volgt een retry op de volgende tick, met een cap per dag."""
    brain = state["brain"]
    now = now_local()
    ran = 0
    for cron in list_crons(brain):
        if not _is_due(cron, now):
            continue
        try:
            if cron["mode"] == "task" and state.get("tasks") is not None:
                # geplande ACHTERGRONDtaak: een sub-agent doet het werk, de chat
                # blijft vrij; resultaat landt in het Taken-paneel
                tid = state["tasks"].submit(cron["text"], title="⏰ " + cron["text"][:50])
                title, detail = (f"⏰ Achtergrondtaak gestart: {cron['text'][:50]}",
                                 f"taak #{tid} — volg de voortgang in het Taken-paneel")
                tg_text = f"⏰ Achtergrondtaak gestart\n{cron['text']}"
            elif cron["mode"] == "execute":
                result = _execute(state, cron["text"])
                if result.startswith("Uitvoering mislukt:"):
                    raise RuntimeError(result)
                title, detail = f"⏰ Uitgevoerd: {cron['text'][:60]}", result[:280]
                tg_text = f"⏰ {cron['text']}\n\n{result}"
            else:
                title, detail = "⏰ Herinnering", cron["text"]
                tg_text = "⏰ HERINNERING\n\n" + cron["text"]
        except Exception as exc:
            attempts = brain.run(
                "MATCH (c:Cron {id: $id}) SET c.attempts = coalesce(c.attempts, 0) + 1 "
                "RETURN c.attempts AS n", id=cron["id"],
            )
            n = attempts[0]["n"] if attempts else 1
            print(f"[cron] {cron['id']} poging {n} mislukt: {exc}", flush=True)
            if n < MAX_CRON_ATTEMPTS:
                continue  # volgende tick opnieuw
            # opgeven voor vandaag, maar wel zichtbaar
            title, detail = f"⏰ Mislukt: {cron['text'][:60]}", f"{MAX_CRON_ATTEMPTS}x geprobeerd; laatste fout: {exc}"
            tg_text = f"⏰ MISLUKT\n{cron['text']}\n\n{exc}"

        # pas hier markeren/verwijderen: de taak is gedaan (of opgegeven)
        if cron["repeat"] == "once":
            delete_cron(brain, cron["id"])
        else:
            brain.run("MATCH (c:Cron {id: $id}) SET c.last_run = $d, c.attempts = 0",
                      id=cron["id"], d=today_local())
        ran += 1

        inbox = state.get("inbox")
        if inbox is not None:
            inbox.add(kind="notify", title=title, detail=detail, urgency="high")
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            try:
                tg.send(tg_text)
            except Exception:
                print(f"[cron] telegram-push mislukt voor {cron['id']}", flush=True)
    return ran


def _execute(state: dict[str, Any], prompt: str) -> str:
    """Voer de geplande opdracht uit als volwaardige agent-beurt."""
    try:
        from span.memory.bootstrap import start_session
        from span.orchestrator.agent import SpanAgent
        agent = SpanAgent(
            state["settings"], state["brain"], state["llm"],
            state.get("work"), o365=state.get("o365"), asana=state.get("asana"),
            inbox=state.get("inbox"), autonomy=state.get("autonomy"),
            disabled_tools=state.get("disabled_tools"),
            fireflies=state.get("fireflies"),
        )
        session_id = start_session(state["brain"])
        agent.begin(session_id, first_message=prompt)
        answer = agent.turn(
            f"[Geplande opdracht, automatisch gestart] {prompt}\n"
            "Voer dit nu uit en geef een beknopt resultaat."
        )
        agent.flush_recording(timeout=5)
        return answer
    except Exception as exc:
        return f"Uitvoering mislukt: {exc}"
