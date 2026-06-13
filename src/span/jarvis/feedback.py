"""Acceptance-feedback (F4.4): leren van wat Bas goedkeurt/afwijst.

Elke goedkeuring/afwijzing van een Agent Inbox-item wordt als Feedback-node
vastgelegd, gekoppeld aan het actie-type. feedback_summary() geeft per type de
verhouding goedgekeurd/afgewezen — die kan de agent (bootstrap/triage)
raadplegen om voorzichtiger te worden met acties die Bas vaak afwijst.
"""

from __future__ import annotations

from typing import Any


def record_feedback(brain: Any, kind: str, action: str, outcome: str) -> None:
    """outcome ∈ {approved, rejected}. Zacht falend (feedback mag nooit de
    afhandeling blokkeren)."""
    if outcome not in ("approved", "rejected"):
        return
    try:
        brain.run(
            "CREATE (:Feedback {kind:$kind, action:$action, outcome:$outcome, "
            "at:datetime()})",
            kind=kind or "", action=action or "", outcome=outcome,
        )
    except Exception as exc:
        print(f"[feedback] vastleggen mislukt: {exc}", flush=True)


def feedback_summary(brain: Any) -> list[dict[str, Any]]:
    """Per actie-type: aantal goedgekeurd/afgewezen + afwijzingsratio."""
    try:
        rows = brain.run(
            "MATCH (f:Feedback) "
            "RETURN coalesce(f.action, f.kind) AS type, f.outcome AS outcome, "
            "count(*) AS n"
        )
    except Exception:
        return []
    agg: dict[str, dict[str, int]] = {}
    for r in rows:
        t = r["type"] or "overig"
        agg.setdefault(t, {"approved": 0, "rejected": 0})
        agg[t][r["outcome"]] = r["n"]
    out = []
    for t, d in agg.items():
        total = d["approved"] + d["rejected"]
        out.append({"type": t, "approved": d["approved"], "rejected": d["rejected"],
                    "reject_ratio": round(d["rejected"] / total, 2) if total else 0.0})
    return sorted(out, key=lambda x: x["reject_ratio"], reverse=True)
