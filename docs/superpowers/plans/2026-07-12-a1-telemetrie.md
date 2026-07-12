# A1 — Beurt-telemetrie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LO meet elke gespreksbeurt in aparte latency-segmenten (stt / llm / tool / tts / turn) zodat de bewijs-gepoorte fase B op cijfers beslist welk segment de bottleneck is, niet op onderbuik.

**Architecture:** Eén neutrale top-level module `span.telemetry` schrijft best-effort append-only JSONL (thread-safe, een telemetrie-fout mag nooit een beurt breken). De bestaande meetpunten worden geïnstrumenteerd op hun natuurlijke grenzen: `/api/stt` (stt), `SpanAgent.turn()` (llm + tool + turn), `/api/tts` en `/api/tts_stream` (tts). Een owner-only `GET /api/telemetry` levert per-segment p50/p95/max over een venster. Segmenten worden onafhankelijk geaggregeerd — geen per-beurt-correlatie nodig om "welk segment domineert?" te beantwoorden.

**Tech Stack:** Python 3, FastAPI, pytest. Geen nieuwe dependencies, geen nieuwe service.

---

## File Structure

- **Create** `src/span/telemetry.py` — kern: `record(seg, ms, meta)` + `aggregate(window_s)`. Top-level (geen server/orchestrator-afhankelijkheid) zodat zowel `span.server.routes` als `span.orchestrator.agent` het mogen importeren zonder laag-inversie.
- **Create** `tests/test_telemetry.py` — unit-tests voor record/aggregate/flag/rotatie.
- **Modify** `src/span/orchestrator/agent.py` — timers in `turn()` rond `self._llm.chat()` en `self._toolbox.dispatch()`; één `turn`-record vóór de eind-`return answer`.
- **Modify** `src/span/server/routes.py` — timers in `/api/stt`, `/api/tts`, `/api/tts_stream`; nieuw `GET /api/telemetry`.
- **Modify** `tests/test_observability.py` — test voor het nieuwe endpoint (past bij de bestaande observability-tests).

---

## Task 1: Telemetrie-kern — record + aggregate

