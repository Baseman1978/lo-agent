# A3 — Taak-vangnet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Falende taken worden opgevangen (retry op transiente fouten), gemeld (eerlijke uitkomsten, geen verzonnen succes), bewaakt (cron-toets op dagafsluiting/consolidatie/weekreview) en afgemeld (Telegram-push bij klaar/definitief mislukt) — met telemetrie-meetpunten die de B3-inrichting (DBOS) sturen.

**Architecture:** Drie kleine nieuwe modules houden de mastodont-bestanden slank: `span.orchestrator.toolretry` (transient-classifier + backoff, toegepast ín `ToolBox.dispatch` alleen om de handler heen en alleen voor read-tools), `span.jarvis.watchdog` (pure cron-toets op de `c.last_<key>`-stempels van de Config-node, aangeroepen uit de bestaande scheduler-loop) en `span.jarvis.task_push` (on_done-callback voor de TaskManager). Eerlijkheid komt van één klein structured signaal `SpanAgent.last_turn_ok`, geconsumeerd door crons en task-runners. Meetpunten gaan als eigen telemetrie-segmenten (`tool_retry`, `task`, `task_long`, `task_interrupted`, `cron_missed`) mee in het bestaande A1-kanaal, zodat `GET /api/telemetry` ze zonder wijziging toont. Alles is best-effort: een vangnet-fout mag nooit een beurt, taak of scheduler-tick breken.

**Tech Stack:** Python 3, FastAPI, pytest, Neo4j (Config-node), Telegram Bot API (bestaande bridge). Geen nieuwe dependencies, geen nieuwe service. Feature-flags: `SPAN_TOOL_RETRY`, `SPAN_TASK_PUSH`, `SPAN_CRON_WATCHDOG`, `SPAN_HONEST_OUTCOMES` (alle vier env-vars, default **aan**, `off/0/false/no` = uit — de spec eist nergens default-uit en het vangnet ís de feature; elke flag maakt het oude gedrag met één env-var terughaalbaar; `SPAN_HONEST_OUTCOMES` dekt de eerlijke-uitkomst-consumenten in crons/task_runners, zodat óók dit deel van fase A per veiligheidsklep §5 omkeerbaar is zonder redeploy).

---

## File Structure

- **Create** `src/span/orchestrator/toolretry.py` — transient-classifier `is_transient(exc)` + `call_with_retry(fn)` met backoff + flag `SPAN_TOOL_RETRY`.
- **Create** `src/span/jarvis/watchdog.py` — cron-toets: `expected_date`, `check_missed_runs(brain, now)` (puur), `watchdog_tick(state)` (meldend, flag `SPAN_CRON_WATCHDOG`).
- **Create** `src/span/jarvis/task_push.py` — `should_push(item)` + `make_task_push(state)` (Telegram/inbox-melding, flag `SPAN_TASK_PUSH`).
- **Create** `tests/test_taakvangnet.py` — alle A3-tests (retry, dispatch, eerlijke uitkomsten, TaskManager-callback, push, watchdog).
- **Modify** `src/span/orchestrator/tools.py` — `ToolBox.dispatch` (r.309-315): retry om de handler-aanroep, alleen `rw=="read"`, onder de guard/approval.
- **Modify** `src/span/orchestrator/agent.py` — `turn()`: `self.last_turn_ok` op r.406 (init) en de foutpaden r.417/443/491. **LET OP: A1-territorium — dit plan bouwt op de gemergde `a1-telemetrie`.**
- **Modify** `src/span/jarvis/crons.py` — `_execute` (r.169-176): gefaalde beurt → `"Uitvoering mislukt: …"`, achter flag `SPAN_HONEST_OUTCOMES`.
- **Modify** `src/span/jarvis/task_runners.py` — module-level helper `honest_outcomes_enabled()` (flag `SPAN_HONEST_OUTCOMES`); `task_runner` (r.68-73) en `team_runner`: gefaalde beurt → exceptie/eerlijke deelresultaten.
- **Modify** `src/span/jarvis/tasks.py` — `TaskManager.__init__` (r.31-32): `on_done`-parameter; `_run` (r.147-166): `_finish` met telemetrie + callback; `_load` (r.93-94): `task_interrupted`-telemetrie voor taken die een herstart niet overleefden.
- **Modify** `src/span/server/app.py` — wiring `on_done=make_task_push(_state)` (r.142-146).
- **Modify** `src/span/jarvis/daily.py` — `daily_scheduler`-loop (r.553-627): watchdog-tick per half uur.
- **Test** `tests/test_taakvangnet.py`; regressie via `tests/test_jarvis.py`, `tests/test_skills_tasks.py`, `tests/test_http_retry.py`.

**NIET aanraken:** `src/span/telemetry.py` en `tests/test_telemetry.py` (A1-sessie) — alleen importeren. Niets bouwen uit fase B/C (geen DBOS, geen idempotency-keys, geen WhatsApp).

---

### Task 1: Retry-kern — transient-classifier + backoff

**Files:**
- Create: `src/span/orchestrator/toolretry.py`
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_taakvangnet.py
"""A3 — taak-vangnet: retries, eerlijke uitkomsten, cron-toets, taak-push."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

import span.orchestrator.toolretry as tr


class TestTransientClassifier:
    """Alleen transiente fouten (429/timeout/5xx/verbinding) zijn retrybaar."""

    def test_transiente_types(self):
        assert tr.is_transient(requests.ConnectionError("verbinding weg"))
        assert tr.is_transient(requests.Timeout("read timed out"))
        assert tr.is_transient(TimeoutError("timed out"))
        assert tr.is_transient(ConnectionError("reset by peer"))

    def test_http_statuscodes(self):
        resp = requests.Response()
        resp.status_code = 503
        assert tr.is_transient(requests.HTTPError(response=resp))
        resp404 = requests.Response()
        resp404.status_code = 404
        assert not tr.is_transient(requests.HTTPError(response=resp404))

    def test_tekst_markers_en_permanente_fouten(self):
        assert tr.is_transient(RuntimeError("HTTP 429 too many requests"))
        assert tr.is_transient(RuntimeError("connection refused door proxy"))
        assert not tr.is_transient(ValueError("verkeerd argument"))
        assert not tr.is_transient(KeyError("ontbrekende sleutel"))


class TestCallWithRetry:
    def test_transient_wordt_herhaald_tot_succes(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("even weg")
            return "ok"

        result, retries = tr.call_with_retry(flaky)
        assert result == "ok" and retries == 2 and calls["n"] == 3

    def test_permanente_fout_gooit_direct_door(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("blijvend kapot")

        with pytest.raises(ValueError):
            tr.call_with_retry(broken)
        assert calls["n"] == 1

    def test_cap_op_max_retries(self, monkeypatch):
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        calls = {"n": 0}

        def always_down():
            calls["n"] += 1
            raise requests.Timeout("blijft traag")

        with pytest.raises(requests.Timeout):
            tr.call_with_retry(always_down)
        assert calls["n"] == 1 + tr.MAX_RETRIES

    def test_flag_schakelt(self, monkeypatch):
        monkeypatch.setenv("SPAN_TOOL_RETRY", "off")
        assert not tr.retry_enabled()
        monkeypatch.setenv("SPAN_TOOL_RETRY", "on")
        assert tr.retry_enabled()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestCallWithRetry -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.orchestrator.toolretry'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/orchestrator/toolretry.py
"""A3 — taak-vangnet: transient-classifier + retry met backoff voor tool-calls.

Alleen transiente fouten (429/timeout/5xx/verbindingsproblemen) komen in
aanmerking; de aanroeper (ToolBox.dispatch) beslist bovendien dat alleen
read-tools herhaald worden — een muterende tool wordt NOOIT blind opnieuw
gedraaid. Bewust klein en kort gehouden: de integraties
(span.integrations.http) doen zelf al 3 HTTP-pogingen met backoff; deze laag
vangt alleen wat daar doorheen lekt plus niet-HTTP-transients (bv. een
haperende Neo4j-verbinding), en blokkeert een worker-thread hoogstens ~3s.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

