# B1 Fast-lane-routering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Korte/sociale beurten laten starten op het snelle model (Haiku), en zodra een beurt een tool aanroept terugschakelen naar het hoofdmodel (Sonnet) voor de eind-synthese — zodat de vele korte beurten sneller antwoorden zonder taak-kwaliteit te verliezen.

**Architecture:** Escalatie-routering, per beurt beslist in `SpanAgent.turn()`. Vóór de chat-loop kiest een kleine helper (`span.orchestrator.fastlane`) het startmodel op basis van de flag `SPAN_FAST_LANE`. Iteratie 0 (tool-selectie of puur gesprek) draait op het lichte model; zodra `tool_calls` verschijnt, schakelt het gekozen model naar `model_main` voor de resterende iteraties (synthese). De genomen route wordt als `lane` (`main`/`fast`/`escalated`) in de bestaande `llm`-telemetrie gelogd zodat de winst meetbaar is. Alles achter een flag, **default UIT** — een poort die Bas opent na review, exact zoals A2.

**Tech Stack:** Python 3.11 (prod) / 3.14 (dev), pytest, ruff. Bestaande stukken: `span.config.Settings` (`model_main`, `model_light`), `span.orchestrator.agent.SpanAgent.turn()`, `span.telemetry.record()`. Idioom overgenomen van A2 (`SPAN_TTS_STREAMING`) en A3 (`toolretry.retry_enabled()`).

---

### Task 1: Fast-lane helper-module

**Files:**
- Create: `src/span/orchestrator/fastlane.py`
- Test: `tests/test_fastlane.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fastlane.py
"""B1 — fast-lane-routering: flag + startmodel-keuze + escalatie."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.orchestrator.fastlane as fl


class TestFlag:
    def test_default_uit(self, monkeypatch):
        monkeypatch.delenv("SPAN_FAST_LANE", raising=False)
        assert fl.enabled() is False

    def test_lege_waarde_uit(self, monkeypatch):
        monkeypatch.setenv("SPAN_FAST_LANE", "")
        assert fl.enabled() is False

    def test_aan_waarden(self, monkeypatch):
        for val in ("1", "true", "yes", "on", "ON", " True "):
            monkeypatch.setenv("SPAN_FAST_LANE", val)
            assert fl.enabled() is True, val

    def test_uit_waarden(self, monkeypatch):
        for val in ("0", "off", "false", "no", "nope"):
            monkeypatch.setenv("SPAN_FAST_LANE", val)
            assert fl.enabled() is False, val


class TestInitialModel:
    def _settings(self):
        s = MagicMock()
        s.model_main = "sonnet"
        s.model_light = "haiku"
        return s

    def test_flag_uit_kiest_main(self, monkeypatch):
        monkeypatch.delenv("SPAN_FAST_LANE", raising=False)
        assert fl.initial_model(self._settings()) == "sonnet"

    def test_flag_aan_kiest_light(self, monkeypatch):
        monkeypatch.setenv("SPAN_FAST_LANE", "on")
        assert fl.initial_model(self._settings()) == "haiku"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fastlane.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'span.orchestrator.fastlane'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/orchestrator/fastlane.py
"""B1 — fast-lane-routering.

Escalatie-model: een beurt start op het lichte model (snel) en schakelt naar
het hoofdmodel zodra er een tool wordt aangeroepen (de synthese ná tool-
resultaten verdient het sterke model). Achter de flag SPAN_FAST_LANE, default
UIT — poort pas open nadat de telemetrie de winst bevestigt (zie A2-precedent).
"""
from __future__ import annotations

import os
from typing import Any

# lane-labels voor de telemetrie (segment "llm", veld "lane")
LANE_MAIN = "main"           # startte én bleef op het hoofdmodel
LANE_FAST = "fast"           # bleef op het lichte model (puur gesprek, geen tool)
LANE_ESCALATED = "escalated"  # startte licht, schakelde naar hoofdmodel bij een tool


def enabled() -> bool:
    """Feature-flag SPAN_FAST_LANE. Default UIT (leeg/0/off/false/no = uit)."""
    return os.environ.get("SPAN_FAST_LANE", "").strip().lower() in (
        "1", "true", "yes", "on")


def initial_model(settings: Any) -> str:
    """Startmodel voor een beurt: het lichte model als de flag aan staat,
    anders het hoofdmodel (dan is de hele routering een no-op)."""
    return settings.model_light if enabled() else settings.model_main
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fastlane.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/span/orchestrator/fastlane.py tests/test_fastlane.py
git commit -m "feat(b1): fast-lane helper — flag + startmodel-keuze"
```