**Files:**
- Create: `src/span/telemetry.py`
- Test: `tests/test_telemetry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telemetry.py
"""A1 — beurt-telemetrie: record/aggregate/flag/rotatie."""
from __future__ import annotations

import json

import span.telemetry as tel


def test_record_and_aggregate(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    for ms in (100.0, 200.0, 300.0):
        tel.record("stt", ms, {"backend": "cpu-local"})
    agg = tel.aggregate(window_s=86400.0)
    seg = agg["segments"]["stt"]
    assert seg["count"] == 3
    assert seg["p50"] == 200.0
    assert seg["max"] == 300.0
    # elke regel is geldige JSON met de verwachte velden
    lines = (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    assert row["seg"] == "stt" and row["ms"] == 100.0 and row["meta"]["backend"] == "cpu-local"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telemetry.py::test_record_and_aggregate -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.telemetry'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/telemetry.py
"""A1 — beurt-telemetrie: gesegmenteerde latency-meting als meetlat voor de
bewijs-gepoorte fase B.

Append-only JSONL, thread-safe, best-effort: een telemetrie-fout mag NOOIT een
gespreksbeurt breken. Segmenten: stt (spraak->tekst), llm (model-generatie),
tool (tool-executie), tts (tekst->eerste-klank), turn (end-to-end). De
aggregatie beantwoordt de poort-vraag "welk segment domineert?" — zonder die
cijfers is de A->B-poort blind.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from span.config import PROJECT_ROOT

_lock = threading.Lock()
_MAX_BYTES = 5_000_000  # ~5 MB -> roteer naar .prev; houdt het bestand begrensd
_MAX_TAIL = 20_000      # aggregate leest hoogstens zoveel recente regels


def _enabled() -> bool:
    val = os.environ.get("SPAN_TELEMETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def _path() -> Path:
    override = os.environ.get("SPAN_TELEMETRY_FILE", "").strip()
    return Path(override) if override else PROJECT_ROOT / "data" / "telemetry.jsonl"


def record(seg: str, ms: float, meta: dict[str, Any] | None = None) -> None:
    """Schrijf één segment-meting weg. Best-effort: slikt elke fout in."""
    if not _enabled():
        return
    row: dict[str, Any] = {"ts": time.time(), "seg": seg, "ms": round(float(ms), 1)}
    if meta:
        row["meta"] = meta
    try:
        p = _path()
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > _MAX_BYTES:
                p.replace(p.with_suffix(p.suffix + ".prev"))
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # telemetrie is best-effort; nooit de beurt breken


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 1)


def aggregate(window_s: float = 86400.0) -> dict[str, Any]:
    """Per-segment count/p50/p95/max over de laatste `window_s` seconden."""
    p = _path()
    cutoff = time.time() - window_s
    try:
        with _lock:
            lines = p.read_text(encoding="utf-8").splitlines()[-_MAX_TAIL:] if p.exists() else []
    except Exception:
        lines = []
    buckets: dict[str, list[float]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if float(row.get("ts", 0)) < cutoff:
            continue
        buckets.setdefault(str(row.get("seg", "?")), []).append(float(row.get("ms", 0)))
    segments: dict[str, Any] = {}
    for seg, vals in buckets.items():
        vals.sort()
        segments[seg] = {
            "count": len(vals),
            "p50": _percentile(vals, 0.50),
            "p95": _percentile(vals, 0.95),
            "max": round(max(vals), 1),
        }
    return {"window_s": window_s, "segments": segments}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_telemetry.py::test_record_and_aggregate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/telemetry.py tests/test_telemetry.py
git commit -m "feat(telemetry): A1 record/aggregate kern"
```

---

## Task 2: Feature-flag — SPAN_TELEMETRY=off is een no-op

**Files:**
- Test: `tests/test_telemetry.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_telemetry.py
def test_flag_off_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "off")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    tel.record("stt", 123.0)
    assert not (tmp_path / "t.jsonl").exists()
    assert tel.aggregate()["segments"] == {}
```

- [ ] **Step 2: Run test to verify it passes (flag already implemented in Task 1)**

Run: `python -m pytest tests/test_telemetry.py::test_flag_off_is_noop -v`
Expected: PASS (the `_enabled()` guard from Task 1 already covers this; this test locks the behavior)

- [ ] **Step 3: Commit**

```bash
git add tests/test_telemetry.py
git commit -m "test(telemetry): borg dat SPAN_TELEMETRY=off niets schrijft"
```

---

## Task 3: Rotatie — bestand blijft begrensd

**Files:**
- Test: `tests/test_telemetry.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_telemetry.py
def test_rotation_keeps_file_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(target))
    monkeypatch.setattr(tel, "_MAX_BYTES", 200)  # forceer rotatie snel
    for i in range(50):
        tel.record("llm", float(i), {"i": i})
    assert target.stat().st_size <= 200 + 500  # net na rotatie klein
    assert target.with_suffix(".jsonl.prev").exists()
```

- [ ] **Step 2: Run test to verify it passes (rotation implemented in Task 1)**

Run: `python -m pytest tests/test_telemetry.py::test_rotation_keeps_file_bounded -v`
Expected: PASS (the `_MAX_BYTES` rotation from Task 1 covers this)

- [ ] **Step 3: Commit**

```bash
git add tests/test_telemetry.py
git commit -m "test(telemetry): borg bestandsrotatie bij _MAX_BYTES"
```

---

## Task 4: Instrumenteer SpanAgent.turn() — llm / tool / turn

**Files:**
- Modify: `src/span/orchestrator/agent.py`

- [ ] **Step 1: Add the telemetry import**

