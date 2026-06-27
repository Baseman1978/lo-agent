"""Microsoft SSO — OIDC auth-code login + ondertekende sessie.

Vervangt de gedeelde SPAN_AUTH_TOKEN-poort door een Microsoft-aanmelding. Eén
login levert identiteit (id_token-claims -> sessie-cookie) én de Graph-token die
de connectors gebruiken (msal vult dezelfde token-cache als de O365-client).

Patroon spiegelt de bestaande MCP-OAuth-flow (routes.py): PKCE + state worden
door msal beheerd; de pending-flow heeft een TTL.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from span.server.state import (
    SESSION_COOKIE, SESSION_MAX_AGE, _state, make_session,
)

router = APIRouter()

_PENDING_LOCK = threading.Lock()
_PENDING_TTL = 600.0  # seconden — een achtergebleven login-poging vervalt


def _web_login_on() -> bool:
    settings = _state.get("settings")
    o365 = _state.get("o365")
    return bool(o365 is not None and settings is not None
                and settings.jarvis.web_login_enabled)


def _redirect_uri(request: Request) -> str:
    """De callback-URL op deze server, met respect voor de externe host achter
    de Cloudflare-tunnel (X-Forwarded-*). Moet exact matchen met de redirect-URI
    op de Entra-app."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host}/auth/callback"


@router.get("/auth/login")
async def auth_login(request: Request) -> Any:
    if not _web_login_on():
        raise HTTPException(status_code=404,
                            detail="Web-login niet geconfigureerd (MS_CLIENT_SECRET).")
    o365 = _state["o365"]
    flow = await asyncio.to_thread(o365.build_auth_flow, _redirect_uri(request))
    with _PENDING_LOCK:
        pend = _state.setdefault("auth_pending", {})
        for st in [k for k, v in pend.items()
                   if time.time() - v.get("ts", 0) > _PENDING_TTL]:
            pend.pop(st, None)
        pend[flow["state"]] = {"flow": flow, "ts": time.time()}
    return RedirectResponse(flow["auth_uri"], status_code=302)


@router.get("/auth/callback")
async def auth_callback(request: Request) -> Any:
    params = dict(request.query_params)
    state = params.get("state", "")
    with _PENDING_LOCK:
        pending = (_state.get("auth_pending") or {}).pop(state, None)
    if not pending or not _web_login_on():
        return PlainTextResponse("Ongeldige of verlopen login — start opnieuw.",
                                 status_code=400)
    if time.time() - pending.get("ts", 0) > _PENDING_TTL:
        return PlainTextResponse("Login verlopen — start opnieuw.", status_code=400)
    o365 = _state["o365"]
    try:
        claims = await asyncio.to_thread(o365.redeem_auth_flow, pending["flow"], params)
    except Exception as exc:
        return PlainTextResponse(f"Login mislukt: {exc}", status_code=400)
    if not claims.get("oid") and not claims.get("sub"):
        return PlainTextResponse("Geen geldige identiteit ontvangen.", status_code=400)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE, make_session(claims), max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax", secure=True, path="/",
    )
    return resp


@router.get("/auth/logout")
async def auth_logout() -> Any:
    resp = RedirectResponse("/auth/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    from span.server.state import _session_user
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Niet ingelogd.")
    return user


@router.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """Onbeveiligd: vertelt de HUD of web-login aan staat en of er een sessie is.
    Zo weet de frontend of 'ie om een token moet vragen (token-modus) of niet
    (SSO-modus: de cookie regelt het)."""
    from span.server.state import _session_user
    return {"web_login": _web_login_on(),
            "authenticated": _session_user(request) is not None}
