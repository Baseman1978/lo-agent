"""Native-adapter: hergebruikt LO's bestaande, al-gegatete tools.

Een native-actie met `tool="..."` wordt uitgevoerd via LO's normale
ToolBox.dispatch — dus de bestaande risico-poort/Agent Inbox/egress/audit
gelden ongewijzigd. Zo worden bestaande integraties (Graph/Teams/Power BI)
zonder nieuwe attack surface als catalogus-acties beschikbaar.

Declaratieve HTTP-acties (base_url + method + path) volgen in een latere fase;
nu vereist een native-actie een bestaande tool-binding.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from span.integrations.broker.adapters.base import Adapter
from span.integrations.broker.connectors import Action, Connector


class NativeAdapter(Adapter):
    provider = "native"

    def is_connected(self, connector: Connector, ctx: Any) -> bool:
        # graph-auth leunt op de bestaande M365-login van deze gebruiker
        if connector.auth == "graph":
            return getattr(ctx, "o365", None) is not None
        # oauth2 (bv. Google) vereist een eigen koppeling die er nog niet is
        return False

    def run(self, connector: Connector, action: Action, payload: dict[str, Any],
            ctx: Any, dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> Any:
        if not action.tool:
            raise NotImplementedError(
                f"'{connector.id}.{action.id}' heeft nog geen tool-binding "
                "(declaratieve HTTP-acties komen in een latere fase).")
        if dispatch is None:
            raise RuntimeError("native-actie vereist een dispatch (tool-uitvoerder).")
        raw = dispatch(action.tool, payload or {})
        try:
            return json.loads(raw)
        except Exception:
            return raw
