"""Cloud-TTS (ElevenLabs) — engine-keuze, WAV-verpakking en faalketen."""

from __future__ import annotations

import io
import wave

import pytest

from span.server import tts


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # schone uitgangssituatie per test; env is al ingelezen -> module-attrs patchen
    monkeypatch.setattr(tts, "ELEVEN_KEY", "")
    monkeypatch.setattr(tts, "XTTS_URL", "")
    monkeypatch.setattr(tts, "_eleven_voices", {})
    monkeypatch.delenv("SPAN_TTS_ENABLED", raising=False)
    monkeypatch.delenv("SPAN_TTS_STREAMING", raising=False)


def test_engine_voorkeursvolgorde(monkeypatch):
    assert tts.engine() == "piper"
    monkeypatch.setattr(tts, "XTTS_URL", "http://xtts:8001")
    assert tts.engine() == "xtts"
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    assert tts.engine() == "elevenlabs"      # cloud wint als de key er is
    assert tts.available() is True


def test_wav_verpakking_klopt():
    pcm = b"\x00\x01" * 220
    data = tts._wav(pcm, 22050)
    with wave.open(io.BytesIO(data)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 22050
        assert wf.readframes(9999) == pcm


def test_synthesize_gebruikt_elevenlabs(monkeypatch):
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    calls = {}

    def fake_eleven(text, speaker):
        calls["text"], calls["speaker"] = text, speaker
        return b"WAVDATA"

    monkeypatch.setattr(tts, "_synth_elevenlabs", fake_eleven)
    out = tts.synthesize("Hallo Bas", speaker="Rachel")
    assert out == b"WAVDATA"
    assert calls == {"text": "Hallo Bas", "speaker": "Rachel"}


def test_faalketen_cloud_naar_xtts(monkeypatch):
    # ElevenLabs faalt (bv. limiet) -> XTTS neemt over, zonder de cloud-spreker
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    monkeypatch.setattr(tts, "XTTS_URL", "http://xtts:8001")
    monkeypatch.setattr(tts, "_synth_elevenlabs",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("429")))
    seen = {}

    def fake_xtts(text, speaker):
        seen["speaker"] = speaker
        return b"XTTSWAV"

    monkeypatch.setattr(tts, "_synth_xtts", fake_xtts)
    out = tts.synthesize("Hallo", speaker="Rachel")
    assert out == b"XTTSWAV"
    assert seen["speaker"] is None   # cloud-naam niet doorgeven aan XTTS


def test_voice_info_vorm_gelijk_aan_xtts(monkeypatch):
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    monkeypatch.setattr(tts, "_eleven_voices", {"Rachel": "21m00", "Daniel": "abc"})
    monkeypatch.setattr(tts, "ELEVEN_VOICE", "21m00")
    info = tts.voice_info()
    assert info["engine"] == "elevenlabs"
    assert info["named_speakers"] is True       # HUD-dropdown werkt ongewijzigd
    assert info["speakers"] == ["Daniel", "Rachel"]
    assert info["default_speaker"] == "Rachel"


# -- keuzemenu: beheerder-override van de spraakbron -------------------------

def test_engine_override_dwingt_lokaal_af(monkeypatch):
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    monkeypatch.setattr(tts, "XTTS_URL", "http://xtts:8001")
    monkeypatch.setattr(tts, "_ENGINE_OVERRIDE", "xtts")
    assert tts.engine() == "xtts"            # cloud-key aanwezig, maar keuze wint
    seen = {}
    monkeypatch.setattr(tts, "_synth_xtts", lambda t, s: seen.update(t=t) or b"X")
    monkeypatch.setattr(tts, "_synth_elevenlabs",
                        lambda *a: (_ for _ in ()).throw(AssertionError("cloud aangeroepen")))
    assert tts.synthesize("hoi") == b"X"


def test_engine_override_valt_terug_als_bron_ontbreekt(monkeypatch):
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    monkeypatch.setattr(tts, "_ENGINE_OVERRIDE", "xtts")   # geen XTTS_URL gezet
    assert tts.engine() == "elevenlabs"      # onbruikbare keuze -> automatisch


def test_engines_available_vorm(monkeypatch):
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    engines = {e["id"]: e["available"] for e in tts.engines_available()}
    assert engines["elevenlabs"] is True
    assert set(engines) == {"elevenlabs", "xtts", "piper"}


# -- A2: feature-flag voor ElevenLabs-WS-streaming ---------------------------

def test_streaming_flag_default_uit(monkeypatch):
    # spec: poort pas open na A1-bewijs -> default UIT, ook mét cloud-key
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    assert tts.streaming_enabled() is False
    assert tts.stream_available() is False


def test_stream_available_vereist_flag_en_elevenlabs(monkeypatch):
    monkeypatch.setenv("SPAN_TTS_STREAMING", "1")
    assert tts.stream_available() is False       # geen key -> engine != elevenlabs
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    assert tts.stream_available() is True        # flag aan + elevenlabs actief
    # beheerder kiest expliciet een lokale bron -> streaming-pad uit
    monkeypatch.setattr(tts, "_piper_ok", lambda: True)
    monkeypatch.setattr(tts, "_ENGINE_OVERRIDE", "piper")
    assert tts.stream_available() is False


def test_synth_elevenlabs_model_override(monkeypatch):
    # A/B-test stuurt per call een ander model mee; default blijft ELEVEN_MODEL
    monkeypatch.setattr(tts, "ELEVEN_KEY", "sk-x")
    seen = {}

    class FakeResp:
        content = b"\x00\x01" * 4
        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, params=None, headers=None, json=None):
            seen["url"], seen["params"], seen["json"] = url, params, json
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)

    out = tts._synth_elevenlabs("Hoi", None, model_id="eleven_flash_v2_5")
    assert seen["json"]["model_id"] == "eleven_flash_v2_5"
    assert seen["params"]["output_format"] == "pcm_22050"
    assert out[:4] == b"RIFF"                    # nog steeds WAV-verpakt

    tts._synth_elevenlabs("Hoi", None)           # zonder override
    assert seen["json"]["model_id"] == tts.ELEVEN_MODEL
