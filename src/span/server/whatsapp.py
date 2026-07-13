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
import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from span.server.state import _state

router = APIRouter()

# referenties op lopende achtergrondtaken (anders kan de GC ze opruimen)
_bg_tasks: set[asyncio.Task] = set()

# A6 M1-fix: SpanAgent is NIET thread-safe (_messages/_ensure_agent/dedupe) en
# Meta levert meerdere berichten per POST + overlappende POSTs. Alle agent-
# beurten moeten daarom strikt serieel. Eén lock per event-loop: in productie
# draait alles in één loop (volledige serialisatie), en per-loop voorkomt de
# "bound to a different event loop"-fout bij losse asyncio.run()-tests.
_turn_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_turn_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _turn_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _turn_locks[loop] = lock
    return lock


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


async def _run_message(bridge: Any, msg: dict[str, Any]) -> None:
    """Eén bericht door de (sync/blocking) bridge, zonder de 200-ack te blokkeren.
    Per-bericht try/except: één kapot bericht mag de rest nooit meeslepen.
    Spec §5: geen stille drops — na een fout krijgt de allowlisted afzender een
    eerlijke foutmelding; alleen server-side loggen is niet genoeg."""
    try:
        await asyncio.to_thread(bridge.handle_message, msg)
    except Exception as exc:
        print(f"[whatsapp] bericht-fout: {type(exc).__name__}: {exc}", flush=True)
        sender = str(msg.get("from") or "")
        if sender in getattr(bridge, "_allowed", frozenset()):
            # best-effort: de foutmelding zelf mag nooit een nieuwe crash geven
            try:
                await asyncio.to_thread(
                    bridge.send_text, sender,
                    "Er ging iets mis bij het verwerken van je bericht — "
                    "probeer het zo nog eens.")
            except Exception as exc2:
                print(f"[whatsapp] foutmelding versturen mislukt: {exc2}",
                      flush=True)


async def _run_batch(bridge: Any, messages: list[dict[str, Any]]) -> None:
    """Verwerk de berichten van één POST strikt serieel én onder de globale
    per-loop lock, zodat agent-beurten binnen én over POSTs heen nooit
    overlappen (M1: SpanAgent is niet thread-safe)."""
    async with _get_turn_lock():
        for msg in messages:
            await _run_message(bridge, msg)


async def _handle_post(request: Request) -> dict[str, Any]:
    secret = _app_secret()
    if not secret:
        raise HTTPException(status_code=404,
                            detail="WhatsApp-webhook niet geconfigureerd.")
    # signature MOET over de rauwe bytes — pas daarna JSON-parsen
    raw = await request.body()
    supplied = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Ongeldige signature.")
    try:
        import json as _json
        body = _json.loads(raw or b"{}")
    except Exception:
        body = {}
    messages: list[dict[str, Any]] = []
    for entry in (body.get("entry") or []):
        for change in (entry.get("changes") or []):
            messages.extend((change.get("value") or {}).get("messages") or [])
    bridge = _state.get("whatsapp")
    if bridge is not None and messages:
        # één taak per POST die de berichten serieel afhandelt (M1)
        task = asyncio.create_task(_run_batch(bridge, messages))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    # ALTIJD 200 op een geldige signature (ook genegeerde afzenders/typen),
    # anders blijft Meta hetzelfde bericht opnieuw aanleveren
    return {"received": len(messages)}
