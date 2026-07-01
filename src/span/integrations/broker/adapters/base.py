"""Eén intern adapter-contract. Providers wisselbaar zonder de rest te raken.

Een adapter vertaalt het interne connector/action-model naar een concrete
provider (mock, native/LO-tools, MCP-server, Nango). De broker roept alleen
deze methodes aan — nooit de rest van de app.
"""

from __future__ import annotations

from typing import Any, Callable

from span.integrations.broker.connectors import Action, Connector


class Adapter:
    provider: str = ""

    def is_connected(self, connector: Connector, ctx: Any) -> bool:
        """Is deze connector voor deze gebruiker bruikbaar (ingelogd/geconfigureerd)?"""
        return False

    def connect_url(self, connector: Connector, ctx: Any, redirect_uri: str) -> str:
        """Start-URL voor de koppel-/login-flow (leeg = n.v.t. voor deze provider)."""
        return ""

    def run(self, connector: Connector, action: Action, payload: dict[str, Any],
            ctx: Any, dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> Any:
        """Voer de actie uit en geef een (klein, gesaneerd) resultaat terug."""
        raise NotImplementedError(f"{connector.provider}-adapter kan '{action.id}' nog niet uitvoeren.")
