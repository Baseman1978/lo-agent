"""Her-anker de audit-keten onder de nu-actieve sleutel.

Gebruik dit ÉÉN keer na het wisselen van de audit-HMAC-sleutel (bv. na het
scheiden van SPAN_AUDIT_HMAC_KEY van SPAN_AUTH_TOKEN). De bestaande keten is
gehasht met de oude sleutel; verify_chain() zou 'm daarna als gebroken zien.
Dit script herberekent alle Action-hashes in seq-volgorde met de huidige
_digest (dus de nieuwe sleutel) en zet algo consistent.

LET OP: dit is een bewuste, operator-geïnitieerde her-ankering — het herstelt
de verifieerbaarheid, maar wist daarmee de tamper-evidence van vóór de wissel.
Draai het alleen direct na een sleutelwissel op een keten die je vertrouwt.

    docker exec span-agent python /app/scripts/reanchor_audit.py
"""
from __future__ import annotations

from span.config import load_settings
from span.db.brain import BrainDB
from span.safety import audit


def main() -> None:
    s = load_settings()
    b = BrainDB(s)
    algo = "hmac" if audit._AUDIT_KEY else "sha256"
    rows = b.run(
        "MATCH (a:Action) WHERE a.seq IS NOT NULL "
        "RETURN a.seq AS seq, a.type AS type, a.detail AS detail, a.at AS at "
        "ORDER BY a.seq"
    )
    prev = audit.GENESIS
    n = 0
    for r in rows:
        h = audit._digest(prev, r["seq"], r["type"], r["detail"] or "",
                          r["at"] or "", algo)
        b.run(
            "MATCH (a:Action {seq:$seq}) SET a.prev_hash=$prev, a.hash=$hash, a.algo=$algo",
            seq=r["seq"], prev=prev, hash=h, algo=algo,
        )
        prev = h
        n += 1
    res = audit.verify_chain(b)
    print(f"her-ankerd: {n} acties met algo={algo}; verify_chain -> {res}")
    b.close()


if __name__ == "__main__":
    main()
