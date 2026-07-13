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
        assert not out.startswith("Uitvoering mislukt:")
        from span.jarvis.task_runners import make_runners
        task_runner, _ = make_runners(state)
        result = task_runner({"goal": "doe iets", "title": "t"},
                             lambda *a, **k: None, lambda: False, {})
        assert "modelaanroep mislukte" in result