---

### Task 2: Escalatie bedraden in de chat-loop

**Files:**
- Modify: `src/span/orchestrator/agent.py` (import bovenaan; loop rond r.447-471, tool-detectie r.497, telemetrie r.605)
- Test: `tests/test_fastlane.py` (klasse toevoegen)

- [ ] **Step 1: Write the failing test**

Voeg toe aan `tests/test_fastlane.py` (het `_agent_double`-recept is overgenomen uit `tests/test_taakvangnet.py`):

```python
def _agent_double(monkeypatch):
    """Minimale SpanAgent-double voor turn() (recept uit test_taakvangnet)."""
    from span.orchestrator.agent import SpanAgent

    agent = SpanAgent.__new__(SpanAgent)
    tb = MagicMock()
    tb.specs_for.return_value = []
    tb.touched = []
    tb.dispatch.return_value = "{}"
    agent._toolbox = tb
    agent._messages = []
    agent._recorders = []

    frag = MagicMock()
    frag.embed.return_value = [0.0]
    frag.search.return_value = []
    frag.search_formal.return_value = []
    agent._fragments = frag

    settings = MagicMock()
    settings.model_main = "sonnet"
    settings.model_light = "haiku"
    agent._settings = settings
    agent._security = {}
    agent._record_turn = lambda *a, **k: None
    agent._persist_messages = lambda *a, **k: None
    agent._verify_active_quest = lambda *a, **k: None
    agent._write_trace = lambda *a, **k: None
    return agent


def _msg(content, tool_calls=None):
    m = MagicMock()
    m.content = content
    m.tool_calls = tool_calls
    return m


def _toolcall(name="brain_search"):
    tc = MagicMock()
    tc.id = "1"
    tc.function.name = name
    tc.function.arguments = "{}"
    return tc


class TestEscalatie:
    def test_flag_uit_gebruikt_altijd_main(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        monkeypatch.delenv("SPAN_FAST_LANE", raising=False)
        agent = _agent_double(monkeypatch)
        llm = MagicMock(); llm.chat.return_value = _msg("hoi terug")
        agent._llm = llm
        agent.turn("hoi")
        assert llm.chat.call_args_list[0].kwargs["model"] == "sonnet"

    def test_flag_aan_puur_gesprek_blijft_licht(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        monkeypatch.setenv("SPAN_FAST_LANE", "on")
        agent = _agent_double(monkeypatch)
        llm = MagicMock(); llm.chat.return_value = _msg("hoi terug")
        agent._llm = llm
        agent.turn("hoi")
        assert llm.chat.call_count == 1
        assert llm.chat.call_args_list[0].kwargs["model"] == "haiku"

    def test_flag_aan_tool_escaleert_naar_main(self, monkeypatch):
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        monkeypatch.setenv("SPAN_FAST_LANE", "on")
        agent = _agent_double(monkeypatch)
        llm = MagicMock()
        # iteratie 0: tool-call (op licht); iteratie 1: synthese (moet main zijn)
        llm.chat.side_effect = [_msg("", [_toolcall()]), _msg("klaar")]
        agent._llm = llm
        out = agent.turn("zoek iets op")
        assert llm.chat.call_args_list[0].kwargs["model"] == "haiku"
        assert llm.chat.call_args_list[1].kwargs["model"] == "sonnet"
        assert "klaar" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fastlane.py::TestEscalatie -v`
