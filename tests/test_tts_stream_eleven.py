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


def test_prewarm_best_effort(monkeypatch):
    ok_ws = FakeWS([])

    async def good(url):
        return ok_ws

    monkeypatch.setattr(tse, "_connect", good)
    assert asyncio.run(tse.prewarm()) is True
    assert ok_ws.closed is True               # handshake netjes weer dicht

    async def bad(url):
        raise OSError("dns weg")

    monkeypatch.setattr(tse, "_connect", bad)
    assert asyncio.run(tse.prewarm()) is False  # fout ingeslikt, niets breekt


def test_route_tts_stream_elevenlabs(monkeypatch, tmp_path):
    import span.server.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setenv("SPAN_TTS_STREAMING", "1")   # flag aan + key (fixture)

    async def fake_stream(text, speaker=None, model_id=None):
        yield b"\x01\x02"
        yield b"\x03\x04"

    monkeypatch.setattr(tse, "stream_pcm", fake_stream)

    req = MagicMock()

    async def _json():
        return {"text": "Hallo Bas."}

    req.json = _json

    async def run():
        resp = await routes.tts_stream(req)
        body = [c async for c in resp.body_iterator]
        return resp, body

    resp, body = asyncio.run(run())
    assert resp.headers["x-sample-rate"] == "22050"   # contract voor de HUD
    assert b"".join(body) == b"\x01\x02\x03\x04"
    # telemetrie op de EERSTE chunk, met engine+model in de meta (A/B-analyse)
    rows = [json.loads(ln) for ln in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["seg"] == "tts"
    assert rows[0]["meta"]["mode"] == "stream"
    assert rows[0]["meta"]["engine"] == "elevenlabs"
    assert rows[0]["meta"]["model"] == "eleven_multilingual_v2"


def test_route_tts_stream_501_zonder_bron(monkeypatch):
    import span.server.routes as routes
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    # fixture: flag uit + XTTS_URL leeg -> geen enkele streamingbron
    req = MagicMock()

    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.tts_stream(req))
    assert ei.value.status_code == 501


def test_status_streaming_volgt_flag(monkeypatch):
    import span.server.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(routes, "_is_owner", lambda request: True)
    # voorkom echte /voices-call in voice_info() (elevenlabs-tak)
    monkeypatch.setattr(tts, "_eleven_voices", {"Rachel": "voice123"})

    req = MagicMock()
    out = asyncio.run(routes.tts_status(req))
    assert out["streaming"] is False              # flag uit -> geen stream-pad

    monkeypatch.setenv("SPAN_TTS_STREAMING", "1")
    out = asyncio.run(routes.tts_status(req))
    assert out["streaming"] is True               # flag aan + elevenlabs actief


def test_tts_batch_meta_bevat_engine_en_model(monkeypatch, tmp_path):
    import span.server.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(tts, "available", lambda: True)
    monkeypatch.setattr(tts, "synthesize", lambda text, **kw: b"RIFF....WAVE")

    req = MagicMock()

    async def _json():
        return {"text": "hallo wereld"}

    req.json = _json
    asyncio.run(routes.text_to_speech(req))

    rows = [json.loads(ln) for ln in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["meta"]["mode"] == "full"
    assert rows[0]["meta"]["engine"] == "elevenlabs"   # key gezet in fixture
    assert rows[0]["meta"]["model"] == "eleven_multilingual_v2"


def test_tts_ab_twee_modellen_met_ms(monkeypatch, tmp_path):
    import span.server.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)

    seen_models = []

    def fake_synth(text, speaker, model_id=None):
        seen_models.append(model_id)
        return b"RIFFWAVE" + (model_id or "").encode()

    monkeypatch.setattr(tts, "_synth_elevenlabs", fake_synth)

    req = MagicMock()

    async def _json():
        return {"text": "Goedemiddag Bas, dit is de luistertest."}

    req.json = _json
    out = asyncio.run(routes.tts_ab(req))

    assert seen_models == ["eleven_flash_v2_5", "eleven_multilingual_v2"]
    assert len(out["results"]) == 2
    for r in out["results"]:
        assert r["tts_ms"] >= 0.0
        audio = base64.b64decode(r["audio_b64"])
        assert audio.startswith(b"RIFFWAVE")      # afspeelbaar in de HUD
    # telemetrie per model, mode=ab (spec: latencyverschil uit A1-telemetrie)
    rows = [json.loads(ln) for ln in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["meta"]["model"] for r in rows] == ["eleven_flash_v2_5",
                                                  "eleven_multilingual_v2"]
    assert all(r["meta"]["mode"] == "ab" for r in rows)


def test_tts_ab_501_zonder_elevenlabs(monkeypatch):
    import span.server.routes as routes
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(tts, "ELEVEN_KEY", "")    # engine() != elevenlabs

    req = MagicMock()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes.tts_ab(req))
    assert ei.value.status_code == 501


def test_route_tts_stream_lege_stream_meldt_empty(monkeypatch, tmp_path):
    """M2: nul chunks zonder exception -> outcome=empty in de telemetrie."""
    import span.server.routes as routes
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setenv("SPAN_TTS_STREAMING", "1")

    async def leeg_stream(text, speaker=None, model_id=None):
        return
        yield  # maakt het een async generator

    monkeypatch.setattr(tse, "stream_pcm", leeg_stream)
    req = MagicMock()

    async def _json():
        return {"text": "Hallo."}

    req.json = _json

    async def run():
        resp = await routes.tts_stream(req)
        return [c async for c in resp.body_iterator]

    body = asyncio.run(run())
    assert body == []
    rows = [json.loads(ln) for ln in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["meta"]["outcome"] == "empty"
