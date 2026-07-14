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
