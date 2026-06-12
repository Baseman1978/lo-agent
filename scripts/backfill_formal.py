"""Eenmalige migratie: formele kennis doorzoekbaar maken (audit-fix 1).

- Oude Insights met title/body krijgen een genormaliseerd content-veld + id.
- Insight/Mistake/Idea-nodes zonder embedding krijgen er één,
  zodat brain_search en bootstrap ze vinden.
Idempotent: draait alleen op nodes die nog niet gemigreerd zijn.
"""
import time

from span.config import load_settings
from span.db.brain import BrainDB
from span.llm.client import LLMClient

settings = load_settings()
brain = BrainDB(settings)
llm = LLMClient(settings)

# 1. title/body → content (+ id) voor oude weekreview/consolidatie-Insights
rows = brain.run(
    "MATCH (n:Insight) WHERE n.content IS NULL AND n.title IS NOT NULL "
    "RETURN elementId(n) AS eid, n.title AS title, coalesce(n.body,'') AS body"
)
for row in rows:
    content = f"{row['title']}: {row['body']}".strip(": ")
    brain.run(
        "MATCH (n:Insight) WHERE elementId(n) = $eid "
        "SET n.content = $content, n.id = coalesce(n.id, $id)",
        eid=row["eid"], content=content,
        id=f"insight-{int(time.time() * 1000) % 10000000}",
    )
    time.sleep(0.002)  # unieke ids
print(f"genormaliseerd: {len(rows)} oude Insights")

# 2. embeddings voor alle formele nodes die er nog geen hebben
total = 0
for label in ("Insight", "Mistake", "Idea"):
    nodes = brain.run(
        f"MATCH (n:{label}) WHERE n.embedding IS NULL AND n.content IS NOT NULL "
        "RETURN elementId(n) AS eid, n.content AS content, "
        "coalesce(n.lesson,'') AS lesson"
    )
    for node in nodes:
        text = f"{label}: {node['content']}"
        if node["lesson"]:
            text += f"\nLes: {node['lesson']}"
        emb = llm.embed_one(text)
        brain.run(
            f"MATCH (n:{label}) WHERE elementId(n) = $eid SET n.embedding = $emb",
            eid=node["eid"], emb=emb,
        )
        total += 1
    print(f"{label}: {len(nodes)} embeddings toegevoegd")
print(f"klaar — {total} formele nodes doorzoekbaar gemaakt")
brain.close()
