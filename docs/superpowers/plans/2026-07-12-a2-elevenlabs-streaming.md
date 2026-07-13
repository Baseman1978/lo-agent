# A2 — ElevenLabs-WS streaming (goed-genoeg) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spraak-uit via de ElevenLabs stream-input-WebSocket — per zin één WS-call met prewarm, achter feature-flag `SPAN_TTS_STREAMING` (default UIT) — plus een A/B-luistertest (Flash v2.5 vs Multilingual v2) en hoorbare micro-bevestigingen bij lange tool-calls.

**Architecture:** Een nieuw zusje `span.server.tts_stream_eleven` bezit alle WS-kennis (async generator `text → PCM16-chunks @22050 Hz`, prewarm, barge-in-veilige afsluiting); `routes.py` krijgt alleen een dunne tak in het bestaande `/api/tts_stream`-endpoint met exact het XTTS-response-contract (ruwe PCM16 + `X-Sample-Rate`-header) zodat de HUD (`voice.js/ttsPlayStream`) ongewijzigd werkt. Fail-soft in twee lagen: een lege/afgebroken stream laat de HUD zelf terugvallen op het batch-pad, en de flag houdt alles default uit. Micro-bevestigingen zijn puur frontend (de `tool`-events bereiken de browser al); de A/B-test is een owner-only endpoint dat beide modellen batch-synthetiseert, `tts_ms` per model retourneert en `telemetry.record("tts", ms, {"mode": "ab", "model": ...})` logt.

**Tech Stack:** Python 3, FastAPI, `websockets` (al gepind op 16.0 via constraints.txt — geen nieuwe dependency), pytest, vanilla JS (HUD). Geen ElevenLabs-SDK.

> **Basis-branch:** A1 is inmiddels gemerged naar `master` — `src/span/telemetry.py` (record/aggregate), `tests/test_telemetry.py` en de A1-instrumentatie in `routes.py` staan daar al. Baseer de A2-branch dus direct op `master`. `src/span/telemetry.py` en `tests/test_telemetry.py` blijven read-only voor A2 — alleen importeren.

---

## File Structure

- **Create** `src/span/server/tts_stream_eleven.py` — WS-client: `stream_pcm()`, `prewarm()`, `_voice_id()`, `SAMPLE_RATE`. Apart bestand (routes.py staat al op 1598 regels, ruim boven de 500-regelgrens).
- **Create** `tests/test_tts_stream_eleven.py` — FakeWS-tests voor de client + route-tests voor stream/status/A-B (netwerkloos, alles gemockt).
- **Modify** `src/span/server/tts.py` (287 r) — `streaming_enabled()` + `stream_available()` na `available()` (r105-114); `_synth_elevenlabs` (r188) krijgt `model_id`-parameter.
- **Modify** `src/span/server/routes.py` — `/api/tts_stream` (r1018-1055): ElevenLabs-tak; `/api/tts/status` (r1058-1071): streaming-vlag; `/api/tts` (r1009-1010): telemetrie-meta met engine/model; nieuw owner-only `POST /api/tts_ab`.
- **Modify** `src/span/server/app.py` — lifespan (na r75 `tts.set_engine_override(...)`): best-effort prewarm-task.
- **Modify** `src/span/server/static/voice.js` (797 r) — `SPAN.microAck()` naast `ttsEnqueue` (r224-229).
- **Modify** `src/span/server/static/jarvis.js` (897 r) — ack-timer in de `tool`-branch (r323-327), reset in `delta`/`done`.
- **Modify** `src/span/server/static/settings.js` (1062 r) — A/B-knop in `ttsInit()` (r649-739).
- **Modify** `src/span/server/static/index.html` — A/B-knop-rij in `#tts-settings` (na r355).
- **Modify** `tests/test_tts_cloud.py` — flag- en model_id-tests bij de bestaande engine-tests.
- **Modify** `.env.example` — `SPAN_TTS_STREAMING=` documenteren (alleen de NAAM; de key zelf staat in `.env` en komt nergens in code of plan).

---

## Task 1: Feature-flag SPAN_TTS_STREAMING (default UIT)

**Files:**
- Modify: `src/span/server/tts.py` (na `available()`, r114)
- Modify: `.env.example` (na r46)
- Test: `tests/test_tts_cloud.py`

- [ ] **Step 1: Write the failing test**

Voeg eerst één regel toe aan de bestaande autouse-fixture `_reset` in `tests/test_tts_cloud.py` (na `monkeypatch.delenv("SPAN_TTS_ENABLED", raising=False)`):

