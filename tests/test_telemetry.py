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