Add to the imports block near the top of `src/span/orchestrator/agent.py` (after line `from span.orchestrator.tools import ToolBox`):

```python
from span import telemetry
```

- [ ] **Step 2: Initialize the turn timers**

In `turn()`, immediately after the toolbox guard (the line `self._toolbox._on_memory = on_memory`), add:

```python
        import time as _time
        _turn_t0 = _time.perf_counter()
        _llm_ms = 0.0
        _tool_ms = 0.0
```

- [ ] **Step 3: Time the model call**

Wrap the existing `self._llm.chat(...)` call. Replace:

```python
                message = self._llm.chat(
                    self._messages + ([memo_msg] if memo_msg else []),
                    model=self._settings.model_main,
                    tools=turn_tools,
                    on_text=on_text,
                )
```

with:

```python
                _c0 = _time.perf_counter()
                message = self._llm.chat(
                    self._messages + ([memo_msg] if memo_msg else []),
                    model=self._settings.model_main,
                    tools=turn_tools,
                    on_text=on_text,
                )
                _llm_ms += (_time.perf_counter() - _c0) * 1000.0
```

- [ ] **Step 4: Time each tool dispatch**

Replace the existing dispatch block:

```python
                result = self._toolbox.dispatch(tc.function.name, arguments)
                if on_tool:
                    try: on_tool(tc.function.name, "done")
                    except Exception: pass
```

with:

```python
                _d0 = _time.perf_counter()
                result = self._toolbox.dispatch(tc.function.name, arguments)
                _dt = (_time.perf_counter() - _d0) * 1000.0
                _tool_ms += _dt
                telemetry.record("tool", _dt, {"name": tc.function.name})
                if on_tool:
                    try: on_tool(tc.function.name, "done")
                    except Exception: pass
```

- [ ] **Step 5: Record the turn + llm segments before returning**

Directly BEFORE the final `return answer` line at the end of `turn()`, add:

```python
        _total_ms = (_time.perf_counter() - _turn_t0) * 1000.0
        telemetry.record("turn", _total_ms,
                         {"outcome": "cancelled" if cancelled else "ok",
                          "tools": len(tools_used)})
        if _llm_ms:
            telemetry.record("llm", _llm_ms, {"tools": len(tools_used)})
```

- [ ] **Step 6: Write the integration test**

```python
# append to tests/test_telemetry.py
def test_turn_records_segments(tmp_path, monkeypatch):
    """turn() legt turn+llm+tool vast zonder de beurt te breken."""
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    from unittest.mock import MagicMock
    from span.orchestrator.agent import SpanAgent

    agent = SpanAgent.__new__(SpanAgent)  # omzeil __init__: we testen alleen turn-instrumentatie

    # minimale doubles voor de paden die turn() raakt
    tb = MagicMock()
    tb.specs_for.return_value = []
    tb.touched = []
    def _dispatch(name, args):
        return "ok"
    tb.dispatch.side_effect = _dispatch
    agent._toolbox = tb
    agent._messages = []
    agent._recorders = []

    frag = MagicMock()
    frag.embed.return_value = [0.0]
    frag.search.return_value = []
    frag.search_formal.return_value = []
    agent._fragments = frag

    settings = MagicMock()
    settings.model_main = "test-model"
    agent._settings = settings
    agent._security = {}

    # één LLM-antwoord met tool-call, daarna een leeg antwoord dat afsluit
    call = MagicMock()
    tool_call = MagicMock()
    tool_call.id = "1"
    tool_call.function.name = "brain_search"
    tool_call.function.arguments = "{}"
    first = MagicMock(); first.content = ""; first.tool_calls = [tool_call]
    second = MagicMock(); second.content = "antwoord"; second.tool_calls = None
    llm = MagicMock()
    llm.chat.side_effect = [first, second]
    agent._llm = llm

    out = agent.turn("hoi")
    assert "antwoord" in out

    agg = tel.aggregate()
    assert agg["segments"]["turn"]["count"] == 1
    assert agg["segments"]["tool"]["count"] == 1
    assert agg["segments"]["llm"]["count"] == 1
```

