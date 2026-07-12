"""WP-C3 — observability: unauth liveness/readiness-probes (audit M-observability).

/livez  = proces leeft (geen auth, geen DB) -> voor de container-healthcheck.
/readyz = brein bereikbaar (geen auth)      -> 503 zodat een proxy/orchestrator
          verkeer kan wegleiden bij een vastgelopen database.
Beide bewust met minimale payload: geen versie of details op de unauth-surface.
"""

from __future__ import annotations

import asyncio

from unittest.mock import MagicMock

import span.server.routes as routes
import span.server.state as st


def test_livez_zonder_auth_en_zonder_db():
    # geen request, geen state, geen brein nodig -> proces leeft is genoeg
    out = asyncio.run(routes.livez())
    assert out["status"] == "ok"
    assert isinstance(out["uptime_s"], int) and out["uptime_s"] >= 0
    # geen informatie-lek op de unauth-surface
    assert set(out) == {"status", "uptime_s"}


def test_readyz_ready_bij_werkend_brein():
    brain = MagicMock()
    brain.run.return_value = [{"ok": 1}]
    st._state["brain"] = brain
    try:
        out = asyncio.run(routes.readyz())
        assert out["status"] == "ready"
    finally:
        st._state.pop("brain", None)


def test_readyz_503_bij_kapot_brein():
    brain = MagicMock()
    brain.run.side_effect = RuntimeError("neo4j down")
    st._state["brain"] = brain
    try:
        resp = asyncio.run(routes.readyz())
        assert resp.status_code == 503
    finally:
        st._state.pop("brain", None)


def test_readyz_503_zonder_brein():
    st._state.pop("brain", None)
    resp = asyncio.run(routes.readyz())
    assert resp.status_code == 503


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
