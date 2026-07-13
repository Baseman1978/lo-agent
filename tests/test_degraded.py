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
