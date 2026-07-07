"""Acceptance-feedback (F4.4): leren van wat Bas goedkeurt/afwijst.

Elke goedkeuring/afwijzing van een Agent Inbox-item wordt als Feedback-node
vastgelegd, gekoppeld aan het actie-type. feedback_summary() geeft per type de
verhouding goedgekeurd/afgewezen — die kan de agent (bootstrap/triage)
raadplegen om voorzichtiger te worden met acties die Bas vaak afwijst.
"""

from __future__ import annotations

from typing import Any


def feedback_action(item: dict[str, Any]) -> str:
    """Feedback-sleutel voor een Agent Inbox-item. Voor notify-mail keyen we op
    de afzender ("notify:<afzender>"), zodat er per afzender signaal ontstaat en
    de triage gericht kan degraderen — niet de hele 'notify'-klasse ineens.
    Voor andere items blijft het gewoon het actie-type."""
    if item.get("kind") == "notify":
        frm = ((item.get("payload") or {}).get("from") or "").strip().lower()
        if frm:
            return "notify:" + frm[:120]
    return item.get("action") or ""


def suppressed_notify_senders(
    feedback: list[dict[str, Any]] | None,
    min_total: int = 3,
    threshold: float = 0.5,
) -> set[str]:
    """Afzenders wier notify-mail Bas overwegend (≥threshold) wegklikt, met
    genoeg datapunten (≥min_total). De triage degradeert die van notify naar
    ignore. Conservatief: needs_reply blijft altijd staan (elders afgedwongen)."""
    out: set[str] = set()
    for f in feedback or []:
        t = f.get("type") or ""
        if not t.startswith("notify:"):
            continue
        total = (f.get("approved") or 0) + (f.get("rejected") or 0)
        if total >= min_total and (f.get("reject_ratio") or 0) >= threshold:
            out.add(t[len("notify:"):])
    return out


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
