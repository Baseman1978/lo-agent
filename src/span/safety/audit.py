"""Tamper-evident audit-trail (F4.6).

Elke actie die Span namens Bas doet wordt als Action-node vastgelegd in een
hash-keten: hash = sha256(vorige_hash + seq + type + detail + at). Wie een
regel wijzigt of verwijdert breekt de keten — verify_chain() detecteert dat.
Vervangt de losse _audit-CREATE; faalt zacht (auditing mag nooit een actie
blokkeren), maar logt een waarschuwing bij een breuk.
"""

from __future__ import annotations

import hashlib
from typing import Any

GENESIS = "0" * 64


def _digest(prev_hash: str, seq: int, action: str, detail: str, at: str) -> str:
    raw = f"{prev_hash}|{seq}|{action}|{detail}|{at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_action(brain: Any, action: str, detail: str) -> None:
    """Voeg een actie toe aan de hash-keten. Zacht falend."""
    try:
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
        h = _digest(prev, seq, action, detail, at)
        brain.run(
            "CREATE (:Action {type:$type, detail:$detail, at:$at, "
            "seq:$seq, prev_hash:$prev, hash:$hash})",
            type=action, detail=detail, at=at, seq=seq, prev=prev, hash=h,
        )
    except Exception as exc:
        print(f"[audit] vastleggen mislukt: {exc}", flush=True)


def verify_chain(brain: Any) -> dict[str, Any]:
    """Herbereken de keten en meld de eerste breuk (gewijzigde/ontbrekende
    regel). Retourneert {ok, count, broken_at?}."""
    rows = brain.run(
        "MATCH (a:Action) WHERE a.seq IS NOT NULL "
        "RETURN a.seq AS seq, a.type AS type, a.detail AS detail, "
        "a.at AS at, a.prev_hash AS prev, a.hash AS hash ORDER BY a.seq"
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
        h = _digest(prev, r["seq"], r["type"], r["detail"] or "", r["at"] or "")
        if h != r["hash"]:
            return {"ok": False, "count": len(rows),
                    "broken_at": r["seq"], "reason": "hash gewijzigd (tampering)"}
        prev = r["hash"]
        expected_seq += 1
    return {"ok": True, "count": len(rows)}
