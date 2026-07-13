# A4 — Geheugen-onderhoud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LO's geheugen blijft snel en betrouwbaar: ontbrekende indexen hersteld, promptgroei per categorie begrensd (quests), de agent overleeft een traag of onbereikbaar brein, en de meetpunten brain-latency + recall-kwaliteit leveren het bewijs voor de B4-poortvraag ("veroorzaakte tijd-blindheid echte recall-fouten?").

**Architecture:** Alle nieuwe logica van enige omvang landt in één kleine module `span.db.health` (index-gezondheid) plus dunne haakjes in bestaande bestanden: range-indexen als constante in `span.db.schema` (zelfde vorm als `MESSAGE_SESSION_INDEX`), een telemetrie-wrapper ín `BrainDB` (segment `brain`, meta `{"op": ...}`), een embed-guard in `SpanAgent.turn()` (de audit-crash) en een degraded-bootstrap in `begin()`. Meetpunten schrijven via de bestaande A1-module `span.telemetry` (alleen importeren, niet wijzigen); aggregatie is gratis via het bestaande `GET /api/telemetry`.

**Tech Stack:** Python 3, FastAPI, Neo4j 5 community, pytest. Geen nieuwe dependencies, geen nieuwe service.

> **Branch-eis:** dit plan bouwt op `a1-telemetrie` (of op master ná de A1-merge). A4 raakt `agent.py` en `routes.py` precies rond de regels die A1 toevoegde — wijzig A1's regels niet, alleen additief. `src/span/telemetry.py`, `tests/test_telemetry.py` en `tests/test_observability.py` worden NIET aangeraakt.

---

## File Structure

- **Create** `src/span/db/health.py` — `index_health(brain)`, `brain_latency_ms(brain)`, `check_brain_health(brain, inbox)`: SHOW INDEXES vs. de verwachte set + latency-probe.
- **Create** `tests/test_schema_indexes.py` — range-indexen + Entity-constraint in `init_schema`.
- **Create** `tests/test_brain_health.py` — index-gezondheid, endpoint, dagtaak.
- **Create** `tests/test_brain_latency.py` — telemetrie-wrapper in `BrainDB`.
- **Create** `tests/test_degraded.py` — embed-guard in `turn()` + degraded-bootstrap in `begin()`.
- **Create** `tests/test_bootstrap_limits.py` — quest-LIMIT + steps-cap.
- **Create** `tests/test_eval_retrieval.py` — gerepareerd eval-script.
- **Modify** `src/span/db/schema.py` — `RANGE_INDEXES` (na r.70) + loop in `init_schema` (na r.177) + `ENTITY_NAME_CONSTRAINT`-stap (fail-soft, met dedup-migratie).
- **Modify** `src/span/db/brain.py` — `_timed`-wrapper rond `run`/`run_read`/`vector_search`; `run_system` bewust ongemeten (schema-ruis).
- **Modify** `src/span/memory/bootstrap.py` — quest-query r.140-149 krijgt LIMIT; steps-cap in `render_bootstrap` r.291-297; nieuw `degraded_enabled()` + `degraded_bootstrap()`.
- **Modify** `src/span/orchestrator/agent.py` — embed-guard r.333-335 + formal-guard r.352 in `turn()`; bootstrap-guard r.282-283 in `begin()`; importregel r.21.
- **Modify** `src/span/server/routes.py` — nieuw `GET /api/brain/health` (owner-only, naast `/api/telemetry` r.950).
- **Modify** `src/span/jarvis/daily.py` — `BRAINHEALTH_TIME` + `do_brainhealth` in `daily_scheduler` (bestaand `due`/`run_task`-patroon).
- **Modify** `scripts/eval_retrieval.py` — kapotte `--hybrid`-vlag weg, vriendelijke fout bij ontbrekende gouden set, recall-uitkomst naar telemetrie.

**Feature-flags (env, consequent zo genoemd):**
- `SPAN_BRAIN_TELEMETRY` — default **aan** (`off/0/false/no` = uit): klep op het brain-latency-meetpunt, apart van `SPAN_TELEMETRY` omdat brain-records volumineus zijn (elke query = één JSONL-regel).
- `SPAN_DEGRADED_MODE` — default **aan** (`off/0/false/no` = uit): mag een sessie met minimale context starten als het brein onbereikbaar is. Uit = het oude gedrag (hard falen bij sessiestart). De embed-guard in `turn()` is bewust NIET gevlagd: dat is de crash-fix uit de audit, geen feature. De quest-limiet (Task 9) is bewust een constante i.p.v. een flag — motivatie staat bij Task 9.

---

## Task 1: Range-indexen — RANGE_INDEXES + init_schema-loop

**Files:**
- Modify: `src/span/db/schema.py` (constante na r.70, loop na r.177)
- Test: `tests/test_schema_indexes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema_indexes.py
"""A4 — geheugen-onderhoud: range-indexen + Entity-constraint in init_schema."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.db.schema as schema

EXPECTED_RANGE_NAMES = {
    "mf_created", "mf_type", "session_started", "quest_status",
    "quest_created", "insight_created", "mistake_created", "inboxitem_item_id",
}


def _settings():
    s = MagicMock()
    s.embed_dims = 8
    s.embed_model = "test-embed"
    return s


def test_range_indexes_zijn_compleet_en_idempotent():
    names = {name for name, _ in schema.RANGE_INDEXES}
    assert names == EXPECTED_RANGE_NAMES
    for name, cypher in schema.RANGE_INDEXES:
        assert "IF NOT EXISTS" in cypher  # draait bij ELKE serverstart -> idempotent
        assert f"CREATE INDEX {name} " in cypher


def test_init_schema_maakt_range_indexen_aan():
    brain = MagicMock()
    brain.run.return_value = []  # ook de drift-guard ziet dan 'geen config' -> geen raise

    log = schema.init_schema(brain, _settings())

    executed = [c.args[0] for c in brain.run.call_args_list]
    for _name, cypher in schema.RANGE_INDEXES:
        assert cypher in executed
    assert any("range-indexen" in regel for regel in log)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema_indexes.py::test_range_indexes_zijn_compleet_en_idempotent -v`
Expected: FAIL with `AttributeError: module 'span.db.schema' has no attribute 'RANGE_INDEXES'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/db/schema.py`, direct ná `MESSAGE_SESSION_INDEX` (r.67-70), voeg toe:

```python
# A4 — range-indexen op properties waar echte queries op filteren of sorteren.
# Elke regel heeft een bewijsplek in de code; zelfde vorm als message_session,
# idempotent (IF NOT EXISTS) en goedkoop: init_schema draait bij elke start
# en per user-brein.
RANGE_INDEXES: list[tuple[str, str]] = [
    # fragments.recent() en session_fragments(): ORDER BY mf.created
    ("mf_created",
     "CREATE INDEX mf_created IF NOT EXISTS FOR (n:MemoryFragment) ON (n.created)"),
    # fragments.recent(mf_type=...): WHERE mf.type = $type (3x per bootstrap)
    ("mf_type",
     "CREATE INDEX mf_type IF NOT EXISTS FOR (n:MemoryFragment) ON (n.type)"),
    # bootstrap recent_sessions + prev_conversation: ORDER BY s.started DESC
    ("session_started",
     "CREATE INDEX session_started IF NOT EXISTS FOR (n:Session) ON (n.started)"),
    # bootstrap quests: WHERE q.status IN ['open', 'active']
    ("quest_status",
     "CREATE INDEX quest_status IF NOT EXISTS FOR (n:Quest) ON (n.status)"),
    # agent._verify_active_quest (na elke beurt met tools): ORDER BY q.created DESC
    ("quest_created",
     "CREATE INDEX quest_created IF NOT EXISTS FOR (n:Quest) ON (n.created)"),
    # bootstrap-recency op formele kennis: ORDER BY n.created DESC
    ("insight_created",
     "CREATE INDEX insight_created IF NOT EXISTS FOR (n:Insight) ON (n.created)"),
    ("mistake_created",
     "CREATE INDEX mistake_created IF NOT EXISTS FOR (n:Mistake) ON (n.created)"),
    # AgentInbox: laden op n.item_id + opschonen WHERE n.item_id < $min
    ("inboxitem_item_id",
     "CREATE INDEX inboxitem_item_id IF NOT EXISTS FOR (n:InboxItem) ON (n.item_id)"),
]
```