> Note: if `turn()` touches an attribute this double doesn't provide (e.g. `self.last_touched`, `self._record_turn`), set it on the `agent` object or `monkeypatch` the background-thread helpers to no-ops (`agent._record_turn = lambda *a, **k: None`, same for `_persist_messages`, `_verify_active_quest`, `_write_trace`). Add those lines until the test exercises `turn()` cleanly. Do NOT change `turn()`'s logic to fit the test.

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_telemetry.py::test_turn_records_segments -v`
Expected: PASS

- [ ] **Step 8: Run the full telemetry + agent test set (no regression)**

Run: `python -m pytest tests/test_telemetry.py tests/test_jarvis.py -q`
Expected: PASS (no existing behavior changed)

- [ ] **Step 9: Commit**

```bash
git add src/span/orchestrator/agent.py tests/test_telemetry.py
git commit -m "feat(telemetry): meet llm/tool/turn-latency in SpanAgent.turn"
```

---

## Task 5: Instrumenteer /api/stt — stt_ms + backend

**Files:**
- Modify: `src/span/server/routes.py`

- [ ] **Step 1: Add timing around the transcribe call**

In the `/api/stt` handler, replace:

```python
        text = await asyncio.to_thread(stt.transcribe, audio)
```

with:

```python
        _t0 = time.perf_counter()
        text = await asyncio.to_thread(stt.transcribe, audio)
        from span import telemetry
        telemetry.record("stt", (time.perf_counter() - _t0) * 1000.0,
                         {"backend": stt.backend()})
