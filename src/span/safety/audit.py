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

# Server-side sleutel BUITEN het brein: wie alleen schrijftoegang tot het brein
# heeft kan de keten dan niet herberekenen (M1). SPAN_AUTH_TOKEN is zo'n geheim;
# een aparte SPAN_AUDIT_HMAC_KEY heeft voorrang. Zonder sleutel valt de keten
# terug op kale sha256 = alleen tegen toevallige/naïeve wijziging bestand.
_AUDIT_KEY = (os.environ.get("SPAN_AUDIT_HMAC_KEY")
              or os.environ.get("SPAN_AUTH_TOKEN") or "").encode("utf-8")

# M2: record_action atomair binnen het proces (ambient-watcher + interactieve
# beurt draaien in dezelfde process; een lock voorkomt dubbele seq -> geen
# valse keten-breuk).
_LOCK = threading.Lock()


def _digest(prev_hash: str, seq: int, action: str, detail: str, at: str,
            algo: str = "") -> str:
    raw = f"{prev_hash}|{seq}|{action}|{detail}|{at}".encode("utf-8")
    use_hmac = (algo == "hmac") or (algo == "" and bool(_AUDIT_KEY))
    if use_hmac and _AUDIT_KEY:
        return hmac.new(_AUDIT_KEY, raw, hashlib.sha256).hexdigest()
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
            algo = "hmac" if _AUDIT_KEY else "sha256"
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
