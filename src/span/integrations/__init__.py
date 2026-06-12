"""Integraties met externe diensten (O365 via Microsoft Graph, Asana).

Elke integratie is optioneel: zonder configuratie bestaat de client
gewoon niet en verschijnen de bijbehorende tools niet in de toolbox.
"""

from __future__ import annotations

from pathlib import Path

from span.config import Settings


def build_integrations(settings: Settings):
    """Maak (o365, asana) clients op basis van de configuratie — of (None, None)."""
    from span.integrations.asana import AsanaClient
    from span.integrations.o365 import O365Client

    o365 = None
    if settings.jarvis.o365_enabled:
        o365 = O365Client(
            client_id=settings.jarvis.ms_client_id,
            tenant_id=settings.jarvis.ms_tenant_id,
            cache_path=Path.home() / ".span" / "msal_cache.json",
        )
    asana = None
    if settings.jarvis.asana_enabled:
        asana = AsanaClient(
            token=settings.jarvis.asana_token,
            workspace_gid=settings.jarvis.asana_workspace,
        )
    return o365, asana