MAX_RETRIES = 2   # bovenop de eerste poging; de HTTP-laag retryt zelf al 3x
BASE_WAIT = 1.0   # backoff 1s, 2s — kort: dit draait in een beurt/worker-thread

# tekst-markers voor exceptions die als string binnenkomen (fouten van diep
# uit een integratie); bewust smal om permanente fouten nooit te herhalen
_TRANSIENT_MARKERS = (
    "429", "502", "503", "504", "timeout", "timed out",
    "connection reset", "connection refused", "connection aborted",
    "temporarily unavailable",
)


def retry_enabled() -> bool:
    """Feature-flag SPAN_TOOL_RETRY (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_TOOL_RETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def is_transient(exc: BaseException) -> bool:
    """Alleen fouten die zo weer weg kunnen zijn: throttle (429), gateway
    (502/503/504), timeout of een gevallen verbinding."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code in {429, 502, 503, 504}
    except Exception:
        pass
    name = type(exc).__name__.lower()
    if "serviceunavailable" in name or "transienterror" in name:  # neo4j-driver
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def call_with_retry(fn: Callable[[], Any],
                    max_retries: int = MAX_RETRIES,
                    base_wait: float = BASE_WAIT) -> tuple[Any, int]:
    """Voer fn uit; herhaal ALLEEN bij een transiente fout, met backoff.
    Geeft (resultaat, aantal_retries) terug. Een niet-transiente fout of de
    laatste mislukte poging gooit gewoon door — de aanroeper vertaalt dat
    (zoals nu al) naar een tool-error voor het model."""
    retries = 0
    while True:
        try:
            return fn(), retries
        except Exception as exc:
            if retries >= max_retries or not is_transient(exc):
                raise
            time.sleep(base_wait * (2 ** retries))
            retries += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestTransientClassifier tests/test_taakvangnet.py::TestCallWithRetry -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/orchestrator/toolretry.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): A3 transient-classifier + retry met backoff"
```

---

### Task 2: Retry in ToolBox.dispatch — alleen read-tools, onder de guard

**Files:**
- Modify: `src/span/orchestrator/tools.py` (dispatch, r.309-315)
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_taakvangnet.py
class TestDispatchRetry:
    """Retry zit ÓM de handler, ONDER de guard: approval/inbox loopt nooit dubbel."""

    def _box(self, monkeypatch):
        import span.safety.guard as guard
        from span.orchestrator.tools import ToolBox
        # guard doorlaten: we testen hier het retry-pad, niet de veiligheidslaag
        monkeypatch.setattr(guard, "assess_tool",
                            lambda *a, **k: {"decision": "allow", "reason": "",
                                             "tier": "low"})
        box = ToolBox.__new__(ToolBox)  # omzeil __init__: alleen dispatch-attrs
        box._used_tools = set()
        box._disabled = set()
        box._perms = {}
        box._autonomy = {}
        box._security = {}
        box._inbox = None
        return box

    def test_read_tool_retryt_transient_en_telt_mee(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "on")
        monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
        monkeypatch.setenv("SPAN_TOOL_RETRY", "on")
        monkeypatch.setattr(tr.time, "sleep", lambda *_a, **_k: None)
        box = self._box(monkeypatch)
        calls = {"n": 0}

        def flaky_search(query, k=5):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("even weg")
            return {"hits": []}

        box._tool_brain_search = flaky_search  # instance-attr schaduwt de methode
        out = box.dispatch("brain_search", {"query": "x"})
        assert calls["n"] == 2 and "hits" in out
        import span.telemetry as tel
        assert tel.aggregate()["segments"]["tool_retry"]["count"] == 1

    def test_write_tool_wordt_nooit_blind_herhaald(self, monkeypatch):
        monkeypatch.setenv("SPAN_TOOL_RETRY", "on")
        box = self._box(monkeypatch)
        calls = {"n": 0}

        def failing_send(**kwargs):
            calls["n"] += 1
            raise requests.ConnectionError("even weg")

        box._tool_o365_mail_send = failing_send
        out = box.dispatch("o365_mail_send",
                           {"to": "x@y.nl", "subject": "s", "body": "b"})
        assert calls["n"] == 1        # muterend: één poging, klaar
        assert "error" in out         # en de fout is eerlijk terug naar het model

    def test_flag_uit_is_oud_gedrag(self, monkeypatch):
        monkeypatch.setenv("SPAN_TOOL_RETRY", "off")
        box = self._box(monkeypatch)
        calls = {"n": 0}

        def flaky_search(query, k=5):
            calls["n"] += 1
            raise requests.ConnectionError("even weg")

        box._tool_brain_search = flaky_search
        out = box.dispatch("brain_search", {"query": "x"})
        assert calls["n"] == 1 and "error" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestDispatchRetry -v`
Expected: FAIL — `test_read_tool_retryt_transient_en_telt_mee` breekt op `assert calls["n"] == 2` (zonder retry blijft n op 1 en komt er een error-JSON terug).

- [ ] **Step 3: Write minimal implementation**

In `src/span/orchestrator/tools.py`, in `dispatch()` (r.309-315), replace:

```python
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return json.dumps({"error": f"Onbekende tool: {name}"})
            result = handler(**arguments)
```

with:

```python
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return json.dumps({"error": f"Onbekende tool: {name}"})
            # A3 taak-vangnet: alleen read-tools mogen bij een transiente fout
            # (429/timeout/5xx/verbinding) opnieuw — muterende tools nooit blind
            # herhalen. Bewust ONDER de guard/approval en alleen óm de handler,
            # zodat een retry nooit een tweede approval/inbox-item veroorzaakt.
            from span.orchestrator import toolretry
            if self._perm_key_rw(name)[1] == "read" and toolretry.retry_enabled():
                import time as _time
                _r0 = _time.perf_counter()
                result, _retries = toolretry.call_with_retry(
                    lambda: handler(**arguments))
                if _retries:
                    from span import telemetry
                    telemetry.record("tool_retry",
                                     (_time.perf_counter() - _r0) * 1000.0,
                                     {"name": name, "retries": _retries})
            else:
                result = handler(**arguments)
```

De bestaande `except`-takken op r.323-326 blijven onaangeraakt: een niet-transiente fout (of de laatste mislukte poging) gooit door en wordt daar — zoals nu — als error-JSON aan het model teruggegeven. De MCP-tak (`_dispatch_mcp`) blijft bewust zonder retry: daar zit de approval-queue ín het pad.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestDispatchRetry -v`
Expected: PASS (3 tests). Regressie: `python -m pytest tests/test_jarvis.py -q` → PASS (o.a. `test_meta_dekt_alle_specs` blijft groen; we voegen geen tool toe).

- [ ] **Step 5: Commit**

```bash
git add src/span/orchestrator/tools.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): retry op transiente fouten in dispatch, alleen read-tools"
```

---

### Task 3: Eerlijk uitkomst-signaal — SpanAgent.last_turn_ok

**Files:**
- Modify: `src/span/orchestrator/agent.py` (turn: r.406, r.416-419, r.434-446, r.490-494 — regelnummers op de a1-telemetrie-branch)
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_taakvangnet.py
def _agent_double(monkeypatch):
    """Minimale SpanAgent-double voor turn(): zelfde recept als test_telemetry."""
    from span.orchestrator.agent import SpanAgent

    agent = SpanAgent.__new__(SpanAgent)  # omzeil __init__
    tb = MagicMock()
    tb.specs_for.return_value = []
    tb.touched = []
    agent._toolbox = tb
    agent._messages = []
    agent._recorders = []

    frag = MagicMock()
    frag.embed.return_value = [0.0]
    frag.search.return_value = []
    frag.search_formal.return_value = []
    agent._fragments = frag

    settings = MagicMock()
    settings.model_main = "test-model"
    agent._settings = settings
    agent._security = {}
    # achtergrond-threads stubben: we testen alléén het uitkomst-signaal
    agent._record_turn = lambda *a, **k: None
    agent._persist_messages = lambda *a, **k: None
    agent._verify_active_quest = lambda *a, **k: None
    agent._write_trace = lambda *a, **k: None
    return agent


class TestEerlijkeUitkomst:
    def test_geslaagde_beurt_zet_signaal_true(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        agent = _agent_double(monkeypatch)
        msg = MagicMock(); msg.content = "prima"; msg.tool_calls = None
        llm = MagicMock(); llm.chat.return_value = msg
        agent._llm = llm
        out = agent.turn("hoi")
        assert "prima" in out
        assert agent.last_turn_ok is True

    def test_modelfout_zet_signaal_false(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        agent = _agent_double(monkeypatch)
        llm = MagicMock(); llm.chat.side_effect = RuntimeError("provider plat")
        agent._llm = llm
        out = agent.turn("hoi")
        assert "modelaanroep mislukte" in out
        assert agent.last_turn_ok is False

    def test_toollimiet_zet_signaal_false(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        agent = _agent_double(monkeypatch)
        tc = MagicMock()
        tc.id = "1"; tc.function.name = "brain_search"; tc.function.arguments = "{}"
        msg = MagicMock(); msg.content = ""; msg.tool_calls = [tc]
        llm = MagicMock(); llm.chat.return_value = msg  # blijft tools aanroepen
        agent._llm = llm
        agent._toolbox.dispatch.return_value = "{}"
        out = agent.turn("hoi", max_steps=2)
        assert "tool-limiet" in out
        assert agent.last_turn_ok is False
```

