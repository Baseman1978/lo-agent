"""A6 — WhatsApp-kanaal (laag 1+2): webhook, allowlist, chat- en voice-flow.

Alles draait offline: Cloud-API-calls, STT en TTS zijn gemockt. Signature-tests
rekenen een échte HMAC over de bytes-body. Dat dekt de spec-eis "testbaar met
mocks/fixtures vóór het Meta-testnummer er is".
"""
from __future__ import annotations

import json
from collections import OrderedDict


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
