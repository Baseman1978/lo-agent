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
    lines = (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    assert row["seg"] == "stt" and row["ms"] == 100.0 and row["meta"]["backend"] == "cpu-local"


def test_flag_off_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "off")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    tel.record("stt", 123.0)
    assert not (tmp_path / "t.jsonl").exists()
    assert tel.aggregate()["segments"] == {}


def test_rotation_keeps_file_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(target))
    monkeypatch.setattr(tel, "_MAX_BYTES", 200)  # forceer rotatie snel
    for i in range(50):
        tel.record("llm", float(i), {"i": i})
    assert target.stat().st_size <= 200 + 500  # net na rotatie klein
    assert target.with_suffix(".jsonl.prev").exists()


def test_record_bad_value_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    tel.record("stt", None)  # non-numeric: must be swallowed, not raised
    tel.record("stt", "oops")
    # a genuinely valid record still works afterwards
    tel.record("stt", 150.0)
    assert tel.aggregate()["segments"]["stt"]["count"] == 1


def test_turn_records_segments(tmp_path, monkeypatch):
    """turn() legt turn+llm+tool vast zonder de beurt te breken."""
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    from unittest.mock import MagicMock
    from span.orchestrator.agent import SpanAgent

    agent = SpanAgent.__new__(SpanAgent)  # omzeil __init__: we testen alleen turn-instrumentatie

    tb = MagicMock()
    tb.specs_for.return_value = []
    tb.touched = []
    tb.dispatch.side_effect = lambda name, args: "ok"
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

    # achtergrond-helpers stubben zodat turn() geen DB raakt
    agent._record_turn = lambda *a, **k: None
    agent._persist_messages = lambda *a, **k: None
    agent._verify_active_quest = lambda *a, **k: None
    agent._write_trace = lambda *a, **k: None
    agent.last_touched = []

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


def test_stt_route_records_stt_segment(tmp_path, monkeypatch):
    """De echte /api/stt-handler legt een stt-segment vast na een transcriptie."""
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    import asyncio

    from span.server import routes
    from span.server import stt
    from span.server.routes import speech_to_text

    # auth uitschakelen zodat we de transcribe-tak bereiken
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe", lambda audio, language="nl": "hallo")

    # minimale request-double: body() async, headers dict-achtig
    audio = b"\x1aE\xdf\xa3" + b"\x00" * 2000  # EBML-magic + genoeg bytes

    class _Req:
        headers = {}

        async def body(self):
            return audio

    result = asyncio.run(speech_to_text(_Req()))
    assert result["text"] == "hallo"
    assert tel.aggregate()["segments"]["stt"]["count"] >= 1
