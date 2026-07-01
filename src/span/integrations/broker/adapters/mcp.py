"""MCP-adapter (stub in Fase 1).

LO heeft al een volwaardige MCP-client + OAuth-login (mcp_client.py /
mcp_oauth.py). In Fase 3 unificeren we die: een 'mcp'-connector koppelen =
z'n mcp_url als MCP-server registreren + inloggen, en acties = de tools van
die server. Voor nu markeert deze adapter zulke connectors als 'nog koppelen'.
"""

from __future__ import annotations

from typing import Any, Callable

from span.integrations.broker.adapters.base import Adapter
from span.integrations.broker.connectors import Action, Connector


class MCPAdapter(Adapter):
    provider = "mcp"

    def is_connected(self, connector: Connector, ctx: Any) -> bool:
        return False  # Fase 3: check tegen de gekoppelde MCP-servers

    def run(self, connector: Connector, action: Action, payload: dict[str, Any],
            ctx: Any, dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> Any:
        raise NotImplementedError(
            f"'{connector.name}' loopt via MCP — koppelen komt in Fase 3 "
            "(Instellingen → MCP-servers). Deze catalogus toont 'm alvast.")
