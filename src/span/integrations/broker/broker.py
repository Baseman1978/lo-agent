"""IntegrationBroker — de kern. Normaliseert de catalogus over providers en
voert acties uit onder LO's governance.

Uitvoer-pad:
- READ / approval='never'  -> direct via de adapter, dan audit.
- WRITE / approval vereist -> een Agent Inbox-item (origin=agent, owner=gebruiker);
  pas na goedkeuring voert `run_approved` het uit. Zo geldt dezelfde
  mens-in-de-lus als bij een gesprek.

De broker praat nooit rechtstreeks met een provider — dat doen de adapters.
"""

from __future__ import annotations

from typing import Any, Callable

from span.integrations.broker.adapters.base import Adapter
from span.integrations.broker.adapters.mcp import MCPAdapter
from span.integrations.broker.adapters.mock import MockAdapter
from span.integrations.broker.adapters.nango import NangoAdapter
from span.integrations.broker.adapters.native import NativeAdapter
from span.integrations.broker.connectors import (
    Action, Connector, connector_dict, get_action, get_connector,
    list_connectors, needs_approval,
)


class IntegrationBroker:
    def __init__(self, adapters: list[Adapter]) -> None:
        self._adapters: dict[str, Adapter] = {a.provider: a for a in adapters}

    def _adapter(self, connector: Connector) -> Adapter | None:
        return self._adapters.get(connector.provider)

    # -- catalogus ----------------------------------------------------------
    def catalog(self, ctx: Any = None, category: str | None = None,
                capability: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for c in list_connectors(category, capability, query):
            d = connector_dict(c)
            d.pop("actions", None)  # compact: acties via /actions
            a = self._adapter(c)
            d["connected"] = bool(a and a.is_connected(c, ctx))
            d["action_count"] = len(c.actions)
            out.append(d)
        return out

    def connector(self, cid: str, ctx: Any = None) -> dict[str, Any] | None:
        c = get_connector(cid)
        if c is None:
            return None
        d = connector_dict(c)
        a = self._adapter(c)
        d["connected"] = bool(a and a.is_connected(c, ctx))
        return d

    def actions(self, cid: str) -> list[dict[str, Any]] | None:
        c = get_connector(cid)
        if c is None:
            return None
        return [connector_dict(c)["actions"][i] for i in range(len(c.actions))]

    # -- uitvoeren ----------------------------------------------------------
    def preview(self, cid: str, aid: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        pair = get_action(cid, aid)
        if pair is None:
            return None
        c, a = pair
        return {"connector": c.id, "action": a.id, "risk": a.risk,
                "requires_approval": needs_approval(a),
                "summary": f"{a.name} · {c.name}"}

    def run(self, cid: str, aid: str, payload: dict[str, Any], ctx: Any, *,
            inbox: Any = None, owner: str = "", audit: Callable[[str, str], None] | None = None,
            dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
        pair = get_action(cid, aid)
        if pair is None:
            return {"error": "Onbekende connector of actie."}
        c, a = pair
        if needs_approval(a):
            if inbox is None:
                return {"error": "Deze actie vereist goedkeuring, maar er is geen Agent Inbox."}
            item_id = inbox.add(
                kind="action", action="integration_run",
                title=f"{a.name} · {c.name}",
                detail=f"{c.id}.{a.id}",
                payload={"connector": c.id, "action": a.id, "payload": payload or {}},
                urgency="normal", origin="agent", owner=owner,
            )
            return {"status": "requires_approval", "queued": item_id,
                    "note": "Wacht op goedkeuring in de Agent Inbox."}
        result = self._execute(c, a, payload or {}, ctx, dispatch)
        if audit is not None:
            audit(f"integration:{c.id}.{a.id}", a.name)
        return {"status": "succeeded", "result": result}

    def run_approved(self, payload: dict[str, Any], ctx: Any = None, *,
                     audit: Callable[[str, str], None] | None = None,
                     dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
        """Uitvoeren ná goedkeuring (aangeroepen vanuit execute_approval)."""
        pair = get_action(payload.get("connector", ""), payload.get("action", ""))
        if pair is None:
            return {"error": "Onbekende connector of actie."}
        c, a = pair
        result = self._execute(c, a, payload.get("payload") or {}, ctx, dispatch)
        if audit is not None:
            audit(f"integration:{c.id}.{a.id}", a.name)
        return {"status": "succeeded", "result": result}

    def _execute(self, c: Connector, a: Action, payload: dict[str, Any],
                 ctx: Any, dispatch: Callable[[str, dict[str, Any]], str] | None) -> Any:
        adapter = self._adapter(c)
        if adapter is None:
            return {"error": f"Geen adapter voor provider {c.provider!r}."}
        try:
            return adapter.run(c, a, payload, ctx, dispatch=dispatch)
        except NotImplementedError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # adapterfout -> nette melding, geen 500
            return {"error": f"{type(exc).__name__}: {exc}"}


def build_broker() -> IntegrationBroker:
    """Standaard-broker met de ingebouwde adapters. NangoAdapter activeert alleen
    als NANGO_HOST + NANGO_SECRET_KEY gezet zijn."""
    return IntegrationBroker([MockAdapter(), NativeAdapter(), MCPAdapter(), NangoAdapter()])
