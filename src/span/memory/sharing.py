"""Delen van kennis naar het gedeelde brein (WP-3, schrijfkant).

Kopieert één knoop (incl. embedding, zodat vector-search werkt) van het privé-
brein naar `brain-shared`, met herkomst (`shared_by`/`shared_at`). Idempotent:
opnieuw delen = bijwerken. `unshare` verwijdert de gedeelde kopie weer.

Alleen bewust deelbare typen mogen mee; ruwe persoonlijke fragmenten blijven
privé tenzij iemand ze expliciet deelt.
"""

from __future__ import annotations

from typing import Any

from span.db.brain import BrainDB

# Bewust deelbare labels (geen automatische lek van privé-context).
SHAREABLE = {"Insight", "Mistake", "Idea", "Skill", "Protocol", "MemoryFragment"}


def share_node(private: BrainDB, shared: BrainDB, node_id: str,
               shared_by: str = "") -> dict[str, Any]:
    """Kopieer een knoop van privé naar het gedeelde brein. Idempotent."""
    rows = private.run(
        "MATCH (n {id:$id}) RETURN labels(n) AS labels, properties(n) AS props LIMIT 1",
        id=node_id,
    )
    if not rows:
        raise ValueError("Knoop niet gevonden in je eigen brein.")
    labels = rows[0]["labels"] or []
    label = next((lb for lb in labels if lb in SHAREABLE), None)
    if label is None:
        raise ValueError(f"Dit type is niet deelbaar ({', '.join(labels) or 'onbekend'}).")
    props = dict(rows[0]["props"] or {})
    props["shared_by"] = shared_by
    props["origin_id"] = node_id
    # label komt uit de gevalideerde SHAREABLE-set -> veilig in de query
    shared.run(
        f"MERGE (n:`{label}` {{id:$id}}) SET n += $props, n.shared_at = datetime()",
        id=node_id, props=props,
    )
    return {"id": node_id, "label": label, "shared_by": shared_by}


def unshare_node(shared: BrainDB, node_id: str) -> dict[str, Any]:
    """Verwijder de gedeelde kopie van een knoop."""
    shared.run("MATCH (n {id:$id}) DETACH DELETE n", id=node_id)
    return {"id": node_id, "unshared": True}
