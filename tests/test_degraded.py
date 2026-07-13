# tests/test_degraded.py
"""A4 — degraded-mode: een embed/brain-uitval mag een beurt of sessiestart
nooit breken."""
from __future__ import annotations

import json


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
