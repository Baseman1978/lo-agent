"""Tamper-evident audit-trail (F4.6).

Elke actie die Span namens Bas doet wordt als Action-node vastgelegd in een
hash-keten: hash = sha256(vorige_hash + seq + type + detail + at). Wie een
regel wijzigt of verwijdert breekt de keten — verify_chain() detecteert dat.
Vervangt de losse _audit-CREATE; faalt zacht (auditing mag nooit een actie
blokkeren), maar logt een waarschuwing bij een breuk.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from typing import Any

GENESIS = "0" * 64

# M2: record_action atomair binnen het proces (ambient-watcher + interactieve
# beurt draaien in dezelfde process; een lock voorkomt dubbele seq -> geen
# valse keten-breuk).
_LOCK = threading.Lock()


def _audit_key() -> bytes:
    """Server-side sleutel BUITEN het brein (M1): wie enkel schrijftoegang tot
    het brein heeft kan de keten dan niet herberekenen. Voorkeur:
    SPAN_AUDIT_HMAC_KEY (eigen, gescheiden geheim) > SPAN_AUTH_TOKEN (legacy).
    ensure_audit_key() zet bij een verse installatie zelf een eigen sleutel in
    de env. Zonder sleutel: kale sha256 (alleen tegen toevallige wijziging)."""
    return (os.environ.get("SPAN_AUDIT_HMAC_KEY")
            or os.environ.get("SPAN_AUTH_TOKEN") or "").encode("utf-8")


def ensure_audit_key(brain: Any) -> str:
    """Zorg dat er een audit-sleutel is — zonder dat Bas eraan hoeft te denken.

    Verse installatie -> genereer een eigen sleutel en bewaar 'm in het
    persistente state-volume (buiten het brein, overleeft rebuilds). Een
    BESTAANDE keten wordt nooit stil omgesleuteld (dat zou 'm breken): die
    blijft op z'n huidige sleutel tot je bewust her-ankert (scripts/). Een
    expliciete SPAN_AUDIT_HMAC_KEY in de omgeving wint altijd. Geeft de
    gekozen modus terug (env|keyfile|generated|legacy-authtoken|fallback)."""
    from pathlib import Path
    if os.environ.get("SPAN_AUDIT_HMAC_KEY"):
        return "env"
    keyfile = Path.home() / ".span" / "audit_hmac.key"
    try:
        if keyfile.exists():
            k = keyfile.read_text(encoding="utf-8").strip()
            if k:
                os.environ["SPAN_AUDIT_HMAC_KEY"] = k
                return "keyfile"
    except Exception:
        pass
    # geen keyfile: alleen genereren als er nog geen keten is die we breken
    try:
        rows = brain.run("MATCH (a:Action) RETURN count(a) AS n")
        existing = int(rows[0]["n"]) if rows else 0
    except Exception:
        existing = 0
    if existing > 0 and os.environ.get("SPAN_AUTH_TOKEN"):
        return "legacy-authtoken"   # bestaande keten op auth-token; niet breken
    import secrets
    k = secrets.token_urlsafe(48)
    try:
        keyfile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        keyfile.write_text(k, encoding="utf-8")
        try:
            keyfile.chmod(0o600)
        except Exception:
            pass
        os.environ["SPAN_AUDIT_HMAC_KEY"] = k
        return "generated"
    except Exception:
        return "fallback"   # kon niet schrijven -> val terug, nooit crashen


def _digest(prev_hash: str, seq: int, action: str, detail: str, at: str,
            algo: str = "") -> str:
    key = _audit_key()
    raw = f"{prev_hash}|{seq}|{action}|{detail}|{at}".encode("utf-8")
    use_hmac = (algo == "hmac") or (algo == "" and bool(key))
    if use_hmac and key:
        return hmac.new(key, raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def record_action(brain: Any, action: str, detail: str) -> None:
    """Voeg een actie toe aan de hash-keten. Zacht falend, en atomair (lock)."""
    try:
        with _LOCK:
            rows = brain.run(
                "MATCH (a:Action) WHERE a.seq IS NOT NULL "
                "RETURN a.seq AS seq, a.hash AS hash "
                "ORDER BY a.seq DESC LIMIT 1"
            )
            if rows and rows[0].get("seq") is not None:
                seq = int(rows[0]["seq"]) + 1
                prev = rows[0].get("hash") or GENESIS
            else:
                seq, prev = 1, GENESIS
            detail = (detail or "")[:300]
            # at deterministisch vastleggen zodat de hash herberekenbaar is
            at_rows = brain.run("RETURN toString(datetime()) AS now")
            at = at_rows[0]["now"] if at_rows else ""
            algo = "hmac" if _audit_key() else "sha256"
            h = _digest(prev, seq, action, detail, at, algo)
            brain.run(
                "CREATE (:Action {type:$type, detail:$detail, at:$at, "
                "seq:$seq, prev_hash:$prev, hash:$hash, algo:$algo})",
                type=action, detail=detail, at=at, seq=seq, prev=prev, hash=h,
                algo=algo,
            )
    except Exception as exc:
        print(f"[audit] vastleggen mislukt: {exc}", flush=True)


def verify_chain(brain: Any) -> dict[str, Any]:
    """Herbereken de keten en meld de eerste breuk (gewijzigde/ontbrekende
    regel). Retourneert {ok, count, broken_at?}."""
    rows = brain.run(
        "MATCH (a:Action) WHERE a.seq IS NOT NULL "
        "RETURN a.seq AS seq, a.type AS type, a.detail AS detail, "
        "a.at AS at, a.prev_hash AS prev, a.hash AS hash, a.algo AS algo ORDER BY a.seq"
    )
    prev = GENESIS
    expected_seq = 1
    for r in rows:
        if r["seq"] != expected_seq:
            return {"ok": False, "count": len(rows),
                    "broken_at": r["seq"], "reason": "ontbrekende of dubbele seq"}
        if r["prev"] != prev:
            return {"ok": False, "count": len(rows),
                    "broken_at": r["seq"], "reason": "prev_hash klopt niet"}
        # per-node algo: oude entries (geen algo) zijn sha256, nieuwe hmac ->
        # de bestaande keten blijft verifieerbaar na het inschakelen van HMAC
        h = _digest(prev, r["seq"], r["type"], r["detail"] or "", r["at"] or "",
                    r.get("algo") or "sha256")
        if h != r["hash"]:
            return {"ok": False, "count": len(rows),
                    "broken_at": r["seq"], "reason": "hash gewijzigd (tampering)"}
        prev = r["hash"]
        expected_seq += 1
    return {"ok": True, "count": len(rows)}
