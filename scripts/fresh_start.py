"""Schoon profiel: brein wissen, schema vers, deelnemer-filter zetten,
volledige Fireflies-import inplannen. Draaien IN de container, nadat er
een backup gemaakt is."""

from span.config import load_settings
from span.db.brain import BrainDB
from span.db.schema import init_schema
from span.jarvis.crons import create_cron

settings = load_settings()
brain = BrainDB(settings)

before = brain.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
print("nodes voor wipe:", before)

brain.run("MATCH (n) DETACH DELETE n")
print("na wipe:", brain.run("MATCH (n) RETURN count(n) AS n")[0]["n"], "nodes")

for line in init_schema(brain, settings):
    print("+", line)

brain.run("MERGE (c:Config {id:'runtime'}) SET c.ff_filter = $f",
          f="b.spaan@lomans.nl")
print("+ ff_filter: alleen meetings met b.spaan@lomans.nl")

out = create_cron(
    brain,
    "Voer een volledige Fireflies-import uit (fireflies_sync met deep=true) "
    "en meld hoeveel meetings en actiepunten er verwerkt zijn.",
    "06:30", "once", run_date="2026-06-13", mode="execute",
)
print("+ cron:", out["id"], "- morgen 06:30 volledige import")
brain.close()
