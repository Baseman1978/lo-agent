"""A6 — WhatsApp-kanaal (laag 1+2): webhook, allowlist, chat- en voice-flow.

Alles draait offline: Cloud-API-calls, STT en TTS zijn gemockt. Signature-tests
rekenen een échte HMAC over de bytes-body. Dat dekt de spec-eis "testbaar met
mocks/fixtures vóór het Meta-testnummer er is".
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections import OrderedDict
from unittest.mock import MagicMock

import pytest


def test_egress_staat_facebook_hosts_toe():
    """Media-URLs wijzen naar lookaside.fbsbx.com; API-calls naar graph.facebook.com.
    Zonder deze hosts blokkeert guarded_get elke media-download."""
    from span.safety.egress import host_allowed
    assert host_allowed("graph.facebook.com")
    assert host_allowed("lookaside.fbsbx.com")


def _bridge(allowed=("31612345678",), voice_reply=False):
    """Bouw een WhatsAppBridge zonder Settings/Neo4j: attributen handmatig."""
    from span.integrations.whatsapp import WhatsAppBridge
    b = WhatsAppBridge.__new__(WhatsAppBridge)
    b._state = {}
    b._token = "wa-test-token"
    b._phone_id = "12345"
    b._allowed = frozenset(allowed)
    b._voice_reply = voice_reply
    b._agent = None
    b._session_id = None
    b._seen = OrderedDict()
    return b


class _Resp:
    """Minimal requests.Response-double."""
    def __init__(self, payload=None, ok=True, status_code=200):
        self._payload = payload or {}
        self.ok = ok
        self.status_code = status_code
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_send_text_chunkt_en_post(monkeypatch):
    import span.integrations.whatsapp as wa
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return _Resp()

    monkeypatch.setattr(wa.requests, "post", fake_post)
    b = _bridge()
    assert b.send_text("31612345678", "x" * 4500)  # > 4000 tekens -> 2 chunks
    assert len(calls) == 2
    url, headers, payload = calls[0]
    assert url == "https://graph.facebook.com/v21.0/12345/messages"
    assert headers["Authorization"] == "Bearer wa-test-token"
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "31612345678" and payload["type"] == "text"
    assert payload["text"]["body"] == "x" * 4000
    assert calls[1][2]["text"]["body"] == "x" * 500


def test_send_text_fout_geeft_false(monkeypatch):
    import span.integrations.whatsapp as wa
    monkeypatch.setattr(wa.requests, "post",
                        lambda *a, **k: _Resp(ok=False, status_code=400))
    assert not _bridge().send_text("31612345678", "hallo")


class _RawStream:
    def __init__(self, data):
        self._data = data

    def read(self, n, decode_content=True):
        return self._data[:n]


class _StreamResp:
    def __init__(self, data):
        self.raw = _RawStream(data)
        self.ok = True

    def raise_for_status(self):
        pass

    def close(self):
        pass


def test_download_media_via_guard(monkeypatch):
    import span.integrations.whatsapp as wa
    seen_urls = []

    def fake_guarded_get(url, **kwargs):
        seen_urls.append(url)
        if url == "https://graph.facebook.com/v21.0/media-1":
            return _Resp({"url": "https://lookaside.fbsbx.com/whatsapp/x"})
        return _StreamResp(b"OggS-audio-bytes")

    monkeypatch.setattr(wa, "guarded_get", fake_guarded_get)
    b = _bridge()
    assert b.download_media("media-1") == b"OggS-audio-bytes"
    # beide hops lopen door de egress-guard (media-URL = untrusted API-antwoord)
    assert seen_urls == ["https://graph.facebook.com/v21.0/media-1",
                         "https://lookaside.fbsbx.com/whatsapp/x"]


def test_download_media_te_groot_is_leeg(monkeypatch):
    import span.integrations.whatsapp as wa

    def fake_guarded_get(url, **kwargs):
        if url.endswith("/media-1"):
            return _Resp({"url": "https://lookaside.fbsbx.com/whatsapp/x"})
        return _StreamResp(b"X" * 100)

    monkeypatch.setattr(wa, "guarded_get", fake_guarded_get)
    monkeypatch.setattr(wa, "_MAX_MEDIA_BYTES", 50)
    assert _bridge().download_media("media-1") == b""  # te groot -> overslaan


def test_handle_message_tekst_flow(monkeypatch):
    b = _bridge()
    sent = []
    monkeypatch.setattr(b, "send_text", lambda to, text: (sent.append((to, text)), True)[1])
    agent = MagicMock()
    agent.turn.return_value = "hoi Bas"
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    msg = {"from": "31612345678", "id": "wamid.1", "type": "text",
           "text": {"body": "hallo"}}
    b.handle_message(msg)
    agent.turn.assert_called_once_with("hallo")
    assert sent == [("31612345678", "hoi Bas")]
    # dedupe: Meta levert dubbel bij trage acks -> zelfde wamid = geen tweede beurt
    b.handle_message(msg)
    agent.turn.assert_called_once()


def test_vreemd_nummer_genegeerd_en_gelogd(monkeypatch, capsys):
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    sender = MagicMock()
    monkeypatch.setattr(b, "send_text", sender)
    b.handle_message({"from": "49170000000", "id": "wamid.x", "type": "text",
                      "text": {"body": "negeer mij"}})
    agent.turn.assert_not_called()
    sender.assert_not_called()  # géén antwoord naar vreemde nummers
    assert "niet-toegestaan" in capsys.readouterr().out


def test_onbekend_berichttype_genegeerd(monkeypatch, capsys):
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    b.handle_message({"from": "31612345678", "id": "wamid.s", "type": "sticker"})
    agent.turn.assert_not_called()
    assert "sticker" in capsys.readouterr().out


def _get_request(params):
    req = MagicMock()
    req.method = "GET"
    req.query_params = params
    return req


def test_webhook_get_handshake(monkeypatch):
    import span.server.whatsapp as wh
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-123")
    resp = asyncio.run(wh.whatsapp_webhook(_get_request({
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-123",
        "hub.challenge": "424242",
    })))
    assert resp.body == b"424242"
    assert resp.media_type == "text/plain"


def test_webhook_get_fout_token_is_403(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-123")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_get_request({
            "hub.mode": "subscribe",
            "hub.verify_token": "fout",
            "hub.challenge": "424242",
        })))
    assert exc.value.status_code == 403


def test_webhook_get_niet_geconfigureerd_is_404(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_get_request({"hub.mode": "subscribe"})))
    assert exc.value.status_code == 404  # fail-closed, zoals /api/webhooks/graph


def _post_request(body: bytes, secret="app-secret", sig=None):
    req = MagicMock()
    req.method = "POST"

    async def _body():
        return body

    req.body = _body
    if sig is None and secret is not None:
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req.headers = {"x-hub-signature-256": sig or ""}
    return req


def _wa_payload(msgs):
    return json.dumps({"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp", "messages": msgs,
    }}]}]}).encode()


def test_webhook_post_geldige_signature_verwerkt_async(monkeypatch):
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    handled = []
    bridge = MagicMock()
    bridge.handle_message.side_effect = lambda m: handled.append(m)
    st._state["whatsapp"] = bridge
    try:
        body = _wa_payload([{"from": "31612345678", "id": "wamid.1",
                             "type": "text", "text": {"body": "hallo"}}])

        async def _run():
            out = await wh.whatsapp_webhook(_post_request(body))
            # de route ackt direct; geef de achtergrondtaken de kans om binnen
            # deze event-loop af te ronden
            await asyncio.gather(*list(wh._bg_tasks))
            return out

        out = asyncio.run(_run())
        assert out == {"received": 1}
        assert handled and handled[0]["id"] == "wamid.1"
    finally:
        st._state.pop("whatsapp", None)


def test_webhook_post_bridge_fout_stuurt_eerlijke_melding(monkeypatch):
    """Spec §5: een exception in handle_message wordt niet stil gedropt — de
    allowlisted afzender krijgt een eerlijke foutmelding via send_text."""
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    bridge = MagicMock()
    bridge._allowed = frozenset({"31612345678"})
    bridge.handle_message.side_effect = RuntimeError("boem")
    st._state["whatsapp"] = bridge
    try:
        body = _wa_payload([{"from": "31612345678", "id": "wamid.e1",
                             "type": "text", "text": {"body": "hallo"}}])

        async def _run():
            out = await wh.whatsapp_webhook(_post_request(body))
            await asyncio.gather(*list(wh._bg_tasks))
            return out

        assert asyncio.run(_run()) == {"received": 1}
        bridge.send_text.assert_called_once()
        to, text = bridge.send_text.call_args.args
        assert to == "31612345678"
        assert "mis" in text  # eerlijke melding, geen stille drop
    finally:
        st._state.pop("whatsapp", None)


def test_webhook_post_foute_signature_is_401(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    body = _wa_payload([])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(
            _post_request(body, sig="sha256=" + "0" * 64)))
    assert exc.value.status_code == 401


def test_webhook_post_zonder_secret_is_404(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_post_request(b"{}")))
    assert exc.value.status_code == 404


def test_webhook_post_zonder_bridge_geeft_200(monkeypatch):
    """Geldige signature maar kanaal (nog) niet actief -> toch 200, anders
    blijft Meta redelivery doen."""
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    st._state.pop("whatsapp", None)
    body = _wa_payload([{"from": "49170000000", "id": "wamid.z",
                         "type": "text", "text": {"body": "x"}}])
    out = asyncio.run(wh.whatsapp_webhook(_post_request(body)))
    assert out == {"received": 1}


def test_webhook_post_status_updates_zijn_ok(monkeypatch):
    """Delivery/read-statussen bevatten geen messages -> received: 0, geen fout."""
    import span.server.whatsapp as wh
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    body = json.dumps({"entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.1", "status": "delivered"}],
    }}]}]}).encode()
    out = asyncio.run(wh.whatsapp_webhook(_post_request(body)))
    assert out == {"received": 0}


def test_voice_note_flow_met_stt_telemetrie(monkeypatch, tmp_path):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    import span.server.stt as stt
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe",
                        lambda audio, language="nl": "wat staat er vandaag op de agenda")

    b = _bridge()
    monkeypatch.setattr(b, "download_media", lambda mid: b"OggS-opus-bytes")
    sent = []
    monkeypatch.setattr(b, "send_text", lambda to, text: (sent.append(text), True)[1])
    agent = MagicMock()
    agent.turn.return_value = "drie afspraken vandaag"
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)

    b.handle_message({"from": "31612345678", "id": "wamid.v1", "type": "audio",
                      "audio": {"id": "media-9", "voice": True}})
    agent.turn.assert_called_once_with("wat staat er vandaag op de agenda")
    assert sent == ["drie afspraken vandaag"]

    import span.telemetry as tel
    seg = tel.aggregate()["segments"]
    assert seg["stt"]["count"] == 1  # kanaal-meting: {"channel": "whatsapp"}


def test_voice_note_zonder_stt_wordt_eerlijk_gemeld(monkeypatch):
    """Spec §5: geen stille drops — zonder STT volgt geen agent-beurt, maar de
    afzender krijgt wél een eerlijk tekstantwoord (geen genegeerde spraakmemo)."""
    import span.server.stt as stt
    monkeypatch.setattr(stt, "available", lambda: False)
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    sent = []
    monkeypatch.setattr(b, "send_text",
                        lambda to, text: (sent.append((to, text)), True)[1])
    b.handle_message({"from": "31612345678", "id": "wamid.v2", "type": "audio",
                      "audio": {"id": "media-10"}})
    agent.turn.assert_not_called()  # geen STT -> geen beurt, geen crash
    assert sent == [("31612345678", "Ik kan spraakmemo's nu niet verwerken — "
                                    "stuur het als tekst.")]