> Note: raakt `turn()` een attribuut dat deze double niet zet, voeg dan een extra stub-regel toe (zelfde recept als `test_turn_records_segments` in `tests/test_telemetry.py` r.53-105). Pas NOOIT `turn()` aan om de test te laten passen.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestEerlijkeUitkomst -v`
Expected: FAIL with `AttributeError: 'SpanAgent' object has no attribute 'last_turn_ok'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/orchestrator/agent.py`, in `turn()`, vier kleine ingrepen (andere regels dan de A1-timing, maar zelfde functie — zie Afhankelijkheden):

**(a)** Replace (r.406):

```python
        cancelled = False
```

with:

```python
        cancelled = False
        # A3 eerlijke uitkomst: klein structured signaal voor cron/taak-consumenten.
        # Blijft True zolang de beurt niet op een intern foutpad eindigt; een
        # barge-in/cancel telt bewust NIET als falen (dat was Bas zelf).
        self.last_turn_ok = True
```

**(b)** Replace (r.416-419, budget-pad):

```python
            except BudgetExceeded as exc:
                answer_parts.append(f"(veiligheidslimiet: {exc} — beurt gestopt)")
                self._messages.append({"role": "assistant", "content": answer_parts[-1]})
                break
```

with:

```python
            except BudgetExceeded as exc:
                self.last_turn_ok = False
                answer_parts.append(f"(veiligheidslimiet: {exc} — beurt gestopt)")
                self._messages.append({"role": "assistant", "content": answer_parts[-1]})
                break
```

**(c)** Replace (r.443-446, modelcall-pad — de logging-regels erboven blijven staan):

```python
                msg = f"(de modelaanroep mislukte: {type(exc).__name__}: {exc})"
                answer_parts.append(msg)
                self._messages.append({"role": "assistant", "content": msg})
                break
```

with:

```python
                self.last_turn_ok = False
                msg = f"(de modelaanroep mislukte: {type(exc).__name__}: {exc})"
                answer_parts.append(msg)
                self._messages.append({"role": "assistant", "content": msg})
                break
```

**(d)** Replace (r.490-494, for-else tool-limiet):

```python
        else:
            answer_parts.append(
                "(tool-limiet bereikt — beurt afgebroken; probeer de vraag kleiner te maken)"
            )
            self._messages.append({"role": "assistant", "content": answer_parts[-1]})
```

with:

```python
        else:
            self.last_turn_ok = False
            answer_parts.append(
                "(tool-limiet bereikt — beurt afgebroken; probeer de vraag kleiner te maken)"
            )
            self._messages.append({"role": "assistant", "content": answer_parts[-1]})
```

Het lege-antwoord-pad (r.508 `"(geen antwoord gegenereerd…)"`) blijft bewust True: het model gaf een leeg maar geldig antwoord — dat is geen infrastructuur-falen. Consumenten lezen het signaal altijd via `getattr(agent, "last_turn_ok", True)` zodat een agent die nooit `turn()` draaide niet als gefaald telt.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestEerlijkeUitkomst -v`
Expected: PASS (3 tests). Regressie: `python -m pytest tests/test_telemetry.py -q` → PASS (A1-instrumentatie onaangeroerd).

- [ ] **Step 5: Commit**

```bash
git add src/span/orchestrator/agent.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): eerlijk uitkomst-signaal last_turn_ok op SpanAgent.turn"
```

---

### Task 4: Eerlijke consumenten — cron-execute en taak-runners

**Files:**
- Modify: `src/span/jarvis/crons.py` (`_execute`, r.169-176)
- Modify: `src/span/jarvis/task_runners.py` (nieuwe module-level `honest_outcomes_enabled()` na r.23; `task_runner` r.68-73; `team_runner` r.126-145)
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_taakvangnet.py
class _FakeFailedAgent:
    """Agent-double waarvan de beurt intern faalde (last_turn_ok=False)."""
    last_turn_ok = False

    def __init__(self, *a, **k):
        pass

    def begin(self, *a, **k):
        return "boot"

    def turn(self, *a, **k):
        return "(de modelaanroep mislukte: RuntimeError: provider plat)"

    def flush_recording(self, *a, **k):
        return None


