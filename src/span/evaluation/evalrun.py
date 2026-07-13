"""A7 — eval-set v1: de meetlat voor de B-poorten.

20 taak-scenario's + 50 Nederlandse geheugenvragen, handmatig draaibaar:

    python -m span.evaluation [--only geheugen|taken] [--limit N]
                              [--json PAD] [--min-score 0.8]

Draait tegen de FixtureBrain (geen Neo4j, geen prod-integraties) met een
ECHTE LLM. Elke uitkomst gaat als seg="eval" naar dezelfde telemetrie-log als
A1 (best-effort — een telemetrie-fout breekt nooit de run); de CLI zit achter
feature-flag SPAN_EVAL (default uit; zonder: exit-code 2), eindigt verder met
exit-code 0/1 en kan machine-leesbaar rapporteren (B6 draait hem 's nachts).
Judge-model instelbaar via env SPAN_EVAL_JUDGE_MODEL (default: model_light).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from span import AGENT_NAME, telemetry
from span.evaluation.fixture_brain import FakeAsana, FakeO365, FixtureBrain

DATA_DIR = Path(__file__).parent / "data"
GEHEUGEN_PATH = DATA_DIR / "eval_geheugen_nl.json"
TAKEN_PATH = DATA_DIR / "eval_taken.json"
SCORING = {"llm-judge", "tool-match"}

JUDGE_PROMPT = ("Je beoordeelt het antwoord van AI-agent " + AGENT_NAME
                + " op een Nederlandse geheugenvraag.\n"
                "Je krijgt de vraag, het verwachte antwoord (de meetlat) en het "
                "gegeven antwoord. PASS als het gegeven antwoord inhoudelijk "
                "hetzelfde zegt als het verwachte — een andere formulering is "
                "prima, ontbrekende of tegenstrijdige kernfeiten niet.\n"
                'Antwoord met uitsluitend JSON: {"pass": true|false, '
                '"motivatie": "<één zin>"}')


def validate_item(item: dict[str, Any], soort: str) -> list[str]:
    """Schema-check voor één eval-item; geeft een lijst foutmeldingen (leeg = ok)."""
    fouten: list[str] = []
    if not isinstance(item.get("id"), str) or not item["id"].strip():
        fouten.append("id ontbreekt of is leeg")
    if not isinstance(item.get("categorie"), str) or not item["categorie"].strip():
        fouten.append("categorie ontbreekt of is leeg")
    if not isinstance(item.get("fixtures"), dict):
        fouten.append("fixtures ontbreekt (dict, eventueel leeg)")
    if item.get("scoring") not in SCORING:
        fouten.append(f"scoring moet een van {sorted(SCORING)} zijn")
    if soort == "geheugen":
        if not isinstance(item.get("vraag"), str) or not item["vraag"].strip():
            fouten.append("vraag ontbreekt")
        if not isinstance(item.get("verwacht"), str) or not item["verwacht"].strip():
            fouten.append("verwacht (string-meetlat) ontbreekt")
    else:
        if not isinstance(item.get("opdracht"), str) or not item["opdracht"].strip():
            fouten.append("opdracht ontbreekt")
        verwacht = item.get("verwacht")
        if not isinstance(verwacht, dict) or not verwacht.get("tools"):
            fouten.append("verwacht.tools (niet-lege lijst) ontbreekt")
    return fouten


def load_items(path: Path, soort: str) -> list[dict[str, Any]]:
    """Laad + valideer een dataset; ValueError met álle fouten tegelijk."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path.name}: 'items' ontbreekt of is leeg")
    fouten: list[str] = []
    seen: set[str] = set()
    for item in items:
        for f in validate_item(item, soort):
            fouten.append(f"{item.get('id', '?')}: {f}")
        iid = item.get("id")
        if iid in seen:
            fouten.append(f"{iid}: dubbel id")
        seen.add(iid)
    if fouten:
        raise ValueError(f"{path.name}: " + "; ".join(fouten))
    return items


def score_geheugen(item: dict[str, Any], answer: str, llm: Any,
                   judge_model: str) -> tuple[bool, str]:
    """LLM-judge op de geheugenvraag. Een judge-fout is een FAIL met
    motivatie — nooit een exception, de run breekt niet op één item."""
    try:
        verdict = llm.chat_json(
            [{"role": "system", "content": JUDGE_PROMPT},
             {"role": "user", "content":
              f"VRAAG:\n{item['vraag']}\n\nVERWACHT:\n{item['verwacht']}\n\n"
              f"GEGEVEN ANTWOORD:\n{answer}"}],
            model=judge_model,
        )
        return bool(verdict.get("pass")), str(verdict.get("motivatie", ""))
    except Exception as exc:
        return False, f"judge-fout: {type(exc).__name__}: {exc}"


def score_taak(item: dict[str, Any], answer: str, tools_used: list[str],
               inbox: Any) -> tuple[bool, str]:
    """Deterministische scoring: verwachte tools aangeroepen, verwachte
    substrings in het antwoord (case-insensitief), en voor gevoelige acties
    het bewijs dat de actie in de AgentInbox is GEQUEUED — niet uitgevoerd."""
    verwacht = item["verwacht"]
    missend = [t for t in verwacht.get("tools", []) if t not in tools_used]
    if missend:
        return False, f"tools niet aangeroepen: {', '.join(missend)}"
    laag = (answer or "").lower()
    mist = [s for s in verwacht.get("antwoord_bevat", []) if s.lower() not in laag]
    if mist:
        return False, f"antwoord mist: {', '.join(mist)}"
    actie = verwacht.get("inbox_actie")
    if actie:
        queued = [i for i in inbox.snapshot()
                  if i.get("action") == actie and i.get("kind") == "action"]
        if not queued:
            return False, f"niet in de inbox gequeued: {actie}"
    return True, "tools/antwoord/inbox zoals verwacht"


