"""Mock-adapter: geen externe koppeling. Voor de catalogus-UI zonder keys en
voor tests van de approval->uitvoer-lus."""

from __future__ import annotations

from typing import Any, Callable

from span.integrations.broker.adapters.base import Adapter
from span.integrations.broker.connectors import Action, Connector


class MockAdapter(Adapter):
    provider = "mock"

    def is_connected(self, connector: Connector, ctx: Any) -> bool:
        return True  # altijd 'gekoppeld' — het is een demo

    def run(self, connector: Connector, action: Action, payload: dict[str, Any],
            ctx: Any, dispatch: Callable[[str, dict[str, Any]], str] | None = None) -> Any:
        if action.id == "echo":
            return {"echo": payload.get("text", "")}
        if action.id == "create_note":
            return {"created": True, "title": payload.get("title", ""),
                    "note": "mock — er is niets echt aangemaakt"}
        return {"ran": action.id, "payload": payload}
