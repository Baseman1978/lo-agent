"""WP-A1 — admin/owner-autorisatie op config/secret-routes (_require_owner)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import span.server.state as st


class _Req:
    """Minimale Request-stand-in voor de auth-helpers."""
    def __init__(self) -> None:
        self.cookies: dict = {}
        self.headers: dict = {}
        self.client = None


def _reset_contexts():
    st._state.pop("contexts", None)


def test_single_user_iedereen_is_owner(monkeypatch):
    monkeypatch.setattr(st, "_session_user", lambda r: {"oid": "x", "upn": "a@lomans.nl"})
    _reset_contexts()  # geen contexts = single-user
    st._require_owner(_Req())  # geen exception


def test_multiuser_owner_mag(monkeypatch):
    monkeypatch.setattr(st, "_session_user", lambda r: {"oid": "OWNER", "upn": "bas@lomans.nl"})
    monkeypatch.setenv("SPAN_OWNER_OID", "OWNER")
    st._state["contexts"] = object()
    try:
        st._require_owner(_Req())  # geen exception
    finally:
        _reset_contexts()


def test_multiuser_niet_owner_krijgt_403(monkeypatch):
    monkeypatch.setattr(st, "_session_user", lambda r: {"oid": "OTHER", "upn": "col@lomans.nl"})
    monkeypatch.setenv("SPAN_OWNER_OID", "OWNER")
    st._state["contexts"] = object()
    try:
        with pytest.raises(HTTPException) as exc:
            st._require_owner(_Req())
        assert exc.value.status_code == 403
    finally:
        _reset_contexts()


def test_multiuser_bearer_token_geldt_als_beheerder(monkeypatch):
    monkeypatch.setattr(st, "_session_user", lambda r: None)      # geen sessie
    monkeypatch.setattr(st, "_check_token", lambda *a, **k: True)  # geldig bearer-token
    monkeypatch.setenv("SPAN_OWNER_OID", "OWNER")
    st._state["contexts"] = object()
    try:
        st._require_owner(_Req())  # bearer = beheerder, geen exception
    finally:
        _reset_contexts()


def test_niet_geauthenticeerd_krijgt_401(monkeypatch):
    monkeypatch.setattr(st, "_session_user", lambda r: None)
    monkeypatch.setattr(st, "_check_token", lambda *a, **k: False)
    st._state["contexts"] = object()
    try:
        with pytest.raises(HTTPException) as exc:
            st._require_owner(_Req())
        assert exc.value.status_code == 401
    finally:
        _reset_contexts()
