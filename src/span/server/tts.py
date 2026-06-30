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


def synthesize(text: str) -> bytes:
    """Tekst → WAV (16-bit PCM mono). Lege tekst → lege bytes."""
    text = (text or "").strip()
    if not text:
        return b""
    voice = _get_voice()
    with _lock:
        chunks = list(voice.synthesize(text))
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
