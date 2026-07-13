"""A6 — WhatsApp-kanaal (laag 1+2): webhook, allowlist, chat- en voice-flow.

Alles draait offline: Cloud-API-calls, STT en TTS zijn gemockt. Signature-tests
rekenen een échte HMAC over de bytes-body. Dat dekt de spec-eis "testbaar met
mocks/fixtures vóór het Meta-testnummer er is".
"""
from __future__ import annotations


def test_egress_staat_facebook_hosts_toe():
    """Media-URLs wijzen naar lookaside.fbsbx.com; API-calls naar graph.facebook.com.
    Zonder deze hosts blokkeert guarded_get elke media-download."""
    from span.safety.egress import host_allowed
    assert host_allowed("graph.facebook.com")
    assert host_allowed("lookaside.fbsbx.com")