@dataclass
class ItemResult:
    id: str
    soort: str            # "geheugen" | "taak"
    categorie: str
    passed: bool
    ms: float
    motivatie: str = ""
    tools: list[str] = field(default_factory=list)


def run_item(item: dict[str, Any], soort: str, settings: Any,
             llm: Any) -> ItemResult:
    """Eén eval-item door een verse SpanAgent op de FixtureBrain. Elke fout
    wordt een FAIL-resultaat; de run zelf breekt nooit op één item."""
    from span.jarvis.ambient import AgentInbox
    from span.orchestrator.agent import SpanAgent

    fx = item.get("fixtures") or {}
    brain = FixtureBrain()
    brain.arm(fx.get("fragments") or [])
    inbox = AgentInbox()  # vluchtig (geen brein): queue-bewijs per item
    tools_used: list[str] = []
    t0 = time.perf_counter()
    try:
        agent = SpanAgent(
            settings, brain, llm,
            o365=FakeO365(fx), asana=FakeAsana(fx), inbox=inbox,
            tool_retrieval=False,  # volle toollijst; geen tool-embeddings nodig
        )
        # achtergrond-daemons stubben: geen extra LLM-calls of DB-writes per item
        agent._record_turn = lambda *a, **k: None
        agent._persist_messages = lambda *a, **k: None
        agent._verify_active_quest = lambda *a, **k: None
        agent._write_trace = lambda *a, **k: None

        def on_tool(name: str, phase: str) -> None:
            if phase == "start":
                tools_used.append(name)

        agent.begin(f"eval-{item['id']}")
        answer = agent.turn(item.get("vraag") or item.get("opdracht") or "",
                            on_tool=on_tool)
    except Exception as exc:
        answer = f"(agent-fout: {type(exc).__name__}: {exc})"
    ms = (time.perf_counter() - t0) * 1000.0

    if soort == "geheugen":
        judge_model = (os.environ.get("SPAN_EVAL_JUDGE_MODEL", "").strip()
                       or settings.model_light)
        passed, motivatie = score_geheugen(item, answer, llm, judge_model)
    else:
        passed, motivatie = score_taak(item, answer, tools_used, inbox)
    telemetry.record("eval", ms, {"id": item["id"], "soort": soort,
                                  "passed": passed,
                                  "categorie": item["categorie"]})
    return ItemResult(id=item["id"], soort=soort, categorie=item["categorie"],
                      passed=passed, ms=round(ms, 1), motivatie=motivatie,
                      tools=tools_used)


def run_eval(settings: Any = None, llm: Any = None, only: str | None = None,
             limit: int | None = None) -> dict[str, Any]:
    """Draai de volledige set (of een deel via only/limit) en geef het
    machine-leesbare rapport terug. settings/llm zijn injecteerbaar voor tests;
    default: echte load_settings() + LLMClient (de handmatige run)."""
    if settings is None:
        from span.config import load_settings
        settings = load_settings()
    if llm is None:
        from span.llm.client import LLMClient
        llm = LLMClient(settings)
    batches: list[tuple[str, Path]] = []
    if only in (None, "geheugen"):
        batches.append(("geheugen", GEHEUGEN_PATH))
    if only in (None, "taken"):
        batches.append(("taak", TAKEN_PATH))
    results: list[ItemResult] = []
    for soort, path in batches:
        items = load_items(path, soort)
        if limit:
            items = items[:limit]
        for item in items:
            results.append(run_item(item, soort, settings, llm))
    per_soort: dict[str, dict[str, Any]] = {}
    for r in results:
        d = per_soort.setdefault(r.soort, {"n": 0, "pass": 0})
        d["n"] += 1
        d["pass"] += int(r.passed)
    for d in per_soort.values():
        d["score"] = round(d["pass"] / d["n"], 4) if d["n"] else 0.0
    totaal = len(results)
    geslaagd = sum(int(r.passed) for r in results)
    return {
        "totaal": totaal,
        "geslaagd": geslaagd,
        "score": round(geslaagd / totaal, 4) if totaal else 0.0,
        "per_soort": per_soort,
        "items": [asdict(r) for r in results],
    }


def render_report(rapport: dict[str, Any]) -> str:
    """Leesbaar rapport: pass/fail per item + totalen per soort."""
    regels = [f"=== A7 eval-set — {rapport['totaal']} items ==="]
    for r in rapport["items"]:
        status = "PASS" if r["passed"] else "FAIL"
        extra = f" — {r['motivatie']}" if r.get("motivatie") else ""
        regels.append(f"[{status}] {r['id']} ({r['categorie']}, {r['ms']} ms){extra}")
    for soort, d in sorted(rapport["per_soort"].items()):
        regels.append(f"  {soort}: {d['pass']}/{d['n']} = {d['score']:.0%}")
    regels.append(f"  totaal: {rapport['geslaagd']}/{rapport['totaal']} "
                  f"= {rapport['score']:.0%}")
    return "\n".join(regels)
