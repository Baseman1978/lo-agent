"""A7 — handmatige eval-run: python -m span.evaluation

Achter feature-flag SPAN_EVAL (default uit, spec-klep r145): zonder
SPAN_EVAL=on weigert de runner met exit-code 2. Werkt lokaal én in de
container (docker exec -e SPAN_EVAL=on span-agent python -m
span.evaluation) — de datasets zitten in het package. Exit-code 0/1 op
--min-score en optioneel --json maken hem door B6 automatiseerbaar."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from span.evaluation.evalrun import render_report, run_eval


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("SPAN_EVAL", "").strip().lower() not in {"on", "1", "true", "yes"}:
        print("SPAN_EVAL staat uit (default) — zet SPAN_EVAL=on om de eval-run te starten.")
        return 2
    ap = argparse.ArgumentParser(prog="python -m span.evaluation",
                                 description="A7 eval-set v1 (LO)")
    ap.add_argument("--only", choices=["geheugen", "taken"], default=None,
                    help="alleen deze soort draaien")
    ap.add_argument("--limit", type=int, default=None,
                    help="max items per soort (smoke-run)")
    ap.add_argument("--json", dest="json_path", default=None,
                    help="schrijf het machine-leesbare rapport naar dit pad")
    ap.add_argument("--min-score", type=float, default=0.8,
                    help="exit-code 1 onder deze totaalscore (default 0.8)")
    args = ap.parse_args(argv)

    rapport = run_eval(only=args.only, limit=args.limit)
    print(render_report(rapport))
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rapport["score"] >= args.min_score else 1


if __name__ == "__main__":
    sys.exit(main())