```python
    monkeypatch.delenv("SPAN_TTS_STREAMING", raising=False)
```

Voeg daarna onderaan `tests/test_tts_cloud.py` toe:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_cloud.py::test_streaming_flag_default_uit -v`
Expected: FAIL with `AttributeError: module 'span.server.tts' has no attribute 'streaming_enabled'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/server/tts.py`, direct na `available()` (r114):

```python
def streaming_enabled() -> bool:
    """A2-feature-flag: ElevenLabs-WS-streaming. Default UIT — de spec zegt:
    poort pas open nadat A1 bewijst dat TTS-latency de bottleneck is."""
    return os.environ.get("SPAN_TTS_STREAMING", "").strip().lower() in (
        "1", "true", "yes", "on")


def stream_available() -> bool:
    """Streaming-pad actief: flag aan én ElevenLabs is de actieve engine
    (engine() garandeert dan dat de API-key aanwezig is)."""
    return streaming_enabled() and engine() == "elevenlabs"
```

En in `.env.example`, na de regel `SPAN_XTTS_URL=` (r46):

```bash
# A2 — ElevenLabs-WS-streaming (eerste klank sneller). Default UIT; pas
# aanzetten ("1") nadat de A1-telemetrie bewijst dat TTS de bottleneck is.
SPAN_TTS_STREAMING=
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_cloud.py -v`
Expected: PASS (alle bestaande + 2 nieuwe tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/tts.py tests/test_tts_cloud.py .env.example
git commit -m "feat(tts): feature-flag SPAN_TTS_STREAMING voor ElevenLabs-WS (default uit)"
```

---

## Task 2: _synth_elevenlabs krijgt een model_id-parameter (A/B-fundament)

**Files:**
- Modify: `src/span/server/tts.py` (r188-205)
- Test: `tests/test_tts_cloud.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tts_cloud.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_cloud.py::test_synth_elevenlabs_model_override -v`
Expected: FAIL with `TypeError: _synth_elevenlabs() got an unexpected keyword argument 'model_id'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/server/tts.py` vervang de signatuur van `_synth_elevenlabs` (r188):

```python
def _synth_elevenlabs(text: str, speaker, model_id: str | None = None) -> bytes:
```

en de json-regel in de `client.post(...)`-call (r203):

```python
            json={"text": text, "model_id": model_id or ELEVEN_MODEL})
```

De bestaande aanroep in `synthesize()` (r240, `_synth_elevenlabs(text, spk)`) blijft ongewijzigd — `model_id` default naar `ELEVEN_MODEL`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_cloud.py -v`
Expected: PASS (incl. de bestaande faalketen-tests: die patchen `_synth_elevenlabs` volledig weg)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/tts.py tests/test_tts_cloud.py
git commit -m "feat(tts): model_id-parameter op _synth_elevenlabs voor de A/B-luistertest"
```

---

## Task 3: WS-client — stream_pcm() met barge-in-veilige afsluiting

**Files:**
- Create: `src/span/server/tts_stream_eleven.py`
- Create: `tests/test_tts_stream_eleven.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tts_stream_eleven.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_stream_eleven.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.server.tts_stream_eleven'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/server/tts_stream_eleven.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_stream_eleven.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/tts_stream_eleven.py tests/test_tts_stream_eleven.py
git commit -m "feat(tts): ElevenLabs stream-input WS-client met barge-in-veilige afsluiting"
```

---

## Task 4: Prewarm-hook in de lifespan (best-effort)

**Files:**
- Modify: `src/span/server/app.py` (na r75, `tts.set_engine_override(...)`)
- Test: `tests/test_tts_stream_eleven.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tts_stream_eleven.py
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
```

- [ ] **Step 2: Run test to verify it passes (prewarm zit al in Task 3)**

Run: `python -m pytest tests/test_tts_stream_eleven.py::test_prewarm_best_effort -v`
Expected: PASS (Task 3 implementeerde `prewarm()` al; deze test borgt het best-effort-gedrag)

- [ ] **Step 3: Wire de prewarm in de lifespan**

In `src/span/server/app.py`, direct ná de regel `tts.set_engine_override(cfg.get("tts_engine") or "")` (r75), toevoegen:

```python
    if tts.stream_available():
        # A2: TTS-prewarm — DNS/TLS/WS-handshake alvast opwarmen zodat de
        # eerste zin geen cold-start betaalt. Best-effort: een prewarm-fout
        # mag de serverstart nooit vertragen of breken (patroon mcp-init).
        from span.server import tts_stream_eleven as _tse
        import asyncio as _aio

        async def _tts_warm():
            ok = await _tse.prewarm()
            print(f"[tts] elevenlabs prewarm: {'ok' if ok else 'overgeslagen'}",
                  flush=True)

        try:
            _aio.get_running_loop().create_task(_tts_warm())
        except RuntimeError:
            pass
```

