"""Gedeelde serverstaat en helpers.

Eén `_state`-dict (gevuld door de lifespan in app.py) plus de helpers die
zowel de routes als de WebSocket nodig hebben: auth, effectieve settings,
audit-log en het tool-overzicht. Apart gehouden zodat app.py (wiring) en
routes.py (endpoints) allebei onder de 500-regelgrens blijven en geen
circulaire import nodig hebben.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from span.config import Settings

STATIC_DIR = Path(__file__).parent / "static"

# -- Microsoft-sessie (na OIDC-login) --------------------------------------

SESSION_COOKIE = "span_session"
SESSION_MAX_AGE = 24 * 3600  # 24 uur


def _session_secret() -> str:
    """Sleutel om de sessie-cookie te ondertekenen. Een eigen secret heeft de
    voorkeur; anders hergebruiken we de bestaande sterke random-secrets."""
    return (os.environ.get("SPAN_SESSION_SECRET", "").strip()
            or os.environ.get("SPAN_AUTH_TOKEN", "").strip()
            or os.environ.get("SPAN_AUDIT_HMAC_KEY", "").strip())


def _session_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret() or "insecure-dev-only",
                                  salt="span-session-v1")


def make_session(claims: dict[str, Any]) -> str:
    """Bouw de ondertekende sessie-waarde uit de id_token-claims."""
    return _session_serializer().dumps({
        "oid": claims.get("oid") or claims.get("sub") or "",
        "upn": (claims.get("preferred_username") or claims.get("email") or "").lower(),
        "name": claims.get("name") or "",
    })


def read_session(token: str) -> dict[str, Any] | None:
    if not token or not _session_secret():
        return None
    try:
        return _session_serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired, Exception):
        return None


def _session_user(request: Request) -> dict[str, Any] | None:
    return read_session(request.cookies.get(SESSION_COOKIE, ""))

# door de lifespan gevuld; alle modules delen deze ene dict-referentie
_state: dict[str, Any] = {}

GRAPH_LABELS = ["Identity", "MemoryFragment", "Insight", "Mistake", "Idea",
                "Quest", "QuestStep", "Skill", "Protocol", "Session", "Entity",
                "Meeting", "Document"]


# -- auth ------------------------------------------------------------------

def _auth_token() -> str:
    return os.environ.get("SPAN_AUTH_TOKEN", "").strip()


def _is_local(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def _check_token(token: str, client_host: str | None,
                 forwarded: bool = False) -> bool:
    expected = _auth_token()
    if expected:
        return hmac.compare_digest(token, expected)
    # geen token gezet: alleen écht lokaal. Een request dat via een proxy of
    # tunnel binnenkomt (X-Forwarded-For) lijkt lokaal maar is het niet.
    return _is_local(client_host) and not forwarded


def _require_rest_auth(request: Request) -> None:
    # 1) Microsoft-sessie (browser-login) — de primaire weg zodra web-login aan staat
    if _session_user(request) is not None:
        return
    # 2) bearer-token — voor API/cron/legacy en lokale dev zonder web-login
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    client_host = request.client.host if request.client else None
    forwarded = bool(request.headers.get("x-forwarded-for")
                     or request.headers.get("x-real-ip"))
    if not _check_token(token, client_host, forwarded=forwarded):
        raise HTTPException(status_code=401, detail="Ongeldige of ontbrekende token.")


# -- afgeleide config / audit ----------------------------------------------

def _effective_settings() -> Settings:
    """Basis-settings + runtime model-overrides (instellingenpagina)."""
    base: Settings = _state["settings"]
    ov = _state.get("model_overrides") or {}
    main = (ov.get("model_main") or "").strip() if ov.get("model_main") else ""
    light = (ov.get("model_light") or "").strip() if ov.get("model_light") else ""
    if not main and not light:
        return base
    return replace(
        base,
        model_main=main or base.model_main,
        model_light=light or base.model_light,
    )


def _audit(action: str, detail: str) -> None:
    """Audit-log in het brein: wat heeft Span namens Bas gedaan."""
    # F4.6: tamper-evident hash-keten i.p.v. een losse CREATE
    from span.safety.audit import record_action
    record_action(_state["brain"], action, detail)


def _tools_overview() -> list[dict[str, Any]]:
    """Alle tools met groep, lees/schrijf en status — voor de permissie-tab."""
    from span.orchestrator.tools import TOOL_META
    disabled = _state.get("disabled_tools") or set()
    available_groups = {
        "Brein": True, "Briefing": True, "Agent Inbox": True,
        "O365 Mail": _state.get("o365") is not None,
        "O365 Agenda": _state.get("o365") is not None,
        "O365 To Do": _state.get("o365") is not None,
        "Asana": _state.get("asana") is not None,
        "Werkdata": _state.get("work") is not None,
        "Weer": True,
        "Fireflies": _state.get("fireflies") is not None,
        "Planning": True,
    }
    return [
        {"name": name, "group": group, "access": access,
         "enabled": name not in disabled,
         "available": available_groups.get(group, True)}
        for name, (group, access) in TOOL_META.items()
    ]
