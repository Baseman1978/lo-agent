"""Integraties met externe diensten (O365 via Microsoft Graph, Asana,
Fireflies). Eén bouwfunctie, één configuratiebron (Settings.jarvis).

Elke integratie is optioneel: zonder configuratie bestaat de client
gewoon niet en verschijnen de bijbehorende tools niet in de toolbox.
Telegram zit niet hier omdat de bridge runtime-state nodig heeft; die
leest zijn token wél uit dezelfde Settings.jarvis (zie server/app.py).
"""

from __future__ import annotations

from pathlib import Path

from span.config import Settings


def build_integrations(settings: Settings):
    """Maak (o365, asana, fireflies) clients op basis van de configuratie.
    Elk element is None wanneer die integratie niet geconfigureerd is."""
    from span.integrations.asana import AsanaClient
    from span.integrations.fireflies import FirefliesClient
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
    fireflies = None
    if settings.jarvis.fireflies_enabled:
        fireflies = FirefliesClient(settings.jarvis.fireflies_api_key)
    return o365, asana, fireflies