```

(`time` is already imported at the top of `routes.py`.)

- [ ] **Step 2: Write the test**

```python
# append to tests/test_telemetry.py
def test_stt_endpoint_records(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    import span.server.routes as routes
    import span.server.stt as stt

    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe", lambda audio, language="nl": "hallo")

    req = MagicMock()
    async def _body():
        return b"RIFFxxxxWAVE"  # bytes-payload; content-type wordt geaccepteerd
    req.body = _body
    req.headers = {"content-type": "audio/webm"}

    out = asyncio.run(routes.stt_transcribe(req)) if hasattr(routes, "stt_transcribe") else None
    # de exacte handlernaam kan afwijken; deze test borgt vooral dat er een
    # stt-segment wordt weggeschreven na een transcriptie
    agg = tel.aggregate()
    assert agg["segments"].get("stt", {}).get("count", 0) >= 1
```

> Note: the `/api/stt` handler's function name may differ from `stt_transcribe`. Before writing this test, open `src/span/server/routes.py` at the `@router.post("/api/stt")` decorator and use the actual function name. Adjust the request double to match how the handler reads the body (it reads raw bytes and checks the content-type). Keep the assertion (an `stt` segment is recorded) unchanged.

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_telemetry.py::test_stt_endpoint_records -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/span/server/routes.py tests/test_telemetry.py
git commit -m "feat(telemetry): meet stt-latency op /api/stt"
```

---

## Task 6: Instrumenteer /api/tts en /api/tts_stream — tts_ms

**Files:**
- Modify: `src/span/server/routes.py`

- [ ] **Step 1: Time the synthesize call in /api/tts**

In `text_to_speech()`, replace:

```python
        audio = await asyncio.to_thread(
            tts.synthesize, text,
            speaker=speaker,
            speaker_id=spk,
            length_scale=_num("length_scale", 0.5, 2.0),
            noise_scale=_num("noise_scale", 0.0, 1.5),
            noise_w_scale=_num("noise_w_scale", 0.0, 1.5),
            volume=_num("volume", 0.1, 2.0),
        )
```

with:

```python
        _t0 = time.perf_counter()
        audio = await asyncio.to_thread(
            tts.synthesize, text,
            speaker=speaker,
            speaker_id=spk,
            length_scale=_num("length_scale", 0.5, 2.0),
            noise_scale=_num("noise_scale", 0.0, 1.5),
            noise_w_scale=_num("noise_w_scale", 0.0, 1.5),
            volume=_num("volume", 0.1, 2.0),
        )
        from span import telemetry
        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0, {"mode": "full"})
```

- [ ] **Step 2: Time first-chunk in /api/tts_stream**

Inside `tts_stream()`'s inner `gen()` async generator, record the time to the first non-empty chunk. Replace the loop:

```python
                async for chunk in r.aiter_bytes():
```

with (keeping the existing body that follows, e.g. `yield chunk`):

```python
                _t0 = time.perf_counter()
                _first = True
                async for chunk in r.aiter_bytes():
                    if _first and chunk:
                        from span import telemetry
                        telemetry.record("tts", (time.perf_counter() - _t0) * 1000.0,
                                         {"mode": "stream"})
                        _first = False
```

> Note: open `routes.py` at line ~1014 and preserve whatever the loop body already does (it yields `chunk`). Only add the timing lines; do not remove the existing `yield`/processing.

- [ ] **Step 3: Write the test for /api/tts**

```python
# append to tests/test_telemetry.py
def test_tts_endpoint_records(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    import span.server.routes as routes
    import span.server.tts as ttsmod

    monkeypatch.setattr(ttsmod, "available", lambda: True)
    monkeypatch.setattr(ttsmod, "synthesize", lambda text, **kw: b"RIFF....WAVE")
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)

    req = MagicMock()
    async def _json():
        return {"text": "hallo wereld"}
    req.json = _json

    asyncio.run(routes.text_to_speech(req))
    agg = tel.aggregate()
    assert agg["segments"]["tts"]["count"] == 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_telemetry.py::test_tts_endpoint_records -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_telemetry.py
git commit -m "feat(telemetry): meet tts-latency op /api/tts en /api/tts_stream"
```

---

## Task 7: GET /api/telemetry — owner-only aggregaten

**Files:**
- Modify: `src/span/server/routes.py`
- Modify: `tests/test_observability.py`

- [ ] **Step 1: Add the endpoint**

Add near the other `@router.get` observability routes in `routes.py`:

```python
@router.get("/api/telemetry")
async def telemetry_aggregates(request: Request) -> dict[str, Any]:
    """Owner-only: per-segment latency-aggregaten (stt/llm/tool/tts/turn).
    De meetlat voor de bewijs-gepoorte fase B — welk segment domineert?"""
    _require_owner(request)
    from span import telemetry
    window_s = 86400.0
    q = request.query_params.get("window_s")
    if q:
        try:
            window_s = max(60.0, min(2_592_000.0, float(q)))  # 1 min .. 30 dagen
        except ValueError:
            pass
    return telemetry.aggregate(window_s=window_s)
```

> Note: `_require_owner` is already imported in `routes.py` (from `span.server.state`). Confirm it is in the existing import list at the top; if not, add it there.

- [ ] **Step 2: Write the test**

```python
# append to tests/test_observability.py
def test_telemetry_endpoint_owner_only(tmp_path, monkeypatch):
    import span.telemetry as tel
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    tel.record("turn", 1500.0, {"outcome": "ok"})

    from unittest.mock import MagicMock
    req = MagicMock()
    req.query_params = {}
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)

    out = asyncio.run(routes.telemetry_aggregates(req))
    assert out["segments"]["turn"]["count"] == 1
    assert out["window_s"] == 86400.0
```

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_observability.py::test_telemetry_endpoint_owner_only -v`
Expected: PASS

- [ ] **Step 4: Full observability + telemetry sweep**

Run: `python -m pytest tests/test_observability.py tests/test_telemetry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_observability.py
git commit -m "feat(telemetry): owner-only GET /api/telemetry aggregaten"
```

---

## Task 8: De twee schakelaars — meet baseline, dan flip (deploy + measure)

**Geen TDD-code — dit is een deploy/meet-taak op z390.** A1's doel is bereikt zodra we per segment cijfers hebben vóór én na elke schakelaar, zodat de A→B-poort op bewijs beslist.

- [ ] **Step 1: Deploy de telemetrie naar prod (z390) en verifieer readyz**

Volg het bestaande deploy-patroon (compose bewaren, `git fetch && git reset --hard origin/master`, compose terugzetten, `chmod 600 .env`, `docker compose up -d --build span`, `sleep 25`, `curl readyz`). `export MSYS_NO_PATHCONV=1` bij Windows-git-bash.

- [ ] **Step 2: Leg de baseline vast (24u normale conversatie)**

Laat LO een dag draaien met de huidige config (CPU-Whisper, niet-streaming TTS). Haal daarna op:

Run: `curl -s -H "Cookie: <owner-cookie>" https://nova.famspaan.nl/api/telemetry | jq`
Noteer p50/p95 per segment. Dit is de nulmeting.

- [ ] **Step 3: Schakelaar 1 — SPAN_STT_URL naar GPU-Whisper (:9000 op z390)**

Zet `SPAN_STT_URL` in `~/nova/.env` op de GPU-whisper-container (OpenAI-compatibele `/v1/audio/transcriptions`), `chmod 600 .env`, herstart span. Verifieer `GET /api/stt/status` toont `backend: gpu-remote`. Meet `stt`-segment opnieuw na een dag; vergelijk met de baseline.

- [ ] **Step 4: Schakelaar 2 — streaming-TTS aan**

Zet de TTS-engine op de XTTS-streaming-backend (bestaande `tts_engine`-instelling / `XTTS_URL`). Verifieer dat `/api/tts_stream` een 200 geeft. Meet `tts`-segment (`mode: stream`) opnieuw; vergelijk met `mode: full`.

- [ ] **Step 5: Schrijf de meetlat-conclusie in de spec**

Voeg aan `docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md` onder §1 een korte tabel toe: per segment p50/p95 baseline vs. na-schakelaar. Deze cijfers voeden de A→B-poortbeslissingen (B1/B2). Commit.

```bash
git add docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md
git commit -m "docs(telemetry): A1 baseline-metingen als fase-B-meetlat"
```

---

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (§1 telemetrie-first + A1 blok):**
- Gesegmenteerde meting stt/llm/tool/tts/turn → Tasks 4/5/6 (instrumentatie), Task 1 (opslag). ✓
- Aggregatie-endpoint → Task 7. ✓
- "Geen nieuwe service; draait in de bestaande FastAPI-server" → module + endpoint in-process. ✓
- Twee schakelaars (streaming-TTS, SPAN_STT_URL) → Task 8. ✓
- Feature-flag SPAN_TELEMETRY → Task 2. ✓
- Best-effort ("mag nooit een beurt breken") → `record()` slikt alle fouten; alle turn-instrumentatie is buiten de kritieke try/except-paden. ✓

**Placeholder-scan:** geen TBD/TODO; alle code-stappen tonen echte code. Twee expliciete "Note"-blokken (Task 5 handlernaam, Task 6 gen-body) markeren waar de engineer de bestaande code moet aflezen i.p.v. gokken — dat is bewuste precisie, geen placeholder.

**Type-consistentie:** `record(seg, ms, meta)` en `aggregate(window_s)` identiek gebruikt in Tasks 1/4/5/6/7. Segment-namen `stt`/`llm`/`tool`/`tts`/`turn` consistent. `_require_owner`/`_require_rest_auth` matchen de bestaande signatures uit `state.py`.

**Scope:** A1 is één subsysteem (telemetrie). A2–A5 krijgen elk hun eigen plan, geschreven wanneer we er zijn, gevoed door A1's cijfers. ✓
