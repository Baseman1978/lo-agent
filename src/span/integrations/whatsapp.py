"""A6 — WhatsApp-kanaal via de Meta Cloud API (officieel, geen BSP).

Dunne, wegneembare adapter op de bestaande agent-loop (spec §A6 + §5): geen
kernlogica in deze laag; weghalen = deze module + de webhook-router + drie
wiring-regels in app.py verwijderen. Onofficiële bibliotheken (whatsmeow,
Baileys) zijn verboden. Alleen nummers op WHATSAPP_ALLOWED_NUMBERS worden
bediend; al het andere wordt genegeerd + gelogd. WhatsApp-inhoud is untrusted
input — de guard/risk-governance draait ongewijzigd binnen elke agent.turn().
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import requests

from span.integrations.http import guarded_get, request_with_retry

_GRAPH = "https://graph.facebook.com/v21.0"
_MAX_MEDIA_BYTES = 20_000_000  # zelfde grens als de Telegram-voice-download (M11)
_MAX_TEXT = 4000               # Cloud API-limiet is 4096 tekens per text-body
_DEDUPE_MAX = 500              # Meta levert dubbel bij trage acks -> id-dedupe


class WhatsAppBridge:
    """Chat (laag 1) + spraakmemo's (laag 2). Webhook-gedreven: geen poll-loop."""

    def __init__(self, state: dict[str, Any]):
        self._state = state
        jc = state["settings"].jarvis
        self._token: str = jc.whatsapp_token
        self._phone_id: str = jc.whatsapp_phone_id
        self._allowed: frozenset[str] = jc.whatsapp_allowlist
        self._voice_reply: bool = jc.whatsapp_voice_reply
        self._agent = None
        self._session_id: str | None = None
        self._seen: OrderedDict[str, float] = OrderedDict()

    # -- cloud api ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def send_text(self, to: str, text: str) -> bool:
        """Stuur een tekstbericht; True alleen als álle chunks zijn aangekomen."""
        ok = True
        for chunk in [text[i:i + _MAX_TEXT] for i in range(0, len(text), _MAX_TEXT)] or [""]:
            resp = request_with_retry(
                lambda c=chunk: requests.post(
                    f"{_GRAPH}/{self._phone_id}/messages",
                    headers=self._headers(),
                    json={"messaging_product": "whatsapp", "to": to,
                          "type": "text", "text": {"body": c}},
                    timeout=30,
                ),
                idempotent=False,  # bericht sturen is niet-idempotent (geen blinde retry)
            )
            if not resp.ok:
                print(f"[whatsapp] send_text {resp.status_code}: {resp.text[:200]}",
                      flush=True)
                ok = False
        return ok

    def download_media(self, media_id: str) -> bytes:
        """media-id -> download-URL -> bytes (bounded). Beide hops via guarded_get:
        de media-URL komt uit een API-antwoord en is dus untrusted (egress-check
        vóór de request de deur uit gaat). Te groot of geen URL -> lege bytes."""
        meta = guarded_get(f"{_GRAPH}/{media_id}", headers=self._headers(), timeout=30)
        meta.raise_for_status()
        url = str((meta.json() or {}).get("url") or "")
        if not url:
            return b""
        r = guarded_get(url, headers=self._headers(), timeout=60, stream=True)
        r.raise_for_status()
        # bounded download (M11): geen onbegrensde body in het geheugen
        data = r.raw.read(_MAX_MEDIA_BYTES + 1, decode_content=True) or b""
        r.close()
        if len(data) > _MAX_MEDIA_BYTES:
            return b""  # te groot -> overslaan i.p.v. geheugen opblazen
        return data
