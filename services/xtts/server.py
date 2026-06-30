"""XTTS-v2 TTS-microservice (GPU) — natuurlijke stem voor LO, volledig lokaal.

POST /tts {text, speaker?, language?} -> audio/wav (16-bit PCM mono).
GET  /speakers -> ingebouwde sprekers (studio-stemmen, spreken elke taal).
GET  /health.

Draait op de z390-GPU; niets verlaat de server. Model laadt bij opstart
(~45s eerste keer; daarna uit de gecachete volume)."""

from __future__ import annotations

import io
import os
import threading
import wave

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

MODEL = os.environ.get("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")
DEFAULT_SPEAKER = os.environ.get("XTTS_SPEAKER", "").strip()
DEFAULT_LANG = os.environ.get("XTTS_LANG", "nl").strip() or "nl"
SR = 24000  # XTTS-uitvoer-samplerate

app = FastAPI(title="LO XTTS")
_tts = None
_gpu_lock = threading.Lock()  # serialiseert GPU-synthese (batch + stream)


def get_tts():
    global _tts
    if _tts is None:
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        from TTS.api import TTS
        _tts = TTS(MODEL).to(dev)
    return _tts


def _speaker_names(t) -> list[str]:
    try:
        return list(t.synthesizer.tts_model.speaker_manager.speaker_names)
    except Exception:
        return []


class Req(BaseModel):
    text: str
    speaker: str | None = None
    language: str | None = None


@app.on_event("startup")
def _warm() -> None:
    try:
        t = get_tts()
        # warmup-synthese: compileert de CUDA-kernels nu i.p.v. bij de 1e echte
        # call, zodat de eerste zin van de gebruiker niet de cold-start meeneemt
        names = _speaker_names(t)
        spk = DEFAULT_SPEAKER or (names[0] if names else None)
        t.tts(text="Hallo.", speaker=spk, language=DEFAULT_LANG)
        print("XTTS warm", flush=True)
    except Exception as exc:  # opstart mag niet hard falen; /health meldt het
        print("XTTS laad-fout:", exc, flush=True)


@app.get("/health")
def health() -> dict:
    return {"ok": _tts is not None}


@app.get("/speakers")
def speakers() -> dict:
    return {"speakers": _speaker_names(get_tts()), "default": DEFAULT_SPEAKER}


@app.post("/tts")
def tts(req: Req) -> Response:
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="lege tekst")
    t = get_tts()
    names = _speaker_names(t)
    spk = (req.speaker or DEFAULT_SPEAKER or (names[0] if names else None))
    if spk and names and spk not in names:
        spk = names[0]
    lang = (req.language or DEFAULT_LANG)
    try:
        wav = t.tts(text=text, speaker=spk, language=lang)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"synthese mislukt: {exc}")
    sr = int(getattr(t.synthesizer, "output_sample_rate", SR))
    with _gpu_lock:
        pcm = (np.clip(np.asarray(wav, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return Response(content=buf.getvalue(), media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


def _speaker_latents(model, name):
    """gpt_cond_latent + speaker_embedding voor een ingebouwde spreker."""
    sp = model.speaker_manager.speakers[name]
    return sp["gpt_cond_latent"], sp["speaker_embedding"]


@app.post("/tts_stream")
def tts_stream(req: Req) -> StreamingResponse:
    """Streamt ruwe PCM (16-bit mono @ 24kHz) terwijl XTTS genereert -> de eerste
    klank komt in ~0,2s i.p.v. te wachten op de hele zin."""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="lege tekst")
    t = get_tts()
    model = t.synthesizer.tts_model
    names = _speaker_names(t)
    spk = (req.speaker or DEFAULT_SPEAKER or (names[0] if names else None))
    if spk and names and spk not in names:
        spk = names[0]
    lang = (req.language or DEFAULT_LANG)
    try:
        gpt, emb = _speaker_latents(model, spk)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"spreker-latents: {exc}")

    def gen():
        with _gpu_lock:
            for chunk in model.inference_stream(
                    text, lang, gpt, emb,
                    stream_chunk_size=20, enable_text_splitting=True):
                arr = chunk.detach().cpu().numpy()
                yield (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"X-Sample-Rate": str(SR), "Cache-Control": "no-store"})
