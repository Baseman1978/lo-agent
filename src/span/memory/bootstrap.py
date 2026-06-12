"""Bootstrap — de cirkel rond.

Volgende sessie haalt op wat vorige sessies leerden: identity, protocollen,
actieve quests, recente beslissingen en anti-patterns, plus fragmenten die
relevant zijn voor de eerste vraag. Zo dient eerder geschreven kennis
zichzelf aan voordat iemand erom vraagt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from span.db.brain import BrainDB
from span.memory.fragments import FragmentStore


@dataclass
class BootstrapContext:
    identity: dict[str, Any]
    protocols: list[dict[str, Any]]
    quests: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    anti_patterns: list[dict[str, Any]]
    soul: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    relevant: list[dict[str, Any]] = field(default_factory=list)
    recent_sessions: list[dict[str, Any]] = field(default_factory=list)
    insights: list[dict[str, Any]] = field(default_factory=list)
    lessons: list[dict[str, Any]] = field(default_factory=list)


def start_session(brain: BrainDB) -> str:
    session_id = f"session-{int(time.time() * 1000)}"
    brain.run(
        "CREATE (s:Session {id: $id, started: datetime()})",
        id=session_id,
    )
    return session_id


def end_session(brain: BrainDB, session_id: str, summary: str) -> None:
    brain.run(
        """
        MATCH (s:Session {id: $id})
        SET s.ended = datetime(), s.summary = $summary
        """,
        id=session_id,
        summary=summary,
    )


def load_bootstrap(
    brain: BrainDB,
    fragments: FragmentStore,
    first_message: str | None = None,
) -> BootstrapContext:
    identity_rows = brain.run(
        """
        MATCH (i:Identity {name: 'Span'})
        RETURN i.name AS name, i.philosophy AS philosophy,
               i.origin AS origin, i.owner AS owner
        """
    )
    if not identity_rows:
        raise RuntimeError("Geen Identity-node gevonden. Draai eerst: span init")

    protocols = brain.run(
        """
        MATCH (:Identity {name: 'Span'})-[:HAS_PROTOCOL]->(p:Protocol)
        RETURN p.name AS name, p.body AS body, p.version AS version
        ORDER BY p.name
        """
    )

    quests = brain.run(
        """
        MATCH (q:Quest) WHERE q.status IN ['open', 'active']
        OPTIONAL MATCH (q)-[:HAS_STEP]->(st:QuestStep)
        WITH q, st ORDER BY st.order
        RETURN q.id AS id, q.title AS title, q.status AS status,
               collect({order: st.order, body: st.body, status: st.status}) AS steps
        ORDER BY q.id
        """
    )

    decisions = fragments.recent(k=8, mf_type="decision")
    anti_patterns = fragments.recent(k=8, mf_type="anti-pattern")
    soul = fragments.recent(k=6, mf_type="soul")

    skills = brain.run(
        """
        MATCH (sk:Skill)
        RETURN sk.name AS name, sk.description AS description,
               sk.trigger AS trigger, coalesce(sk.usage_count, 0) AS usage_count
        ORDER BY sk.usage_count DESC LIMIT 10
        """
    )

    # formele kennis uit de evaluatiecirkel — de leeskant van het leren
    insights = brain.run(
        """
        MATCH (n:Insight) WHERE n.content IS NOT NULL
        RETURN n.id AS id, n.content AS content
        ORDER BY n.created DESC LIMIT 8
        """
    )
    lessons = brain.run(
        """
        MATCH (n:Mistake) WHERE n.content IS NOT NULL
        RETURN n.id AS id, n.content AS content, coalesce(n.lesson, '') AS lesson
        ORDER BY n.created DESC LIMIT 6
        """
    )

    relevant: list[dict[str, Any]] = []
    if first_message and first_message.strip():
        relevant = fragments.search(first_message, k=6)

    # usage-context: waar ging het de laatste tijd over? (proactiviteit)
    recent_sessions = brain.run(
        """
        MATCH (s:Session) WHERE s.summary IS NOT NULL
        RETURN s.id AS id, s.summary AS summary
        ORDER BY s.started DESC LIMIT 5
        """
    )

    return BootstrapContext(
        identity=identity_rows[0],
        protocols=protocols,
        quests=quests,
        decisions=decisions,
        anti_patterns=anti_patterns,
        soul=soul,
        skills=skills,
        relevant=relevant,
        recent_sessions=recent_sessions,
        insights=insights,
        lessons=lessons,
    )


def render_bootstrap(ctx: BootstrapContext) -> str:
    """Maakt het bootstrap-blok voor de system prompt."""
    lines: list[str] = []
    ident = ctx.identity
    lines.append(f"# Identity\nNaam: {ident['name']} — eigenaar: {ident['owner']}")
    lines.append(f"Philosophy: \"{ident['philosophy']}\"")
    lines.append(f"Origin: {ident['origin']}")

    lines.append("\n# Protocollen")
    for p in ctx.protocols:
        lines.append(f"- [{p['name']} v{p['version']}] {p['body']}")

    if ctx.quests:
        lines.append("\n# Actieve quests")
        for q in ctx.quests:
            lines.append(f"- {q['id']} · {q['title']} ({q['status']})")
            for st in q["steps"]:
                if st.get("body"):
                    lines.append(f"    - step-{st['order']}: {st['body']} [{st.get('status', 'open')}]")

    if ctx.skills:
        lines.append("\n# Skills")
        for sk in ctx.skills:
            lines.append(f"- {sk['name']}: {sk['description']} (trigger: {sk['trigger']})")

    if ctx.decisions:
        lines.append("\n# Recente beslissingen")
        for d in ctx.decisions:
            lines.append(f"- [{d['id']}] {d['content']}")

    if ctx.anti_patterns:
        lines.append("\n# Anti-patterns (bekende valkuilen)")
        for a in ctx.anti_patterns:
            lines.append(f"- [{a['id']}] {a['content']}")

    if ctx.soul:
        lines.append("\n# Soul (persoonlijkheidsmomenten)")
        for s in ctx.soul:
            lines.append(f"- {s['content']}")

    if ctx.insights:
        lines.append("\n# Inzichten (gedestilleerd door de evaluatiecirkel)")
        for i in ctx.insights:
            lines.append(f"- [{i['id']}] {i['content']}")

    if ctx.lessons:
        lines.append("\n# Lessen uit fouten")
        for m in ctx.lessons:
            les = f" → Les: {m['lesson']}" if m.get("lesson") else ""
            lines.append(f"- [{m['id']}] {m['content']}{les}")

    if ctx.recent_sessions:
        lines.append("\n# Recente sessies (waar het de laatste tijd over ging)")
        for s in ctx.recent_sessions:
            lines.append(f"- [{s['id']}] {s['summary']}")

    if ctx.relevant:
        lines.append("\n# Relevant voor deze sessie (vector match op eerste vraag)")
        for r in ctx.relevant:
            lines.append(f"- [{r['id']} · {r['type']} · score {r['score']}] {r['content']}")

    return "\n".join(lines)
