# src/span/db/health.py
"""A4 — geheugen-onderhoud: gezondheid van het brein (indexen + latency).

Klein en zelfstandig: alleen leesqueries. Gebruikt door het owner-endpoint
GET /api/brain/health en de nachtelijke scheduler-taak. Een index die niet
ONLINE is (POPULATING/FAILED) laat db.index.vector.queryNodes hard falen —
dat willen we 's nachts of in het dashboard zien, niet middenin een gesprek.
Fouten propageren naar de aanroeper; die vangt zelf (endpoint/run_task).
"""
from __future__ import annotations

import time
from typing import Any

from span.db.brain import BrainDB
from span.db.schema import FORMAL_VECTOR_INDEXES, RANGE_INDEXES

# Alle indexen die init_schema hoort aan te maken, op naam zoals SHOW INDEXES
# ze toont. De entity_name-constraint staat hier bewust NIET in: die is
# fail-soft optioneel (Task 2) en zou anders elke nacht ruis melden zolang de
# migratie op een brein nog niet gelukt is.
EXPECTED_INDEXES: list[str] = (
    ["mf_embedding", "message_embedding", "message_session"]
    + [name for name, _label in FORMAL_VECTOR_INDEXES]
    + [name for name, _cypher in RANGE_INDEXES]
)


def index_health(brain: BrainDB) -> dict[str, Any]:
    """Vergelijk SHOW INDEXES met de verwachte set (werkt op Neo4j 5 community)."""
    rows = brain.run(
        "SHOW INDEXES YIELD name, state, type, populationPercent "
        "RETURN name, state, type, populationPercent"
    )
    by_name = {r["name"]: r for r in rows}
    missing = sorted(n for n in EXPECTED_INDEXES if n not in by_name)
    not_online = sorted(
        n for n in EXPECTED_INDEXES
        if n in by_name and by_name[n].get("state") != "ONLINE"
    )
    return {
        "ok": not missing and not not_online,
        "missing": missing,
        "not_online": not_online,
        "count": len(rows),
    }


def brain_latency_ms(brain: BrainDB) -> float:
    """Eén lichte probe-query, in milliseconden."""
    t0 = time.perf_counter()
    brain.run("RETURN 1 AS ok")
    return round((time.perf_counter() - t0) * 1000.0, 1)
