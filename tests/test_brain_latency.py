# tests/test_brain_latency.py
"""A4 — brain-latency-meetpunt: BrainDB meet run/read/vector naar telemetrie."""
from __future__ import annotations

import json

import pytest

import span.telemetry as tel
from span.db.brain import BrainDB


def _braindouble():
    from unittest.mock import MagicMock
    b = BrainDB.__new__(BrainDB)  # omzeil __init__: geen echte driver nodig
    driver = MagicMock()
    rec = MagicMock()
    rec.data.return_value = {"ok": 1}
    driver.session.return_value.__enter__.return_value.run.return_value = [rec]
    b._driver = driver
    b.database = "test"
    return b, driver


def _rows(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]


def test_run_meet_brain_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, _ = _braindouble()
    assert b.run("RETURN 1 AS ok") == [{"ok": 1}]
    rows = _rows(tmp_path)
    assert rows[0]["seg"] == "brain" and rows[0]["meta"]["op"] == "run"


def test_vector_search_meet_op_vector(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, _ = _braindouble()
    b.vector_search("mf_embedding", [0.1] * 8, k=3)
    rows = _rows(tmp_path)
    # precies één record (vector gaat NIET nog eens dubbel via run)
    assert len(rows) == 1 and rows[0]["meta"]["op"] == "vector"


def test_fout_krijgt_outcome_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, driver = _braindouble()
    driver.session.return_value.__enter__.return_value.run.side_effect = \
        RuntimeError("neo4j down")
    with pytest.raises(RuntimeError):
        b.run("RETURN 1 AS ok")
    rows = _rows(tmp_path)
    assert rows[0]["meta"]["outcome"] == "error"


def test_flag_uit_meet_niets(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "off")
    b, _ = _braindouble()
    assert b.run("RETURN 1 AS ok") == [{"ok": 1}]  # query werkt gewoon
    assert not (tmp_path / "t.jsonl").exists()
    assert tel.aggregate()["segments"] == {}