Expected: FAIL — `test_flag_aan_puur_gesprek_blijft_licht` en `test_flag_aan_tool_escaleert_naar_main` falen omdat `turn()` nog altijd `model_main` gebruikt (call 0 = "sonnet" i.p.v. "haiku").

- [ ] **Step 3a: Voeg de import toe**

In `src/span/orchestrator/agent.py`, bij de andere `from span...`-imports bovenaan (rond r.20, direct ná `from span.llm.client import LLMClient`):

```python
from span.orchestrator import fastlane
```

- [ ] **Step 3b: Kies het startmodel vóór de loop**

In `src/span/orchestrator/agent.py`, direct ná de regel `turn_tools = self._toolbox.specs_for(user_message, embedding=embedding)` (r.447) en vóór `cancelled = False` (r.448):

```python
        # B1 fast-lane: start licht (snel), escaleer naar het hoofdmodel zodra
        # er een tool wordt aangeroepen (synthese verdient het sterke model).
        chosen_model = fastlane.initial_model(self._settings)
        _lane = (fastlane.LANE_FAST if chosen_model == self._settings.model_light
                 else fastlane.LANE_MAIN)
```

- [ ] **Step 3c: Gebruik het gekozen model in de chat-call**

In `src/span/orchestrator/agent.py`, in de chat-call (r.469-474), vervang `model=self._settings.model_main,` door `model=chosen_model,`:

```python
                message = self._llm.chat(
                    self._messages + ([memo_msg] if memo_msg else []),
                    model=chosen_model,
                    tools=turn_tools,
                    on_text=on_text,
                )
```

- [ ] **Step 3d: Escaleer bij een tool-call**

In `src/span/orchestrator/agent.py`, direct ná `tool_calls = getattr(message, "tool_calls", None)` (r.497) en vóór `if not tool_calls:` (r.498):

```python
            if tool_calls and chosen_model != self._settings.model_main:
                # deze beurt gebruikt tools -> vanaf de volgende iteratie
                # (de synthese) het hoofdmodel gebruiken.
                chosen_model = self._settings.model_main
                _lane = fastlane.LANE_ESCALATED
```

- [ ] **Step 3e: Log de gekozen route in de telemetrie**

In `src/span/orchestrator/agent.py`, vervang de `llm`-telemetrieregel (r.604-605):

```python
        if _llm_ms:
            telemetry.record("llm", _llm_ms,
                             {"tools": len(tools_used), "lane": _lane})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fastlane.py -v`
Expected: PASS (9 passed — 6 uit Task 1 + 3 escalatie)

- [ ] **Step 5: Volledige regressie + lint**

Run: `python -m pytest tests/ -q && ruff check src/span/orchestrator/agent.py src/span/orchestrator/fastlane.py tests/test_fastlane.py`
Expected: alle tests groen (op de bekende `test_llm_client`-fails na wanneer `ORQ_API_KEY` ontbreekt), ruff schoon.

- [ ] **Step 6: Commit**

```bash
git add src/span/orchestrator/agent.py tests/test_fastlane.py
git commit -m "feat(b1): escalatie-routering in turn() — licht start, main bij tool"
```

---

### Task 3: Flag documenteren in .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Voeg de flag toe onder de bestaande model-instellingen**

Voeg in `.env.example`, direct ná de regel `SPAN_MODEL_LIGHT=...` (r.7), toe:

```bash
# B1 fast-lane: korte/sociale beurten op SPAN_MODEL_LIGHT, escaleert naar
# SPAN_MODEL_MAIN zodra een beurt een tool aanroept. Default UIT (poort na review).
# Aanzetten: SPAN_FAST_LANE=on
SPAN_FAST_LANE=
```

