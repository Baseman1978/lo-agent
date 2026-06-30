"""Schema en seed voor het brein.

Het schema is bewust evolueerbaar: de evaluatiecirkel mag types en relaties
voorstellen (schema-evolve). Dit bestand legt alleen de vaste kern vast.
"""

from __future__ import annotations

from span.config import Settings
from span.db.brain import BrainDB

CONSTRAINTS = [
    "CREATE CONSTRAINT identity_name IF NOT EXISTS FOR (n:Identity) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT mf_id IF NOT EXISTS FOR (n:MemoryFragment) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT session_id IF NOT EXISTS FOR (n:Session) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT protocol_name IF NOT EXISTS FOR (n:Protocol) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (n:Skill) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT quest_id IF NOT EXISTS FOR (n:Quest) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT insight_id IF NOT EXISTS FOR (n:Insight) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT mistake_id IF NOT EXISTS FOR (n:Mistake) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT idea_id IF NOT EXISTS FOR (n:Idea) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT task_id IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE",
    # M19: gearchiveerde mail is idempotent — één fragment per Graph-mail-id.
    # (Neo4j-uniqueness geldt alleen voor nodes mét de property, dus gewone
    # fragmenten zonder mail_graph_id raken niet beperkt.)
    "CREATE CONSTRAINT mf_mail_graph_id IF NOT EXISTS FOR (n:MemoryFragment) REQUIRE n.mail_graph_id IS UNIQUE",
]

VECTOR_INDEX = """
CREATE VECTOR INDEX mf_embedding IF NOT EXISTS
FOR (mf:MemoryFragment) ON (mf.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: $dims,
  `vector.similarity_function`: 'cosine'
}}
"""

# Formele kennis (cirkel-leeskant): ook Insight/Mistake/Idea zijn doorzoekbaar.
FORMAL_VECTOR_INDEXES = [
    ("insight_embedding", "Insight"),
    ("mistake_embedding", "Mistake"),
    ("idea_embedding", "Idea"),
]

FORMAL_VECTOR_INDEX_TEMPLATE = """
CREATE VECTOR INDEX {index_name} IF NOT EXISTS
FOR (n:{label}) ON (n.embedding)
OPTIONS {{indexConfig: {{
  `vector.dimensions`: $dims,
  `vector.similarity_function`: 'cosine'
}}}}
"""

IDENTITY_SEED = """
MERGE (i:Identity {name: 'LO'})
ON CREATE SET
  i.philosophy = 'Treat this graph as my brain, my memory, my intelligence.',
  i.origin = 'LO — de AI-assistent van Lomans (lomans.nl), gebouwd voor het bedrijf waar de agent werkt.',
  i.owner = 'Bas Spaan',
  i.created = datetime()
RETURN i.name AS name
"""

# Kernprotocollen — de werkwijze die de agent bij elke bootstrap meekrijgt.
CORE_PROTOCOLS: list[tuple[str, str]] = [
    (
        "bootstrap",
        "Bij sessiestart: laad Identity, kernprotocollen, actieve Quests, recente "
        "decisions en anti-patterns, plus MemoryFragments die relevant zijn voor de "
        "eerste vraag. Verwijs naar opgehaalde kennis met het MF-id.",
    ),
    (
        "continuous-recording",
        "Schrijf tijdens het werk continu kleine observaties weg als MemoryFragment: "
        "wat besproken, besloten, ontdekt. Kies het type zelf per moment "
        "(interaction-log, decision, anti-pattern, reflection, soul, observation). "
        "De vorm is licht; liever vaak en klein dan zelden en groot.",
    ),
    (
        "evaluation",
        "Bij sessie-einde: evalueer de MemoryFragments van de sessie en destilleer "
        "formele knopen — Insight, Mistake, Idea, Quest. Bij herhaling van een "
        "patroon: formaliseer tot Skill. Bij een schema-gat: stel een uitbreiding "
        "voor als Idea met kind 'schema'.",
    ),
    (
        "write-scope",
        "Schrijf uitsluitend in het eigen brein (span-brain). Productiedata is "
        "alleen-lezen, zonder uitzondering. De audit-trail blijft daarmee helder en "
        "het risico op corruptie is weg.",
    ),
    (
        "proactive-memory",
        "Pin open eindjes vast voordat ze fouten worden: signaleer je een "
        "ontbrekende deliverable of een bekende valkuil, koppel die observatie dan "
        "direct aan de relevante Quest-stap of als anti-pattern.",
    ),
    (
        "grounding",
        "Antwoorden zijn actueel, gegrond en traceerbaar: haal eerst relevante "
        "kennis uit de graph op (RAG) en herleid elke stellige uitspraak naar de "
        "knoop waaruit die kwam. Weet je iets niet uit de graph, zeg dat.",
    ),
]

PROTOCOL_SEED = """
MERGE (p:Protocol {name: $name})
ON CREATE SET p.body = $body, p.version = 1, p.created = datetime()
WITH p
MATCH (i:Identity)
MERGE (i)-[:HAS_PROTOCOL]->(p)
"""


def init_schema(brain: BrainDB, settings: Settings) -> list[str]:
    """Maakt database, constraints, vector index en seeds aan. Idempotent."""
    log: list[str] = []
    brain.ensure_database()
    log.append(f"database '{brain.database}' beschikbaar")

    for constraint in CONSTRAINTS:
        brain.run(constraint)
    log.append(f"{len(CONSTRAINTS)} constraints")

    brain.run(VECTOR_INDEX, dims=settings.embed_dims)
    log.append(f"vector index mf_embedding ({settings.embed_dims} dims, cosine)")

    for index_name, label in FORMAL_VECTOR_INDEXES:
        brain.run(FORMAL_VECTOR_INDEX_TEMPLATE.format(index_name=index_name, label=label),
                  dims=settings.embed_dims)
    log.append(f"{len(FORMAL_VECTOR_INDEXES)} formele vector indexen (Insight/Mistake/Idea)")

    # embedding-drift: een ander model of andere dims maakt bestaande vectors
    # stil onbruikbaar — dan liever hard falen met een duidelijke melding
    rows = brain.run(
        "MATCH (c:Config {id:'runtime'}) "
        "RETURN c.embed_model AS model, c.embed_dims AS dims"
    )
    stored = rows[0] if rows else {}
    if stored.get("model") and (
        stored["model"] != settings.embed_model
        or stored.get("dims") != settings.embed_dims
    ):
        raise RuntimeError(
            f"Embedding-config wijzigde: brein is gebouwd met "
            f"{stored['model']}/{stored.get('dims')} dims, .env zegt nu "
            f"{settings.embed_model}/{settings.embed_dims}. Bestaande vectors "
            "worden dan onvergelijkbaar. Zet de oude waarden terug, of "
            "her-embed alles bewust (scripts/backfill_formal.py als voorbeeld) "
            "en werk daarna de Config-node bij."
        )
    brain.run(
        "MERGE (c:Config {id:'runtime'}) "
        "SET c.embed_model = $model, c.embed_dims = $dims",
        model=settings.embed_model, dims=settings.embed_dims,
    )
    log.append(f"embedding-config vastgelegd ({settings.embed_model}, {settings.embed_dims} dims)")

    brain.run(IDENTITY_SEED)
    log.append("identity 'LO' geseed")

    for name, body in CORE_PROTOCOLS:
        brain.run(PROTOCOL_SEED, name=name, body=body)
    log.append(f"{len(CORE_PROTOCOLS)} kernprotocollen")

    return log