class TestEerlijkeConsumenten:
    def test_cron_execute_meldt_falen_expliciet(self, monkeypatch):
        import span.jarvis.crons as crons
        import span.memory.bootstrap as bootstrap
        import span.orchestrator.agent as agent_mod
        monkeypatch.setattr(agent_mod, "SpanAgent", _FakeFailedAgent)
        monkeypatch.setattr(bootstrap, "start_session", lambda brain: "sessie-1")
        state = {"settings": MagicMock(), "brain": MagicMock(), "llm": MagicMock()}
        out = crons._execute(state, "doe iets")
        # run_due_crons herkent dit prefix (r.110) -> retry/cap i.p.v. "⏰ Uitgevoerd"
        assert out.startswith("Uitvoering mislukt:")
        assert "modelaanroep mislukte" in out

    def test_task_runner_gooit_bij_gefaalde_beurt(self, monkeypatch):
        import span.memory.bootstrap as bootstrap
        import span.orchestrator.agent as agent_mod
        monkeypatch.setattr(agent_mod, "SpanAgent", _FakeFailedAgent)
        monkeypatch.setattr(bootstrap, "start_session", lambda brain: "sessie-1")
        from span.jarvis.task_runners import make_runners
        state = {"settings": MagicMock(), "brain": MagicMock(), "llm": MagicMock(),
                 "inbox": MagicMock(), "autonomy": {}}
        task_runner, _ = make_runners(state)
        with pytest.raises(RuntimeError):
            task_runner({"goal": "doe iets", "title": "t"},
                        lambda *a, **k: None, lambda: False, {})

    def test_team_runner_faalt_eerlijk_als_alle_deeltaken_falen(self, monkeypatch):
        import span.memory.bootstrap as bootstrap
        import span.orchestrator.agent as agent_mod
        monkeypatch.setattr(agent_mod, "SpanAgent", _FakeFailedAgent)
        monkeypatch.setattr(bootstrap, "start_session", lambda brain: "sessie-1")
        from span.jarvis.task_runners import make_runners
        plan = MagicMock()
        plan.content = '{"subtasks": [{"role": "zoeker", "goal": "zoek iets"}]}'
        llm = MagicMock(); llm.chat.return_value = plan
        settings = MagicMock(); settings.model_main = "test-model"
        state = {"settings": settings, "brain": MagicMock(), "llm": llm,
                 "inbox": MagicMock(), "autonomy": {}}
        _, team_runner = make_runners(state)
        with pytest.raises(RuntimeError):
            team_runner({"goal": "doe iets"},
                        lambda *a, **k: None, lambda: False, {})

    def test_flag_uit_geeft_oud_gedrag(self, monkeypatch):
        # kill switch SPAN_HONEST_OUTCOMES: consumenten negeren last_turn_ok
        monkeypatch.setenv("SPAN_HONEST_OUTCOMES", "off")
        from span.jarvis.task_runners import honest_outcomes_enabled
        assert not honest_outcomes_enabled()
        monkeypatch.setenv("SPAN_HONEST_OUTCOMES", "on")
        assert honest_outcomes_enabled()
        monkeypatch.setenv("SPAN_HONEST_OUTCOMES", "off")
        import span.jarvis.crons as crons
        import span.memory.bootstrap as bootstrap
        import span.orchestrator.agent as agent_mod
        monkeypatch.setattr(agent_mod, "SpanAgent", _FakeFailedAgent)
        monkeypatch.setattr(bootstrap, "start_session", lambda brain: "sessie-1")
        state = {"settings": MagicMock(), "brain": MagicMock(), "llm": MagicMock(),
                 "inbox": MagicMock(), "autonomy": {}}
        out = crons._execute(state, "doe iets")
        assert not out.startswith("Uitvoering mislukt:")  # oud gedrag terug
        from span.jarvis.task_runners import make_runners
        task_runner, _ = make_runners(state)
        result = task_runner({"goal": "doe iets", "title": "t"},
                             lambda *a, **k: None, lambda: False, {})
        assert "modelaanroep mislukte" in result  # geen exceptie -> status done
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestEerlijkeConsumenten -v`
Expected: FAIL — `_execute` geeft de foutzin als gewoon resultaat terug (geen prefix), en beide runners raisen niets. Bovendien FAIL op `test_flag_uit_geeft_oud_gedrag` met `ImportError` (helper `honest_outcomes_enabled` bestaat nog niet).

- [ ] **Step 3: Write minimal implementation**

**(a)** In `src/span/jarvis/task_runners.py`: voeg `import os` toe aan de imports (r.13, onder `from typing import Any, Callable`) en add module-level, direct na `TASK_LABELS` (r.23):

```python
def honest_outcomes_enabled() -> bool:
    """Feature-flag SPAN_HONEST_OUTCOMES (default aan; off/0/false/no/'' = uit).
    Uit = het oude gedrag: cron- en taak-consumenten negeren last_turn_ok.
    Veiligheidsklep §5 (omkeerbaarheid): ook een vals-negatief in last_turn_ok
    is dan met één env-var terug te draaien, zonder git-revert of redeploy."""
    val = os.environ.get("SPAN_HONEST_OUTCOMES", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}
```

(Zelfde patroon als `retry_enabled()`/`push_enabled()`/`watchdog_enabled()`. Eén gedeelde helper: crons.py importeert hem hieruit — task_runners importeert crons niet, dus geen import-cyclus. Het zétten van `self.last_turn_ok` in agent.py (Task 3) blijft bewust onvoorwaardelijk: een attribuut zetten heeft geen gedragseffect; alleen de consumenten staan achter de flag.)

**(b)** In `src/span/jarvis/crons.py`, `_execute` (r.169-176), replace:

```python
        agent.flush_recording(timeout=5)
        return answer
```

with:

```python
        agent.flush_recording(timeout=5)
        from span.jarvis.task_runners import honest_outcomes_enabled
        if honest_outcomes_enabled() and not getattr(agent, "last_turn_ok", True):
            # A3 eerlijke uitkomst: de beurt faalde intern (modelcall/budget/
            # tool-limiet). Zonder dit prefix zou run_due_crons "⏰ Uitgevoerd"
            # melden over een mislukking — falen vermomt zich dan als succes.
            # SPAN_HONEST_OUTCOMES=off = kill switch naar het oude gedrag.
            return f"Uitvoering mislukt: {answer}"
        return answer
```

**(c)** In `src/span/jarvis/task_runners.py`, `task_runner` (r.68-73), replace:

```python
        result = agent.turn(goal, on_tool=on_tool, should_cancel=should_cancel, max_steps=30)
        try:
            agent.flush_recording()
        except Exception:
            pass
        return result
```

with:

```python
        result = agent.turn(goal, on_tool=on_tool, should_cancel=should_cancel, max_steps=30)
        try:
            agent.flush_recording()
        except Exception:
            pass
        if honest_outcomes_enabled() and not getattr(agent, "last_turn_ok", True):
            # A3 eerlijke uitkomst: exceptie -> TaskManager._run zet status
            # "error" i.p.v. "done" met de foutzin als "resultaat"
            raise RuntimeError(f"achtergrondtaak-beurt faalde: {result}")
        return result
```

**(d)** In `src/span/jarvis/task_runners.py`, `team_runner` → `run_sub` (r.126-140), replace:

```python
            ans = agent.turn(f"[Deeltaak — rol: {st.get('role', 'uitvoerder')}] {st['goal']}",
                             should_cancel=should_cancel, max_steps=20)
            try:
                agent.flush_recording()
            except Exception:
                pass
            results[i] = {"role": st.get("role", ""), "result": ans}
```

with:

```python
            ans = agent.turn(f"[Deeltaak — rol: {st.get('role', 'uitvoerder')}] {st['goal']}",
                             should_cancel=should_cancel, max_steps=20)
            try:
                agent.flush_recording()
            except Exception:
                pass
            if honest_outcomes_enabled() and not getattr(agent, "last_turn_ok", True):
                # eerlijk deelresultaat: de synthese ziet dat dit deel faalde
                ans = "[DEELTAAK MISLUKT] " + (ans or "")
            results[i] = {"role": st.get("role", ""), "result": ans}
```

**(e)** In `team_runner`, direct after (r.144-145):

```python
        if should_cancel():
            return "(geannuleerd)"
```

add:

```python
        # A3 eerlijke uitkomst: als álle deeltaken faalden valt er niets samen
        # te vatten -> foutstatus i.p.v. een verzonnen synthese
        done_results = [r for r in results if r is not None]
        if done_results and all(str(r.get("result", "")).startswith("[DEELTAAK MISLUKT]")
                                for r in done_results):
            raise RuntimeError("teamtaak mislukt: alle deeltaken faalden")
```

(Dit blok hoeft zelf niet achter de flag: met `SPAN_HONEST_OUTCOMES=off` krijgt geen enkel deelresultaat de `[DEELTAAK MISLUKT]`-marker uit **(d)**, dus deze raise kan dan nooit vuren.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestEerlijkeConsumenten -v`
Expected: PASS (4 tests). Regressie: `python -m pytest tests/test_skills_tasks.py tests/test_jarvis.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/span/jarvis/crons.py src/span/jarvis/task_runners.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): cron-execute en taak-runners melden falen eerlijk"
```

---

### Task 5: TaskManager — on_done-callback + taak-telemetrie (task / task_long / task_interrupted)

