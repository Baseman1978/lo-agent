"""A6 — WhatsApp-webhook (Meta Cloud API).

GET  = verificatie-handshake: Meta stuurt hub.mode/hub.verify_token/hub.challenge
       en verwacht de challenge plain terug (zelfde vorm als de validationToken-tak
       van /api/webhooks/graph).
POST = berichten (Task 7): X-Hub-Signature-256 over de RAUWE body, daarna direct
       200 — Meta's timeout is ~10s met redelivery en een agent-beurt duurt
       seconden, dus de afhandeling draait als achtergrondtaak.

De payload is untrusted input: hij wordt uitsluitend als user-bericht van een
allowlisted nummer behandeld, nooit als instructie voor deze laag. Auth is de
eigen verificatie (verify-token/signature) — bewust géén _require_rest_auth,
zoals de bestaande webhook-routes. Fail-closed: zonder env-config -> 404.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()

# referenties op lopende achtergrondtaken (anders kan de GC ze opruimen)
_bg_tasks: set[asyncio.Task] = set()


def _verify_token() -> str:
    return os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()


def _app_secret() -> str:
    return os.environ.get("WHATSAPP_APP_SECRET", "").strip()


@router.api_route("/api/webhooks/whatsapp", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request) -> Any:
    if request.method == "GET":
        expected = _verify_token()
        if not expected:
            raise HTTPException(status_code=404,
                                detail="WhatsApp-webhook niet geconfigureerd.")
        supplied = request.query_params.get("hub.verify_token", "")
        challenge = request.query_params.get("hub.challenge", "")
        if (request.query_params.get("hub.mode") == "subscribe"
                and hmac.compare_digest(supplied, expected)):
            return PlainTextResponse(challenge, media_type="text/plain")
        raise HTTPException(status_code=403, detail="Ongeldige verify-token.")

    return await _handle_post(request)


async def _handle_post(request: Request) -> dict[str, Any]:
    """Task 7 vult dit in; tot dan is POST niet geconfigureerd."""
    raise HTTPException(status_code=404,
                        detail="WhatsApp-webhook niet geconfigureerd.")
