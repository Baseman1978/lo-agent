"""Retrieval-kwaliteit meten: recall@k op de gouden eval-set.

De meetlat voor de retrieval-verbeteringen (hybrid, formal-per-beurt, decay).
Routeert per verwachte node naar de juiste zoekfunctie (MemoryFragment ->
search(); Insight/Mistake/Idea -> search_formal()) en rapporteert recall@k,
uitgesplitst naar query-soort. Draai in de container:
    docker exec span-agent python /app/eval_retrieval.py [--decay soft] [--hybrid]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from span.config import load_settings
from span.db.brain import BrainDB
from span.llm.client import LLMClient
from span.memory.fragments import FragmentStore

EVAL_PATH = Path(__file__).with_name("eval_retrieval_set.json")
K_VALUES = (1, 3, 5, 10)


def _is_formal(node_id: str) -> bool:
    return node_id.split("-", 1)[0] in {"insight", "mistake", "idea"}


def run(decay: str, hybrid: bool) -> None:
    s = load_settings()
    b = BrainDB(s)
    llm = LLMClient(s)
    fs = FragmentStore(b, llm, decay_mode=decay)
    eval_set = json.loads(EVAL_PATH.read_text(encoding="utf-8"))["eval_set"]

    # valideer dat de verwachte ids bestaan (oude-stijl ids kunnen verdwenen zijn)
    existing = {r["id"] for r in b.run(
        "MATCH (n) WHERE n.id IS NOT NULL RETURN n.id AS id")}
    cases = [c for c in eval_set if c["expected_id"] in existing]
    skipped = [c["expected_id"] for c in eval_set if c["expected_id"] not in existing]
    if skipped:
        print(f"[let op] {len(skipped)} eval-ids niet meer in het brein, overgeslagen: {skipped}")

    maxk = max(K_VALUES)
    hits = {k: 0 for k in K_VALUES}
    per_kind: dict[str, dict[str, int]] = {}
    for c in cases:
        emb = fs.embed(c["query"])
        kw = {"hybrid": True} if hybrid else {}
        if _is_formal(c["expected_id"]):
            res = fs.search_formal(c["query"], k=maxk, embedding=emb, **kw)
        else:
            res = fs.search(c["query"], k=maxk, embedding=emb, **kw)
        ids = [r["id"] for r in res]
        rank = ids.index(c["expected_id"]) + 1 if c["expected_id"] in ids else None
        kind = c["kind"]
        per_kind.setdefault(kind, {"n": 0, **{f"r{k}": 0 for k in K_VALUES}})
        per_kind[kind]["n"] += 1
        for k in K_VALUES:
            if rank is not None and rank <= k:
                hits[k] += 1
                per_kind[kind][f"r{k}"] += 1

    n = len(cases)
    mode = f"decay={decay}, hybrid={hybrid}"
    print(f"\n=== Retrieval-eval ({mode}) — {n} queries ===")
    for k in K_VALUES:
        print(f"  recall@{k}: {hits[k]}/{n} = {hits[k]/n:.2%}")
    print("  per soort (recall@5):")
    for kind, d in sorted(per_kind.items()):
        print(f"    {kind:11s} {d['r5']}/{d['n']} = {d['r5']/d['n']:.0%}")
    b.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decay", default="off", choices=["off", "soft", "log"])
    ap.add_argument("--hybrid", action="store_true")
    a = ap.parse_args()
    run(a.decay, a.hybrid)
