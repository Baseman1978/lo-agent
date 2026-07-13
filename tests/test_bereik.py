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


def test_wav_naar_ogg_opus():
    """WAV (PCM16, 22.05 kHz zoals Piper) -> OGG/Opus-bytes voor sendVoice."""
    pytest.importorskip("av")  # PyAV zit in het image; lokaal evt. niet
    from span.integrations.audio import wav_to_ogg_opus
    from span.server.tts import _wav
    pcm = b"\x00\x00" * 22050          # 1 seconde stilte, 16-bit mono
    ogg = wav_to_ogg_opus(_wav(pcm, 22050))
    assert ogg[:4] == b"OggS"
    assert len(ogg) > 100


class TestTelegramVoice:
    """Telegram voice-uit: sendVoice met OGG/Opus, altijd met tekst-fallback."""

    def _bridge(self):
        from span.integrations.telegram import TelegramBridge
        brain = MagicMock()
        # __init__ leest telegram_chat_id ('cid') en last_tg_daily ('d')
        brain.run.return_value = [{"cid": "123", "d": ""}]
        return TelegramBridge("tok", {"brain": brain})

    def test_send_voice_stuurt_ogg_multipart(self, monkeypatch):
        import span.integrations.telegram as tgmod
        import span.integrations.audio as audiomod
        import span.server.tts as tts
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        monkeypatch.setattr(tts, "available", lambda: True)
        monkeypatch.setattr(tts, "synthesize", lambda text, **kw: b"RIFF....WAVE")
        monkeypatch.setattr(audiomod, "wav_to_ogg_opus",
                            lambda wav, bitrate=32_000: b"OggSfake")
        posted = {}

        def fake_post(url, **kw):
            posted["url"] = url
            posted.update(kw)
            resp = MagicMock()
            resp.ok = True
            return resp

        monkeypatch.setattr(tgmod.requests, "post", fake_post)
        bridge = self._bridge()
        assert bridge.send_voice("hoi bas") is True
        assert posted["url"].endswith("/sendVoice")
        assert posted["data"] == {"chat_id": "123"}
        assert posted["files"]["voice"][1] == b"OggSfake"

    def test_send_voice_faalt_zacht_zonder_tts(self, monkeypatch):
        import span.server.tts as tts
        monkeypatch.setattr(tts, "available", lambda: False)
        bridge = self._bridge()
        assert bridge.send_voice("hoi") is False   # aanroeper valt terug op tekst

    def test_send_voice_weigert_lange_teksten(self, monkeypatch):
        import span.server.tts as tts
        monkeypatch.setattr(tts, "available", lambda: True)
        bridge = self._bridge()
        assert bridge.send_voice("x" * 1000) is False  # lang antwoord leest beter als tekst
