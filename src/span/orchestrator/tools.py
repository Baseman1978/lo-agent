"""Tools die de agent tijdens een sessie kan aanroepen.

Schrijven kan alleen in het brein, en alleen via gerichte tools
(remember, quest). Vrije cypher is overal alleen-lezen — ook op het
eigen brein, zodat de structuur via de evaluatiecirkel evolueert en
niet via losse schrijfacties middenin een gesprek.
"""

from __future__ import annotations

import json
from typing import Any

from span.db.brain import BrainDB
from span.db.work import WorkDB, assert_read_only, ReadOnlyViolation
from span.integrations.asana import AsanaClient
from span.integrations.o365 import O365Client
from span.jarvis.briefing import build_briefing
from span.memory.fragments import FragmentStore, MF_TYPES
from span.orchestrator.tool_specs import (  # noqa: F401  (re-export)
    TOOL_SPECS, TOOL_META, O365_TOOLS, ASANA_TOOLS,
)


class ToolBox:
    def __init__(
        self,
        brain: BrainDB,
        fragments: FragmentStore,
        session_id: str,
        work: WorkDB | None = None,
        o365: O365Client | None = None,
        asana: AsanaClient | None = None,
        inbox: Any = None,
        autonomy: dict[str, str] | None = None,
        llm: Any = None,
        light_model: str | None = None,
        disabled: set[str] | None = None,
        user_location: dict[str, float] | None = None,
        fireflies: Any = None,
    ):
        self._brain = brain
        self._fragments = fragments
        self._session_id = session_id
        self._work = work
        self._o365 = o365
        self._asana = asana
        self._inbox = inbox            # AgentInbox: gevoelige acties wachten op akkoord
        self._autonomy = autonomy or {}  # per actie: "ask" (default) of "auto"
        self._llm = llm                # voor concept-generatie bij inbox_approve
        self._light_model = light_model
        self._disabled = disabled or set()  # door Bas uitgezet in instellingen
        self._user_location = user_location  # {lat, lon} uit de browser
        self._fireflies = fireflies
        self.touched: list[str] = []   # mf-ids geraadpleegd deze beurt (hologram)

    def specs(self) -> list[dict[str, Any]]:
        hidden: set[str] = set()
        if self._work is None:
            hidden.add("work_cypher")
        if self._inbox is None:
            hidden |= {"inbox_open", "inbox_approve", "inbox_reject"}
        if self._fireflies is None:
            hidden |= {"fireflies_meetings", "fireflies_sync"}
        if self._o365 is None:
            hidden |= O365_TOOLS
        if self._asana is None:
            hidden |= ASANA_TOOLS
        if self._o365 is None and self._asana is None:
            hidden.add("jarvis_briefing")
        hidden |= self._disabled
        return [t for t in TOOL_SPECS if t["function"]["name"] not in hidden]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name in self._disabled:
            return json.dumps({"error": f"Tool '{name}' is door Bas uitgeschakeld "
                               "in de instellingen."}, ensure_ascii=False)
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return json.dumps({"error": f"Onbekende tool: {name}"})
            result = handler(**arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except (ReadOnlyViolation, ValueError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # tool-fouten terug naar het model, niet crashen
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    # -- handlers --------------------------------------------------------

    def _tool_brain_search(self, query: str, k: int = 5) -> Any:
        embedding = self._fragments.embed(query)
        results = self._fragments.search(query, k=min(int(k), 20), embedding=embedding)
        self.touched.extend(r["id"] for r in results if r.get("score", 0) > 0.5)
        formal = self._fragments.search_formal(query, k=3, embedding=embedding)
        if formal:
            return {"fragments": results, "formal_knowledge": formal}
        return results

    def _tool_brain_cypher(self, query: str) -> Any:
        assert_read_only(query)  # vriendelijke eerste foutmelding
        # de database dwingt het af: READ_ACCESS weigert elke schrijfactie,
        # ook via procedures die de regex niet kent
        return self._brain.run_read(query)[:50]

    def _tool_remember(self, type: str, content: str, context: str = "") -> Any:
        mf_id = self._fragments.write(
            mf_type=type, content=content, context=context, session_id=self._session_id
        )
        return {"stored": mf_id}

    def _tool_quest_upsert(
        self,
        title: str,
        status: str,
        id: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> Any:
        from uuid import uuid4
        quest_id = id.strip() or f"quest-{uuid4().hex[:12]}"
        self._brain.run(
            """
            MERGE (q:Quest {id: $id})
            ON CREATE SET q.created = datetime()
            SET q.title = $title, q.status = $status, q.updated = datetime()
            """,
            id=quest_id,
            title=title,
            status=status,
        )
        if steps is not None:
            self._brain.run(
                """
                MATCH (q:Quest {id: $id})-[r:HAS_STEP]->(st:QuestStep)
                DELETE r, st
                """,
                id=quest_id,
            )
            for order, step in enumerate(steps, start=1):
                self._brain.run(
                    """
                    MATCH (q:Quest {id: $id})
                    CREATE (q)-[:HAS_STEP]->(:QuestStep {
                      order: $order, body: $body, status: $status
                    })
                    """,
                    id=quest_id,
                    order=order,
                    body=step["body"],
                    status=step.get("status", "open"),
                )
        return {"quest": quest_id, "status": status, "steps": len(steps or [])}

    def _tool_work_cypher(self, query: str) -> Any:
        if self._work is None:
            return {"error": "Geen productiedata gekoppeld (WORK_NEO4J_URI leeg)."}
        return self._work.run(query)[:50]

    # -- JARVIS: briefing, O365, Asana ------------------------------------

    def _tool_jarvis_briefing(self) -> Any:
        return build_briefing(self._brain, self._o365, self._asana)

    def _tool_o365_mail_inbox(self, top: int = 10, unread_only: bool = False) -> Any:
        return self._require_o365().inbox(top=top, unread_only=unread_only)

    def _tool_o365_mail_send(self, to: list[str], subject: str, body: str) -> Any:
        if self._inbox is not None and self._autonomy.get("mail", "ask") != "auto":
            item_id = self._inbox.add(
                kind="action", action="mail_send",
                title=f"Mail aan {', '.join(to)}",
                detail=f"{subject} — {body[:120]}",
                payload={"to": to, "subject": subject, "body": body},
                origin="agent",  # door Span gequeued → alleen Bas mag goedkeuren
            )
            return {"queued": item_id,
                    "status": "Wacht op goedkeuring in de Agent Inbox (HUD)."}
        return self._require_o365().send_mail(to=to, subject=subject, body=body)

    def _tool_o365_draft_reply(self, message_id: str, body: str) -> Any:
        return self._require_o365().draft_reply(message_id=message_id, body=body)

    def _tool_o365_thread_summary(self, conversation_id: str) -> Any:
        return self._require_o365().conversation_messages(conversation_id)

    def _tool_o365_calendar(self, days: int = 1) -> Any:
        return self._require_o365().calendar(days=days)

    def _tool_o365_event_create(
        self,
        subject: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        body: str = "",
    ) -> Any:
        if self._inbox is not None and self._autonomy.get("event", "ask") != "auto":
            item_id = self._inbox.add(
                kind="action", action="event_create",
                title=f"Afspraak: {subject}",
                detail=f"{start} – {end}" + (f" · {len(attendees)} genodigden" if attendees else ""),
                payload={"subject": subject, "start": start, "end": end,
                         "attendees": attendees or [], "body": body},
                origin="agent",  # door Span gequeued → alleen Bas mag goedkeuren
            )
            return {"queued": item_id,
                    "status": "Wacht op goedkeuring in de Agent Inbox (HUD)."}
        return self._require_o365().create_event(
            subject=subject, start_iso=start, end_iso=end, attendees=attendees, body=body
        )

    def _tool_o365_todo_list(self, top: int = 20) -> Any:
        return self._require_o365().todo_tasks(top=top)

    def _tool_o365_todo_create(self, title: str, due: str = "", body: str = "") -> Any:
        return self._require_o365().todo_create(title=title, due=due, body=body)

    def _tool_o365_todo_complete(self, task_id: str) -> Any:
        return self._require_o365().todo_complete(task_id)

    def _tool_asana_my_tasks(self, top: int = 20) -> Any:
        return self._require_asana().my_tasks(top=top)

    def _tool_asana_task_create(
        self, name: str, notes: str = "", due_on: str = "", project_gid: str = ""
    ) -> Any:
        return self._require_asana().create_task(
            name=name, notes=notes, due_on=due_on, project_gid=project_gid
        )

    def _tool_asana_task_complete(self, task_gid: str) -> Any:
        return self._require_asana().complete_task(task_gid)

    def _tool_asana_search(self, text: str) -> Any:
        return self._require_asana().search_tasks(text)

    def _tool_asana_projects(self) -> Any:
        return self._require_asana().projects()

    def _tool_inbox_open(self) -> Any:
        return [
            {"id": i["id"], "kind": i["kind"], "title": i["title"], "detail": i["detail"]}
            for i in self._inbox.snapshot() if i["status"] == "open"
        ]

    def _tool_inbox_approve(self, item_id: int) -> Any:
        from span.jarvis.ambient import execute_approval
        peek = self._inbox.get(int(item_id))
        if peek is not None and peek.get("origin") == "agent":
            # injectie-vangrail: een actie die Span zelf heeft klaargezet mag
            # Span niet ook zelf goedkeuren — dat doet Bas in de HUD of CLI
            return {"error": "Dit item is door mijzelf klaargezet; goedkeuren kan "
                             "alleen via de knop in de HUD (of de terminal)."}
        item = self._inbox.claim(int(item_id))
        if item is None:
            return {"error": "Item niet gevonden of al afgehandeld."}
        try:
            result = execute_approval(item, self._o365, self._llm, self._light_model,
                                      asana=self._asana)
        except Exception:
            self._inbox.release(int(item_id))
            raise
        self._inbox.resolve(int(item_id), "done")
        return {"approved": True, "result": result}

    def _tool_inbox_reject(self, item_id: int) -> Any:
        item = self._inbox.resolve(int(item_id), "rejected")
        return {"rejected": item is not None}

    def _tool_cron_create(self, text: str, at: str, repeat: str = "once",
                          run_date: str = "", weekday: int | None = None,
                          mode: str = "remind") -> Any:
        from span.jarvis.crons import create_cron
        return create_cron(self._brain, text, at, repeat, run_date, weekday, mode)

    def _tool_cron_list(self) -> Any:
        from span.jarvis.crons import list_crons
        return list_crons(self._brain)

    def _tool_cron_delete(self, cron_id: str) -> Any:
        from span.jarvis.crons import delete_cron
        return {"deleted": delete_cron(self._brain, cron_id)}

    def _tool_triage_rules_get(self) -> Any:
        rows = self._brain.run(
            "MATCH (c:Config {id:'runtime'}) RETURN c.triage_rules AS r"
        )
        return {"rules": (rows[0]["r"] if rows else None) or "(geen regels ingesteld)"}

    def _tool_triage_rules_set(self, rules: str) -> Any:
        rules = (rules or "").strip()[:2000]
        self._brain.run(
            "MERGE (c:Config {id:'runtime'}) SET c.triage_rules = $r", r=rules
        )
        return {"saved": True, "rules": rules}

    def _tool_fireflies_meetings(self, limit: int = 5) -> Any:
        if self._fireflies is None:
            return {"error": "Fireflies niet geconfigureerd (FIREFLIES_API_KEY leeg)."}
        return self._fireflies.recent_transcripts(limit=limit)

    def _tool_fireflies_sync(self, deep: bool = False) -> Any:
        from types import SimpleNamespace

        from span.jarvis.meetings import sync_meetings
        # mini-state met wat sync_meetings nodig heeft
        state = {"fireflies": self._fireflies, "brain": self._brain,
                 "llm": self._llm, "inbox": self._inbox, "asana": self._asana,
                 "settings": SimpleNamespace(model_light=self._light_model)}
        return sync_meetings(state, deep=bool(deep))

    def _tool_weather(self, place: str = "", days: int = 3) -> Any:
        from span.integrations import weather as wx
        if place.strip():
            loc = wx.geocode(place.strip())
            if loc is None:
                return {"error": f"Plaats '{place}' niet gevonden."}
            return wx.forecast(loc["lat"], loc["lon"], days, place=loc["name"])
        if self._user_location:
            return wx.forecast(self._user_location["lat"], self._user_location["lon"],
                               days, place="huidige locatie van de gebruiker")
        return wx.forecast(wx.DEFAULT_LAT, wx.DEFAULT_LON, days, place=wx.DEFAULT_PLACE)

    def _require_o365(self) -> O365Client:
        if self._o365 is None:
            raise ValueError("O365 niet geconfigureerd (MS_CLIENT_ID leeg).")
        return self._o365

    def _require_asana(self) -> AsanaClient:
        if self._asana is None:
            raise ValueError("Asana niet geconfigureerd (ASANA_TOKEN leeg).")
        return self._asana
