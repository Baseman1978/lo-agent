"""Server-side spraakherkenning — faster-whisper, browser-onafhankelijk.

Bestaat omdat de Web Speech API achter zakelijke proxies (Edge/Lomans)
geblokkeerd wordt. De browser stuurt audio-segmenten (webm/opus) naar
/api/stt; Whisper draait lokaal in de container. Eerste aanroep downloadt
het model (~75 MB, gecachet in het span-models volume).
"""

from __future__ import annotations

import io
import os
import threading

_model = None
_lock = threading.Lock()

# F2.4 — instelbaar via SPAN_STT_MODEL. 'base' = veilige CPU-default;
# 'large-v3-turbo' is nauwkeuriger maar zwaarder (meet latency op ARM64 vóór
# je het de default maakt — vandaar instelbaar i.p.v. hard gewijzigd).
MODEL_NAME = os.environ.get("SPAN_STT_MODEL", "base").strip() or "base"


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def transcribe(audio_bytes: bytes, language: str = "nl") -> str:
    """Audio (webm/opus of wav) → tekst. Serialiseert calls: CPU-model."""
    model = _get_model()
    with _lock:
        segments, _info = model.transcribe(
            io.BytesIO(audio_bytes),
            language=language,
            beam_size=2,
            vad_filter=True,  # knipt stilte er zelf uit
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
