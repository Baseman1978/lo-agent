"""OAuth 2.0-login voor MCP-servers (DCR + PKCE + authorization_code).

Volgt de MCP-auth-spec: ontdek de authorization server via de well-known
metadata, registreer dynamisch een client (DCR), en doorloop de
authorization_code-flow met PKCE (S256). Span vangt de redirect op
/api/mcp/oauth/callback; geen device-flow nodig omdat Span een webserver heeft.

Tokens (access + refresh) worden per server in de Config-node bewaard (via de
mcp_servers-lijst). Bij een 401 op een MCP-call kan refresh_token() een nieuw
access_token halen zonder dat Bas opnieuw hoeft in te loggen.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from span.safety.egress import assert_egress

TIMEOUT = 20.0


def _origin(mcp_url: str) -> str:
    p = urlparse(mcp_url)
    return f"{p.scheme}://{p.netloc}"


def discover(mcp_url: str) -> dict[str, Any]:
    """Haal de authorization-server-metadata op (endpoints + capabilities).

    Egress-poort (I2): élke opgehaalde URL — ook de uit untrusted metadata
    afgeleide authorization-server — moet https zijn, op de allowlist staan en
    naar een publiek IP resolven. Voorkomt SSRF en het blind volgen van een
    aanvaller-gekozen origin."""
    base = _origin(mcp_url)
    # protected-resource wijst naar de authorization server(s)
    pr_url = urljoin(base + "/", ".well-known/oauth-protected-resource")
    assert_egress(pr_url)
    pr = requests.get(pr_url, timeout=TIMEOUT)
    as_url = base
    scopes = ["mcp:tools"]
    if pr.ok:
        d = pr.json()
        servers = d.get("authorization_servers") or [base]
        as_url = servers[0]
        scopes = d.get("scopes_supported") or scopes
    meta_url = urljoin(as_url + "/", ".well-known/oauth-authorization-server")
    assert_egress(meta_url)   # as_url komt uit untrusted JSON -> hard valideren
    meta = requests.get(meta_url, timeout=TIMEOUT)
    meta.raise_for_status()
    m = meta.json()
    m["_scopes"] = scopes
    return m


def register_client(meta: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    """Dynamic Client Registration (RFC 7591); geeft client_id (+ evt secret)."""
    reg = meta.get("registration_endpoint")
    if not reg:
        raise RuntimeError("Server ondersteunt geen dynamische registratie.")
    assert_egress(reg)   # endpoint uit untrusted metadata
    resp = requests.post(reg, json={
        "client_name": "Span",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # public client + PKCE
        "scope": " ".join(meta.get("_scopes", ["mcp:tools"])),
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def make_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) met S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(meta: dict[str, Any], client_id: str, redirect_uri: str,
                  challenge: str, state: str) -> str:
    from urllib.parse import urlencode
    q = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(meta.get("_scopes", ["mcp:tools"])),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return f"{meta['authorization_endpoint']}?{q}"


def exchange_code(meta: dict[str, Any], client_id: str, code: str,
                  verifier: str, redirect_uri: str) -> dict[str, Any]:
    assert_egress(meta["token_endpoint"])   # auth-code nooit blind naar vreemde host
    resp = requests.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def refresh_token(meta: dict[str, Any], client_id: str, refresh: str) -> dict[str, Any]:
    assert_egress(meta["token_endpoint"])   # refresh-token nooit blind naar vreemde host
    resp = requests.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
    }, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()