- [ ] **Step 4: Run the regression sweep**

Run: `python -m pytest tests/test_tts_stream_eleven.py tests/test_tts_cloud.py -q`
Expected: PASS (de lifespan-tak is inert zolang de flag uit staat)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/app.py tests/test_tts_stream_eleven.py
git commit -m "feat(tts): best-effort prewarm van de ElevenLabs-WS bij serverstart"
```

---

## Task 5: /api/tts_stream — ElevenLabs-tak met identiek response-contract

**Files:**
- Modify: `src/span/server/routes.py` (r1018-1055, volledige handler vervangen)
- Test: `tests/test_tts_stream_eleven.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tts_stream_eleven.py
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
    rows = [json.loads(l) for l in
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_stream_eleven.py::test_route_tts_stream_elevenlabs -v`
Expected: FAIL — de bestaande handler gooit 501 (`if not ttsmod.XTTS_URL`) vóórdat de ElevenLabs-tak bestaat.

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/server/routes.py` de VOLLEDIGE `tts_stream`-handler (r1018-1055) door:

```python
@router.post("/api/tts_stream")
async def tts_stream(request: Request) -> Any:
    """Streamt audio (ruwe PCM16) terwijl de engine genereert — eerste klank
    snel. Twee bronnen: ElevenLabs-WS (A2, achter SPAN_TTS_STREAMING) of de
    lokale XTTS-GPU-service. Zelfde response-contract (PCM16 + X-Sample-Rate)
    zodat de HUD (voice.js/ttsPlayStream) ongewijzigd werkt."""
    _require_rest_auth(request)
    from span.server import tts as ttsmod
    use_eleven = ttsmod.stream_available()
    if not use_eleven and not ttsmod.XTTS_URL:
        raise HTTPException(status_code=501, detail="Streaming niet beschikbaar.")
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Lege tekst.")
    text = text[:1200]
    spk = body.get("speaker")
    speaker = str(spk)[:80] if spk else None
    from fastapi.responses import StreamingResponse

    if use_eleven:
        from span.server import tts_stream_eleven as tse

        async def gen_eleven():
            _t0 = time.perf_counter()
            _first = True
            try:
                async for chunk in tse.stream_pcm(text, speaker=speaker):
                    if _first and chunk:
                        from span import telemetry
                        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0,
                                         {"mode": "stream", "engine": "elevenlabs",
                                          "model": ttsmod.ELEVEN_MODEL})
                        _first = False
                    if chunk:
                        yield chunk
            except Exception as exc:
                # fail-soft: lege/afgebroken stream -> de HUD valt zelf terug
                # op het batch-pad (voice.js: "lege stream" -> ttsPlayBatch).
                # Maar degradatie moet zíchtbaar zijn (spec: outcome/foutklasse
                # in A1-telemetrie + eerlijk over wat niet lukte) — dus loggen
                # én registreren; telemetry.record is zelf al best-effort.
                print(f"[tts] elevenlabs stream-fout: {type(exc).__name__}: {exc}",
                      flush=True)
                from span import telemetry
                telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0,
                                 {"mode": "stream", "engine": "elevenlabs",
                                  "outcome": "error",
                                  "error_class": type(exc).__name__})
                return

        return StreamingResponse(gen_eleven(), media_type="application/octet-stream",
                                 headers={"X-Sample-Rate": str(tse.SAMPLE_RATE),
                                          "Cache-Control": "no-store"})

    payload: dict[str, Any] = {"text": text, "language": "nl"}
    if speaker:
        payload["speaker"] = speaker
    import httpx

    async def gen():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", ttsmod.XTTS_URL + "/tts_stream",
                                     json=payload) as r:
                if r.status_code != 200:
                    return
                _t0 = time.perf_counter()
                _first = True
                async for chunk in r.aiter_bytes():
                    if _first and chunk:
                        from span import telemetry
                        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0,
                                         {"mode": "stream", "engine": "xtts"})
                        _first = False
                    if chunk:
                        yield chunk

    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"X-Sample-Rate": "24000",
                                      "Cache-Control": "no-store"})
```

Dit behoudt het A1-telemetriepunt (eerste-chunk-meting) op het XTTS-pad en breidt de meta uit met `engine`; het ElevenLabs-pad krijgt dezelfde meting plus `model` (voor de A/B-latency-analyse uit de spec).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_stream_eleven.py tests/test_telemetry.py -v`
Expected: PASS — inclusief de (read-only) A1-tests: het XTTS-stream-telemetriegedrag is ongewijzigd op de extra meta-key na.

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_tts_stream_eleven.py
git commit -m "feat(tts): /api/tts_stream streamt via ElevenLabs-WS achter SPAN_TTS_STREAMING"
```

---

## Task 6: /api/tts/status-streaming-vlag + engine/model in de batch-telemetrie

**Files:**
- Modify: `src/span/server/routes.py` (r1066 en r1009-1010)
- Test: `tests/test_tts_stream_eleven.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tts_stream_eleven.py
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

    rows = [json.loads(l) for l in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["meta"]["mode"] == "full"
    assert rows[0]["meta"]["engine"] == "elevenlabs"   # key gezet in fixture
    assert rows[0]["meta"]["model"] == "eleven_multilingual_v2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_stream_eleven.py::test_status_streaming_volgt_flag -v`
Expected: FAIL — `out["streaming"]` blijft `False` (huidige regel eist `XTTS_URL` + engine xtts) resp. de meta mist `engine`/`model`.

- [ ] **Step 3: Write minimal implementation**

In `src/span/server/routes.py` vervang r1066:

```python
        info["streaming"] = bool(tts.XTTS_URL) and tts.engine() == "xtts"
```

door:

```python
        # streamen: ElevenLabs-WS (achter de flag) óf XTTS. Moet true worden,
        # anders kiest de HUD nooit het stream-pad (settings.js zet hieruit
        # SPAN._ttsStreaming; voice.js kiest daarop ttsPlayStream).
        info["streaming"] = tts.stream_available() or (
            bool(tts.XTTS_URL) and tts.engine() == "xtts")
```

En in `text_to_speech()` (r1009-1010) vervang:

```python
        from span import telemetry
        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0, {"mode": "full"})
```

door:

```python
        from span import telemetry
        _meta: dict[str, Any] = {"mode": "full", "engine": tts.engine()}
        if _meta["engine"] == "elevenlabs":
            _meta["model"] = tts.ELEVEN_MODEL
        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0, _meta)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_stream_eleven.py tests/test_telemetry.py tests/test_observability.py -q`
Expected: PASS (A1-tests blijven groen: zij asserteren alleen count, niet de exacte meta)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_tts_stream_eleven.py
git commit -m "feat(tts): status-streamingvlag voor ElevenLabs + engine/model in tts-telemetrie"
```

---

## Task 7: Owner-only A/B-endpoint — POST /api/tts_ab

**Files:**
- Modify: `src/span/server/routes.py` (nieuw endpoint direct na `tts_status`, r1071)
- Test: `tests/test_tts_stream_eleven.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tts_stream_eleven.py
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
    rows = [json.loads(l) for l in
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tts_stream_eleven.py::test_tts_ab_twee_modellen_met_ms -v`
Expected: FAIL with `AttributeError: module 'span.server.routes' has no attribute 'tts_ab'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/server/routes.py`, direct na de `tts_status`-handler (r1071) toevoegen:

```python
_AB_MODELS = ("eleven_flash_v2_5", "eleven_multilingual_v2")


@router.post("/api/tts_ab")
async def tts_ab(request: Request) -> dict[str, Any]:
    """Owner-only A/B-luistertest (A2): dezelfde zin door Flash v2.5 én
    Multilingual v2, batch-gesynthetiseerd, met tts_ms per model. Bas kiest op
    oor; het latencyverschil komt óók in de A1-telemetrie (mode=ab, meta.model)
    terecht. Fail-soft per model: één model kapot -> het andere komt gewoon."""
    _require_owner(request)
    import base64 as _b64
    from span.server import tts
    if tts.engine() != "elevenlabs":
        raise HTTPException(status_code=501, detail="ElevenLabs niet actief.")
    body = await request.json()
    text = (body.get("text") or "").strip()[:300]
    if not text:
        raise HTTPException(status_code=422, detail="Lege tekst.")
    from span import telemetry
    results: list[dict[str, Any]] = []
    for model_id in _AB_MODELS:
        _t0 = time.perf_counter()
        try:
            audio = await asyncio.to_thread(
                tts._synth_elevenlabs, text, None, model_id)
        except Exception as exc:
            results.append({"model": model_id, "error": str(exc)[:200]})
            continue
        ms = (time.perf_counter() - _t0) * 1000.0
        telemetry.record("tts", ms, {"mode": "ab", "model": model_id})
        results.append({"model": model_id, "tts_ms": round(ms, 1),
                        "audio_b64": _b64.b64encode(audio).decode("ascii")})
    return {"text": text, "results": results}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tts_stream_eleven.py -v`
Expected: PASS (alle tests in het bestand)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_tts_stream_eleven.py
git commit -m "feat(tts): owner-only POST /api/tts_ab — Flash v2.5 vs Multilingual v2"
```

---

## Task 8: A/B-knop in de HUD (instellingen → Stem)

> Frontend-taak: de repo heeft geen JS-testrunner, dus de pytest-stappen worden
> hier vervangen door een precieze handmatige verificatie — zelfde soort
> afwijking als Task 8 (deploy/meet) in het A1-plan.

**Files:**
- Modify: `src/span/server/static/index.html` (na r355, de `tts-test`/`tts-reset`-rij)
- Modify: `src/span/server/static/settings.js` (in `ttsInit()`, r649-739)

- [ ] **Step 1: Voeg de knop toe aan index.html**

In `src/span/server/static/index.html`, direct na de bestaande rij met `tts-test`/`tts-reset` (die eindigt op r355 met `</div>`), binnen `#tts-settings`:

```html
      <div class="setrow" id="tts-ab-row">
        <button id="tts-ab" class="ghost">A/B: Flash vs Multilingual</button>
        <div class="m" id="tts-ab-note" style="opacity:.7"></div>
      </div>
```

- [ ] **Step 2: Gate en handler in settings.js**

In `ttsInit()` (settings.js), direct na de regel `SPAN._ttsStreaming = !!s.streaming;` (r659) toevoegen:

```javascript
      // A2 — A/B-knop alleen voor de beheerder én als ElevenLabs actief is
      const abRow = $("tts-ab-row");
      if (abRow) abRow.style.display =
        (s.is_owner === false || s.engine !== "elevenlabs") ? "none" : "";
```

En onderaan `ttsInit()`, direct na het `tts-reset`-blok (r733-737), toevoegen:

```javascript
    // A2 — A/B-luistertest: dezelfde zin door Flash v2.5 en Multilingual v2.
    // Audio komt als base64-WAV terug met tts_ms per model; na elkaar afspelen
    // zodat Bas op oor kan kiezen. Fouten alleen tonen, nooit door-crashen.
    const ab = $("tts-ab");
    if (ab) ab.onclick = async () => {
      const note = $("tts-ab-note");
      ab.disabled = true;
      if (note) note.textContent = "genereren…";
      try {
        const res = await fetch("/api/tts_ab", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...SPAN.authHeaders() },
          body: JSON.stringify({
            text: "Goedemiddag Bas. Je hebt vandaag drie afspraken; " +
                  "de eerste begint om half tien.",
          }),
        });
        if (!res.ok) throw new Error("ab " + res.status);
        const d = await res.json();
        const lines = [];
        for (const r of d.results || []) {
          if (r.error) { lines.push(r.model + ": fout"); continue; }
          lines.push(r.model.replace("eleven_", "") + ": " + Math.round(r.tts_ms) + " ms");
          if (note) note.textContent = "speelt: " + r.model;
          const audio = new Audio("data:audio/wav;base64," + r.audio_b64);
          await audio.play().catch(() => {});
          await new Promise((ok) => { audio.onended = ok; audio.onerror = ok; });
        }
        if (note) note.textContent = lines.join(" · ");
      } catch (e) {
        if (note) note.textContent = "A/B-test mislukt";
      }
      ab.disabled = false;
    };
```

- [ ] **Step 3: Handmatige verificatie (lokaal, zonder cloud-key)**

Start de server lokaal, open de HUD → instellingen → tab "stem":
- Zonder `ELEVENLABS_API_KEY` (engine = piper/xtts): de A/B-rij is VERBORGEN.
- Browser-console: `document.getElementById("tts-ab-row").style.display` → `"none"`.
- Geen JS-fouten in de console bij het openen van de instellingen.

(Met key + owner-login verschijnt de knop; het echte luisteren gebeurt in Task 10 op z390.)

- [ ] **Step 4: Commit**

```bash
git add src/span/server/static/index.html src/span/server/static/settings.js
git commit -m "feat(hud): A/B-luistertestknop Flash v2.5 vs Multilingual v2 in steminstellingen"
```

---

## Task 9: Micro-bevestigingen bij lange tool-calls

> Frontend-taak: handmatige verificatie i.p.v. pytest (geen JS-testrunner).
> Geen serverwijziging nodig — de `tool`-events (start/done) bereiken de
> browser al via `on_tool` in app.py (r325-331).

**Files:**
- Modify: `src/span/server/static/voice.js` (na `ttsEnqueue`, r224-229)
- Modify: `src/span/server/static/jarvis.js` (`tool`-branch r323-327, `delta` r315-322, `done` r334-360, declaratie bij r382)

- [ ] **Step 1: SPAN.microAck() in voice.js**

Direct ná de definitie van `ttsEnqueue` (r229) toevoegen (binnen dezelfde closure — `lastTTS` en `ttsIdleServer` zijn daar in scope):

```javascript
  // A2 — micro-bevestiging: korte gesproken cue als een tool-call lang duurt,
  // zodat hoorbaar is dat LO nog bezig is. Alleen bij server-TTS, alleen als
  // voorlezen aanstaat, de spraak-pijplijn idle is en er geen barge-in loopt;
  // hoogstens één cue per beurt (SPAN._ackSpoken; jarvis.js reset bij "done").
  // Vaste korte frasen: het echo-filter (lastTTS) moet ze kunnen herkennen.
  const ACK_PHRASES = ["Momentje.", "Ik ben ermee bezig.", "Even geduld."];
  SPAN._ackSpoken = false;
  SPAN.microAck = () => {
    if (!SPAN.serverTTS || !SPAN.speakOn) return;
    if (SPAN._muteStream || SPAN._ackSpoken) return;
    if (!ttsIdleServer()) return;   // nooit door het echte antwoord heen praten
    const phrase = ACK_PHRASES[Math.floor(Math.random() * ACK_PHRASES.length)];
    SPAN._ackSpoken = true;
    lastTTS += " " + phrase;        // echo-filter rekent de cue mee
    ttsEnqueue(phrase, false);
  };
```

- [ ] **Step 2: Ack-timer in jarvis.js**

Bij de bestaande declaratie `let turnStart = 0;` (r382) toevoegen:

```javascript
let ackTimer = 0;   // A2 — micro-bevestiging bij lang lopende tool-calls
```

Vervang de `tool`-branch in `handle(msg)` (r323-327):

```javascript
  else if (msg.type === "tool") {
    // live tonen welke tool draait -> duidelijk dat Span bezig is
    if (msg.phase === "start") {
      SPAN.working(SPAN.toolLabel(msg.name) + "…");
      // A2 — draait de tool na 2,5 s nog, spreek dan een korte cue uit
      if (ackTimer) clearTimeout(ackTimer);
      ackTimer = setTimeout(() => { if (SPAN.microAck) SPAN.microAck(); }, 2500);
    } else {
      SPAN.working((SPAN._agentName || "LO") + " werkt verder…");
      if (ackTimer) { clearTimeout(ackTimer); ackTimer = 0; }
    }
  }
```

In de `delta`-branch (r315-322), direct na `SPAN.working(null);` toevoegen (tekst stroomt binnen → geen cue meer nodig):

```javascript
    if (ackTimer) { clearTimeout(ackTimer); ackTimer = 0; }
```

In de `done`-branch, direct na `SPAN._muteStream = false;` (r348) toevoegen:

```javascript
    if (ackTimer) { clearTimeout(ackTimer); ackTimer = 0; }
    SPAN._ackSpoken = false;   // volgende beurt mag weer één cue geven
```

- [ ] **Step 3: Handmatige verificatie**

Start de server lokaal, open de HUD met voorlezen aan (server-TTS):
- Browser-console: `SPAN.microAck()` → hoorbare korte frase; direct nogmaals aanroepen → stil (`SPAN._ackSpoken === true`).
- Stel een vraag die een trage tool raakt (bv. een webzoekopdracht): na ~2,5 s klinkt één cue, daarna het gewone antwoord — de cue praat er niet doorheen.
- Barge-in tijdens de cue: spreken stopt en er volgt géén tweede cue in dezelfde beurt.
- Snelle beurten (tool < 2,5 s of direct tekst): géén cue.

- [ ] **Step 4: Commit**

```bash
git add src/span/server/static/voice.js src/span/server/static/jarvis.js
git commit -m "feat(hud): gesproken micro-bevestiging bij lang lopende tool-calls"
```

---

## Task 10: Poortcheck, deploy en de A/B-keuze (deploy + meet, z390)

**Files:**
- Modify: `docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md` (A2-blok: A/B-uitkomst + activering noteren, Step 5)
- Modify (server, niet in repo): `~/nova/.env` op z390 (`SPAN_TTS_STREAMING=1` in Step 2; `ELEVENLABS_MODEL=<keuze>` in Step 5 — alleen env-var-namen, waarden nooit in repo of plan)

**Geen TDD-code — dit is de bewijs-gepoorte activering.** De flag blijft UIT tot Stap 1 het bewijs levert.

- [ ] **Step 1: Poortcheck — bevestig met A1-cijfers dat TTS de bottleneck is**

Run: `curl -s -H "Cookie: <owner-cookie>" https://nova.famspaan.nl/api/telemetry | jq`
Vergelijk `segments.tts.p50/p95` met `stt`/`llm`. Alleen als tts (mode=full) een dominant of groot aandeel van de beurt is: door naar Stap 2. Zo niet: STOP — dit plan blijft geparkeerd (flag uit, code is inert).

- [ ] **Step 2: Deploy met flag aan**

Eerst de CI-gate (zie "Afhankelijkheden & volgorde"): push- én PR-run groen vóór merge en deploy. Daarna op z390 (`ssh z390`, repo in `~/nova`) het volledige deploy-recept:

```bash
cd ~/nova
export MSYS_NO_PATHCONV=1                      # alleen nodig bij Windows-git-bash
cp docker-compose.yml /tmp/compose.z390.bak    # server-compose bewaren (lokale afwijkingen)
git fetch origin
git reset --hard origin/master                 # of origin/<a2-branch> vóór de merge
cp /tmp/compose.z390.bak docker-compose.yml    # compose terugzetten
chmod 600 .env
# Flag aan in ~/nova/.env (de ELEVENLABS_API_KEY staat er al — waarde nooit
# printen of committen; we raken alleen de SPAN_TTS_STREAMING-regel aan):
grep -q '^SPAN_TTS_STREAMING=' .env \
  && sed -i 's/^SPAN_TTS_STREAMING=.*/SPAN_TTS_STREAMING=1/' .env \
  || echo 'SPAN_TTS_STREAMING=1' >> .env
docker compose up -d --build span
sleep 25
curl -s https://nova.famspaan.nl/readyz        # verwacht: ready
```

Verifieer: `docker compose logs span | grep prewarm` toont `[tts] elevenlabs prewarm: ok`, en `GET /api/tts/status` geeft `"streaming": true` met `"engine": "elevenlabs"`.

- [ ] **Step 3: Luister en meet het stream-pad**

Stel in de HUD een vraag met voorlezen aan: eerste klank hoort merkbaar sneller te komen dan batch. Haal daarna `GET /api/telemetry` op en vergelijk `tts` mode=stream (eerste-chunk) met de eerdere mode=full-cijfers. Test ook barge-in (onderbreken tijdens spreken) — daarna moet een volgende zin gewoon klinken.

- [ ] **Step 4: A/B-luistertest — Bas kiest op oor**

HUD → instellingen → stem → "A/B: Flash vs Multilingual". Luister beide samples (Nederlandse zin), noteer de getoonde `tts_ms` per model; het duurzame verschil staat ook in de telemetrie (`mode=ab`, `meta.model`). Kies het model op oor; latency is de tiebreaker.

- [ ] **Step 5: Keuze vastleggen**

Zet de keuze in `~/nova/.env` als `ELEVENLABS_MODEL=<gekozen model-id>` en herstart span. Noteer de uitkomst (gekozen model + p50-verschil) kort in `docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md` bij het A2-blok. Commit:

```bash
git add docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md
git commit -m "docs(tts): A2 A/B-uitkomst en streaming-activering vastgelegd"
```

---

## Afhankelijkheden & volgorde

- **A1-voorwaarde is reeds vervuld:** A1 is gemerged naar `master` — `telemetry.record`/`aggregate` en de A1-instrumentatie in routes.py (r999-1010, r1043-1050) staan daar al. Baseer de A2-branch direct op `master`. `src/span/telemetry.py` en `tests/test_telemetry.py` NIET aanraken — alleen importeren.
- **CI-gate (spec-klep, les uit PR #110):** de a2-branch alleen mergen op groene CI — zowel de push-run als de PR-run moeten groen zijn, nooit mergen op rood. Ook vóór de deploy in Task 10 Step 2 eerst controleren dat de CI op de te deployen ref groen is.
- **Task-volgorde:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Taken 8 en 9 (frontend) kunnen desnoods parallel na Task 7; Task 10 is strikt als laatste en alleen na de poortcheck (Stap 1).
- **De A→B-poort (spec):** de flag gaat pas AAN (Task 10) nadat A1-cijfers bewijzen dat TTS de bottleneck is. Alle code t/m Task 9 is zonder flag volledig inert en veilig te mergen.
- **Geen nieuwe dependencies:** `websockets==16.0` en `httpx==0.28.1` staan al in constraints.txt (de Dockerfile pint via PIP_CONSTRAINT) — niets aan requirements/constraints wijzigen.
- **Read-only voor deze sessie:** `src/span/telemetry.py`, `tests/test_telemetry.py`.

## Handmatige verificatie

Wat Bas op prod (z390, nova.famspaan.nl) moet zien/horen na Task 10:

1. **Prewarm:** `docker logs span | grep prewarm` → `[tts] elevenlabs prewarm: ok` kort na de start.
2. **Status:** `GET /api/tts/status` → `"streaming": true`, `"engine": "elevenlabs"`.
3. **Eerste klank:** met voorlezen aan begint spraak merkbaar sneller dan vóór de flag (batch wachtte op de hele WAV); `GET /api/telemetry` toont `tts`-records met `meta.mode="stream"` en `meta.engine="elevenlabs"`.
4. **Barge-in:** LO onderbreken tijdens het spreken stopt de audio direct; de volgende beurt spreekt weer normaal (geen hangende verbindingen, geen dubbele audio).
5. **Fail-soft:** flag uit of ElevenLabs onbereikbaar → spraak blijft werken via het batch-pad (hooguit trager, nooit stil-kapot); een telemetrie-fout breekt nooit een beurt. Let op het mid-stream-geval: faalt de WS pas ná de eerste chunks, dan speelt de HUD die zin afgeknot af zonder batch-fallback (de fallback triggert alleen op een lége stream) — geaccepteerd goed-genoeg-gedrag voor A2, maar de fout moet zichtbaar zijn in de serverlog (`[tts] elevenlabs stream-fout: ...`) en in de telemetrie (`mode=stream`, `outcome=error`, `error_class`); de volgende beurt spreekt weer normaal.
6. **Micro-bevestiging:** bij een vraag die een trage tool raakt (>2,5 s) klinkt één korte cue ("Momentje." o.i.d.), maximaal één per beurt, nooit door het antwoord heen.
7. **A/B:** de knop in instellingen → stem speelt beide modellen na elkaar en toont ms per model; Bas' keuze staat daarna als `ELEVENLABS_MODEL` in de server-env en in de design-spec genoteerd.

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (A2-blok):**
- ElevenLabs-WS-streaming "goed-genoeg" (per zin één WS-call, geen persistente verbinding — dat is B2) → Tasks 3/5. ✓
- A/B-luistertest Flash v2.5 vs Multilingual v2, keuze op oor + latency uit A1-telemetrie (`meta.model`, `mode=ab` én direct `tts_ms` in het endpoint-antwoord) → Tasks 2/7/8/10. ✓
- Micro-bevestigingen bij lange tool-calls → Task 9 (puur frontend; tool-events bestonden al). ✓
- Prewarm tegen cold-start → Tasks 3/4. ✓
- Poort + feature-flag default UIT (`SPAN_TTS_STREAMING`) → Tasks 1/10; alles vóór Task 10 is inert. ✓
- Niets uit fase B/C gebouwd (geen persistente WS, geen reconnect/backpressure, geen EU-residency-werk). ✓

**Placeholder-scan:** geen TBD/TODO; elke code-stap toont volledige code. De frontend-taken (8/9) vervangen pytest door expliciete handmatige verificatie omdat de repo geen JS-testrunner heeft — benoemde, bewuste afwijking.

**Type-consistentie:** `stream_pcm(text, speaker=None, model_id=None) -> AsyncIterator[bytes]` identiek in Tasks 3/5 en de fake in de route-test; `_synth_elevenlabs(text, speaker, model_id=None)` identiek in Tasks 2/7; `SAMPLE_RATE`/`X-Sample-Rate` consistent 22050 op het ElevenLabs-pad en 24000 op het XTTS-pad; flagnaam overal `SPAN_TTS_STREAMING`.

**Veiligheid:** geen secrets in code of plan (alleen env-var-namen `ELEVENLABS_API_KEY`, `SPAN_TTS_STREAMING`, `ELEVENLABS_MODEL`); alle ElevenLabs-calls in tests gemockt (FakeWS/FakeClient/fake_synth); telemetrie en prewarm best-effort — een fout breekt nooit een gesprek; barge-in sluit de WS in `finally`.

**Bestandsgroottes:** nieuwe serverlogica in `tts_stream_eleven.py` (~100 r) en `tts.py` (287→~300 r); routes.py krijgt alleen dunne endpoint-code — conform de 500-regelregel-richting uit state.py.
