"""Evaluatie — formele knopen uit informele fragmenten.

De stap die de cirkel rond maakt: MemoryFragments van een sessie worden
gedestilleerd tot Insight / Mistake / Idea / Quest. Bij herhaling wordt een
patroon geformaliseerd tot Skill; bij een schema-gat komt er een
uitbreidingsvoorstel (Idea, kind 'schema'). Protocollen mogen evolueren.
"""

from __future__ import annotations

import json
import time
from typing import Any

from span.config import Settings
from span.db.brain import BrainDB
from span.llm.client import LLMClient
from span.memory.bootstrap import end_session
from span.memory.fragments import FragmentStore

REFLECT_PROMPT = """Je bent het evaluatie-subsysteem van AI-agent Span.
Hieronder de MemoryFragments van één sessie, plus bestaande skills en protocollen.

Destilleer formele kennis. Wees streng: alleen wat toekomstige sessies echt
verder helpt. Verwijs per item naar de bron-fragmenten via hun ids.

Herhaling: zie je een patroon dat al eerder als insight/fragment voorkwam en
nu weer — formaliseer het dan tot een skill (naam kebab-case, met trigger).
Schema-gat: paste kennis nergens goed in? Stel een schema-uitbreiding voor
als idea met kind "schema".
Protocollen: alleen bijwerken als de sessie daar concrete aanleiding toe gaf.

Antwoord met uitsluitend JSON:
{
  "summary": "<2-4 zinnen sessiesamenvatting>",
  "insights": [{"content": "...", "source_ids": ["mf-..."]}],
  "mistakes": [{"content": "...", "lesson": "...", "source_ids": []}],
  "ideas": [{"content": "...", "kind": "general|schema", "source_ids": []}],
  "quests": [{"title": "...", "status": "open", "steps": ["..."]}],
  "skills": [{"name": "...", "description": "...", "trigger": "...", "body": "...", "source_ids": []}],
  "protocol_updates": [{"name": "<bestaand protocol>", "body": "<nieuwe volledige tekst>", "reason": "..."}]
}
Lege lijsten zijn prima. Liever niets dan ruis."""


def reflect_session(
    settings: Settings,
    brain: BrainDB,
    llm: LLMClient,
    fragments: FragmentStore,
    session_id: str,
) -> dict[str, Any]:
    mfs = fragments.session_fragments(session_id)
    if not mfs:
        end_session(brain, session_id, "Lege sessie — niets te evalueren.")
        return {"summary": "Lege sessie", "written": {}}

    existing_skills = brain.run(
        "MATCH (sk:Skill) RETURN sk.name AS name, sk.description AS description"
    )
    protocols = brain.run(
        "MATCH (p:Protocol) RETURN p.name AS name, p.body AS body, p.version AS version"
    )

    payload = {
        "fragments": mfs,
        "existing_skills": existing_skills,
        "protocols": protocols,
    }
    parsed = llm.chat_json(
        [
            {"role": "system", "content": REFLECT_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        model=settings.model_main,
        max_tokens=8192,
    )

    written: dict[str, list[str]] = {}

    for label, key in (("Insight", "insights"), ("Mistake", "mistakes"), ("Idea", "ideas")):
        for item in parsed.get(key, []):
            content = (item.get("content") or "").strip()
            if not content:
                continue
            props = {"content": content, "session_id": session_id}
            if label == "Mistake":
                props["lesson"] = (item.get("lesson") or "").strip()
            if label == "Idea":
                props["kind"] = item.get("kind", "general")
            node_id = _write_formal_node(brain, label, props, item.get("source_ids", []))
            written.setdefault(key, []).append(node_id)

    for quest in parsed.get("quests", []):
        title = (quest.get("title") or "").strip()
        if not title:
            continue
        quest_id = f"quest-{int(time.time() * 1000) % 1000000}"
        brain.run(
            """
            CREATE (q:Quest {id: $id, title: $title, status: $status, created: datetime()})
            """,
            id=quest_id,
            title=title,
            status=quest.get("status", "open"),
        )
        for order, body in enumerate(quest.get("steps", []), start=1):
            brain.run(
                """
                MATCH (q:Quest {id: $id})
                CREATE (q)-[:HAS_STEP]->(:QuestStep {order: $order, body: $body, status: 'open'})
                """,
                id=quest_id,
                order=order,
                body=body,
            )
        written.setdefault("quests", []).append(quest_id)

    for skill in parsed.get("skills", []):
        name = (skill.get("name") or "").strip()
        if not name:
            continue
        brain.run(
            """
            MERGE (sk:Skill {name: $name})
            ON CREATE SET sk.created = datetime(), sk.usage_count = 0
            ON MATCH SET sk.usage_count = coalesce(sk.usage_count, 0) + 1
            SET sk.description = $description, sk.trigger = $trigger,
                sk.body = $body, sk.updated = datetime()
            """,
            name=name,
            description=skill.get("description", ""),
            trigger=skill.get("trigger", ""),
            body=skill.get("body", ""),
        )
        _link_sources(brain, "Skill", "name", name, skill.get("source_ids", []), "FORMALIZED_FROM")
        written.setdefault("skills", []).append(name)

    known_protocols = {p["name"] for p in protocols}
    for update in parsed.get("protocol_updates", []):
        name = (update.get("name") or "").strip()
        body = (update.get("body") or "").strip()
        if name not in known_protocols or not body:
            continue  # evaluatie mag bestaande protocollen bijwerken, geen nieuwe verzinnen
        brain.run(
            """
            MATCH (p:Protocol {name: $name})
            SET p.body = $body, p.version = p.version + 1,
                p.last_reason = $reason, p.updated = datetime()
            """,
            name=name,
            body=body,
            reason=update.get("reason", ""),
        )
        written.setdefault("protocol_updates", []).append(name)

    summary = (parsed.get("summary") or "").strip() or "Geen samenvatting."
    end_session(brain, session_id, summary)
    return {"summary": summary, "written": written}


def _write_formal_node(
    brain: BrainDB, label: str, props: dict[str, Any], source_ids: list[str]
) -> str:
    node_id = f"{label.lower()}-{int(time.time() * 1000) % 10000000}"
    assert label in {"Insight", "Mistake", "Idea"}  # label komt uit vaste set hierboven
    brain.run(
        f"CREATE (n:{label} {{id: $id, created: datetime()}}) SET n += $props",
        id=node_id,
        props=props,
    )
    _link_sources(brain, label, "id", node_id, source_ids, "DISTILLED_FROM")
    return node_id


def _link_sources(
    brain: BrainDB,
    label: str,
    key: str,
    value: str,
    source_ids: list[str],
    rel: str,
) -> None:
    assert label in {"Insight", "Mistake", "Idea", "Skill"} and rel in {
        "DISTILLED_FROM",
        "FORMALIZED_FROM",
    }
    for mf_id in source_ids or []:
        brain.run(
            f"""
            MATCH (n:{label} {{{key}: $value}})
            MATCH (mf:MemoryFragment {{id: $mf_id}})
            MERGE (n)-[:{rel}]->(mf)
            """,
            value=value,
            mf_id=mf_id,
        )
