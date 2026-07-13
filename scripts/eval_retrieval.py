"""Retrieval-kwaliteit meten: recall@k op de gouden eval-set.

Het A4-meetpunt voor recall-kwaliteit en de meetlat voor de B4-poortvraag
("veroorzaakte tijd-blindheid echte recall-fouten?"). Routeert per verwachte
node naar de juiste zoekfunctie (MemoryFragment -> search(); Insight/Mistake/
Idea -> search_formal()) en rapporteert recall@k, uitgesplitst naar
query-soort. Schrijft de uitkomst ook naar de telemetrie-JSONL (segment
'recall', waarde = recall@5 in procenten in het ms-veld) zodat hij naast de
A1-latencies staat.

De gouden set (eval_retrieval_set.json) leeft naast dit script OP DE SERVER —
bewust niet in de repo (bevat privé-geheugeninhoud). Ontbreekt hij, dan stopt
het script met een duidelijke melding. De uitgebreide set (20 taak-scenario's
+ 50 geheugenvragen) is A7.

Draai in de container:
    docker exec span-agent python /app/eval_retrieval.py [--decay soft]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from span import telemetry
from span.config import load_settings
from span.db.brain import BrainDB
from span.llm.client import LLMClient
from span.memory.fragments import FragmentStore

EVAL_PATH = Path(__file__).with_name("eval_retrieval_set.json")
K_VALUES = (1, 3, 5, 10)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--decay", default="off", choices=["off", "soft", "log"])
    # --hybrid is verwijderd: fs.search()/search_formal() kennen die parameter
    # niet — de vlag gooide een TypeError zodra je hem gebruikte.
    return ap


def load_eval_set(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"Gouden set niet gevonden: {path}. eval_retrieval_set.json leeft "
            "op de server naast dit script (privé-geheugeninhoud, bewust niet "
            "in de repo). Draai in de container (docker exec span-agent) of "
            "zet de set op die plek neer."
        )
    return json.loads(path.read_text(encoding="utf-8"))["eval_set"]


def _is_formal(node_id: str) -> bool:
    return node_id.split("-", 1)[0] in {"insight", "mistake", "idea"}


def run(decay: str) -> None:
    s = load_settings()
    b = BrainDB(s)
    llm = LLMClient(s)
    fs = FragmentStore(b, llm, decay_mode=decay)
    eval_set = load_eval_set(EVAL_PATH)

    # valideer dat de verwachte ids bestaan (oude-stijl ids kunnen verdwenen zijn)
    existing = {r["id"] for r in b.run(
        "MATCH (n) WHERE n.id IS NOT NULL RETURN n.id AS id")}
    cases = [c for c in eval_set if c["expected_id"] in existing]
    skipped = [c["expected_id"] for c in eval_set if c["expected_id"] not in existing]
    if skipped:
        print(f"[let op] {len(skipped)} eval-ids niet meer in het brein, "
              f"overgeslagen: {skipped}")

    maxk = max(K_VALUES)
    hits = {k: 0 for k in K_VALUES}
    per_kind: dict[str, dict[str, int]] = {}
    for c in cases:
        emb = fs.embed(c["query"])
        if _is_formal(c["expected_id"]):
            res = fs.search_formal(c["query"], k=maxk, embedding=emb)
        else:
            res = fs.search(c["query"], k=maxk, embedding=emb)
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
    print(f"\n=== Retrieval-eval (decay={decay}) — {n} queries ===")
    for k in K_VALUES:
        print(f"  recall@{k}: {hits[k]}/{n} = {hits[k]/n:.2%}")
    print("  per soort (recall@5):")
    for kind, d in sorted(per_kind.items()):
        print(f"    {kind:11s} {d['r5']}/{d['n']} = {d['r5']/d['n']:.0%}")
    if n:
        # A4-meetpunt: recall@5 (procenten, in het ms-veld van de JSONL) naast
        # de A1-latencies — één meetlat voor de B4-poortvraag.
        telemetry.record("recall", hits[5] / n * 100.0,
                         {"k": 5, "n": n, "decay": decay})
    b.close()


if __name__ == "__main__":
    args = build_parser().parse_args()
    run(args.decay)
