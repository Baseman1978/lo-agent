"""Server-side TTS — twee backends, browser-onafhankelijk.

- **XTTS (voorkeur):** als SPAN_XTTS_URL gezet is, gaat de tekst naar de lokale
  XTTS-v2-GPU-service (natuurlijke stem, blijft op de server). Sprekers zijn
  namen (studio-stemmen).
- **Piper (fallback):** in-container neurale TTS op CPU; sprekers zijn nummers,
  met regelbare expressie/variatie/tempo.

De HUD haalt per zin audio op bij /api/tts en speelt die af via WebAudio, zodat
barge-in schoon werkt.
"""

from __future__ import annotations

import io
import os
import threading
import wave

VOICE_PATH = (os.environ.get("SPAN_TTS_VOICE", "").strip()
              or "/app/voices/nl_NL-mls-medium.onnx")

# Lokale XTTS-GPU-service (base-URL, bv. http://xtts:8001). Leeg = Piper.
XTTS_URL = os.environ.get("SPAN_XTTS_URL", "").strip().rstrip("/")


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


# Piper server-defaults (de HUD kan ze per call overschrijven)
DEF_SPEAKER = _envi("SPAN_TTS_SPEAKER")
DEF_LENGTH = _envf("SPAN_TTS_LENGTH")
DEF_NOISE = _envf("SPAN_TTS_NOISE")
DEF_NOISEW = _envf("SPAN_TTS_NOISEW")
DEF_VOLUME = _envf("SPAN_TTS_VOLUME")

_voice = None
_lock = threading.Lock()


def engine() -> str:
    return "xtts" if XTTS_URL else "piper"


def available() -> bool:
    if os.environ.get("SPAN_TTS_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return False
    if XTTS_URL:
        return True
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(VOICE_PATH)


def voice_info() -> dict:
    """Stem-metadata voor de HUD (welke backend, sprekers, defaults)."""
    if XTTS_URL:
        info = {"engine": "xtts", "named_speakers": True,
                "speakers": [], "default_speaker": ""}
        try:
            import httpx
            d = httpx.get(XTTS_URL + "/speakers", timeout=10).json()
            info["speakers"] = d.get("speakers", [])
            info["default_speaker"] = d.get("default", "") or (
                info["speakers"][0] if info["speakers"] else "")
        except Exception:
            pass
        return info
    # Piper
    info = {"engine": "piper", "named_speakers": False, "num_speakers": 1,
            "model_length": None, "model_noise": None, "model_noisew": None}
    try:
        import json
        cfg = json.load(open(VOICE_PATH + ".json", encoding="utf-8"))
        info["num_speakers"] = int(cfg.get("num_speakers", 1))
        inf = cfg.get("inference", {})
        info["model_length"] = DEF_LENGTH if DEF_LENGTH is not None else inf.get("length_scale", 1.0)
        info["model_noise"] = DEF_NOISE if DEF_NOISE is not None else inf.get("noise_scale", 0.667)
        info["model_noisew"] = DEF_NOISEW if DEF_NOISEW is not None else inf.get("noise_w", 0.8)
    except Exception:
        pass
    return info


def _synth_xtts(text: str, speaker) -> bytes:
    import httpx
    body = {"text": text, "language": "nl"}
    if speaker:
        body["speaker"] = str(speaker)
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(XTTS_URL + "/tts", json=body)
        resp.raise_for_status()
        return resp.content


def _get_voice():
    global _voice
    with _lock:
        if _voice is None:
            from piper import PiperVoice
            _voice = PiperVoice.load(VOICE_PATH)
        return _voice


def synthesize(text: str, speaker=None, speaker_id=None, length_scale=None,
               noise_scale=None, noise_w_scale=None, volume=None) -> bytes:
    """Tekst → WAV (16-bit PCM mono). `speaker` is een naam (XTTS) of nummer
    (Piper); `speaker_id` is de Piper-variant. Lege tekst → lege bytes."""
    text = (text or "").strip()
    if not text:
        return b""

    if XTTS_URL:
        spk = speaker if speaker not in (None, "") else None
        try:
            return _synth_xtts(text, spk)
        except Exception:
            # XTTS laadt nog of is even weg -> Piper-fallback (mits beschikbaar)
            try:
                import piper  # noqa: F401
            except ImportError:
                raise
            speaker = None  # XTTS-naam is geen geldige Piper-spreker

    # Piper-backend
    sid = speaker_id
    if sid is None and speaker not in (None, ""):
        try:
            sid = int(speaker)
        except (TypeError, ValueError):
            sid = None
    voice = _get_voice()
    vals = {
        "speaker_id": sid if sid is not None else DEF_SPEAKER,
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
