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
