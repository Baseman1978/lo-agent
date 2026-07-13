# tests/test_taakvangnet.py
"""A3 — taak-vangnet: retries, eerlijke uitkomsten, cron-toets, taak-push."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

import span.orchestrator.toolretry as tr


class TestTransientClassifier:
    """Alleen transiente fouten (429/timeout/5xx/verbinding) zijn retrybaar."""

    def test_transiente_types(self):
        assert tr.is_transient(requests.ConnectionError("verbinding weg"))
        assert tr.is_transient(requests.Timeout("read timed out"))
        assert tr.is_transient(TimeoutError("timed out"))
        assert tr.is_transient(ConnectionError("reset by peer"))

    def test_http_statuscodes(self):
        resp = requests.Response()
        resp.status_code = 503
        assert tr.is_transient(requests.HTTPError(response=resp))
        resp404 = requests.Response()
        resp404.status_code = 404
        assert not tr.is_transient(requests.HTTPError(response=resp404))

    def test_tekst_markers_en_permanente_fouten(self):
        assert tr.is_transient(RuntimeError("HTTP 429 too many requests"))
        assert tr.is_transient(RuntimeError("connection refused door proxy"))
        assert not tr.is_transient(ValueError("verkeerd argument"))
        assert not tr.is_transient(KeyError("ontbrekende sleutel"))


class TestCallWithRetry:
    def test_transient_wordt_herhaald_tot_succes(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("even weg")
            return "ok"

        result, retries = tr.call_with_retry(flaky)
        assert result == "ok" and retries == 2 and calls["n"] == 3

    def test_permanente_fout_gooit_direct_door(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("blijvend kapot")

        with pytest.raises(ValueError):
            tr.call_with_retry(broken)
        assert calls["n"] == 1

    def test_cap_op_max_retries(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def always_down():
            calls["n"] += 1
            raise requests.Timeout("blijft traag")

        with pytest.raises(requests.Timeout):
            tr.call_with_retry(always_down)
        assert calls["n"] == 1 + tr.MAX_RETRIES

    def test_flag_schakelt(self, monkeypatch):
        monkeypatch.setenv("SPAN_TOOL_RETRY", "off")
        assert not tr.retry_enabled()
        monkeypatch.setenv("SPAN_TOOL_RETRY", "on")
        assert tr.retry_enabled()
