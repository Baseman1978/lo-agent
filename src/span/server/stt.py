"""Server-side spraakherkenning — faster-whisper, browser-onafhankelijk.

Bestaat omdat de Web Speech API achter zakelijke proxies (Edge/Lomans)
geblokkeerd wordt. De browser stuurt audio-segmenten (webm/opus) naar
/api/stt.

Twee backends:
- **remote (Fase 4, voorkeur):** als SPAN_STT_URL gezet is, gaat de audio naar
  een dedicated faster-whisper-server op de GPU (OpenAI-compatibele
  /v1/audio/transcriptions). Veel lagere latency dan CPU.
- **lokaal (fallback):** faster-whisper in deze container op CPU (int8). Eerste
  aanroep downloadt het model (~75 MB), gecachet in het models-volume.
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

# Fase 4: dedicated GPU-Whisper. Leeg = lokale CPU-backend.
STT_URL = os.environ.get("SPAN_STT_URL", "").strip()
STT_REMOTE_MODEL = (os.environ.get("SPAN_STT_REMOTE_MODEL", "").strip()
                    or "Systran/faster-whisper-small")


def available() -> bool:
    if STT_URL:
        return True
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def backend() -> str:
    return "gpu-remote" if STT_URL else "cpu-local"


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        return _model


def _transcribe_remote(audio_bytes: bytes, language: str) -> str:
    """Stuur het audio-segment naar de GPU-whisper-server (OpenAI-API)."""
    import httpx
    files = {"file": ("audio.webm", audio_bytes, "audio/webm")}
    data = {
        "model": STT_REMOTE_MODEL,
        "language": language,
        "response_format": "json",
        # VAD knipt stilte eruit -> snellere, schonere transcriptie
        "vad_filter": "true",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(STT_URL, files=files, data=data)
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()


def transcribe(audio_bytes: bytes, language: str = "nl") -> str:
    """Audio (webm/opus of wav) → tekst."""
    if STT_URL:
        try:
            return _transcribe_remote(audio_bytes, language)
        except Exception:
            # GPU-server even weg/overbelast -> val terug op de lokale CPU-backend
            # i.p.v. de transcriptie te laten mislukken (mits faster_whisper er is)
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                raise
    # lokale CPU-backend: serialiseert calls (één model).
    model = _get_model()
    with _lock:
        segments, _info = model.transcribe(
            io.BytesIO(audio_bytes),
            language=language,
            beam_size=2,
            vad_filter=True,  # knipt stilte er zelf uit
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
