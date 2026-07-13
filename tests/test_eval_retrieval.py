# tests/test_eval_retrieval.py
"""A4 — recall-meetpunt: eval-script is draaibaar (kapotte --hybrid weg,
ontbrekende gouden set faalt vriendelijk)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_retrieval.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_retrieval", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parser_kent_geen_kapotte_hybrid_vlag_meer():
    mod = _load()
    ap = mod.build_parser()
    assert ap.parse_args([]).decay == "off"
    assert ap.parse_args(["--decay", "soft"]).decay == "soft"
    with pytest.raises(SystemExit):  # --hybrid gooide voorheen een TypeError diep
        ap.parse_args(["--hybrid"])  # in de run; nu bestaat de vlag simpelweg niet


def test_ontbrekende_gouden_set_faalt_vriendelijk(tmp_path):
    mod = _load()
    with pytest.raises(SystemExit) as excinfo:
        mod.load_eval_set(tmp_path / "bestaat_niet.json")
    assert "eval_retrieval_set.json" in str(excinfo.value)


def test_bestaande_gouden_set_wordt_geladen(tmp_path):
    mod = _load()
    p = tmp_path / "eval_retrieval_set.json"
    p.write_text('{"eval_set": [{"query": "q", "expected_id": "mf-1", '
                 '"kind": "feit"}]}', encoding="utf-8")
    cases = mod.load_eval_set(p)
    assert cases == [{"query": "q", "expected_id": "mf-1", "kind": "feit"}]
