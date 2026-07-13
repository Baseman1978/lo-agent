"""A2 — ElevenLabs stream-input WebSocket, versie "goed-genoeg".

Per zin één WS-call: verbinden, tekst sturen, base64-PCM-chunks ontvangen en
als ruwe bytes yielden. De route verpakt dit als StreamingResponse met een
X-Sample-Rate-header — exact het contract dat de HUD (voice.js/ttsPlayStream)
al kent van XTTS, dus de frontend werkt ongewijzigd.

Barge-in: als de HTTP-client verbreekt (ttsAbort.abort in de HUD) krijgt de
async generator een GeneratorExit -> het finally-blok sluit de WS. Zonder die
afsluiting lekken verbindingen én kosten (ElevenLabs rekent per teken door).

Bewust NIET hier (dat is B2, productie-hardening): een persistente verbinding
over zinnen heen, reconnect-logica, backpressure. prewarm() dempt de per-zin
handshakekosten (DNS/TLS/WS-upgrade) best-effort.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

from span.server import tts

_WS_BASE = "wss://api.elevenlabs.io/v1/text-to-speech"
SAMPLE_RATE = 22050  # pcm_22050 -> zelfde rate als het batch-pad (_synth_elevenlabs)


def _voice_id(speaker) -> str:
    """Spreker-naam -> voice_id, zelfde resolutie als _synth_elevenlabs."""
    if not speaker:
        return tts.ELEVEN_VOICE
    if not tts._eleven_voices:
        try:
            tts._eleven_load_voices()
        except Exception:
            pass
    return tts._eleven_voices.get(str(speaker), tts.ELEVEN_VOICE)


def _ws_url(voice_id: str, model_id: str) -> str:
    return (f"{_WS_BASE}/{voice_id}/stream-input"
            f"?model_id={model_id}&output_format=pcm_22050")


async def _connect(url: str):
    """Losse helper zodat tests hem kunnen vervangen door een FakeWS."""
    import websockets  # al gepind (16.0) via constraints.txt / uvicorn[standard]
    return await websockets.connect(
        url,
        additional_headers={"xi-api-key": tts.ELEVEN_KEY},
        open_timeout=10, close_timeout=3)


async def stream_pcm(text: str, speaker=None,
                     model_id: str | None = None) -> AsyncIterator[bytes]:
    """Eén zin -> ruwe PCM16-chunks @22050 Hz. Sluit de WS altijd (finally)."""
    mid = model_id or tts.ELEVEN_MODEL
    ws = await _connect(_ws_url(_voice_id(speaker), mid))
    try:
        # protocol: init-frame (voice_settings) -> tekst -> EOS (lege string)
        await ws.send(json.dumps({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}))
        await ws.send(json.dumps({"text": text + " ",
                                  "try_trigger_generation": True}))
        await ws.send(json.dumps({"text": ""}))
        async for raw in ws:
            msg = json.loads(raw)
            audio = msg.get("audio")
            if audio:
                yield base64.b64decode(audio)
            if msg.get("isFinal"):
                break
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def prewarm() -> bool:
    """Best-effort cold-start-demping: één handshake opzetten en sluiten.
    Mag nooit iets breken -> elke fout wordt ingeslikt (False)."""
    try:
        ws = await _connect(_ws_url(tts.ELEVEN_VOICE, tts.ELEVEN_MODEL))
        await ws.close()
        return True
    except Exception:
        return False
