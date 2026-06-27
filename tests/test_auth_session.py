"""Microsoft-sessie: ondertekende cookie + config-schakelaar voor web-login."""

import pytest

from span.config import JarvisConfig
from span.server import state


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    monkeypatch.delenv("SPAN_SESSION_SECRET", raising=False)
    monkeypatch.delenv("SPAN_AUDIT_HMAC_KEY", raising=False)
    monkeypatch.setenv("SPAN_AUTH_TOKEN", "a" * 64)


CLAIMS = {"oid": "abc-123", "preferred_username": "B.Spaan@Lomans.nl", "name": "Bas Spaan"}


def test_session_roundtrip():
    token = state.make_session(CLAIMS)
    user = state.read_session(token)
    assert user is not None
    assert user["oid"] == "abc-123"
    assert user["upn"] == "b.spaan@lomans.nl"  # genormaliseerd naar lowercase
    assert user["name"] == "Bas Spaan"


def test_tampered_token_rejected():
    token = state.make_session(CLAIMS)
    assert state.read_session(token + "x") is None
    assert state.read_session("garbage") is None
    assert state.read_session("") is None


def test_other_secret_rejected(monkeypatch):
    token = state.make_session(CLAIMS)
    monkeypatch.setenv("SPAN_AUTH_TOKEN", "b" * 64)  # andere sleutel
    assert state.read_session(token) is None


def test_no_secret_means_no_session(monkeypatch):
    monkeypatch.delenv("SPAN_AUTH_TOKEN", raising=False)
    # zonder enige secret kan er geen geldige sessie bestaan
    assert state.read_session("whatever") is None


def test_web_login_flag():
    assert JarvisConfig(ms_client_secret="").web_login_enabled is False
    assert JarvisConfig(ms_client_secret="s3cr3t").web_login_enabled is True
