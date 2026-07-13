# tests/test_brain_health.py
"""A4 — index-gezondheid: SHOW INDEXES vs. de verwachte set + latency-probe."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.db.health as health


def _rij(name, state="ONLINE", typ="RANGE", pct=100.0):
    return {"name": name, "state": state, "type": typ, "populationPercent": pct}


def test_expected_indexes_dekken_vector_en_range():
    # de vijf vector-indexen + message_session + alle A4-range-indexen
    assert "mf_embedding" in health.EXPECTED_INDEXES
    assert "message_embedding" in health.EXPECTED_INDEXES
    assert "insight_embedding" in health.EXPECTED_INDEXES
    assert "message_session" in health.EXPECTED_INDEXES
    assert "quest_status" in health.EXPECTED_INDEXES


def test_index_health_ok_bij_alles_online():
    brain = MagicMock()
    brain.run.return_value = [_rij(n) for n in health.EXPECTED_INDEXES]
    out = health.index_health(brain)
    assert out["ok"] is True
    assert out["missing"] == [] and out["not_online"] == []


def test_index_health_ziet_missend_en_niet_online():
    brain = MagicMock()
    rows = [_rij(n) for n in health.EXPECTED_INDEXES]
    kwijt = rows.pop()                 # één verwachte index ontbreekt
    rows[0]["state"] = "POPULATING"    # en één is nog niet ONLINE
    brain.run.return_value = rows
    out = health.index_health(brain)
    assert out["ok"] is False
    assert kwijt["name"] in out["missing"]
    assert rows[0]["name"] in out["not_online"]


def test_brain_latency_ms_meet_een_probe():
    brain = MagicMock()
    brain.run.return_value = [{"ok": 1}]
    ms = health.brain_latency_ms(brain)
    assert isinstance(ms, float) and ms >= 0.0
    brain.run.assert_called_once_with("RETURN 1 AS ok")


def test_brain_health_endpoint_owner_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import span.server.routes as routes

    brain = MagicMock()
    brain.run.side_effect = [
        [_rij(n) for n in health.EXPECTED_INDEXES],  # SHOW INDEXES
        [{"ok": 1}],                                  # latency-probe RETURN 1
    ]
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes, "_request_context",
                        lambda request: SimpleNamespace(brain=brain))

    out = asyncio.run(routes.brain_health(MagicMock()))
    assert out["ok"] is True
    assert out["latency_ms"] >= 0.0


def test_brain_health_endpoint_faalt_zacht_bij_kapot_brein(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import span.server.routes as routes

    brain = MagicMock()
    brain.run.side_effect = RuntimeError("neo4j down")
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes, "_request_context",
                        lambda request: SimpleNamespace(brain=brain))

    out = asyncio.run(routes.brain_health(MagicMock()))
    assert out["ok"] is False
    assert "RuntimeError" in out["error"]
