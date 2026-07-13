"""A5 — bereik + duurzaamheid: QR/HTTPS, iOS-STT, Telegram-voice."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


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


def _stt_request(payload: bytes):
    req = MagicMock()

    async def _body():
        return payload
    req.body = _body
    return req


def test_stt_accepteert_ios_mp4(monkeypatch):
    """iOS Safari MediaRecorder levert audio/mp4 ('ftyp' op offset 4)."""
    import span.server.routes as routes
    import span.server.stt as stt
    monkeypatch.setenv("SPAN_TELEMETRY", "off")   # geen jsonl-schrijfsel in tests
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe", lambda audio, language="nl": "hallo vanaf de iphone")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000
    out = asyncio.run(routes.speech_to_text(_stt_request(mp4)))
    assert out["text"] == "hallo vanaf de iphone"


def test_stt_weigert_onbekend_formaat_nog_steeds(monkeypatch):
    """De allowlist blijft dicht voor niet-audio (M12)."""
    from fastapi import HTTPException
    import span.server.routes as routes
    import span.server.stt as stt
    monkeypatch.setenv("SPAN_TELEMETRY", "off")
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(stt, "available", lambda: True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.speech_to_text(_stt_request(b"\x00" * 2000)))
    assert exc.value.status_code == 415
