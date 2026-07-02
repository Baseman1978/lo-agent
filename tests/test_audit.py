"""WP-B4 — actor-identiteit in de audit-hash, backward-compatible."""

from __future__ import annotations

from span.safety.audit import _digest


def test_actor_zit_in_de_hash_bij_actor_algo(monkeypatch):
    monkeypatch.setenv("SPAN_AUDIT_HMAC_KEY", "k")
    a = _digest("p", 1, "act", "d", "at", "hmac-a", "bas@lomans.nl")
    b = _digest("p", 1, "act", "d", "at", "hmac-a", "ander@lomans.nl")
    assert a != b   # verschillende actor -> verschillende hash


def test_oud_formaat_negeert_actor(monkeypatch):
    monkeypatch.setenv("SPAN_AUDIT_HMAC_KEY", "k")
    # records zonder '-a' (oude keten) negeren de actor -> blijven verifieerbaar
    assert _digest("p", 1, "act", "d", "at", "hmac", "bas@x") == \
           _digest("p", 1, "act", "d", "at", "hmac", "")
    assert _digest("p", 1, "act", "d", "at", "sha256", "x") == \
           _digest("p", 1, "act", "d", "at", "sha256", "")


def test_hmac_a_verschilt_van_plain(monkeypatch):
    monkeypatch.setenv("SPAN_AUDIT_HMAC_KEY", "k")
    # met sleutel -> HMAC, niet kale sha256
    import hashlib
    plain = hashlib.sha256(b"p|1|act|d|at|bas").hexdigest()
    assert _digest("p", 1, "act", "d", "at", "hmac-a", "bas") != plain
