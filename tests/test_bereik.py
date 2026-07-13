"""A5 — bereik + duurzaamheid: QR/HTTPS, iOS-STT, Telegram-voice."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_netinfo_geeft_public_url(monkeypatch):
    """De QR-flow heeft een HTTPS-bron nodig: SPAN_PUBLIC_URL via netinfo."""
    import span.server.routes as routes
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setenv("SPAN_PUBLIC_URL", "https://nova.famspaan.nl/")
    monkeypatch.setenv("SPAN_LAN_HOST", "192.168.2.10")
    out = asyncio.run(routes.netinfo(MagicMock()))
    assert out["public_url"] == "https://nova.famspaan.nl"  # slash gestript
    assert out["lan_ip"] == "192.168.2.10"
    assert out["hint"] == ""


def test_netinfo_zonder_public_url_houdt_lan_fallback(monkeypatch):
    """In Docker zonder SPAN_PUBLIC_URL: leeg lan_ip + de bestaande hint."""
    import span.server.routes as routes
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.delenv("SPAN_PUBLIC_URL", raising=False)
    monkeypatch.setenv("SPAN_LAN_HOST", "172.17.0.2")  # container-adres
    out = asyncio.run(routes.netinfo(MagicMock()))
    assert out["public_url"] == ""
    assert out["lan_ip"] == ""          # container-IP is niet bruikbaar
    assert "ipconfig" in out["hint"]
