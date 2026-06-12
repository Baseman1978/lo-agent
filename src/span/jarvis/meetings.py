"""Fireflies-meetings het brein in — en actiepunten richting Asana.

sync_meetings draait periodiek: nieuwe transcripties worden Meeting-nodes
+ MemoryFragments (vindbaar via brain_search), en het lichte model filtert
actiepunten voor Bas eruit. Die komen als voorstel in de Agent Inbox;
goedkeuren = Asana-taak in Mijn taken (of een project dat Span herkent).
"""

from __future__ import annotations

import json
from typing import Any

ACTIONS_PROMPT = """Je bent het meeting-subsysteem van Span, de JARVIS van Bas Spaan.
Hieronder de actiepunten-tekst uit een vergadertranscript (Fireflies).
Filter UITSLUITEND actiepunten die voor Bas zelf zijn (toegewezen aan Bas,
of duidelijk zijn verantwoordelijkheid). Maak er concrete taaknamen van.

Antwoord met uitsluitend JSON:
{"tasks": [{"name": "<korte taaknaam>", "notes": "<context uit de meeting>", "due": "<YYYY-MM-DD indien genoemd, anders leeg>"}]}
Geen taken voor Bas: {"tasks": []}"""


def sync_meetings(state: dict[str, Any], limit: int = 8, deep: bool = False) -> dict[str, int]:
    """Idempotent: al bekende transcripts (Meeting-node op ff_id) slaat hij over.
    deep=True: volledige historie (gepagineerd) i.p.v. alleen de recentste."""
    ff = state.get("fireflies")
    if ff is None:
        return {"new": 0, "tasks": 0}
    brain = state["brain"]
    transcripts = ff.all_transcripts() if deep else ff.recent_transcripts(limit=limit)
    known = {
        r["id"] for r in brain.run(
            "MATCH (m:Meeting) RETURN m.ff_id AS id"
        )
    }
    # deelnemer-filter (Config ff_filter): alleen meetings waar Bas bij was;
    # transcripts zonder deelnemerslijst zijn eigen opnames → meenemen
    rows = brain.run("MATCH (c:Config {id:'runtime'}) RETURN c.ff_filter AS f")
    participant_filter = ((rows[0].get("f") if rows else None) or "").strip().lower()
    new_count = task_count = 0

    for t in transcripts:
        if t["id"] in known or not (t["overview"] or t["action_items"]):
            continue
        if participant_filter and t["participants"]:
            attendees = [p.lower() for p in t["participants"]]
            if not any(participant_filter in p for p in attendees):
                continue
        new_count += 1
        brain.run(
            """
            CREATE (:Meeting {
              ff_id: $id, title: $title, date: $date, duration_min: $dur,
              participants: $participants, overview: $overview,
              action_items: $actions, created: datetime()
            })
            """,
            id=t["id"], title=t["title"], date=t["date"], dur=t["duration_min"],
            participants=t["participants"], overview=t["overview"][:1500],
            actions=t["action_items"][:1500],
        )
        # samenvatting het levende geheugen in (embeddings → brain_search vindt het)
        try:
            from span.memory.fragments import FragmentStore
            from span.memory.bootstrap import start_session
            fragments = FragmentStore(brain, state["llm"])
            session_id = state.get("meeting_session") or start_session(brain)
            state["meeting_session"] = session_id
            fragments.write(
                mf_type="observation",
                content=f"Meeting '{t['title']}' ({t['date']}): {t['overview'][:400]}",
                context=f"Fireflies · deelnemers: {', '.join(t['participants'][:6])}",
                session_id=session_id,
                source="fireflies",
            )
        except Exception:
            pass

        # actiepunten voor Bas → Agent Inbox → (na akkoord) Asana
        if t["action_items"] and state.get("asana") is not None:
            try:
                parsed = state["llm"].chat_json(
                    [
                        {"role": "system", "content": ACTIONS_PROMPT},
                        {"role": "user",
                         "content": f"Meeting: {t['title']}\n\n{t['action_items']}"},
                    ],
                    model=state["settings"].model_light,
                )
                inbox = state.get("inbox")
                for task in (parsed.get("tasks") or [])[:6]:
                    name = (task.get("name") or "").strip()
                    if not name or inbox is None:
                        continue
                    inbox.add(
                        kind="action", action="asana_task",
                        title=f"Asana-taak uit '{t['title']}'",
                        detail=name,
                        payload={"name": name,
                                 "notes": (task.get("notes") or "") +
                                 f"\n\nBron: Fireflies meeting '{t['title']}' ({t['date']})",
                                 "due_on": (task.get("due") or "").strip()},
                    )
                    task_count += 1
            except Exception:
                pass

    return {"new": new_count, "tasks": task_count}
