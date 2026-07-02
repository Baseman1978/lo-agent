"""WP-C4 — retry-policy: niet-idempotente sends niet blind herhalen (audit H2)."""

from __future__ import annotations

import pytest
import requests

from span.integrations import http as H


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.headers: dict = {}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(H.time, "sleep", lambda *_a, **_k: None)


def test_idempotent_timeout_retryt(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def do():
        calls["n"] += 1
        raise requests.Timeout()

    with pytest.raises(requests.Timeout):
        H.request_with_retry(do, attempts=3, idempotent=True)
    assert calls["n"] == 3   # idempotent -> alle pogingen


def test_niet_idempotent_timeout_geen_retry(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def do():
        calls["n"] += 1
        raise requests.Timeout()

    with pytest.raises(requests.Timeout):
        H.request_with_retry(do, attempts=3, idempotent=False)
    assert calls["n"] == 1   # dubbelzinnig na send -> geen retry


def test_niet_idempotent_503_niet_herhaald(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def do():
        calls["n"] += 1
        return _Resp(503)

    r = H.request_with_retry(do, attempts=3, idempotent=False)
    assert r.status_code == 503 and calls["n"] == 1   # 503 niet in {429}


def test_niet_idempotent_429_wel_herhaald(monkeypatch):
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def do():
        calls["n"] += 1
        return _Resp(429) if calls["n"] < 2 else _Resp(200)

    r = H.request_with_retry(do, attempts=3, idempotent=False)
    assert r.status_code == 200 and calls["n"] == 2   # throttle -> wel herhaald

    # connectiefout (vóór verzenden) is óók veilig te herhalen
    calls2 = {"n": 0}

    def do2():
        calls2["n"] += 1
        if calls2["n"] < 2:
            raise requests.ConnectionError()
        return _Resp(200)

    r2 = H.request_with_retry(do2, attempts=3, idempotent=False)
    assert r2.status_code == 200 and calls2["n"] == 2
