"""A2 — ElevenLabs-WS-streaming: protocol, barge-in, prewarm en routes.
Alles netwerkloos: de WS-verbinding en de synth-functies worden gemockt."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from span.server import tts
from span.server import tts_stream_eleven as tse


class FakeWS:
    """Nagebootste websocket: onthoudt verzonden frames, levert audio-frames."""

    def __init__(self, frames):
        self._frames = frames
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self._frames:
            yield json.dumps(f)

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # env is al bij import gelezen -> module-attrs patchen (patroon test_tts_cloud)
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-test")
    monkeypatch.setattr(tts, "ELEVEN_VOICE", "voice123")
    monkeypatch.setattr(tts, "ELEVEN_MODEL", "eleven_multilingual_v2")
    monkeypatch.setattr(tts, "_eleven_voices", {})
    monkeypatch.setattr(tts, "XTTS_URL", "")
    monkeypatch.delenv("SPAN_TTS_STREAMING", raising=False)
    monkeypatch.setenv("SPAN_TELEMETRY", "off")   # tests schrijven niet in data/


def test_stream_pcm_levert_pcm_en_sluit_af(monkeypatch):
    pcm1, pcm2 = b"\x01\x02" * 8, b"\x03\x04" * 8
    ws = FakeWS([
        {"audio": base64.b64encode(pcm1).decode()},
        {"audio": base64.b64encode(pcm2).decode(), "isFinal": True},
    ])
    seen = {}

    async def fake_connect(url):
        seen["url"] = url
        return ws

    monkeypatch.setattr(tse, "_connect", fake_connect)

    async def run():
        return [c async for c in tse.stream_pcm("Hallo Bas.")]

    chunks = asyncio.run(run())
    assert chunks == [pcm1, pcm2]
    assert "voice123/stream-input" in seen["url"]
    assert "model_id=eleven_multilingual_v2" in seen["url"]
    assert "output_format=pcm_22050" in seen["url"]
    # protocol: init-frame -> tekst -> EOS (lege string)
    assert ws.sent[0]["text"] == " "
    assert ws.sent[1]["text"] == "Hallo Bas. "
    assert ws.sent[-1]["text"] == ""
    assert ws.closed is True


def test_barge_in_sluit_websocket(monkeypatch):
    b64 = base64.b64encode(b"\x00\x01").decode()
    ws = FakeWS([{"audio": b64}, {"audio": b64}, {"audio": b64}])

    async def fake_connect(url):
        return ws

    monkeypatch.setattr(tse, "_connect", fake_connect)

    async def run():
        agen = tse.stream_pcm("Lang verhaal dat wordt afgebroken.")
        await agen.__anext__()    # eerste chunk binnen
        await agen.aclose()       # HUD deed ttsAbort.abort() -> GeneratorExit

    asyncio.run(run())
    assert ws.closed is True      # finally sluit de WS: geen lek, geen kosten


def test_voice_id_resolutie(monkeypatch):
    monkeypatch.setattr(tts, "_eleven_voices", {"Daniel": "d123"})
    assert tse._voice_id("Daniel") == "d123"
    assert tse._voice_id("Onbekend") == "voice123"   # onbekende naam -> default
    assert tse._voice_id(None) == "voice123"