- [ ] **Step 2: Verifieer dat de regel klopt**

Run: `grep -n "SPAN_FAST_LANE" .env.example`
Expected: één regel `SPAN_FAST_LANE=`

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(b1): SPAN_FAST_LANE flag in .env.example"
```

---

### Task 4: Activering + meting op de z390 — ✅ UITGEVOERD 2026-07-14 (akkoord Bas)

**Uitkomst:** fast-lane is live en actief op de z390 (`SPAN_FAST_LANE=on`).

- **Deploy:** master (`54a976b`, PR #131) ge-synct naar `~/nova` (git-reset + custom compose hersteld), span-image herbouwd + gerecreëerd, `readyz` 200.
- **Baseline (pre-flag, alles Sonnet):** `llm` p50 4.516ms / p95 12.924ms (n=113) — model dominant, bevestigt spec §B1.
- **Kwaliteit (eval-set mét flag aan):** totaal **92,9% (65/70)** · geheugen **100% (50/50)** · taken **75% (15/20)** — géén regressie t.o.v. de nulmeting (89% / 96% / 70%); taken hielden stand boven 70%.
- **Latency per lane (telemetrie sinds activering):** `fast` (Haiku, geen tool) p50 **1.708ms** (n=56) = ~2,6× sneller dan de Sonnet-baseline; `escalated` (tool → Sonnet) p50 4.591ms (n=41) = kwaliteit behouden.
- **Rollback:** `~/nova/.env.bak-pre-fastlane-20260714` terugzetten + `docker compose up -d span` = puur Sonnet.

Historisch draaiboek (zoals uitgevoerd):

- [ ] **Step 1: Deploy master naar de z390** (recept in runbook / geheugen `project-nova-span-deploy`): tar → scp → wegwerp-`pnpm`/`pip`-install niet nodig (geen nieuwe dep) → `docker compose -p jarvis up -d --build span` (of het `nova-`-recept van deze host), `readyz` 200 verifiëren.

- [ ] **Step 2: Baseline vastleggen** — vóór activering de huidige `llm`-p50/p95 uit de telemetrie halen (dominant volgens de meting van 2026-07-14: p50 ~4,5 s).

- [ ] **Step 3: Flag aanzetten** — `SPAN_FAST_LANE=on` in `~/nova/.env` (z390), `docker compose ... up -d --force-recreate span`. Bewaar de vorige `.env` voor rollback.

- [ ] **Step 4: Meten** — na een reeks echte beurten de telemetrie per `lane` uitsplitsen (`main`/`fast`/`escalated`): bevestig dat `fast`-beurten fors lager liggen dan de Sonnet-baseline en dat `escalated`-beurten geen taak-kwaliteit verliezen (draai de eval-set met `SPAN_FAST_LANE=on` en vergelijk taken-score met de 70%-nulmeting).

- [ ] **Step 5: Uitkomst vastleggen** in dit plan + geheugen. Bij regressie op taken: rollback (`SPAN_FAST_LANE` leeg + recreate) = terug naar puur Sonnet.

---

## Bekende, bewust-gelaten punten (v1)

- **Pre-tool-tekst op het lichte model:** als het lichte model in iteratie 0 tekst streamt vóór de tool-call, komt die van Haiku. In de praktijk emitteren tool-beurten daar zelden tekst; de eind-synthese draait op Sonnet. Meten in Task 4; pas fijnslijpen als het merkbaar is.
- **Tool-selectie op het lichte model:** iteratie 0 kiest de tool op Haiku (betrouwbaar en goedkoop); de kwaliteitsgevoelige synthese draait op Sonnet. Bewuste afweging voor v1.
- **Streaming-STT (het andere B1-onderdeel)** is een aparte, latere stap — de telemetrie wees `llm_ms` als dominant aan, dus fast-lane eerst (spec §B1).
