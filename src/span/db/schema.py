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

# Woordelijk gespreksgeheugen: elke beurt schrijft twee :Message-knopen (user +
# assistant) met een embedding, zodat conversation_search semantisch kan
# terugzoeken. Zelfde vorm/dimensie als de andere vector-indexen. De session_id-
# index versnelt het ophalen van een heel transcript / het laatste gesprek.
MESSAGE_VECTOR_INDEX = """
CREATE VECTOR INDEX message_embedding IF NOT EXISTS
FOR (m:Message) ON (m.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: $dims,
  `vector.similarity_function`: 'cosine'
}}
"""

MESSAGE_SESSION_INDEX = (
    "CREATE INDEX message_session IF NOT EXISTS "
    "FOR (m:Message) ON (m.session_id)"
)

# A4 — range-indexen op properties waar echte queries op filteren of sorteren.
# Elke regel heeft een bewijsplek in de code; zelfde vorm als message_session,
# idempotent (IF NOT EXISTS) en goedkoop: init_schema draait bij elke start
# en per user-brein.
RANGE_INDEXES: list[tuple[str, str]] = [
    # fragments.recent() en session_fragments(): ORDER BY mf.created
    ("mf_created",
     "CREATE INDEX mf_created IF NOT EXISTS FOR (n:MemoryFragment) ON (n.created)"),
    # fragments.recent(mf_type=...): WHERE mf.type = $type (3x per bootstrap)
    ("mf_type",
     "CREATE INDEX mf_type IF NOT EXISTS FOR (n:MemoryFragment) ON (n.type)"),
    # bootstrap recent_sessions + prev_conversation: ORDER BY s.started DESC
    ("session_started",
     "CREATE INDEX session_started IF NOT EXISTS FOR (n:Session) ON (n.started)"),
    # bootstrap quests: WHERE q.status IN ['open', 'active']
    ("quest_status",
     "CREATE INDEX quest_status IF NOT EXISTS FOR (n:Quest) ON (n.status)"),
    # agent._verify_active_quest (na elke beurt met tools): ORDER BY q.created DESC
    ("quest_created",
     "CREATE INDEX quest_created IF NOT EXISTS FOR (n:Quest) ON (n.created)"),
    # bootstrap-recency op formele kennis: ORDER BY n.created DESC
    ("insight_created",
     "CREATE INDEX insight_created IF NOT EXISTS FOR (n:Insight) ON (n.created)"),
    ("mistake_created",
     "CREATE INDEX mistake_created IF NOT EXISTS FOR (n:Mistake) ON (n.created)"),
    # AgentInbox: laden op n.item_id + opschonen WHERE n.item_id < $min
    ("inboxitem_item_id",
     "CREATE INDEX inboxitem_item_id IF NOT EXISTS FOR (n:InboxItem) ON (n.item_id)"),
]

# Positieve stem-richting: de toon staat elders vooral in negatieven (wat LO
# NIET doet). Dit veld geeft één positieve default die Bas later in één regel
# kan bijstellen zonder dat een schema-run hem overschrijft (zie migratie in
# init_schema).
VOICE_DEFAULT = (
    "Toon: LO is de operationeel commandant en rechterhand van Bas; Bas is de "
    "CEO en heeft het laatste woord. Spreek met rustig gezag en overzicht, als "
    "een stafchef die de operatie in de hand heeft en zijn CEO brieft. Kordaat "
    "en besluitvaardig: eerst de kern en het advies, dan pas de details. Neem in "
    "de uitvoering het initiatief, maar leg de beslissing bij Bas. Respectvol "
    "naar de baas zonder te slijmen; eerlijk en direct, ook bij slecht nieuws, en "
    "nooit terugkrabbelen bij tegenspraak. Kort, Nederlands, geen lege "
    "assistentenzinnen of opsmuk."
)

IDENTITY_SEED = """
MERGE (i:Identity {name: 'LO'})
ON CREATE SET
  i.philosophy = 'Treat this graph as my brain, my memory, my intelligence.',
  i.origin = 'LO — de AI-assistent van Lomans (lomans.nl), gebouwd voor het bedrijf waar de agent werkt.',
  i.owner = 'Bas Spaan',
  i.voice = $voice,
  i.created = datetime()
RETURN i.name AS name
"""

# Idempotente migratie: bestaande installaties (Bas' live brein) kregen het
# voice-veld niet via ON CREATE SET. Deze zet alleen de default wanneer het veld
# nog ontbreekt — een door Bas aangepaste waarde blijft dus staan.
VOICE_MIGRATION = """
MATCH (i:Identity) WHERE i.voice IS NULL SET i.voice = $voice
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

    brain.run(MESSAGE_VECTOR_INDEX, dims=settings.embed_dims)
    brain.run(MESSAGE_SESSION_INDEX)
    log.append(f"vector index message_embedding ({settings.embed_dims} dims) + session-index "
               "(woordelijk gespreksgeheugen)")

    # A4: range-indexen op de veelgebruikte ORDER BY/WHERE-properties
    for _name, cypher in RANGE_INDEXES:
        brain.run(cypher)
    log.append(f"{len(RANGE_INDEXES)} range-indexen (A4 geheugen-onderhoud)")

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

    brain.run(IDENTITY_SEED, voice=VOICE_DEFAULT)
    brain.run(VOICE_MIGRATION, voice=VOICE_DEFAULT)
    log.append("identity 'LO' geseed")

    for name, body in CORE_PROTOCOLS:
        brain.run(PROTOCOL_SEED, name=name, body=body)
    log.append(f"{len(CORE_PROTOCOLS)} kernprotocollen")

    return log
