"""API-sleutels van integraties, in de UI ingevoerd en in het brein bewaard.

Zodat een koppeling als Asana/Fireflies via een API-token gekoppeld kan worden
zonder .env-bewerking of herstart. Opgeslagen op de Config-node (zoals de
MCP-tokens). De sleutel wordt NOOIT terug naar de frontend gestuurd — alleen of
hij gezet is.

Let op (bewuste keuze, consistent met de MCP-tokenopslag): de sleutels staan
platt in het (vertrouwde) brein. Encryptie-at-rest is een mogelijke verharding.
"""

from __future__ import annotations

import json
from typing import Any


def load_keys(brain: Any) -> dict[str, str]:
    try:
        rows = brain.run("MATCH (c:Config {id:'runtime'}) RETURN c.integration_keys AS k")
        raw = rows[0].get("k") if rows else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def save_key(brain: Any, name: str, secret: str) -> None:
    keys = load_keys(brain)
    keys[name] = secret
    brain.run("MERGE (c:Config {id:'runtime'}) SET c.integration_keys = $k",
              k=json.dumps(keys))


def delete_key(brain: Any, name: str) -> None:
    keys = load_keys(brain)
    keys.pop(name, None)
    brain.run("MERGE (c:Config {id:'runtime'}) SET c.integration_keys = $k",
              k=json.dumps(keys))


def get_key(brain: Any, name: str) -> str:
    return (load_keys(brain).get(name) or "").strip()


def has_key(brain: Any, name: str) -> bool:
    return bool(get_key(brain, name))
