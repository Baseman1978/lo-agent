"""Server-side TTS — Piper (neuraal, Nederlands), browser-onafhankelijk.

Geeft een heldere, consistente stem i.p.v. de wisselende browser-stemmen
(SpeechSynthesis). De HUD haalt per zin audio op bij /api/tts en speelt die af
via WebAudio, zodat barge-in (stoppen zodra de gebruiker praat) schoon werkt.

Piper draait op CPU en synthetiseert ~realtime (een zin in ~0,2s).
"""

from __future__ import annotations

import io
import os
import threading
import wave

VOICE_PATH = (os.environ.get("SPAN_TTS_VOICE", "").strip()
              or "/app/voices/nl_NL-mls-medium.onnx")


def _envf(name: str):
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _envi(name: str):
    v = os.environ.get(name, "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None


# server-brede defaults (de HUD kan ze per call overschrijven)
DEF_SPEAKER = _envi("SPAN_TTS_SPEAKER")
DEF_LENGTH = _envf("SPAN_TTS_LENGTH")
DEF_NOISE = _envf("SPAN_TTS_NOISE")
DEF_NOISEW = _envf("SPAN_TTS_NOISEW")
DEF_VOLUME = _envf("SPAN_TTS_VOLUME")

_voice = None
_lock = threading.Lock()


def available() -> bool:
    if os.environ.get("SPAN_TTS_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return False
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(VOICE_PATH)


def _get_voice():
    global _voice
    with _lock:
        if _voice is None:
            from piper import PiperVoice
            _voice = PiperVoice.load(VOICE_PATH)
        return _voice


def voice_info() -> dict:
    """Stem-metadata voor de HUD (aantal sprekers + actieve defaults)."""
    info = {"num_speakers": 1, "speaker_id": DEF_SPEAKER, "length_scale": DEF_LENGTH,
            "noise_scale": DEF_NOISE, "noise_w_scale": DEF_NOISEW, "volume": DEF_VOLUME}
    try:
        import json
        cfg = json.load(open(VOICE_PATH + ".json", encoding="utf-8"))
        info["num_speakers"] = int(cfg.get("num_speakers", 1))
        inf = cfg.get("inference", {})
        # effectieve default = env-override (server-default) vóór de modelstandaard,
        # zodat het paneel en "standaard herstellen" de echte basisklank tonen
        info["model_length"] = DEF_LENGTH if DEF_LENGTH is not None else inf.get("length_scale", 1.0)
        info["model_noise"] = DEF_NOISE if DEF_NOISE is not None else inf.get("noise_scale", 0.667)
        info["model_noisew"] = DEF_NOISEW if DEF_NOISEW is not None else inf.get("noise_w", 0.8)
    except Exception:
        pass
    return info


def synthesize(text: str, speaker_id=None, length_scale=None,
               noise_scale=None, noise_w_scale=None, volume=None) -> bytes:
    """Tekst → WAV (16-bit PCM mono). Lege tekst → lege bytes.

    Parameters overschrijven de env-defaults; niet-opgegeven waarden gebruiken
    de modeldefault."""
    text = (text or "").strip()
    if not text:
        return b""
    voice = _get_voice()
    # bouw alleen velden die gezet zijn (anders modeldefault)
    vals = {
        "speaker_id": speaker_id if speaker_id is not None else DEF_SPEAKER,
        "length_scale": length_scale if length_scale is not None else DEF_LENGTH,
        "noise_scale": noise_scale if noise_scale is not None else DEF_NOISE,
        "noise_w_scale": noise_w_scale if noise_w_scale is not None else DEF_NOISEW,
        "volume": volume if volume is not None else DEF_VOLUME,
    }
    syn = None
    kwargs = {k: v for k, v in vals.items() if v is not None}
    if kwargs:
        from piper import SynthesisConfig
        syn = SynthesisConfig(**kwargs)
    with _lock:
        chunks = list(voice.synthesize(text, syn_config=syn))
    if not chunks:
        return b""
    sr = getattr(chunks[0], "sample_rate", 22050)
    nch = getattr(chunks[0], "sample_channels", 1)
    pcm = b"".join(c.audio_int16_bytes for c in chunks)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return buf.getvalue()
