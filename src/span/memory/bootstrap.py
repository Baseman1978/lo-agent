"""Bootstrap — de cirkel rond.

Volgende sessie haalt op wat vorige sessies leerden: identity, protocollen,
actieve quests, recente beslissingen en anti-patterns, plus fragmenten die
relevant zijn voor de eerste vraag. Zo dient eerder geschreven kennis
zichzelf aan voordat iemand erom vraagt.
"""

from __future__ import annotations

import os
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
    prev_conversation: list[dict[str, Any]] = field(default_factory=list)
    insights: list[dict[str, Any]] = field(default_factory=list)
    lessons: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)


# A4 quest-limiet: quests waren de enige onbegrensde promptcategorie die door
# normaal agent-gedrag hard groeit (protocollen en feedback zijn ook ongecapt
# maar gecureerd/zelf-dempend). De limiet zit op de LEESkant (quest_upsert
# valideert status niet en de schrijfkant is vrij): recentst-bijgewerkte
# quests eerst, en per quest een cap op de stappen.
QUEST_LIMIT = 6
QUEST_STEPS_LIMIT = 8


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


def degraded_enabled() -> bool:
    """SPAN_DEGRADED_MODE (default aan): mag de agent met een minimale context
    starten als het brein onbereikbaar is? 'off/0/false/no' = het oude gedrag
    (hard falen bij sessiestart)."""
    val = os.environ.get("SPAN_DEGRADED_MODE", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def degraded_bootstrap() -> BootstrapContext:
    """Minimale, eerlijke context voor brain-down: identiteit uit AGENT_NAME,
    verder leeg. De origin-regel meldt de degraded-toestand expliciet in de
    system prompt — geen stille fallback, geen verzonnen geheugen."""
    from span import AGENT_NAME
    return BootstrapContext(
        identity={
            "name": AGENT_NAME,
            "owner": "Bas Spaan",
            "philosophy": "Treat this graph as my brain, my memory, my intelligence.",
            "origin": (f"{AGENT_NAME} draait tijdelijk in degraded-mode: het brein "
                       "(Neo4j) is onbereikbaar. Geheugen, protocollen en quests "
                       "ontbreken deze sessie — zeg dat eerlijk als het relevant is."),
            "voice": None,
        },
        protocols=[], quests=[], decisions=[], anti_patterns=[], soul=[], skills=[],
    )


def _pick_formal(
    brain: BrainDB,
    fragments: FragmentStore,
    first_message: str | None,
    *,
    recency_query: str,
    index: str,
    build_row: Any,
    limit: int,
    pin: int = 2,
) -> list[dict[str, Any]]:
    """Kies formele kennis (Insight/Mistake) voor de bootstrap.

    Zonder first_message: pure recency (het oude gedrag). Mét first_message:
    rangschik op relevantie (cosine tegen de eerste vraag via vector_search op
    `index`), met de meest relevante bovenaan. De `pin` nieuwste items worden
    sowieso meegenomen (verse lessen mogen niet wegvallen), aangevuld tot
    `limit`. Fail-safe: elke fout (geen embedding, lege index, ...) valt terug
    op de recency-query, zodat load_bootstrap nooit harder faalt dan voorheen.
    """
    recency_rows = brain.run(recency_query)
    if not (first_message and first_message.strip()):
        return recency_rows
    try:
        embedding = fragments.embed(first_message)
        pinned = recency_rows[:pin]

        # relevantie-geordende kandidaten (hoogste cosine eerst), ontdubbeld
        relevant: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for hit in brain.vector_search(index, embedding, k=limit + pin + 4):
            row = build_row(hit.get("node") or {})
            if not row or row["id"] in seen:
                continue
            seen.add(row["id"])
            relevant.append(row)

        # de nieuwste `pin` die niet vanzelf al bij de relevante hits zitten,
        # reserveren we zodat ze gegarandeerd meekomen
        missing_pins = [p for p in pinned if p["id"] not in seen]
        head = relevant[: max(0, limit - len(missing_pins))]
        picked = (head + missing_pins)[:limit]
        return picked or recency_rows
    except Exception as exc:
        print(f"[bootstrap] relevantie-tak ({index}) mislukt, terug naar recency: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return recency_rows


def load_bootstrap(
    brain: BrainDB,
    fragments: FragmentStore,
    first_message: str | None = None,
    shared: BrainDB | None = None,
) -> BootstrapContext:
    identity_rows = brain.run(
        """
        MATCH (i:Identity)
        RETURN i.name AS name, i.philosophy AS philosophy,
               i.origin AS origin, i.owner AS owner, i.voice AS voice
        LIMIT 1
        """
    )
    if not identity_rows:
        raise RuntimeError("Geen Identity-node gevonden. Draai eerst: span init")

    protocols = brain.run(
        """
        MATCH (:Identity)-[:HAS_PROTOCOL]->(p:Protocol)
        RETURN p.name AS name, p.body AS body, p.version AS version
        ORDER BY p.name
        """
    )
    # gedeelde team-protocollen erbij (ontdubbeld op naam, privé wint)
    if shared is not None:
        try:
            shp = shared.run(
                "MATCH (p:Protocol) RETURN p.name AS name, p.body AS body, "
                "p.version AS version, true AS shared ORDER BY p.name")
            have = {p["name"] for p in protocols}
            protocols = protocols + [p for p in shp if p["name"] not in have]
        except Exception:
            pass

    quests = brain.run(
        """
        MATCH (q:Quest) WHERE q.status IN ['open', 'active']
        WITH q ORDER BY coalesce(q.updated, q.created) DESC LIMIT $quest_limit
        OPTIONAL MATCH (q)-[:HAS_STEP]->(st:QuestStep)
        WITH q, st ORDER BY st.order
        RETURN q.id AS id, q.title AS title, q.status AS status,
               collect({order: st.order, body: st.body, status: st.status}) AS steps
        ORDER BY q.id
        """,
        quest_limit=QUEST_LIMIT,
    )

    decisions = fragments.recent(k=8, mf_type="decision")
    anti_patterns = fragments.recent(k=8, mf_type="anti-pattern")
    soul = fragments.recent(k=6, mf_type="soul")

    skills = brain.run(
        """
        MATCH (sk:Skill) WHERE coalesce(sk.enabled, true)
        RETURN sk.name AS name, sk.description AS description,
               sk.trigger AS trigger, coalesce(sk.kind, 'workflow') AS kind,
               coalesce(sk.usage_count, 0) AS usage_count
        ORDER BY sk.usage_count DESC LIMIT 12
        """
    )
    # gedeelde team-skills erbij (ontdubbeld op naam, privé wint)
    if shared is not None:
        try:
            shs = shared.run(
                "MATCH (sk:Skill) WHERE coalesce(sk.enabled, true) "
                "RETURN sk.name AS name, sk.description AS description, "
                "sk.trigger AS trigger, coalesce(sk.kind, 'workflow') AS kind, "
                "coalesce(sk.usage_count, 0) AS usage_count, "
                "true AS shared ORDER BY sk.usage_count DESC LIMIT 10")
            have = {s["name"] for s in skills}
            skills = skills + [s for s in shs if s["name"] not in have]
        except Exception:
            pass

    # formele kennis uit de evaluatiecirkel — de leeskant van het leren.
    # Met een first_message kiezen we op RELEVANTIE (cosine tegen de vraag)
    # i.p.v. pure recency, zodat de nuttigste les bovenkomt i.p.v. de toevallig
    # nieuwste — dat scheelt statische context-bloat. Insights én Mistakes hebben
    # allebei een embedding (reflect._write_formal_node zet n.embedding; indexen
    # insight_embedding / mistake_embedding), dus beide kunnen relevantie-
    # gebaseerd. Zonder first_message blijft recency de fallback; elke fout in de
    # relevantie-tak valt óók terug op recency zodat load_bootstrap nooit harder
    # faalt dan voorheen. LIMITs blijven 8/6 zodat de prompt niet groeit.
    insights = _pick_formal(
        brain, fragments, first_message,
        recency_query="""
            MATCH (n:Insight) WHERE n.content IS NOT NULL
            RETURN n.id AS id, n.content AS content
            ORDER BY n.created DESC LIMIT 8
        """,
        index="insight_embedding",
        build_row=lambda n: (
            {"id": n.get("id"), "content": n.get("content")}
            if n.get("content") is not None else None),
        limit=8,
    )
    lessons = _pick_formal(
        brain, fragments, first_message,
        recency_query="""
            MATCH (n:Mistake) WHERE n.content IS NOT NULL
            RETURN n.id AS id, n.content AS content, coalesce(n.lesson, '') AS lesson
            ORDER BY n.created DESC LIMIT 6
        """,
        index="mistake_embedding",
        build_row=lambda n: (
            {"id": n.get("id"), "content": n.get("content"),
             "lesson": n.get("lesson") or ""}
            if n.get("content") is not None else None),
        limit=6,
    )

    # F4.4 acceptance-feedback: acties die Bas vaak afwijst -> Span wordt
    # voorzichtiger (alleen tonen bij een duidelijk patroon)
    from span.jarvis.feedback import feedback_summary
    feedback = [f for f in feedback_summary(brain)
                if f["reject_ratio"] >= 0.5 and (f["approved"] + f["rejected"]) >= 3]

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

    # continuïteit: de laatste ~4 woordelijke berichten van de MEEST RECENTE
    # sessie mét gesprekstekst, zodat LO de draad van het vorige gesprek oppakt.
    # Klein gehouden (max 4 regels, elk afgekapt) tegen prompt-bloat; leeg als er
    # nog geen Message-knopen zijn. Faalt zacht: een fout mag de bootstrap niet raken.
    prev_conversation: list[dict[str, Any]] = []
    try:
        recent_with_msgs = brain.run(
            """
            MATCH (s:Session)-[:HAS_MESSAGE]->(:Message)
            RETURN s.id AS id ORDER BY s.started DESC LIMIT 1
            """
        )
        if recent_with_msgs:
            tail = brain.run(
                """
                MATCH (:Session {id: $sid})-[:HAS_MESSAGE]->(m:Message)
                RETURN m.role AS role, m.text AS text, m.seq AS seq
                ORDER BY m.seq DESC LIMIT 4
                """,
                sid=recent_with_msgs[0]["id"],
            )
            prev_conversation = list(reversed(tail))  # oudste eerst, leesvolgorde
    except Exception as exc:
        print(f"[bootstrap] vorig gesprek ophalen mislukt: "
              f"{type(exc).__name__}: {exc}", flush=True)

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
        prev_conversation=prev_conversation,
        insights=insights,
        lessons=lessons,
        feedback=feedback,
    )


def render_bootstrap(ctx: BootstrapContext) -> str:
    """Maakt het bootstrap-blok voor de system prompt."""
    lines: list[str] = []
    ident = ctx.identity
    lines.append(f"# Identity\nNaam: {ident['name']} — eigenaar: {ident['owner']}")
    lines.append(f"Philosophy: \"{ident['philosophy']}\"")
    lines.append(f"Origin: {ident['origin']}")
    if ident.get("voice"):
        lines.append(f"Stem & toon: {ident['voice']}")

    lines.append("\n# Protocollen")
    for p in ctx.protocols:
        lines.append(f"- [{p['name']} v{p['version']}] {p['body']}")

    if ctx.quests:
        lines.append("\n# Actieve quests")
        for q in ctx.quests:
            lines.append(f"- {q['id']} · {q['title']} ({q['status']})")
            steps = [st for st in q["steps"] if st.get("body")]
            for st in steps[:QUEST_STEPS_LIMIT]:
                lines.append(f"    - step-{st['order']}: {st['body']} [{st.get('status', 'open')}]")
            if len(steps) > QUEST_STEPS_LIMIT:
                lines.append(f"    - … (+{len(steps) - QUEST_STEPS_LIMIT} "
                             "stappen verborgen — zie quest via brain_search)")

    if ctx.skills:
        lines.append("\n# Skills")
        lines.append("Herbruikbare werkwijzen en uitvoerbare macro's. Zet een macro in met "
                     "skill_use(name); een werkwijze volg je zelf met de gewone tools. "
                     "Komt een aanpak vaker terug? Leg 'm vast met skill_create.")
        for sk in ctx.skills:
            kind = sk.get("kind", "workflow")
            tag = "⚙ macro — skill_use" if kind == "macro" else "werkwijze"
            team = " [team]" if sk.get("shared") else ""
            trig = f" (trigger: {sk['trigger']})" if sk.get("trigger") else ""
            lines.append(f"- {sk['name']} · {tag}{team}: {sk['description']}{trig}")

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

    if ctx.feedback:
        lines.append("\n# Feedback-patroon (Bas wijst dit vaak af — wees voorzichtig)")
        for f in ctx.feedback:
            lines.append(f"- {f['type']}: {int(f['reject_ratio']*100)}% afgewezen "
                         f"({f['rejected']}/{f['approved'] + f['rejected']}) — "
                         "stel zo'n actie liever eerst voor i.p.v. uit te voeren")

    if ctx.recent_sessions:
        lines.append("\n# Recente sessies (waar het de laatste tijd over ging)")
        for s in ctx.recent_sessions:
            lines.append(f"- [{s['id']}] {s['summary']}")

    if ctx.prev_conversation:
        lines.append("\n# Vorig gesprek (kort)")
        for m in ctx.prev_conversation:
            wie = ident["owner"] if m.get("role") == "user" else ident["name"]
            lines.append(f"- {wie}: {(m.get('text') or '')[:160]}")

    if ctx.relevant:
        lines.append("\n# Relevant voor deze sessie (vector match op eerste vraag)")
        for r in ctx.relevant:
            lines.append(f"- [{r['id']} · {r['type']} · score {r['score']}] {r['content']}")

    return "\n".join(lines)
