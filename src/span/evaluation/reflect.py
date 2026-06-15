"""Evaluatie — formele knopen uit informele fragmenten.

De stap die de cirkel rond maakt: MemoryFragments van een sessie worden
gedestilleerd tot Insight / Mistake / Idea / Quest. Bij herhaling wordt een
patroon geformaliseerd tot Skill; bij een schema-gat komt er een
uitbreidingsvoorstel (Idea, kind 'schema'). Protocollen mogen evolueren.
"""

from __future__ import annotations

import json
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

    # Reflectie gaat over het REDENEREN van de sessie, niet over bulk-ingest.
    # Archival fragmenten (mail-archief, documenten) eruit: anders blaast een
    # import van honderden mails de prompt op ("Input is too long").
    convo = [m for m in mfs if (m.get("source") or "span") not in ("mail", "document")]
    if not convo:
        end_session(brain, session_id,
                    "Alleen archief-fragmenten — geen redenering om te evalueren.")
        return {"summary": "Archief-sessie, niets te destilleren", "written": {}}
    # harde backstop tegen context-overschrijding: meest recente N + truncatie
    MAX_FRAGS, MAX_CONTENT = 120, 600
    trimmed = len(convo) > MAX_FRAGS
    mfs = [{**m, "content": (m.get("content") or "")[:MAX_CONTENT]}
           for m in convo[-MAX_FRAGS:]]

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
        for index, item in enumerate(parsed.get(key, []), start=1):
            content = (item.get("content") or "").strip()
            if not content:
                continue
            # grounding: valideer source_ids tegen echt bestaande fragmenten.
            # Insight/Mistake ZONDER geldige bron worden geweigerd — dit is de
            # bewezen mitigatie tegen 'compounding silent errors' / over-
            # generalisatie, het #1 faalmodel van zelflerende agents. Een
            # single-user agent heeft geen tweede paar ogen. Idea mag bron-loos
            # (schema-voorstellen e.d. zijn vaak nieuw, niet gedestilleerd).
            valid_sources = _valid_sources(brain, item.get("source_ids", []))
            if label in ("Insight", "Mistake") and not valid_sources:
                print(f"[reflect] {label} geweigerd (geen geldige bron-fragmenten): "
                      f"{content[:70]}", flush=True)
                continue
            props = {"content": content, "session_id": session_id}
            if label == "Mistake":
                props["lesson"] = (item.get("lesson") or "").strip()
            if label == "Idea":
                props["kind"] = item.get("kind", "general")
            # deterministische id: een retry van dezelfde sessie maakt
            # geen duplicaten (MERGE) en kan nooit botsen met andere sessies
            node_id = f"{label.lower()}-{session_id.removeprefix('session-')}-{index}"
            _write_formal_node(brain, llm, label, node_id, props, valid_sources)
            written.setdefault(key, []).append(node_id)

    for index, quest in enumerate(parsed.get("quests", []), start=1):
        title = (quest.get("title") or "").strip()
        if not title:
            continue
        quest_id = f"quest-{session_id.removeprefix('session-')}-{index}"
        brain.run(
            """
            MERGE (q:Quest {id: $id})
            ON CREATE SET q.title = $title, q.status = $status, q.created = datetime()
            """,
            id=quest_id,
            title=title,
            status=quest.get("status", "open"),
        )
        for order, body in enumerate(quest.get("steps", []), start=1):
            brain.run(
                """
                MATCH (q:Quest {id: $id})
                MERGE (q)-[:HAS_STEP]->(st:QuestStep {order: $order})
                ON CREATE SET st.body = $body, st.status = 'open'
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


def _valid_sources(brain: BrainDB, source_ids: list[str]) -> list[str]:
    """Filter de door de LLM aangedragen source_ids tot de fragmenten die
    echt bestaan — voorkomt grounding op verzonnen/foute ids."""
    ids = [s for s in (source_ids or []) if s]
    if not ids:
        return []
    rows = brain.run(
        "MATCH (mf:MemoryFragment) WHERE mf.id IN $ids RETURN mf.id AS id", ids=ids
    )
    return [r["id"] for r in rows]


def _write_formal_node(
    brain: BrainDB, llm: LLMClient, label: str, node_id: str,
    props: dict[str, Any], source_ids: list[str],
) -> str:
    assert label in {"Insight", "Mistake", "Idea"}  # label komt uit vaste set hierboven
    # embedding maakt formele kennis vindbaar via brain_search (cirkel-leeskant)
    embed_text = f"{label}: {props['content']}"
    if props.get("lesson"):
        embed_text += f"\nLes: {props['lesson']}"
    try:
        props = {**props, "embedding": llm.embed_one(embed_text)}
    except Exception as exc:
        print(f"[reflect] embedding {node_id} mislukt: {exc}", flush=True)
    # F3 dedup-vóór-schrijven: bestaat er al een bijna-identieke formele node
    # (ook uit een andere sessie)? Dan niet dubbel opslaan — voorkomt dat
    # dezelfde les bij elke reflectie opnieuw als nieuw inzicht binnenkomt.
    emb = props.get("embedding")
    if emb:
        index = {"Insight": "insight_embedding", "Mistake": "mistake_embedding",
                 "Idea": "idea_embedding"}[label]
        try:
            hits = brain.vector_search(index, emb, k=1)
            if hits and hits[0]["score"] > 0.95:
                existing = hits[0]["node"].get("id")
                if existing and existing != node_id:
                    _link_sources(brain, label, "id", existing, source_ids,
                                  "DISTILLED_FROM")
                    return existing  # hergebruik de bestaande node
        except Exception:
            pass  # geen index/leeg — gewoon doorgaan met schrijven
    brain.run(
        f"MERGE (n:{label} {{id: $id}}) ON CREATE SET n.created = datetime() "
        "SET n += $props",
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