**Files:**
- Modify: `src/span/jarvis/tasks.py` (`__init__` r.30-43; `_run` r.147-166; nieuwe `_finish`; `_load` r.93-94)
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_taakvangnet.py
class TestTaskManagerVangnet:
    def _wait(self, mgr, tid, timeout=5.0):
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            it = mgr.get(tid)
            if it and it["status"] not in ("queued", "running"):
                return it
            time.sleep(0.02)
        raise AssertionError("taak werd niet afgerond binnen de timeout")

    def test_on_done_krijgt_snapshot_en_task_telemetrie(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "on")
        monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
        from span.jarvis.tasks import TaskManager
        seen = []
        mgr = TaskManager(lambda task, sp, sc, ctx: "klaar!",
                          on_done=lambda item: seen.append(item))
        tid = mgr.submit("test-doel")
        it = self._wait(mgr, tid)
        assert it["status"] == "done"
        assert seen and seen[0]["id"] == tid and seen[0]["status"] == "done"
        import span.telemetry as tel
        assert tel.aggregate()["segments"]["task"]["count"] == 1

    def test_kapotte_callback_breekt_worker_niet(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        from span.jarvis.tasks import TaskManager

        def boom(item):
            raise RuntimeError("callback kapot")

        mgr = TaskManager(lambda task, sp, sc, ctx: "klaar!", on_done=boom)
        tid = mgr.submit("test-doel")
        it = self._wait(mgr, tid)
        assert it["status"] == "done"  # de callback-fout is geslikt

    def test_error_status_bereikt_callback(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        from span.jarvis.tasks import TaskManager

        def failing(task, sp, sc, ctx):
            raise RuntimeError("beurt faalde")

        seen = []
        mgr = TaskManager(failing, on_done=lambda item: seen.append(item))
        tid = mgr.submit("test-doel")
        it = self._wait(mgr, tid)
        assert it["status"] == "error" and "beurt faalde" in it["result"]
        assert seen and seen[0]["status"] == "error"

    def test_interrupted_bij_opstart_telt_mee(self, tmp_path, monkeypatch):
        # spec-meetpunt A3 (r.67): "hoeveel moesten herstart overleven" —
        # interrupted wordt in _load gezet, buiten _run, dus _finish ziet
        # deze taken nooit; dit segment is het derde B3-cijfer.
        monkeypatch.setenv("SPAN_TELEMETRY", "on")
        monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
        from span.jarvis.tasks import TaskManager
        ts = datetime.now(timezone.utc).isoformat()
        brain = MagicMock()
        brain.run.return_value = [  # stond nog op 'running' bij de herstart
            {"id": 1, "goal": "doel", "title": "t", "status": "running",
             "progress": "", "result": "", "owner": "", "team": False,
             "created": ts, "updated": ts}]
        mgr = TaskManager(lambda task, sp, sc, ctx: "klaar!", brain=brain)
        assert mgr.get(1)["status"] == "interrupted"
        import span.telemetry as tel
        assert tel.aggregate()["segments"]["task_interrupted"]["count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestTaskManagerVangnet -v`
Expected: FAIL — de eerste drie tests met `TypeError: TaskManager.__init__() got an unexpected keyword argument 'on_done'`; `test_interrupted_bij_opstart_telt_mee` met `KeyError: 'task_interrupted'` (segment bestaat nog niet).

- [ ] **Step 3: Write minimal implementation**

In `src/span/jarvis/tasks.py`:

**(a)** Add module-constant boven de klasse (na `_DONE`, r.23):

```python
LONG_TASK_SECS = 120.0  # A3: 'langlopend' = meer dan enkele minuten (meetpunt + push)
```

**(b)** Replace de `__init__`-kop (r.31-35):

```python
    def __init__(self, runner: Callable[..., str], brain: Any = None,
                 max_workers: int = 2, team_runner: Callable[..., str] | None = None) -> None:
        # runner(task, set_progress, should_cancel, ctx) -> resultaat-string
        self._runner = runner
        self._team_runner = team_runner  # coördinator + parallelle sub-agents
```

with:

```python
    def __init__(self, runner: Callable[..., str], brain: Any = None,
                 max_workers: int = 2, team_runner: Callable[..., str] | None = None,
                 on_done: Callable[[dict[str, Any]], None] | None = None) -> None:
        # runner(task, set_progress, should_cancel, ctx) -> resultaat-string
        self._runner = runner
        self._team_runner = team_runner  # coördinator + parallelle sub-agents
        self._on_done = on_done  # A3: callback met item-snapshot na afronding
```

**(c)** In `_run` (r.147-166), na de bestaande `except`-tak, replace:

```python
        except Exception as exc:  # nooit de worker laten crashen
            self._update(tid, persist=True, status="error",
                         result=f"{type(exc).__name__}: {exc}", progress="fout")
```

with:

```python
        except Exception as exc:  # nooit de worker laten crashen
            self._update(tid, persist=True, status="error",
                         result=f"{type(exc).__name__}: {exc}", progress="fout")
        self._finish(tid)

    def _finish(self, tid: int) -> None:
        """A3 best-effort afronding: taak-telemetrie + on_done-callback.
        Mag de worker-thread nooit breken."""
        snap = self.get(tid)
        if snap is None:
            return
        try:
            from span import telemetry
            a = datetime.fromisoformat(snap.get("created") or _now())
            b = datetime.fromisoformat(snap.get("updated") or _now())
            dur_ms = max(0.0, (b - a).total_seconds() * 1000.0)
            meta = {"outcome": snap["status"], "team": bool(snap.get("team"))}
            telemetry.record("task", dur_ms, meta)
            if dur_ms >= LONG_TASK_SECS * 1000.0:  # meetpunt: langlopers apart telbaar
                telemetry.record("task_long", dur_ms, meta)
        except Exception:
            pass  # telemetrie is best-effort
        if self._on_done is not None:
            try:
                self._on_done(snap)
            except Exception as exc:
                print(f"[tasks] on_done-callback faalde voor taak {tid}: {exc}",
                      flush=True)
```

(`datetime` staat al in de imports van tasks.py, r.19.)

**(d)** In `_load` (r.93-94), replace:

```python
            if status == "interrupted":
                self._persist(item)
```

with:

```python
            if status == "interrupted":
                self._persist(item)
                # A3 meetpunt: taak overleefde de herstart niet. Dit is het
                # derde B3-cijfer (naast tool_retry en task_long) en zou
                # zonder deze regel nergens tellen — _finish draait alleen
                # in de worker-thread (_run), nooit voor dit opstartpad.
                # Best-effort: telemetrie mag _load nooit breken.
                try:
                    from span import telemetry
                    telemetry.record("task_interrupted", 0.0,
                                     {"id": tid, "team": bool(item.get("team"))})
                except Exception:
                    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestTaskManagerVangnet -v`
Expected: PASS (4 tests). Regressie: `python -m pytest tests/test_skills_tasks.py -q` → PASS (bestaande aanroepen zonder `on_done` blijven werken; default None = geen callback).

- [ ] **Step 5: Commit**

```bash
git add src/span/jarvis/tasks.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): on_done-callback + task/task_long/task_interrupted-telemetrie in TaskManager"
```

---

### Task 6: Telegram-push bij taak klaar / definitief mislukt

**Files:**
- Create: `src/span/jarvis/task_push.py`
- Modify: `src/span/server/app.py` (r.142-146)
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

De Telegram-bridge wordt volledig gemockt (MagicMock) en `send_respecting_quiet` wordt gemonkeypatcht — er gaat in tests nooit een echt bericht de deur uit.

```python
# append to tests/test_taakvangnet.py
class TestTaskPush:
    def _item(self, status="done", secs=300.0, owner=""):
        b = datetime.now(timezone.utc)
        a = b - timedelta(seconds=secs)
        return {"id": 7, "title": "rapport maken", "goal": "rapport maken",
                "status": status, "result": "hier is het rapport", "team": False,
                "owner": owner, "created": a.isoformat(), "updated": b.isoformat()}

    def _state(self, monkeypatch, sent):
        import span.jarvis.daily as daily
        monkeypatch.setattr(
            daily, "send_respecting_quiet",
            lambda tg, text, brain, urgent=False: sent.append((text, urgent)) or True)
        tg = MagicMock(); tg.linked = True
        return {"telegram": tg, "brain": MagicMock(), "inbox": MagicMock()}

    def test_langlopende_done_taak_pusht(self, monkeypatch):
        monkeypatch.setenv("SPAN_TASK_PUSH", "on")
        from span.jarvis.task_push import make_task_push
        sent = []
        make_task_push(self._state(monkeypatch, sent))(self._item(secs=300.0))
        assert sent and "Achtergrondtaak klaar" in sent[0][0]
        assert sent[0][1] is False  # klaar-ping respecteert de stille uren

    def test_korte_done_taak_pusht_niet(self, monkeypatch):
        monkeypatch.setenv("SPAN_TASK_PUSH", "on")
        from span.jarvis.task_push import make_task_push
        sent = []
        make_task_push(self._state(monkeypatch, sent))(self._item(secs=10.0))
        assert sent == []

    def test_definitief_mislukt_pusht_altijd_en_urgent(self, monkeypatch):
        monkeypatch.setenv("SPAN_TASK_PUSH", "on")
        from span.jarvis.task_push import make_task_push
        sent = []
        state = self._state(monkeypatch, sent)
        make_task_push(state)(self._item(status="error", secs=5.0))
        assert sent and "Achtergrondtaak mislukt" in sent[0][0]
        assert sent[0][1] is True          # falen breekt door de stille uren
        state["inbox"].add.assert_called_once()  # en landt ook in de Agent Inbox

    def test_flag_uit_en_vreemde_owner_pushen_niet(self, monkeypatch):
        from span.jarvis.task_push import make_task_push
        sent = []
        state = self._state(monkeypatch, sent)
        monkeypatch.setenv("SPAN_TASK_PUSH", "off")
        make_task_push(state)(self._item(status="error"))
        monkeypatch.setenv("SPAN_TASK_PUSH", "on")
        monkeypatch.setenv("SPAN_OWNER_OID", "oid-bas")
        make_task_push(state)(self._item(status="error", owner="oid-iemand-anders"))
        assert sent == []  # Telegram is alléén Bas' kanaal

    def test_geannuleerd_pusht_niet(self, monkeypatch):
        monkeypatch.setenv("SPAN_TASK_PUSH", "on")
        from span.jarvis.task_push import make_task_push
        sent = []
        make_task_push(self._state(monkeypatch, sent))(
            self._item(status="cancelled", secs=300.0))
        assert sent == []  # door Bas zelf gestopt -> geen ping
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestTaskPush -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.jarvis.task_push'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/jarvis/task_push.py
"""A3 — taak-push: melding wanneer een langlopende achtergrondtaak klaar is
of definitief faalt. Maakt de belofte van spawn_task ("ik meld het als 'ie
klaar is") eindelijk waar.

Regels: definitief mislukt -> altijd melden (urgent + Agent Inbox); klaar ->
alleen als de taak lang liep (LONG_PUSH_SECS); geannuleerd/onderbroken -> stil
(dat deed Bas zelf of een herstart). Telegram is alléén Bas' kanaal, dus
taken van andere web-login-gebruikers pushen nooit. Best-effort, achter
SPAN_TASK_PUSH (default aan)."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

LONG_PUSH_SECS = 120.0  # 'langlopend': pas boven deze duur een klaar-ping


def push_enabled() -> bool:
    """Feature-flag SPAN_TASK_PUSH (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_TASK_PUSH", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def _duration_s(item: dict[str, Any]) -> float:
    try:
        a = datetime.fromisoformat(item.get("created") or "")
        b = datetime.fromisoformat(item.get("updated") or "")
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return 0.0


def _mine(item: dict[str, Any]) -> bool:
    """Alleen systeem-/owner-taken; nooit die van een andere gebruiker."""
    owner = (item.get("owner") or "").strip()
    return owner in ("", os.environ.get("SPAN_OWNER_OID", "").strip())


def should_push(item: dict[str, Any]) -> bool:
    if not _mine(item):
        return False
    status = item.get("status")
    if status == "error":
        return True  # definitief mislukt -> altijd eerlijk melden
    return status == "done" and _duration_s(item) >= LONG_PUSH_SECS


def make_task_push(state: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    """on_done-callback voor de TaskManager; closure over de server-state
    (telegram/inbox/brain) — wiring in app.py."""

    def on_done(item: dict[str, Any]) -> None:
        if not push_enabled() or not should_push(item):
            return
        status = item.get("status")
        titel = ((item.get("title") or item.get("goal") or "").strip())[:60]
        if status == "error":
            kop = "❌ Achtergrondtaak mislukt"
            inbox = state.get("inbox")
            if inbox is not None:
                inbox.add(kind="notify", title=f"Taak mislukt: {titel}",
                          detail=(item.get("result") or "")[:240], urgency="high")
        else:
            kop = "✅ Achtergrondtaak klaar"
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            try:
                from span.jarvis.daily import send_respecting_quiet
                send_respecting_quiet(
                    tg, f"{kop}: {titel}\n\n{(item.get('result') or '')[:500]}",
                    state["brain"], urgent=(status == "error"))
            except Exception:
                print(f"[task-push] telegram mislukt voor taak {item.get('id')}",
                      flush=True)

    return on_done
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestTaskPush -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Wire de callback in app.py**

In `src/span/server/app.py` (r.142-146), replace:

```python
    from span.jarvis.tasks import TaskManager
    from span.jarvis.task_runners import make_runners
    _task_runner, _team_runner = make_runners(_state)
    _state["tasks"] = TaskManager(_task_runner, brain=brain, max_workers=2,
                                  team_runner=_team_runner)
```

with:

```python
    from span.jarvis.tasks import TaskManager
    from span.jarvis.task_runners import make_runners
    from span.jarvis.task_push import make_task_push
    _task_runner, _team_runner = make_runners(_state)
    _state["tasks"] = TaskManager(_task_runner, brain=brain, max_workers=2,
                                  team_runner=_team_runner,
                                  on_done=make_task_push(_state))
```

(De closure leest `_state["telegram"]` pas bij een afgeronde taak — dat de Telegram-bridge verderop in de lifespan pas gezet wordt (r.157-160) is dus geen probleem.)

- [ ] **Step 6: Run the full task test set (no regression)**

Run: `python -m pytest tests/test_taakvangnet.py tests/test_skills_tasks.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/span/jarvis/task_push.py src/span/server/app.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): Telegram-push bij taak klaar of definitief mislukt"
```

---

### Task 7: Cron-toets — watchdog op de dagtaak-stempels

**Files:**
- Create: `src/span/jarvis/watchdog.py`
- Test: `tests/test_taakvangnet.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_taakvangnet.py
class TestCronWatchdog:
    def _brain(self, last=None, reported=None):
        """MagicMock-brein dat per key de juiste last/reported-rij teruggeeft."""
        brain = MagicMock()

        def run(query, **kw):
            for key in ("evening", "consolidate", "weekreview"):
                if f"last_{key}" in query:
                    return [{"last": (last or {}).get(key, ""),
                             "reported": (reported or {}).get(key, "")}]
            return []  # MERGE-stempels e.d.

        brain.run.side_effect = run
        return brain

    def test_expected_date_daily_en_weekreview(self):
        from span.jarvis import watchdog
        maandag = datetime(2026, 7, 13, 9, 0)  # ma -> gisteren zo 12-07, vr 10-07
        assert watchdog.expected_date("evening", maandag) == "2026-07-12"
        assert watchdog.expected_date("consolidate", maandag) == "2026-07-12"
        assert watchdog.expected_date("weekreview", maandag) == "2026-07-10"

    def test_gemiste_dagafsluiting_wordt_gezien(self):
        from span.jarvis import watchdog
        brain = self._brain(last={"evening": "2026-07-06",      # 2 dagen gat
                                  "consolidate": "2026-07-08",  # bij
                                  "weekreview": "2026-07-03"})  # laatste vr: bij
        missed = watchdog.check_missed_runs(brain, datetime(2026, 7, 9, 10, 0))
        assert [m["key"] for m in missed] == ["evening"]
        assert missed[0]["expected"] == "2026-07-08"

    def test_al_gemelde_misser_niet_dubbel(self):
        from span.jarvis import watchdog
        brain = self._brain(last={"evening": "2026-07-06",
                                  "consolidate": "2026-07-08",
                                  "weekreview": "2026-07-03"},
                            reported={"evening": "2026-07-08"})
        assert watchdog.check_missed_runs(brain, datetime(2026, 7, 9, 10, 0)) == []

    def test_tick_meldt_via_inbox_en_telegram_en_stempelt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPAN_CRON_WATCHDOG", "on")
        monkeypatch.setenv("SPAN_TELEMETRY", "on")
        monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
        import span.jarvis.daily as daily
        from span.jarvis import watchdog
        sent = []
        monkeypatch.setattr(
            daily, "send_respecting_quiet",
            lambda tg, text, brain, urgent=False: sent.append(text) or True)
        monkeypatch.setattr(watchdog, "now_local",
                            lambda: datetime(2026, 7, 9, 10, 0))
        brain = self._brain(last={"evening": "2026-07-06",
                                  "consolidate": "2026-07-08",
                                  "weekreview": "2026-07-03"})
        inbox = MagicMock()
        tg = MagicMock(); tg.linked = True
        n = watchdog.watchdog_tick({"brain": brain, "inbox": inbox, "telegram": tg})
        assert n == 1
        inbox.add.assert_called_once()
        assert sent and "dagafsluiting" in sent[0]
        import span.telemetry as tel
        assert tel.aggregate()["segments"]["cron_missed"]["count"] == 1

    def test_tick_flag_uit_is_noop(self, monkeypatch):
        monkeypatch.setenv("SPAN_CRON_WATCHDOG", "off")
        from span.jarvis import watchdog
        brain = self._brain(last={})
        assert watchdog.watchdog_tick({"brain": brain, "inbox": MagicMock()}) == 0
        brain.run.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_taakvangnet.py::TestCronWatchdog -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.jarvis.watchdog'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/jarvis/watchdog.py
"""A3 — cron-toets: bewaakt dat de geplande dagtaken (dagafsluiting,
consolidatie, weekreview) daadwerkelijk liepen.

De scheduler stempelt c.last_<key> op de runtime-Config-node pas NA succes
(daily.py _mark_run); deze watchdog toetst achteraf of die stempel er voor de
laatst verplichte dag staat. Een gat (server een dag plat, scheduler-coroutine
dood) = één melding via Agent Inbox + Telegram, met een gemeld-stempel
(c.watchdog_<key>) zodat dezelfde misser nooit spamt. Vandaag telt bewust niet
mee: de run van vandaag kan nog komen. Best-effort, achter SPAN_CRON_WATCHDOG
(default aan)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from span.jarvis.daily import now_local

# key -> mensvriendelijk label; keys matchen c.last_<key> uit daily.py
WATCHED = {
    "evening": "dagafsluiting (dagelijks 17:15)",
    "consolidate": "consolidatie (dagelijks 03:30)",
    "weekreview": "weekreview (vrijdag 16:30)",
}


def watchdog_enabled() -> bool:
    """Feature-flag SPAN_CRON_WATCHDOG (default aan; off/0/false/no/'' = uit)."""
    val = os.environ.get("SPAN_CRON_WATCHDOG", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def expected_date(key: str, now: datetime) -> str:
    """Meest recente dag vóór vandaag waarop de taak af had moeten zijn."""
    d = now.date() - timedelta(days=1)
    if key == "weekreview":
        while d.weekday() != 4:  # terug naar de laatste vrijdag
            d -= timedelta(days=1)
    return d.isoformat()


def check_missed_runs(brain: Any, now: datetime) -> list[dict[str, str]]:
    """Pure toets: welke bewaakte taken misten hun laatst verplichte run?
    Datum-strings (YYYY-MM-DD) vergelijken lexicografisch = chronologisch.
    Een lege last ('nooit gelopen') telt ook als misser — een scheduler die
    nooit één keer slaagde mag niet onzichtbaar blijven."""
    missed: list[dict[str, str]] = []
    for key, label in WATCHED.items():
        expected = expected_date(key, now)
        rows = brain.run(
            f"MATCH (c:Config {{id:'runtime'}}) "
            f"RETURN c.last_{key} AS last, c.watchdog_{key} AS reported"
        )
        row = rows[0] if rows else {}
        last = row.get("last") or ""
        reported = row.get("reported") or ""
        if last >= expected or reported >= expected:
            continue  # gelopen, of deze misser is al gemeld
        missed.append({"key": key, "label": label,
                       "expected": expected, "last": last})
    return missed


def _mark_reported(brain: Any, key: str, expected: str) -> None:
    brain.run(
        f"MERGE (c:Config {{id:'runtime'}}) SET c.watchdog_{key} = $d",
        d=expected,
    )


def watchdog_tick(state: dict[str, Any]) -> int:
    """Meld gemiste runs (inbox + Telegram, best-effort) en stempel ze als
    gemeld. Geeft het aantal meldingen terug; mag een tick nooit breken."""
    if not watchdog_enabled():
        return 0
    brain = state["brain"]
    try:
        missed = check_missed_runs(brain, now_local())
    except Exception as exc:
        print(f"[watchdog] toets mislukt: {type(exc).__name__}: {exc}", flush=True)
        return 0
    for m in missed:
        titel = f"Geplande taak niet gelopen: {m['label']}"
        detail = (f"Verwacht op {m['expected']}; laatste geslaagde run: "
                  f"{m['last'] or 'nooit'}. Check de serverlog van die dag.")
        inbox = state.get("inbox")
        if inbox is not None:
            inbox.add(kind="notify", title=titel, detail=detail, urgency="high")
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            try:
                from span.jarvis.daily import send_respecting_quiet
                send_respecting_quiet(tg, f"⚠️ {titel}\n{detail}", brain)
            except Exception:
                print(f"[watchdog] telegram-push mislukt voor {m['key']}", flush=True)
        try:
            from span import telemetry
            telemetry.record("cron_missed", 0.0,
                             {"key": m["key"], "expected": m["expected"]})
        except Exception:
            pass  # telemetrie is best-effort
        try:
            _mark_reported(brain, m["key"], m["expected"])
        except Exception as exc:
            print(f"[watchdog] stempel mislukt voor {m['key']}: {exc}", flush=True)
    return len(missed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_taakvangnet.py::TestCronWatchdog -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/jarvis/watchdog.py tests/test_taakvangnet.py
git commit -m "feat(vangnet): cron-toets watchdog op de dagtaak-stempels"
```

---

### Task 8: Watchdog-wiring in de scheduler + volledige regressie

**Files:**
- Modify: `src/span/jarvis/daily.py` (`daily_scheduler`, r.553-627)

Geen nieuwe failing test: de tick-logica zelf is in Task 7 volledig getest; deze taak hangt hem alleen in de bestaande minuut-loop (zelfde interval-patroon als `orphan_last`, dat geen eigen loop-test heeft). De borging is de regressie-sweep in Step 2.

- [ ] **Step 1: Wire de watchdog in de scheduler-loop**

**(a)** In `src/span/jarvis/daily.py`, replace (r.555-557):

```python
    HALF_HOUR = timedelta(minutes=30)
    orphan_last = now_local() - HALF_HOUR
    ff_last = now_local() - HALF_HOUR
```

with:

```python
    HALF_HOUR = timedelta(minutes=30)
    orphan_last = now_local() - HALF_HOUR
    ff_last = now_local() - HALF_HOUR
    wd_last = now_local() - HALF_HOUR
```

**(b)** Direct ná het orphan-reflectie-blok (r.605-613, vóór het Fireflies-blok), add:

```python
            # A3 cron-toets: liepen de dagtaken van gisteren echt? (elk half uur;
            # de eerste tick na een herstart toetst meteen — juist dán is een
            # gat waarschijnlijk)
            if now - wd_last >= HALF_HOUR:
                wd_last = now
                from span.jarvis.watchdog import watchdog_tick
                try:
                    n = await asyncio.to_thread(watchdog_tick, state)
                    if n:
                        log(f"watchdog: {n} gemiste geplande taken gemeld")
                except Exception as exc:
                    log(f"watchdog: mislukt — {exc}")
```

- [ ] **Step 2: Run the full A3 + jarvis regression sweep**

Run: `python -m pytest tests/test_taakvangnet.py tests/test_jarvis.py tests/test_skills_tasks.py tests/test_http_retry.py tests/test_telemetry.py tests/test_observability.py -q`
Expected: PASS — geen enkele bestaande test geraakt.

- [ ] **Step 3: Commit**

```bash
git add src/span/jarvis/daily.py
git commit -m "feat(vangnet): watchdog-tick per half uur in de daily_scheduler"
```

---

## Afhankelijkheden & volgorde

- **A1-telemetrie moet af en gemerged zijn vóór dit plan start.** A3 importeert `span.telemetry.record` (Tasks 2/5/7) en wijzigt `agent.py` in dezelfde functie (`turn()`) die A1 net instrumenteerde. Bouw dit plan op de `a1-telemetrie`-branch (of op master ná de merge); de regelnummers in Task 3 zijn die van de a1-telemetrie-versie van `agent.py`.
- **Verboden terrein:** `src/span/telemetry.py` en `tests/test_telemetry.py` worden alleen geïmporteerd/gedraaid, nooit gewijzigd (parallelle sessie). `routes.py` wordt in dit plan helemaal niet aangeraakt — de meetpunten verschijnen automatisch als nieuwe segmenten in het bestaande owner-only `GET /api/telemetry`, omdat `aggregate()` per segment telt (meta wordt daar genegeerd; daarom eigen segment-namen `tool_retry`/`task`/`task_long`/`task_interrupted`/`cron_missed` in plaats van alleen meta.retries).
- **Taakvolgorde binnen het plan:** 1→2 (retry-kern vóór dispatch), 3→4 (signaal vóór consumenten), 5→6 (callback vóór push-wiring), 7→8 (watchdog vóór scheduler-wiring). Task 3/4 en 5/6 en 7/8 zijn onderling onafhankelijke paren.
- **Niets uit fase B/C bouwen:** geen DBOS, geen idempotency-keys/dry-run op muterende tools, geen voortgangs-pushes tijdens het draaien (B3), geen WhatsApp (A6/C). De A3-meetpunten zijn juist de input voor die B3-beslissing.
- **Secrets:** dit plan introduceert geen nieuwe secrets; alleen de env-var-namen `SPAN_TOOL_RETRY`, `SPAN_TASK_PUSH`, `SPAN_CRON_WATCHDOG`, `SPAN_HONEST_OUTCOMES`, `SPAN_OWNER_OID` (bestaand) en `TELEGRAM_BOT_TOKEN` (bestaand) komen in tekst voor — waarden horen uitsluitend in `~/nova/.env` op z390.

## Handmatige verificatie (prod, z390)

Na deploy (bestaand patroon: compose bewaren, `git fetch && git reset --hard origin/master`, `.env` terug, `docker compose up -d --build span`, `curl readyz`):

1. **Push bij langlopende taak:** start via de chat een achtergrondtaak die > 2 minuten loopt (bv. "zoek in het archief naar X en maak een samenvatting" als taak). Verwacht: bij afronden een Telegram-bericht "✅ Achtergrondtaak klaar: …". Een taak van 30 seconden geeft géén ping.
2. **Push bij falen:** zet tijdelijk een kapotte `SPAN_STT_URL`-achtige afhankelijkheid niet — simpeler: submit een taak met een doel dat gegarandeerd een modelfout uitlokt kan niet netjes; gebruik in plaats daarvan de teststap hierboven en controleer bij de eerstvolgende échte taakfout dat er "❌ Achtergrondtaak mislukt" + een hoog-urgent inbox-item verschijnt (status in het Taken-paneel = error, niet done).
3. **Cron-toets:** zet in Neo4j-browser `MATCH (c:Config {id:'runtime'}) SET c.last_evening = '2026-07-01'` en wacht max. 30 minuten. Verwacht: inbox-item + Telegram "⚠️ Geplande taak niet gelopen: dagafsluiting …", precies één keer (stempel `c.watchdog_evening` staat daarna op gisteren). Herstel daarna de waarde niet — de eerstvolgende geslaagde dagafsluiting stempelt zelf.
4. **Eerlijke cron-uitkomst:** een execute-cron die faalt (bv. tijdens een bewuste provider-storing) meldt na 3 pogingen "⏰ Mislukt: …" en nooit meer "⏰ Uitgevoerd" met een foutzin als resultaat.
5. **Meetpunten:** `curl -s -H "Cookie: <owner-cookie>" https://nova.famspaan.nl/api/telemetry | jq '.segments | {tool_retry, task, task_long, task_interrupted, cron_missed}'` — na een paar dagen draaien: `task.count` = aantal afgeronde taken, `tool_retry.count` = hoeveel tool-calls een retry nodig hadden, `task_long.count` = hoeveel taken > 2 minuten liepen, `task_interrupted.count` = hoeveel taken een herstart niet overleefden (dat laatste cijfer is het sterkste argument vóór/tegen DBOS durable execution). Dit zijn de cijfers die de B3-inrichting (welke taaktypen eerst op DBOS) sturen. Sneltest voor task_interrupted: start een lange taak en herstart de container — na de herstart staat de taak op 'interrupted' in het Taken-paneel én telt het segment +1.
6. **Kill switch:** `SPAN_TOOL_RETRY=off` in `~/nova/.env` + herstart geeft byte-voor-byte het oude dispatch-gedrag terug (idem `SPAN_TASK_PUSH` / `SPAN_CRON_WATCHDOG`); `SPAN_HONEST_OUTCOMES=off` geeft het oude uitkomst-gedrag van crons en taak-runners terug (gefaalde beurten weer als "⏰ Uitgevoerd"/status done gemeld — alleen als noodrem bij een vals-negatief in `last_turn_ok`).

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (A3-blok, spec r.62-67):**
- Retries met backoff, alleen transiënt, muterende tools nooit blind → Tasks 1+2 (classifier is type- én status-gebaseerd; dispatch retryt uitsluitend `rw=="read"` via `_perm_key_rw`/TOOL_META, onder de guard zodat approval/inbox nooit dubbelt). ✓
- Eerlijke uitkomsten → Tasks 3+4 (`last_turn_ok` + consumenten: cron-execute meldt "Uitvoering mislukt", taak wordt status error i.p.v. done, teamtaak zonder één geslaagde deeltaak faalt; de drie consument-ingrepen achter `SPAN_HONEST_OUTCOMES` zodat ook dit deel per veiligheidsklep §5 met één env-var omkeerbaar is — het zetten van het signaal zelf is onvoorwaardelijk maar gedragsloos). ✓
- Cron-toets op dagafsluiting/consolidatie/weekreview, gemiste run = melding → Tasks 7+8 (toets op de bestaande `c.last_<key>`-stempels; overleeft herstarts omdat de stempels persistent zijn, in tegenstelling tot de in-memory attempts-dict). ✓
- Telegram-push bij klaar/definitief mislukt → Tasks 5+6 (on_done-callback, stille uren gerespecteerd, falen urgent, owner-filter). ✓
- Meetpunt retries + langlopers + herstart-slachtoffers (spec r.67: "hoeveel moesten herstart overleven") → segmenten `tool_retry`/`task`/`task_long`/`task_interrupted` (+ bonus `cron_missed`), zichtbaar in het bestaande `GET /api/telemetry` zonder routes.py of telemetry.py aan te raken; `task_interrupted` wordt in `_load` gemeten omdat het opstart-herstelpad buiten `_run`/`_finish` om gaat. ✓

**Valkuilen uit de verkenning afgedekt:** dubbel-retry begrensd (2 korte retries bovenop de HTTP-laag, max ~3s slaap); retry ónder de guard en alleen om de handler (geen dubbele approval/inbox); MCP-pad ongemoeid; agent.py-wijziging minimaal en op andere regels dan A1; nieuwe logica in drie kleine modules i.p.v. de mastodonten (<500-regels-regel); Telegram faalt stil volgens het crons-patroon; alle muterende integratie-calls in tests gemockt (MagicMock-telegram + gemonkeypatchte `send_respecting_quiet`; geen echte HTTP in enige test).

**Placeholder-scan:** geen TBD/TODO; elke code-stap toont de echte code. Task 8 heeft bewust geen nieuwe failing test (pure wiring van een in Task 7 volledig geteste functie, met regressie-sweep als borging) — dat is een expliciete keuze, geen gat.

**Type-consistentie:** `call_with_retry(fn) -> tuple[Any, int]` identiek in Tasks 1/2; `on_done: Callable[[dict[str, Any]], None]` identiek in Tasks 5/6; `check_missed_runs(brain, now) -> list[dict[str, str]]` identiek in Task 7-tests en -implementatie; flag-helpers volgen exact het `_enabled()`-patroon van `span.telemetry`.
