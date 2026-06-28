"""Tool-definities en permissie-registry voor de agent.

Gescheiden van de ToolBox-logica (tools.py) zodat beide bestanden onder
de 500-regelgrens blijven. Geimporteerd door tools.py (die de namen
re-exporteert voor bestaande imports) en door de server (TOOL_META).
"""

from __future__ import annotations

from typing import Any

from span.memory.fragments import MF_TYPES  # voor de remember-tool enum

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
                    "scope": {
                        "type": "string",
                        "enum": ["algemeen", "werk", "prive"],
                        "description": "Domein: 'werk' (Lomans), 'prive', of 'algemeen' (default)",
                    },
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
    {
        "type": "function",
        "function": {
            "name": "mail_archive_folder",
            "description": "Archiveer mails uit een Outlook-MAP naar het geheugen via "
            "de gekoppelde M365 MCP-server (bv. de map '10: Verwerkt'). Idempotent: "
            "herhaald aanroepen vult de map verder. Per aanroep tot 'limit' mails. "
            "Gebruik dit i.p.v. losse MCP-mailtools voor bulk-archivering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "(deel van de) mapnaam"},
                    "limit": {"type": "integer", "description": "max mails deze keer (default 200)"},
                },
                "required": ["folder"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_propose_server",
            "description": "Stel een MCP-server voor om te koppelen (bv. een publieke "
            "server die nuttige tools biedt). Dit VOEGT NIETS DIRECT TOE — het komt "
            "als voorstel in de Agent Inbox; Bas keurt goed en logt zelf in. Gebruik "
            "alleen voor servers die je echt nuttig acht; noem in 'reason' waarom.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "korte naam"},
                    "url": {"type": "string", "description": "https://…/mcp-URL"},
                    "reason": {"type": "string", "description": "waarom nuttig voor Bas"},
                },
                "required": ["name", "url", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_share",
            "description": "Stel voor om een kennisknoop (Insight, les/Mistake, Idea, "
            "Skill, Protocol of MemoryFragment) met het team te delen. Dit DEELT NIETS "
            "DIRECT — het komt als voorstel in de Agent Inbox; Bas keurt goed en pas dan "
            "gaat de knoop naar het gedeelde brein. Gebruik dit als je kennis tegenkomt "
            "die teamgenoten duidelijk zou helpen; noem in 'reason' waarom.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "id van de te delen knoop"},
                    "reason": {"type": "string", "description": "waarom dit nuttig is voor het team"},
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_goal",
            "description": "Decomponeer een groter doel of meerstaps-opdracht in een "
            "bevroren stappenplan (Quest). Gebruik dit bij 'maak een plan voor…' of "
            "een opdracht die meerdere stappen/sessies beslaat. Een aparte planner "
            "zonder tools maakt het plan; daarna werk je de stappen af.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "het doel in één zin"},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Zoek actuele informatie op het internet. Gebruik dit als "
            "de vraag recente/externe kennis vereist die niet in het geheugen of de "
            "tools zit. Citeer de bron-URL in je antwoord.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "zoekopdracht"},
                    "max_results": {"type": "integer", "description": "1-8, default 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": "Haal een publieke webpagina op en lees de kern. Geef een "
            "volledige http(s)-URL. De inhoud wordt veilig samengevat. Citeer de URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "volledige http(s)-URL"},
                },
                "required": ["url"],
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
    "plan_goal": ("Planning", "write"),
    "mail_archive_folder": ("O365 Mail", "read"),
    "mcp_propose_server": ("MCP", "write"),
    "propose_share": ("Gedeeld geheugen", "write"),
    "web_search": ("Web", "read"),
    "web_read": ("Web", "read"),
}
ASANA_TOOLS = {"asana_my_tasks", "asana_task_create", "asana_task_complete",
               "asana_search", "asana_projects"}