In `init_schema`, direct ná het `MESSAGE_SESSION_INDEX`-blok (na de log-regel op r.176-177) en VÓÓR de embedding-drift-guard (die bewust hard raise't — nieuwe stappen komen daarvóór zodat een drift-raise de indexen niet blokkeert):

```python
    # A4: range-indexen op de veelgebruikte ORDER BY/WHERE-properties
    for _name, cypher in RANGE_INDEXES:
        brain.run(cypher)
    log.append(f"{len(RANGE_INDEXES)} range-indexen (A4 geheugen-onderhoud)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schema_indexes.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/db/schema.py tests/test_schema_indexes.py
git commit -m "feat(schema): A4 range-indexen op veelgebruikte query-properties"
```

---

## Task 2: Entity.name unique constraint — met dedup-migratie, fail-soft

`agent._link_entities` (agent.py r.761-771) doet `MERGE (e:Entity {name: $name})` ZONDER constraint of index: een label-scan per recorder-write én een race die dubbele nodes maakt bij parallelle recorder-threads. Bestaande duplicaten laten de constraint-creatie falen op het live brein — daarom éérst `dedup_entities` (bestaat al: daily.py r.360), dan de constraint. Fail-soft: init_schema draait bij elke serverstart; een mislukte constraint mag de boot nooit blokkeren.

**Files:**
- Modify: `src/span/db/schema.py` (nieuwe constante + stap in `init_schema`, ná de range-index-loop uit Task 1)
- Test: `tests/test_schema_indexes.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_schema_indexes.py
def test_init_schema_zet_entity_constraint_na_dedup(monkeypatch):
    dedup_calls: list = []

    def nep_dedup(brain):
        dedup_calls.append(brain)
        return 0

    # init_schema importeert dedup_entities lazy -> patchen op de bronmodule werkt
    monkeypatch.setattr("span.jarvis.daily.dedup_entities", nep_dedup)
    brain = MagicMock()
    brain.run.return_value = []  # SHOW CONSTRAINTS: entity_name bestaat nog niet

    log = schema.init_schema(brain, _settings())

    executed = [c.args[0] for c in brain.run.call_args_list]
    assert schema.ENTITY_NAME_CONSTRAINT in executed
    assert len(dedup_calls) == 1  # dedup draait VOOR de constraint
    assert any("entity_name" in regel for regel in log)


def test_entity_constraint_faalt_zacht(monkeypatch):
    def kapotte_dedup(brain):
        raise RuntimeError("dubbele Entity-namen")

    monkeypatch.setattr("span.jarvis.daily.dedup_entities", kapotte_dedup)
    brain = MagicMock()
    brain.run.return_value = []

    log = schema.init_schema(brain, _settings())  # geen exception = fail-soft werkt

    executed = [c.args[0] for c in brain.run.call_args_list]
    assert schema.ENTITY_NAME_CONSTRAINT not in executed
    assert any("overgeslagen" in regel for regel in log)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schema_indexes.py::test_init_schema_zet_entity_constraint_na_dedup -v`
Expected: FAIL with `AttributeError: module 'span.db.schema' has no attribute 'ENTITY_NAME_CONSTRAINT'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/db/schema.py`, direct ná de `RANGE_INDEXES`-constante:

```python
# A4 — Entity.name uniek: _link_entities MERGE't op naam zonder index (label-scan
# per recorder-write + dubbele-node-race bij parallelle recorder-threads).
# Bewust NIET in CONSTRAINTS: bestaande duplicaten laten de creatie falen en de
# constraints-loop faalt hard -> deze krijgt een eigen fail-soft stap.
ENTITY_NAME_CONSTRAINT = (
    "CREATE CONSTRAINT entity_name IF NOT EXISTS "
    "FOR (n:Entity) REQUIRE n.name IS UNIQUE"
)
```

In `init_schema`, direct ná de range-index-loop uit Task 1 (nog steeds vóór de drift-guard):

```python
    # A4: Entity.name uniek — eerst bestaande duplicaten samenvoegen (anders
    # faalt de constraint-creatie op het live brein), dan de constraint.
    # Fail-soft: het brein blijft bruikbaar zonder deze constraint.
    try:
        have = brain.run(
            "SHOW CONSTRAINTS YIELD name WHERE name = 'entity_name' RETURN name")
        if not have:
            from span.jarvis.daily import dedup_entities
            merged = dedup_entities(brain)
            brain.run(ENTITY_NAME_CONSTRAINT)
            log.append(f"entity_name-constraint gezet "
                       f"({merged} duplicaten samengevoegd)")
        else:
            log.append("entity_name-constraint al aanwezig")
    except Exception as exc:
        print(f"[schema] entity_name-constraint niet gezet: "
              f"{type(exc).__name__}: {exc}", flush=True)
        log.append("entity_name-constraint overgeslagen (zie serverlog)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schema_indexes.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/db/schema.py tests/test_schema_indexes.py
git commit -m "feat(schema): A4 Entity.name-constraint met dedup-migratie (fail-soft)"
```

---

## Task 3: Index-gezondheid — nieuwe module span.db.health

**Files:**
- Create: `src/span/db/health.py`
- Test: `tests/test_brain_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_health.py
"""A4 — index-gezondheid: SHOW INDEXES vs. de verwachte set + latency-probe."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.db.health as health


def _rij(name, state="ONLINE", typ="RANGE", pct=100.0):
    return {"name": name, "state": state, "type": typ, "populationPercent": pct}


def test_expected_indexes_dekken_vector_en_range():
    # de vijf vector-indexen + message_session + alle A4-range-indexen
    assert "mf_embedding" in health.EXPECTED_INDEXES
    assert "message_embedding" in health.EXPECTED_INDEXES
    assert "insight_embedding" in health.EXPECTED_INDEXES
    assert "message_session" in health.EXPECTED_INDEXES
    assert "quest_status" in health.EXPECTED_INDEXES


def test_index_health_ok_bij_alles_online():
    brain = MagicMock()
    brain.run.return_value = [_rij(n) for n in health.EXPECTED_INDEXES]
    out = health.index_health(brain)
    assert out["ok"] is True
    assert out["missing"] == [] and out["not_online"] == []


def test_index_health_ziet_missend_en_niet_online():
    brain = MagicMock()
    rows = [_rij(n) for n in health.EXPECTED_INDEXES]
    kwijt = rows.pop()                 # één verwachte index ontbreekt
    rows[0]["state"] = "POPULATING"    # en één is nog niet ONLINE
    brain.run.return_value = rows
    out = health.index_health(brain)
    assert out["ok"] is False
    assert kwijt["name"] in out["missing"]
    assert rows[0]["name"] in out["not_online"]


def test_brain_latency_ms_meet_een_probe():
    brain = MagicMock()
    brain.run.return_value = [{"ok": 1}]
    ms = health.brain_latency_ms(brain)
    assert isinstance(ms, float) and ms >= 0.0
    brain.run.assert_called_once_with("RETURN 1 AS ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brain_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.db.health'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/db/health.py
"""A4 — geheugen-onderhoud: gezondheid van het brein (indexen + latency).

Klein en zelfstandig: alleen leesqueries. Gebruikt door het owner-endpoint
GET /api/brain/health en de nachtelijke scheduler-taak. Een index die niet
ONLINE is (POPULATING/FAILED) laat db.index.vector.queryNodes hard falen —
dat willen we 's nachts of in het dashboard zien, niet middenin een gesprek.
Fouten propageren naar de aanroeper; die vangt zelf (endpoint/run_task).
"""
from __future__ import annotations

import time
from typing import Any

from span.db.brain import BrainDB
from span.db.schema import FORMAL_VECTOR_INDEXES, RANGE_INDEXES

# Alle indexen die init_schema hoort aan te maken, op naam zoals SHOW INDEXES
# ze toont. De entity_name-constraint staat hier bewust NIET in: die is
# fail-soft optioneel (Task 2) en zou anders elke nacht ruis melden zolang de
# migratie op een brein nog niet gelukt is.
EXPECTED_INDEXES: list[str] = (
    ["mf_embedding", "message_embedding", "message_session"]
    + [name for name, _label in FORMAL_VECTOR_INDEXES]
    + [name for name, _cypher in RANGE_INDEXES]
)


def index_health(brain: BrainDB) -> dict[str, Any]:
    """Vergelijk SHOW INDEXES met de verwachte set (werkt op Neo4j 5 community)."""
    rows = brain.run(
        "SHOW INDEXES YIELD name, state, type, populationPercent "
        "RETURN name, state, type, populationPercent"
    )
    by_name = {r["name"]: r for r in rows}
    missing = sorted(n for n in EXPECTED_INDEXES if n not in by_name)
    not_online = sorted(
        n for n in EXPECTED_INDEXES
        if n in by_name and by_name[n].get("state") != "ONLINE"
    )
    return {
        "ok": not missing and not not_online,
        "missing": missing,
        "not_online": not_online,
        "count": len(rows),
    }


def brain_latency_ms(brain: BrainDB) -> float:
    """Eén lichte probe-query, in milliseconden."""
    t0 = time.perf_counter()
    brain.run("RETURN 1 AS ok")
    return round((time.perf_counter() - t0) * 1000.0, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brain_health.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/db/health.py tests/test_brain_health.py
git commit -m "feat(health): A4 index-gezondheid + latency-probe voor het brein"
```

---

## Task 4: GET /api/brain/health — owner-only momentopname

Naast `/api/telemetry` (routes.py r.950-963): telemetrie is de historie, dit endpoint is de momentopname. Zelfde auth-tier (`_require_owner`), zelfde per-user-brein via `_request_context` als `/api/health` (r.1198-1215).

**Files:**
- Modify: `src/span/server/routes.py` (nieuw endpoint direct ná `telemetry_aggregates`, r.963)
- Test: `tests/test_brain_health.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brain_health.py
def test_brain_health_endpoint_owner_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import span.server.routes as routes

    brain = MagicMock()
    brain.run.side_effect = [
        [_rij(n) for n in health.EXPECTED_INDEXES],  # SHOW INDEXES
        [{"ok": 1}],                                  # latency-probe RETURN 1
    ]
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes, "_request_context",
                        lambda request: SimpleNamespace(brain=brain))

    out = asyncio.run(routes.brain_health(MagicMock()))
    assert out["ok"] is True
    assert out["latency_ms"] >= 0.0


def test_brain_health_endpoint_faalt_zacht_bij_kapot_brein(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import span.server.routes as routes

    brain = MagicMock()
    brain.run.side_effect = RuntimeError("neo4j down")
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes, "_request_context",
                        lambda request: SimpleNamespace(brain=brain))

    out = asyncio.run(routes.brain_health(MagicMock()))
    assert out["ok"] is False
    assert "RuntimeError" in out["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brain_health.py::test_brain_health_endpoint_owner_only -v`
Expected: FAIL with `AttributeError: module 'span.server.routes' has no attribute 'brain_health'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/server/routes.py`, direct ná `telemetry_aggregates` (r.963):

```python
@router.get("/api/brain/health")
async def brain_health(request: Request) -> dict[str, Any]:
    """Owner-only: index-gezondheid + brein-latency (A4 geheugen-onderhoud).
    Naast /api/telemetry: dít is de momentopname, telemetrie is de historie.
    Faalt zacht: een kapot brein geeft ok=False + de fout, geen 500."""
    _require_owner(request)
    ctx = _request_context(request)
    from span.db import health
    try:
        report = await asyncio.to_thread(health.index_health, ctx.brain)
        report["latency_ms"] = await asyncio.to_thread(
            health.brain_latency_ms, ctx.brain)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return report
```

(`_require_owner`, `_request_context` en `asyncio` zijn al geïmporteerd in `routes.py` — controleer de importlijst bovenin, ze worden door `/api/health` en `/api/telemetry` al gebruikt.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brain_health.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_brain_health.py
git commit -m "feat(health): owner-only GET /api/brain/health momentopname"
```

---

## Task 5: Nachtelijke brain-health-taak — scheduler + inbox-melding

Het bestaande `due`/`run_task`-patroon in `daily_scheduler` (daily.py r.469-492): mark-after-success, MAX_ATTEMPTS per dag, daarna inbox-melding. De inhoudelijke logica landt in `health.py` (daily.py is al 630 regels); daily.py krijgt alleen een dunne closure.

**Files:**
- Modify: `src/span/db/health.py` (nieuwe functie `check_brain_health`)
- Modify: `src/span/jarvis/daily.py` (constante bij r.450-451, closure direct ná `do_weekreview` r.535-551, due-regel in de while-loop bij r.591)
- Test: `tests/test_brain_health.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_brain_health.py
def test_check_brain_health_meldt_inbox_bij_probleem(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    brain = MagicMock()
    brain.run.side_effect = [
        [_rij("mf_embedding", state="FAILED", typ="VECTOR", pct=40.0)],  # SHOW INDEXES
        [{"ok": 1}],                                                     # latency-probe
    ]
    inbox = MagicMock()

    report = health.check_brain_health(brain, inbox)

    assert report["ok"] is False
    inbox.add.assert_called_once()
    assert inbox.add.call_args.kwargs["urgency"] == "high"
    assert inbox.add.call_args.kwargs["kind"] == "notify"


def test_check_brain_health_stil_bij_gezond_brein(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    brain = MagicMock()
    brain.run.side_effect = [
        [_rij(n) for n in health.EXPECTED_INDEXES],
        [{"ok": 1}],
    ]
    inbox = MagicMock()

    report = health.check_brain_health(brain, inbox)

    assert report["ok"] is True
    inbox.add.assert_not_called()  # geen dagelijkse ruis in de Agent Inbox
    # het meetpunt schrijft wél een brain-record (op=healthcheck)
    import span.telemetry as tel
    assert tel.aggregate()["segments"]["brain"]["count"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brain_health.py::test_check_brain_health_meldt_inbox_bij_probleem -v`
Expected: FAIL with `AttributeError: module 'span.db.health' has no attribute 'check_brain_health'`

- [ ] **Step 3: Write minimal implementation**

Append aan `src/span/db/health.py`:

```python
def check_brain_health(brain: BrainDB, inbox: Any = None) -> dict[str, Any]:
    """Dagelijkse controle: index-gezondheid + latency. Meldt alleen bij een
    écht probleem in de Agent Inbox (geen dagelijkse ruis); de latency gaat
    altijd als brain-record (op=healthcheck) naar de telemetrie-meetlat."""
    report = index_health(brain)
    report["latency_ms"] = brain_latency_ms(brain)
    from span import telemetry
    telemetry.record("brain", report["latency_ms"], {"op": "healthcheck"})
    if not report["ok"] and inbox is not None:
        probleem = ", ".join(report["missing"] + report["not_online"]) or "onbekend"
        inbox.add(kind="notify", title="Brein-index niet gezond",
                  detail=(f"Ontbrekend of niet ONLINE: {probleem}. Controleer met "
                          "SHOW INDEXES in Neo4j. Ontbreekt een index: een "
                          "herstart van span draait init_schema opnieuw en maakt "
                          "hem aan. Staat een index op FAILED: eerst DROP INDEX "
                          "<naam> in de Neo4j-browser (de index bestáát dan nog, "
                          "dus IF NOT EXISTS slaat hem over), daarna span "
                          "herstarten."),
                  urgency="high")
    return report
```

In `src/span/jarvis/daily.py`, bij de bestaande tijd-constantes (r.450-451):

```python
BRAINHEALTH_TIME = "03:45"  # ná de consolidatie van 03:30
```

In `daily_scheduler`, direct ná de `do_weekreview`-closure:

```python
    async def do_brainhealth() -> None:
        from span.db.health import check_brain_health
        report = await asyncio.to_thread(
            check_brain_health, state["brain"], state.get("inbox"))
        log(f"brainhealth: ok={report['ok']} latency={report['latency_ms']}ms")
```

In de while-loop, direct ná de `consolidate`-regel (r.591-592):

```python
            if due(BRAINHEALTH_TIME, "brainhealth", now):
                await run_task("brainhealth", do_brainhealth)
```

(`run_task` markeert via `_mark_run` de Config-prop `c.last_brainhealth` — geen extra administratie nodig.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brain_health.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the jarvis-tests (no regression op daily.py)**

Run: `python -m pytest tests/test_jarvis.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/span/db/health.py src/span/jarvis/daily.py tests/test_brain_health.py
git commit -m "feat(health): nachtelijke brain-health-taak met inbox-melding"
```

---

## Task 6: Brain-latency-meetpunt — telemetrie-wrapper in BrainDB

Eén wrapper in `BrainDB` dekt bootstrap, fragments, tools én scheduler in één klap. Meta `{"op": "run"|"read"|"vector"}` maakt de p50/p95 per operatie leesbaar in `GET /api/telemetry` (segment `brain` verschijnt daar vanzelf). `run_system` blijft bewust ongemeten (schema/system-ruis). Volume-klep: `SPAN_BRAIN_TELEMETRY` (default aan). Let op: alleen het privé/shared/per-user-brein (`BrainDB`); `WorkDB` is een aparte klasse en blijft buiten scope.

**Files:**
- Modify: `src/span/db/brain.py`
- Test: `tests/test_brain_latency.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_latency.py
"""A4 — brain-latency-meetpunt: BrainDB meet run/read/vector naar telemetrie."""
from __future__ import annotations

import json

import pytest

import span.telemetry as tel
from span.db.brain import BrainDB


def _braindouble():
    from unittest.mock import MagicMock
    b = BrainDB.__new__(BrainDB)  # omzeil __init__: geen echte driver nodig
    driver = MagicMock()
    rec = MagicMock()
    rec.data.return_value = {"ok": 1}
    driver.session.return_value.__enter__.return_value.run.return_value = [rec]
    b._driver = driver
    b.database = "test"
    return b, driver


def _rows(tmp_path):
    return [json.loads(line) for line in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]


def test_run_meet_brain_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, _ = _braindouble()
    assert b.run("RETURN 1 AS ok") == [{"ok": 1}]
    rows = _rows(tmp_path)
    assert rows[0]["seg"] == "brain" and rows[0]["meta"]["op"] == "run"


def test_vector_search_meet_op_vector(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, _ = _braindouble()
    b.vector_search("mf_embedding", [0.1] * 8, k=3)
    rows = _rows(tmp_path)
    # precies één record (vector gaat NIET nog eens dubbel via run)
    assert len(rows) == 1 and rows[0]["meta"]["op"] == "vector"


def test_fout_krijgt_outcome_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "on")
    b, driver = _braindouble()
    driver.session.return_value.__enter__.return_value.run.side_effect = \
        RuntimeError("neo4j down")
    with pytest.raises(RuntimeError):
        b.run("RETURN 1 AS ok")
    rows = _rows(tmp_path)
    assert rows[0]["meta"]["outcome"] == "error"


def test_flag_uit_meet_niets(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_BRAIN_TELEMETRY", "off")
    b, _ = _braindouble()
    assert b.run("RETURN 1 AS ok") == [{"ok": 1}]  # query werkt gewoon
    assert not (tmp_path / "t.jsonl").exists()
    assert tel.aggregate()["segments"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brain_latency.py -v`
Expected: FAIL — `test_run_meet_brain_segment` en `test_vector_search_meet_op_vector` falen omdat er geen JSONL-bestand ontstaat (`FileNotFoundError` in `_rows`); alleen `test_flag_uit_meet_niets` slaagt al.

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/db/brain.py` het import-blok (r.1-9) en de methodes `run`, `run_read` en `vector_search` (r.30-42 en r.76-88); `run_system` en `ensure_database` blijven ongewijzigd:

```python
"""Brein-database: Neo4j read/write client voor de span-brain graph."""

from __future__ import annotations

import os
import time
from typing import Any

from neo4j import GraphDatabase, Driver, READ_ACCESS

from span import telemetry
from span.config import Settings


def _tel_enabled() -> bool:
    """SPAN_BRAIN_TELEMETRY (default aan): klep op het A4-meetpunt brain-latency.
    Apart van SPAN_TELEMETRY omdat brain-records volumineus zijn (elke query
    is één JSONL-regel); 'off/0/false/no' zet alleen dít segment uit."""
    val = os.environ.get("SPAN_BRAIN_TELEMETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}
```

En in de klasse (de bestaande bodies van `run`/`run_read` worden `_run_raw`/`_read_raw`; `vector_search` gaat via `_timed` zodat hij NIET dubbel via `run` meet):

```python
    def _run_raw(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self.database) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def _read_raw(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(
            database=self.database, default_access_mode=READ_ACCESS
        ) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def _timed(self, op: str, fn: Any, query: str,
               **params: Any) -> list[dict[str, Any]]:
        """A4-meetpunt: brain-latency per operatie naar de telemetrie-JSONL.
        telemetry.record is zelf al best-effort; dit pad kan een query dus
        nooit breken — bij een query-fout meten we de duur mét outcome=error
        en gooien we de fout gewoon door."""
        if not _tel_enabled():
            return fn(query, **params)
        t0 = time.perf_counter()
        try:
            out = fn(query, **params)
        except Exception:
            telemetry.record("brain", (time.perf_counter() - t0) * 1000.0,
                             {"op": op, "outcome": "error"})
            raise
        telemetry.record("brain", (time.perf_counter() - t0) * 1000.0, {"op": op})
        return out

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        return self._timed("run", self._run_raw, query, **params)

    def run_read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Strikt lezen: de database zelf weigert schrijfacties (READ_ACCESS),
        onafhankelijk van wat een regex-check ervan vindt."""
        return self._timed("read", self._read_raw, query, **params)

    def vector_search(
        self, index: str, embedding: list[float], k: int = 5
    ) -> list[dict[str, Any]]:
        return self._timed(
            "vector", self._run_raw,
            """
            CALL db.index.vector.queryNodes($index, $k, $embedding)
            YIELD node, score
            RETURN node, score
            """,
            index=index, k=k, embedding=embedding,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brain_latency.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite (BrainDB wordt overal gebruikt — geen regressie)**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/span/db/brain.py tests/test_brain_latency.py
git commit -m "feat(brain): A4 brain-latency-meetpunt in BrainDB (SPAN_BRAIN_TELEMETRY)"
```

---

## Task 7: Degraded-mode in turn() — embed-guard (de audit-crash)

De audit-bevinding: `agent.py` r.334 (`embedding = self._fragments.embed(user_message)`) heeft géén try/except — bij ORQ-uitval propageert de exception uit `turn()` en crasht de WS-beurt. Alles stroomafwaarts valt al netjes terug: `_vsearch_all` slikt vector-fouten (fragments.py r.56-69), `specs_for` heeft een eigen vangnet en werkt met `embedding=None` (tools.py r.187-190, r.200), `_persist_messages` guardt zijn embed al (agent.py r.730-735). De fix is dus klein: embed guarden → `embedding=None`, RAG-zoekacties overslaan, eerlijke log naar print én `uvicorn.error` (les van de productie-uitval van 2026-07-02), meetpunt-record. NIET gevlagd: dit is een bug-fix. A1's regels (r.325-326, r.480-483, r.551-556) blijven onaangeraakt.

**Files:**
- Modify: `src/span/orchestrator/agent.py` (r.333-335 en r.350-356)
- Test: `tests/test_degraded.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_degraded.py
"""A4 — degraded-mode: een embed/brain-uitval mag een beurt of sessiestart
nooit breken."""
from __future__ import annotations

import json

import span.telemetry as tel


def _agent_double(frag, llm):
    from unittest.mock import MagicMock
    from span.orchestrator.agent import SpanAgent

    agent = SpanAgent.__new__(SpanAgent)  # omzeil __init__: we testen alleen turn()
    tb = MagicMock()
    tb.specs_for.return_value = []
    tb.touched = []
    agent._toolbox = tb
    agent._messages = []
    agent._recorders = []
    agent._fragments = frag
    settings = MagicMock()
    settings.model_main = "test-model"
    agent._settings = settings
    agent._security = {}
    agent._llm = llm
    # achtergrond-helpers uit: hier testen we alleen het degraded-pad
    agent._record_turn = lambda *a, **k: None
    agent._persist_messages = lambda *a, **k: None
    agent._verify_active_quest = lambda *a, **k: None
    agent._write_trace = lambda *a, **k: None
    return agent


def test_turn_overleeft_embed_uitval(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    from unittest.mock import MagicMock

    frag = MagicMock()
    frag.embed.side_effect = RuntimeError("ORQ onbereikbaar")

    reply = MagicMock()
    reply.content = "antwoord zonder geheugen"
    reply.tool_calls = None
    llm = MagicMock()
    llm.chat.return_value = reply

    agent = _agent_double(frag, llm)
    out = agent.turn("hoi")

    assert "antwoord zonder geheugen" in out
    # zonder embedding géén RAG-zoekacties (search zou anders zelf opnieuw
    # embedden en alsnog crashen)
    frag.search.assert_not_called()
    frag.search_formal.assert_not_called()
    # en het meetpunt legt de uitval vast
    rows = [json.loads(line) for line in
            (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    embeds = [r for r in rows
              if r["seg"] == "brain" and r.get("meta", {}).get("op") == "embed"]
    assert embeds and embeds[0]["meta"]["outcome"] == "error"


def test_turn_met_werkende_embed_doet_gewoon_rag(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    from unittest.mock import MagicMock

    frag = MagicMock()
    frag.embed.return_value = [0.1] * 8
    frag.search.return_value = []
    frag.search_formal.return_value = []

    reply = MagicMock()
    reply.content = "antwoord"
    reply.tool_calls = None
    llm = MagicMock()
    llm.chat.return_value = reply

    agent = _agent_double(frag, llm)
    out = agent.turn("hoi")

    assert "antwoord" in out
    frag.search.assert_called_once()   # geen regressie op het normale RAG-pad
    frag.search_formal.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_degraded.py::test_turn_overleeft_embed_uitval -v`
Expected: FAIL with `RuntimeError: ORQ onbereikbaar` (de ongeguarde embed-crash — exact de audit-bevinding)

- [ ] **Step 3: Write minimal implementation**

In `src/span/orchestrator/agent.py`, vervang r.333-335:

```python
        memo_msg: dict[str, Any] | None = None
        embedding = self._fragments.embed(user_message)
        relevant = self._fragments.search(user_message, k=2, embedding=embedding)
```

door:

```python
        memo_msg: dict[str, Any] | None = None
        # A4 degraded-mode: de audit vond dat een ORQ/embed-uitval hier de hele
        # WS-beurt liet crashen. Zonder embedding werkt de beurt gewoon door,
        # alleen zonder geheugen-RAG — specs_for en _persist_messages vallen
        # stroomafwaarts zelf al netjes terug op embedding=None.
        embedding: list[float] | None
        _e0 = _time.perf_counter()
        try:
            embedding = self._fragments.embed(user_message)
        except Exception as exc:
            embedding = None
            import logging
            logging.getLogger("uvicorn.error").warning(
                "[degraded] embedding onbereikbaar — beurt zonder geheugen-RAG "
                "(%s: %s)", type(exc).__name__, exc)
            print(f"[degraded] embedding onbereikbaar — beurt zonder geheugen-RAG: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            telemetry.record("brain", (_time.perf_counter() - _e0) * 1000.0,
                             {"op": "embed", "outcome": "error"})
        relevant = (self._fragments.search(user_message, k=2, embedding=embedding)
                    if embedding is not None else [])
```

En vervang r.350-356 (het formal-blok; `search_formal` zou bij `embedding=None` zelf opnieuw embedden en alsnog crashen):

```python
        # formele kennis (Insights/Mistakes/Ideas): duurste, gedestilleerde
        # kennis; één sterk passende hit volstaat meestal.
        for r in self._fragments.search_formal(user_message, k=1, embedding=embedding):
            if r["score"] > 0.55:
                les = f" → {r['lesson']}" if r.get("lesson") else ""
                lines.append(f"- [{r['id']} · {r['label']} · score {r['score']}] "
                             f"{r['content']}{les}")
```

door:

```python
        # formele kennis (Insights/Mistakes/Ideas): duurste, gedestilleerde
        # kennis; één sterk passende hit volstaat meestal. In degraded-mode
        # (embedding=None) overslaan: search_formal zou anders zelf embedden.
        formal_hits = (self._fragments.search_formal(user_message, k=1,
                                                     embedding=embedding)
                       if embedding is not None else [])
        for r in formal_hits:
            if r["score"] > 0.55:
                les = f" → {r['lesson']}" if r.get("lesson") else ""
                lines.append(f"- [{r['id']} · {r['label']} · score {r['score']}] "
                             f"{r['content']}{les}")
```

(`_time` bestaat al op r.324 uit de A1-instrumentatie; `telemetry` is al geïmporteerd op r.24. `specs_for(user_message, embedding=embedding)` op r.405 blijft ongewijzigd — dat pad werkt al met `None`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_degraded.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run de A1-turn-tests (gedeelde regels — geen regressie)**

Run: `python -m pytest tests/test_telemetry.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/span/orchestrator/agent.py tests/test_degraded.py
git commit -m "harden(agent): A4 embed-guard in turn() — beurt overleeft ORQ/brain-uitval"
```

---

## Task 8: Degraded-mode in begin() — minimale bootstrap bij brain-down

`begin()` r.282 heeft géén vangnet: `load_bootstrap` faalt hard bij brain-down — de identity-query (bootstrap.py r.111-118) propageert de driver-fout (bijv. `ServiceUnavailable`) ongehinderd; r.119-120 is de aparte geen-Identity-raise — en de sessie start niet — dat raakt óók crons.py r.168 en task_runners.py (achtergrondtaken). Ontwerpkeuze: bij brain-down start de sessie met een minimale, EERLIJKE context (identiteit uit `AGENT_NAME`, verder leeg; de origin-regel in de prompt meldt expliciet dat het geheugen onbereikbaar is — geen stille fallback). Achter `SPAN_DEGRADED_MODE` (default aan; uit = oude hard-fail gedrag).

**Files:**
- Modify: `src/span/memory/bootstrap.py` (nieuw: `degraded_enabled`, `degraded_bootstrap`; `import os` bovenin)
- Modify: `src/span/orchestrator/agent.py` (importregel r.21 + guard om r.282-283)
- Test: `tests/test_degraded.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_degraded.py
def _begin_double(brain, monkeypatch):
    from unittest.mock import MagicMock
    import span.orchestrator.agent as agent_mod
    from span.orchestrator.agent import SpanAgent

    # ToolBox-constructie is hier niet onder test -> vervangen door een dubbel
    monkeypatch.setattr(agent_mod, "ToolBox", MagicMock())
    agent = SpanAgent.__new__(SpanAgent)
    agent._brain = brain
    agent._fragments = MagicMock()
    settings = MagicMock()
    settings.model_light = "test-light"
    agent._settings = settings
    agent.user_location = None
    for attr in ("_work", "_o365", "_asana", "_inbox", "_autonomy", "_llm",
                 "_disabled_tools", "_integration_perms", "_fireflies",
                 "_telegram", "_security", "_mcp", "_shared", "_tasks",
                 "_progress_cb", "_tool_retrieval", "_tool_retrieval_k"):
        setattr(agent, attr, None)
    return agent


def test_begin_start_degraded_bij_brain_down(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    monkeypatch.setenv("SPAN_DEGRADED_MODE", "on")

    from unittest.mock import MagicMock

    brain = MagicMock()
    brain.run.side_effect = RuntimeError("neo4j down")
    agent = _begin_double(brain, monkeypatch)

    ctx = agent.begin("session-test", first_message=None)

    assert ctx.protocols == [] and ctx.quests == []
    assert ctx.identity["name"]  # naam komt uit AGENT_NAME (default 'LO')
    system = agent._messages[0]["content"][0]["text"]
    assert "degraded" in system  # eerlijke melding in de prompt, geen stille fallback


def test_begin_flag_uit_geeft_oude_hard_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_DEGRADED_MODE", "off")

    from unittest.mock import MagicMock

    import pytest

    brain = MagicMock()
    brain.run.side_effect = RuntimeError("neo4j down")
    agent = _begin_double(brain, monkeypatch)

    with pytest.raises(RuntimeError):
        agent.begin("session-test", first_message=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_degraded.py::test_begin_start_degraded_bij_brain_down -v`
Expected: FAIL with `RuntimeError: neo4j down` (begin() heeft nog geen vangnet)

- [ ] **Step 3: Write minimal implementation**

In `src/span/memory/bootstrap.py`: voeg `import os` toe aan het import-blok (r.11, naast `import time`) en append onder `end_session` (na r.53):

```python
def degraded_enabled() -> bool:
    """SPAN_DEGRADED_MODE (default aan): mag de agent met een minimale context
    starten als het brein onbereikbaar is? 'off/0/false/no' = het oude gedrag
    (hard falen bij sessiestart)."""
    val = os.environ.get("SPAN_DEGRADED_MODE", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def degraded_bootstrap() -> BootstrapContext:
    """Minimale, eerlijke context voor brain-down: identiteit uit AGENT_NAME,
    verder leeg. De origin-regel meldt de degraded-toestand expliciet in de
    system prompt — geen stille fallback, geen verzonnen geheugen."""
    from span import AGENT_NAME
    return BootstrapContext(
        identity={
            "name": AGENT_NAME,
            "owner": "Bas Spaan",
            "philosophy": "Treat this graph as my brain, my memory, my intelligence.",
            "origin": (f"{AGENT_NAME} draait tijdelijk in degraded-mode: het brein "
                       "(Neo4j) is onbereikbaar. Geheugen, protocollen en quests "
                       "ontbreken deze sessie — zeg dat eerlijk als het relevant is."),
            "voice": None,
        },
        protocols=[], quests=[], decisions=[], anti_patterns=[], soul=[], skills=[],
    )
```

In `src/span/orchestrator/agent.py`, vervang de importregel r.21:

```python
from span.memory.bootstrap import BootstrapContext, load_bootstrap, render_bootstrap
```

door:

```python
from span.memory.bootstrap import (BootstrapContext, degraded_bootstrap,
                                   degraded_enabled, load_bootstrap,
                                   render_bootstrap)
```

En vervang in `begin()` r.282-283:

```python
        self._bootstrap = load_bootstrap(self._brain, self._fragments, first_message,
                                         shared=self._shared)
```

door:

```python
        # A4 degraded-mode: brain-down mag een sessiestart niet meer blokkeren
        # (raakt ook crons/task_runners die begin() aanroepen). Eerlijk falen
        # blijft mogelijk via SPAN_DEGRADED_MODE=off.
        import time as _time
        _b0 = _time.perf_counter()
        try:
            self._bootstrap = load_bootstrap(self._brain, self._fragments,
                                             first_message, shared=self._shared)
        except Exception as exc:
            if not degraded_enabled():
                raise
            import logging
            logging.getLogger("uvicorn.error").warning(
                "[degraded] bootstrap onbereikbaar — sessie start met minimale "
                "context (%s: %s)", type(exc).__name__, exc)
            print(f"[degraded] bootstrap onbereikbaar — sessie start met minimale "
                  f"context: {type(exc).__name__}: {exc}", flush=True)
            telemetry.record("brain", (_time.perf_counter() - _b0) * 1000.0,
                             {"op": "bootstrap", "outcome": "error"})
            self._bootstrap = degraded_bootstrap()
```

(De Config-systeemprompt-leesquery direct eronder, r.286-293, heeft al een eigen try/except en faalt dus vanzelf zacht mee.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_degraded.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/memory/bootstrap.py src/span/orchestrator/agent.py tests/test_degraded.py
git commit -m "harden(agent): A4 degraded-bootstrap — sessie start ook bij brain-down"
```

---

## Task 9: Quest-limiet — begrensde promptgroei in de bootstrap

Quests zijn de enige onbegrensde promptcategorie die door normaal agent-gedrag hard groeit (skills, decisions, insights, lessons en sessions hebben k/LIMIT: 12, 8, 8, 6, 5). Twee andere categorieën zijn strikt genomen óók ongelimiteerd maar groeien niet vanzelf: protocollen (bootstrap.py r.122-138, handmatig gecureerde set van enkele stuks) en feedback (r.217-219, ratio-filter `reject_ratio >= 0.5` én minimaal 3 beoordelingen — zelf-dempend); die krijgen bewust géén LIMIT (YAGNI). `quest_upsert` valideert `status` niet (vrij tekstveld, tools.py r.412-452), dus de limiet moet op de LEESkant: LIMIT in de query (recentste eerst, `q.updated` wordt bij elke upsert gezet, `q.created` bij creatie) plus een steps-cap per quest in `render_bootstrap`.

**Bewust géén env-flag (afwijking van spec §5, gemotiveerd):** de limiet is een leeskant-cap, puur additief in de query en render; terugdraaien = de constantes `QUEST_LIMIT`/`QUEST_STEPS_LIMIT` verhogen (één regel, geen migratie, geen datamutatie). Een runtime-schakelaar zou een tweede codepad in de bootstrap betekenen voor een wijziging die alleen de prompt-wéérgave raakt — zelfde afweging als bij de embed-guard in Task 7.

**Bewuste beperking (geaccepteerd bij review):** dit begrenst de LEESkant (prompt-weergave), niet de schrijfkant — het brein kan open quests blijven accumuleren. De spec-formulering "begrens geheugen-groei per categorie" wordt dus op het voelbare punt (promptgroei) gedekt, niet op datavolume; een schrijfkant-cap is lastig omdat `quest_upsert` status niet valideert. Mogelijke follow-up buiten dit plan: `check_brain_health` laten tellen hoeveel open/active quests er zijn en boven een drempel (bv. 25) een inbox-melding sturen (leeswerk, past in het Task 5-patroon — geen datamutatie).

**Files:**
- Modify: `src/span/memory/bootstrap.py` (constantes bovenin; query r.140-149; render r.291-297)
- Test: `tests/test_bootstrap_limits.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bootstrap_limits.py
"""A4 — quest-limiet: quests waren de enige hard groeiende onbegrensde categorie."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.memory.bootstrap as bootstrap


def test_quests_query_heeft_limit_en_recentste_eerst(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def run(query, **params):
        if "MATCH (i:Identity)" in query:
            return [{"name": "LO", "philosophy": "p", "origin": "o",
                     "owner": "Bas Spaan", "voice": None}]
        captured.append((query, params))
        return []

    brain = MagicMock()
    brain.run.side_effect = run
    brain.vector_search.return_value = []
    frag = MagicMock()
    frag.recent.return_value = []
    frag.search.return_value = []
    # feedback_summary wordt lazy geïmporteerd -> patchen op de bronmodule
    monkeypatch.setattr("span.jarvis.feedback.feedback_summary", lambda b: [])

    ctx = bootstrap.load_bootstrap(brain, frag, first_message=None)

    assert ctx.quests == []
    quest_queries = [(q, p) for q, p in captured if "MATCH (q:Quest)" in q]
    assert len(quest_queries) == 1
    query, params = quest_queries[0]
    assert "LIMIT $quest_limit" in query
    assert params["quest_limit"] == bootstrap.QUEST_LIMIT
    assert "ORDER BY coalesce(q.updated, q.created) DESC" in query


def test_render_bootstrap_capt_quest_steps():
    ident = {"name": "LO", "owner": "Bas Spaan", "philosophy": "p",
             "origin": "o", "voice": None}
    steps = [{"order": i, "body": f"stap {i}", "status": "open"}
             for i in range(1, 15)]  # 14 stappen
    ctx = bootstrap.BootstrapContext(
        identity=ident, protocols=[],
        quests=[{"id": "quest-1", "title": "Grote quest", "status": "open",
                 "steps": steps}],
        decisions=[], anti_patterns=[], soul=[], skills=[],
    )
    out = bootstrap.render_bootstrap(ctx)
    getoond = [regel for regel in out.splitlines()
               if regel.strip().startswith("- step-")]
    assert len(getoond) == bootstrap.QUEST_STEPS_LIMIT
    verborgen = 14 - bootstrap.QUEST_STEPS_LIMIT
    assert f"+{verborgen} stappen verborgen" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap_limits.py -v`
Expected: FAIL with `AttributeError: module 'span.memory.bootstrap' has no attribute 'QUEST_LIMIT'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/memory/bootstrap.py`, boven `start_session` (na de `BootstrapContext`-dataclass):

```python
# A4 quest-limiet: quests waren de enige onbegrensde promptcategorie die door
# normaal agent-gedrag hard groeit (protocollen en feedback zijn ook ongecapt
# maar gecureerd/zelf-dempend). De limiet zit op de LEESkant (quest_upsert
# valideert status niet en de schrijfkant is vrij): recentst-bijgewerkte
# quests eerst, en per quest een cap op de stappen.
QUEST_LIMIT = 6
QUEST_STEPS_LIMIT = 8
```

Vervang de quests-query (r.140-149):

```python
    quests = brain.run(
        """
        MATCH (q:Quest) WHERE q.status IN ['open', 'active']
        OPTIONAL MATCH (q)-[:HAS_STEP]->(st:QuestStep)
        WITH q, st ORDER BY st.order
        RETURN q.id AS id, q.title AS title, q.status AS status,
               collect({order: st.order, body: st.body, status: st.status}) AS steps
        ORDER BY q.id
        """
    )
```

door:

```python
    quests = brain.run(
        """
        MATCH (q:Quest) WHERE q.status IN ['open', 'active']
        WITH q ORDER BY coalesce(q.updated, q.created) DESC LIMIT $quest_limit
        OPTIONAL MATCH (q)-[:HAS_STEP]->(st:QuestStep)
        WITH q, st ORDER BY st.order
        RETURN q.id AS id, q.title AS title, q.status AS status,
               collect({order: st.order, body: st.body, status: st.status}) AS steps
        ORDER BY q.id
        """,
        quest_limit=QUEST_LIMIT,
    )
```

Vervang in `render_bootstrap` het quests-blok (r.291-297):

```python
    if ctx.quests:
        lines.append("\n# Actieve quests")
        for q in ctx.quests:
            lines.append(f"- {q['id']} · {q['title']} ({q['status']})")
            for st in q["steps"]:
                if st.get("body"):
                    lines.append(f"    - step-{st['order']}: {st['body']} [{st.get('status', 'open')}]")
```

door:

```python
    if ctx.quests:
        lines.append("\n# Actieve quests")
        for q in ctx.quests:
            lines.append(f"- {q['id']} · {q['title']} ({q['status']})")
            steps = [st for st in q["steps"] if st.get("body")]
            for st in steps[:QUEST_STEPS_LIMIT]:
                lines.append(f"    - step-{st['order']}: {st['body']} [{st.get('status', 'open')}]")
            if len(steps) > QUEST_STEPS_LIMIT:
                lines.append(f"    - … (+{len(steps) - QUEST_STEPS_LIMIT} "
                             "stappen verborgen — zie quest via brain_search)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap_limits.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run de degraded-tests opnieuw (zelfde bestand gewijzigd)**

Run: `python -m pytest tests/test_degraded.py tests/test_bootstrap_limits.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/span/memory/bootstrap.py tests/test_bootstrap_limits.py
git commit -m "feat(bootstrap): A4 quest-limiet — begrensde promptgroei per categorie"
```

---

## Task 10: Recall-meetpunt — eval_retrieval.py repareren

Twee gebreken (geverifieerd): (a) de `--hybrid`-vlag geeft `hybrid=True` door aan `fs.search`/`search_formal` (r.49-53) maar die signaturen (fragments.py r.187-188, r.256-257) kennen die parameter niet → TypeError zodra je de vlag gebruikt; (b) `eval_retrieval_set.json` ontbreekt in de repo (de gouden set leeft op de server; de uitgebreide set is A7) → nu een kale `FileNotFoundError`. Fix: vlag weg (YAGNI — er ís geen hybrid search), vriendelijke fout, en de recall@5-uitkomst als `telemetry.record("recall", ...)` zodat hij in dezelfde JSONL-meetlat zit als de A1-latencies (ontwerp A7 wil dat expliciet).

**Files:**
- Modify: `scripts/eval_retrieval.py` (volledig herschreven, zie Step 3)
- Test: `tests/test_eval_retrieval.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_eval_retrieval.py -v`
Expected: FAIL with `AttributeError: module 'eval_retrieval' has no attribute 'build_parser'`

- [ ] **Step 3: Write minimal implementation**

Vervang `scripts/eval_retrieval.py` volledig door:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_eval_retrieval.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Full test sweep (alle A4-tests + bestaande suite)**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/eval_retrieval.py tests/test_eval_retrieval.py
git commit -m "fix(eval): A4 recall-meetpunt — kapotte --hybrid weg, uitkomst naar telemetrie"
```

---

## Task 11: Deploy + meten op z390 — de A4-meetlat vastleggen

**Geen TDD-code — dit is een deploy/meet-taak op z390** (Ubuntu, Docker, container `span-agent`, Neo4j-container met brein `span-brain`). A4's meetpunt is pas af als er cijfers zijn: brain-latency-baseline + recall-baseline, want die beantwoorden de B4-poortvraag.

**Files:**
- Modify: `docker-compose.yml` (op z390, buiten de repo — Step 1; zelfde structuur als de repo-compose)
- Modify: `docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md` (Step 6)

- [ ] **Step 1: Telemetrie-pad rebuild-bestendig maken**

`data/telemetry.jsonl` (PROJECT_ROOT/data = `/app/data` in de container) is NIET gemount in docker-compose.yml — metingen overleven een rebuild niet. Gekozen route: een named volume voor `/app/data`, zelfde patroon als `span-msal`/`span-models` (het alternatief — `SPAN_TELEMETRY_FILE` naar een bestaand gemount pad wijzen — vermengt telemetrie met documenten en valt af). In de compose op z390, onder de `span`-service:

```yaml
  span:
    volumes:
      - span-msal:/root/.span     # bestaand
      - span-models:/root/.cache  # bestaand
      - ./documents:/data/documents  # bestaand
      - span-data:/app/data       # A4: telemetry.jsonl overleeft rebuilds
```

En onderaan in het top-level `volumes:`-blok:

```yaml
volumes:
  span-data:
```

Daarna: `docker compose up -d span` (herstart volstaat, geen rebuild nodig). Geen secrets in de compose — dit zijn alleen volume-regels.

- [ ] **Step 2: Deploy en verifieer de schema-run**

Op z390 (`ssh z390`, repo in `~/nova`) het volledige deploy-recept (zelfde patroon als A1/A2):

```bash
cd ~/nova
export MSYS_NO_PATHCONV=1                      # alleen nodig bij Windows-git-bash
cp docker-compose.yml /tmp/compose.z390.bak    # server-compose bewaren (lokale afwijkingen, incl. het span-data-volume uit Step 1)
git fetch origin
git reset --hard origin/master                 # of origin/<a4-branch> vóór de merge
cp /tmp/compose.z390.bak docker-compose.yml    # compose terugzetten
chmod 600 .env
docker compose up -d --build span
sleep 25
curl -s https://nova.famspaan.nl/readyz        # verwacht: ready
```

Controleer daarna:

Run: `docker logs span-agent 2>&1 | grep -E "range-indexen|entity_name"`
Expected: `8 range-indexen (A4 geheugen-onderhoud)` en `entity_name-constraint gezet (N duplicaten samengevoegd)` (of `al aanwezig` bij een tweede start).

- [ ] **Step 3: Verifieer index-gezondheid en het brain-segment**

Run: `curl -s -H "Cookie: <owner-cookie>" https://nova.famspaan.nl/api/brain/health | jq`
Expected: `"ok": true`, lege `missing`/`not_online`, `latency_ms` enkele ms.

Run (na wat gebruik): `curl -s -H "Cookie: <owner-cookie>" https://nova.famspaan.nl/api/telemetry | jq '.segments.brain'`
Expected: `count`/`p50`/`p95`/`max` verschijnen. Noteer p50/p95 — dit is de brain-latency-baseline.

- [ ] **Step 4: Degraded-drill (gecontroleerd, buiten werktijd)**

Stop de Neo4j-container tijdelijk (`docker stop <neo4j-container>`). Stuur een chatbeurt via de HUD: LO antwoordt gewoon (zonder geheugen), serverlog toont `[degraded] embedding onbereikbaar — beurt zonder geheugen-RAG`. Start een nieuwe sessie: die begint met de degraded-melding in de context i.p.v. een 500. Start Neo4j weer (`docker start ...`) en controleer dat `/readyz` weer `ready` geeft en een nieuwe beurt weer RAG-hits toont.

- [ ] **Step 5: Recall-baseline draaien**

Run: `docker exec span-agent python /app/eval_retrieval.py`
Expected: recall@1/3/5/10-tabel (de gouden set staat op de server). Controleer dat er een `recall`-regel in de telemetrie-JSONL bijkwam. Ontbreekt de set op de server: noteer dat expliciet — de baseline schuift dan naar A7 (eval-set v1), niet stilletjes overslaan.

- [ ] **Step 6: Schrijf de meetlat-conclusie in de spec**

Voeg aan `docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md` onder het A4-blok een korte tabel toe: brain-latency p50/p95 (per op), recall@5-baseline, en één zin voor de B4-poort: zijn er recall-fouten die aan tijd-blindheid te wijten zijn (verwachte node bestond, maar verkeerde/oude versie kwam boven)? Commit:

```bash
git add docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md
git commit -m "docs(health): A4 baseline brain-latency + recall als B4-poortmeetlat"
```

---

## Afhankelijkheden & volgorde

- **A1-telemetrie moet af zijn en deze branch moet erop bouwen.** A4 gebruikt `span.telemetry.record/aggregate` (Tasks 5-8, 10) en raakt `agent.py`/`routes.py` precies rond A1's regels (r.325-326, r.480-483, r.551-556, r.950-963). Bouwen op master-zonder-A1 geeft merge-conflicten én ontbrekende imports.
- **Taakvolgorde binnen dit plan:** Task 1 → 2 (schema), Task 3 → 4 → 5 (health bouwt op `RANGE_INDEXES` uit Task 1), Task 6 (brain-meetpunt, onafhankelijk), Task 7 → 8 (degraded; Task 8 hergebruikt het testbestand van 7), Task 9 (quest-limiet), Task 10 (eval), Task 11 laatst (deploy + meten).
- **Niet aanraken:** `src/span/telemetry.py` en `tests/test_telemetry.py` (A1-sessie); `tests/test_observability.py` alleen draaien, niet wijzigen (nieuwe tests staan in eigen bestanden).
- **Niets uit fase B/C bouwen:** geen bi-temporeel schema, geen tijd-bewuste recall, geen TEI-embeddings (dat is B4, en juist het A4-meetpunt beslist óf dat er komt). Geen retries/durable execution (A3/B3).
- **A7 levert de echte gouden set.** Task 10 maakt het script alleen draaibaar en meetbaar; de uitgebreide eval-set is expliciet A7-scope.

## Handmatige verificatie (wat Bas op prod moet zien)

1. **Serverstart:** `docker logs span-agent` toont de nieuwe init_schema-regels (`8 range-indexen`, `entity_name-constraint ...`) en de server start net zo snel als voorheen.
2. **Neo4j:** `SHOW INDEXES` in de Neo4j-browser toont `mf_created`, `mf_type`, `session_started`, `quest_status`, `quest_created`, `insight_created`, `mistake_created`, `inboxitem_item_id` — allemaal state ONLINE.
3. **Dashboard:** `GET /api/brain/health` (owner) geeft `ok: true` + `latency_ms`; `GET /api/telemetry` toont een `brain`-segment met p50/p95 en na een eval-run een `recall`-segment.
4. **Degraded-drill:** met Neo4j gestopt blijft LO antwoorden (trager, zonder geheugen, eerlijke melding in de serverlog); een nieuwe sessie start met de degraded-context; na `docker start` herstelt alles zonder herstart van span.
5. **Prompt-omvang:** bij veel open quests toont de bootstrap er maximaal 6 met elk maximaal 8 stappen plus een `… (+N stappen verborgen)`-regel.
6. **De nacht erna:** scheduler-log toont `brainhealth: ok=True latency=...ms` rond 03:45; GEEN inbox-melding (die komt alleen bij een index-probleem).
7. **Meetlat:** de spec bevat de baseline-tabel (brain p50/p95 per op, recall@5) met de B4-poortconclusie.

---

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (A4-blok, r.69-73 van het ontwerp):**
- Ontbrekende/suboptimale indexen herstellen → Task 1 (8 range-indexen met bewijsplek per index) + Task 2 (Entity.name-constraint incl. dedup-migratie); bestaande HNSW/vector-indexen gecontroleerd: die bestaan al (schema.py r.29-65) en worden bewaakt via Task 3 + de nachttaak van Task 5. **Bewuste beperking (geaccepteerd bij review):** een vector-index in state FAILED wordt gedetecteerd en gemeld (inbox, urgency high, met remediatie-instructie), maar niet automatisch hersteld — herstel blijft handmatig (`DROP INDEX <naam>` + span-herstart, want `IF NOT EXISTS` slaat een bestaande FAILED-index over). Een auto-remediate achter een flag (DROP + init_schema-herrun) is een mogelijke follow-up buiten dit plan; voor een homelab is detectie + handmatige stap voldoende. ✓
- Index-gezondheid controleren → Task 3 (module) + Task 4 (endpoint) + Task 5 (nachttaak met inbox-melding). ✓
- Quest-limiet (begrens geheugen-groei per categorie) → Task 9; quests bevestigd als enige onbegrensde categorie die door agent-gedrag hard groeit (protocollen/feedback zijn ook ongecapt maar gecureerd/zelf-dempend); limiet op de leeskant omdat de schrijfkant vrij is — de schrijfkant blijft bewust onbegrensd (geaccepteerd bij review; drempel-signaal in de nachttaak is een benoemde follow-up, zie Task 9). ✓
- Health-check + degraded-mode (audit: embed in turn() zonder try/except) → Task 7 (exact de audit-regel, r.334) + Task 8 (begin()/bootstrap, achter `SPAN_DEGRADED_MODE`). ✓
- Meetpunt recall + brain-latency (`tel.record('brain', ...)`) → Task 6 (BrainDB-wrapper, segment `brain`) + Task 10 (recall naar dezelfde JSONL) + Task 11 (baseline in de spec = de B4-poortinput). ✓
- Niets uit fase B/C gebouwd. ✓

**Harde regels:** een telemetrie/feature-fout breekt nooit een gesprek — `telemetry.record` slikt zelf alles (A1), de `_timed`-wrapper re-raise't alleen de échte query-fout, de embed-guard en bootstrap-guard vangen en loggen; flags `SPAN_BRAIN_TELEMETRY`/`SPAN_DEGRADED_MODE` consequent genoemd, default aan (de spec vraagt resilience, geen opt-in); geen secrets in code of plan (alleen env-var-namen). **Veiligheidsklep §5 — twee gemotiveerde afwijkingen, expliciet geaccepteerd bij de adversariële review van 2026-07-13:** (1) de embed-guard in `turn()` (Task 7) is ongevlagd omdat het een crash-fix uit de audit is, geen feature; (2) de quest-limiet (Task 9) is een constante i.p.v. een env-flag omdat het een leeskant-cap is die met één regel terug te draaien is. Tasks 1-2 (indexen/constraint) zijn conform de geest van §5: de spec noemt indexen zelf als omkeerbaar en de constraint is fail-soft. Muterende integraties: n.v.t. in dit plan — de enige "integratie" is de interne AgentInbox en die is in tests gemockt (`inbox = MagicMock()`).

**Placeholder-scan:** geen TBD/TODO; elke code-stap toont volledige, echte code; regelnummers verwijzen naar de werkelijke stand op branch `a1-telemetrie`.

**Type-consistentie:** `index_health(brain) -> dict` / `brain_latency_ms(brain) -> float` / `check_brain_health(brain, inbox) -> dict` identiek gebruikt in Tasks 3/4/5; `telemetry.record(seg, ms, meta)` conform de A1-API; `RANGE_INDEXES: list[tuple[str, str]]` gedeeld tussen schema (Task 1) en health (Task 3); `embedding: list[float] | None` consistent in turn()/search/specs_for.

**Bestandsgrootte-regel:** nieuwe logica in `src/span/db/health.py` (nieuwe module); agent.py/routes.py/daily.py krijgen alleen dunne haakjes (respectievelijk ~25, ~15 en ~10 regels).

**Open punt (bewust):** de gouden eval-set staat niet in de repo — Task 10 documenteert dat en faalt vriendelijk; Task 11 Step 5 benoemt expliciet dat de recall-baseline naar A7 schuift als de set op de server ontbreekt.
