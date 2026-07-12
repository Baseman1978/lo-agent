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
