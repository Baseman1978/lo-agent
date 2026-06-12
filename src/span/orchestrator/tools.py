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

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "brain_search",
            "description": "Semantisch zoeken in het eigen geheugen: MemoryFragments "
            "én formele kennis (Insights, Mistakes/lessen, Ideas). Gebruik dit vóór "
            "je een vraag beantwoordt waarvan je vermoedt dat er eerdere kennis over "
            "bestaat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Zoekvraag in natuurlijke taal"},
                    "k": {"type": "integer", "description": "Aantal resultaten (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_cypher",
            "description": "Alleen-lezen Cypher op het brein (identity, protocols, quests, "
            "skills, insights, mistakes, sessions, memory fragments).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Read-only Cypher query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Schrijf direct een MemoryFragment in het brein. Gebruik bij "
            "besluiten, ontdekkingen, valkuilen of persoonlijkheidsmomenten — klein en vaak.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": sorted(MF_TYPES),
                        "description": "Type fragment",
                    },
                    "content": {"type": "string", "description": "De observatie zelf, beknopt"},
                    "context": {"type": "string", "description": "Optionele context/bron"},
                },
                "required": ["type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quest_upsert",
            "description": "Maak of update een Quest (doel met stappen). Status: open, "
            "active, done. Stappen vervangen de bestaande stappenlijst.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Quest-id, bv. quest-110. Leeg = nieuw."},
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["open", "active", "done"]},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "body": {"type": "string"},
                                "status": {"type": "string", "enum": ["open", "done"]},
                            },
                            "required": ["body"],
                        },
                    },
                },
                "required": ["title", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jarvis_briefing",
            "description": "Volledige briefing: agenda vandaag, ongelezen mail, To Do, "
            "Asana-taken en open quests. Gebruik bij 'briefing', 'wat staat er vandaag', "
            "'update me' of als sessie-opening.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_mail_inbox",
            "description": "Lees de Outlook-inbox (onderwerp, afzender, preview).",
            "parameters": {
                "type": "object",
                "properties": {
                    "top": {"type": "integer", "description": "Aantal mails (default 10, max 25)"},
                    "unread_only": {"type": "boolean", "description": "Alleen ongelezen"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_mail_send",
            "description": "Verstuur een e-mail via Outlook. Vat eerst samen wat je gaat "
            "sturen en aan wie; verstuur alleen na expliciete bevestiging van de gebruiker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}, "description": "E-mailadressen"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Platte tekst"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_calendar",
            "description": "Agenda van nu tot +N dagen (Outlook calendarView).",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Aantal dagen vooruit (default 1, max 31)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_event_create",
            "description": "Maak een agenda-afspraak. Tijden in lokale tijd (W. Europe), "
            "ISO-formaat: 2026-06-12T14:00:00.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "start": {"type": "string", "description": "Start, bv. 2026-06-12T14:00:00"},
                    "end": {"type": "string", "description": "Einde, bv. 2026-06-12T15:00:00"},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "E-mailadressen (optioneel)"},
                    "body": {"type": "string", "description": "Omschrijving (optioneel)"},
                },
                "required": ["subject", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_draft_reply",
            "description": "Maak een antwoord-CONCEPT op een mail in Outlook (verstuurt "
            "niets — Bas leest en verstuurt zelf). Gebruik graph_id uit o365_mail_inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "graph_id van de mail"},
                    "body": {"type": "string", "description": "Concepttekst, platte tekst, in de stijl van Bas"},
                },
                "required": ["message_id", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_thread_summary",
            "description": "Haal alle berichten van een mailthread op (conversation_id uit "
            "o365_mail_inbox) zodat je hem kunt samenvatten.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_todo_list",
            "description": "Open taken uit Microsoft To Do (standaardlijst), met id's.",
            "parameters": {
                "type": "object",
                "properties": {
                    "top": {"type": "integer", "description": "Aantal taken (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_todo_create",
            "description": "Maak een taak in Microsoft To Do.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "due": {"type": "string", "description": "Deadline YYYY-MM-DD (optioneel)"},
                    "body": {"type": "string", "description": "Toelichting (optioneel)"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "o365_todo_complete",
            "description": "Vink een Microsoft To Do-taak af. Haal eerst het id op via o365_todo_list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asana_my_tasks",
            "description": "Open taken uit 'Mijn taken' in Asana.",
            "parameters": {
                "type": "object",
                "properties": {
                    "top": {"type": "integer", "description": "Aantal taken (default 20)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asana_task_create",
            "description": "Maak een Asana-taak aan, toegewezen aan de gebruiker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Taaknaam"},
                    "notes": {"type": "string", "description": "Omschrijving (optioneel)"},
                    "due_on": {"type": "string", "description": "Deadline YYYY-MM-DD (optioneel)"},
                    "project_gid": {"type": "string", "description": "Project-gid (optioneel, zie asana_projects)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asana_task_complete",
            "description": "Vink een Asana-taak af. Zoek eerst de gid via asana_my_tasks "
            "of asana_search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_gid": {"type": "string", "description": "Asana task gid"},
                },
                "required": ["task_gid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asana_search",
            "description": "Zoek taken in Asana op tekst (typeahead).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Zoektekst"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "asana_projects",
            "description": "Lijst actieve Asana-projecten (naam + gid).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inbox_open",
            "description": "Toon open items in de Agent Inbox (acties die op goedkeuring "
            "wachten + meldingen). Gebruik bij 'wat staat er in mijn inbox/wachtrij'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inbox_approve",
            "description": "Keur een Agent Inbox-item goed en voer het uit (mail versturen, "
            "afspraak maken, concept schrijven). Alleen na expliciete opdracht van de gebruiker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Id uit inbox_open"},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inbox_reject",
            "description": "Wijs een Agent Inbox-item af / markeer als gezien.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "Id uit inbox_open"},
                },
                "required": ["item_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "triage_rules_get",
            "description": "Lees de huidige mail-triage-regels (hoe de ambient watcher "
            "inkomende mail beoordeelt: negeren/melden/urgent).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "triage_rules_set",
            "description": "Vervang de mail-triage-regels (vrije tekst, max 2000 tekens). "
            "LET OP: dit vervangt ALLES — lees eerst met triage_rules_get en lever de "
            "volledige bijgewerkte tekst aan. Bevestig daarna kort wat er nu geldt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rules": {"type": "string", "description": "De volledige nieuwe regels-tekst"},
                },
                "required": ["rules"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fireflies_meetings",
            "description": "Recente vergadertranscripties (Fireflies): titel, datum, "
            "deelnemers, samenvatting en actiepunten. Oudere meetings zitten ook in "
            "het brein (brain_search).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Aantal (default 5)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fireflies_sync",
            "description": "Verwerk Fireflies-meetings naar het geheugen (Meeting-nodes + "
            "herinneringen + actiepunten naar de Agent Inbox). deep=true: volledige "
            "historie. Idempotent — al verwerkte meetings worden overgeslagen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deep": {"type": "boolean", "description": "Hele historie i.p.v. recentste"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cron_create",
            "description": "Plan een taak of herinnering. mode 'remind' = melding op het "
            "moment; mode 'execute' = jij voert de opdracht dan zelf uit (met al je tools) "
            "en levert het resultaat af. repeat: once/daily/weekdays/weekly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Wat er moet gebeuren of gemeld"},
                    "at": {"type": "string", "description": "Tijd HH:MM"},
                    "repeat": {"type": "string", "enum": ["once", "daily", "weekdays", "weekly"]},
                    "run_date": {"type": "string", "description": "YYYY-MM-DD, alleen bij once (default vandaag)"},
                    "weekday": {"type": "integer", "description": "0=maandag … 6=zondag, alleen bij weekly"},
                    "mode": {"type": "string", "enum": ["remind", "execute"]},
                },
                "required": ["text", "at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cron_list",
            "description": "Toon alle geplande taken en herinneringen.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cron_delete",
            "description": "Verwijder een geplande taak (id uit cron_list).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cron_id": {"type": "string"},
                },
                "required": ["cron_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Actueel weer + verwachting (1-7 dagen). Zonder argumenten: "
            "de locatie van de gebruiker (browser-GPS) of Amersfoort. Met place: "
            "elke plaatsnaam wereldwijd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "place": {"type": "string", "description": "Plaatsnaam (optioneel)"},
                    "days": {"type": "integer", "description": "Dagen vooruit, 1-7 (default 3)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "work_cypher",
            "description": "Alleen-lezen Cypher op de productiedata (locations, projects, "
            "models, drawings, assets, documents, requirements, process). Schrijfacties "
            "worden geweigerd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Read-only Cypher query"},
                },
                "required": ["query"],
            },
        },
    },
]


O365_TOOLS = {"o365_mail_inbox", "o365_mail_send", "o365_calendar", "o365_event_create",
              "o365_draft_reply", "o365_thread_summary",
              "o365_todo_list", "o365_todo_create", "o365_todo_complete"}

# Permissie-registry: groep + lezen/schrijven, voor de instellingenpagina.
TOOL_META: dict[str, tuple[str, str]] = {
    "brain_search": ("Brein", "read"),
    "brain_cypher": ("Brein", "read"),
    "remember": ("Brein", "write"),
    "quest_upsert": ("Brein", "write"),
    "jarvis_briefing": ("Briefing", "read"),
    "o365_mail_inbox": ("O365 Mail", "read"),
    "o365_thread_summary": ("O365 Mail", "read"),
    "o365_draft_reply": ("O365 Mail", "write"),
    "o365_mail_send": ("O365 Mail", "write"),
    "o365_calendar": ("O365 Agenda", "read"),
    "o365_event_create": ("O365 Agenda", "write"),
    "o365_todo_list": ("O365 To Do", "read"),
    "o365_todo_create": ("O365 To Do", "write"),
    "o365_todo_complete": ("O365 To Do", "write"),
    "asana_my_tasks": ("Asana", "read"),
    "asana_search": ("Asana", "read"),
    "asana_projects": ("Asana", "read"),
    "asana_task_create": ("Asana", "write"),
    "asana_task_complete": ("Asana", "write"),
    "inbox_open": ("Agent Inbox", "read"),
    "inbox_approve": ("Agent Inbox", "write"),
    "inbox_reject": ("Agent Inbox", "write"),
    "work_cypher": ("Werkdata", "read"),
    "weather": ("Weer", "read"),
    "fireflies_meetings": ("Fireflies", "read"),
    "fireflies_sync": ("Fireflies", "write"),
    "triage_rules_get": ("O365 Mail", "read"),
    "triage_rules_set": ("O365 Mail", "write"),
    "cron_create": ("Planning", "write"),
    "cron_list": ("Planning", "read"),
    "cron_delete": ("Planning", "write"),
}
ASANA_TOOLS = {"asana_my_tasks", "asana_task_create", "asana_task_complete",
               "asana_search", "asana_projects"}


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
