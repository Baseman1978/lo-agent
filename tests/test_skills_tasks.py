"""Skills, achtergrondtaken en Agent-Inbox-isolatie — zonder netwerk of DB.

Dekt de lagen die deze sessie zijn toegevoegd:
  * span.memory.skills      — validatie, param-templating, macro-uitvoering, prompt-render
  * span.jarvis.tasks       — TaskManager: levenscyclus, owner-isolatie, annuleren, team-routing
  * span.jarvis.ambient     — AgentInbox: per-gebruiker zichtbaarheid + goedkeur-isolatie
  * span.orchestrator.tools — ToolBox tagt gequeue'de acties met de eigenaar (brain-db)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from span.memory.skills import (
    normalize_name, validate_steps, execute_macro, render_for_prompt,
)
from span.jarvis.tasks import TaskManager
from span.jarvis.ambient import AgentInbox
from span.orchestrator.tools import ToolBox


# --------------------------------------------------------------------------
# Skills — pure logica
# --------------------------------------------------------------------------
class TestSkillValidation:
    def test_normalize_name_kebab(self):
        assert normalize_name("  Mijn Skill!  ") == "mijn-skill"
        assert normalize_name("Foo_Bar 123") == "foobar-123"
        assert len(normalize_name("x" * 80)) == 48

    def test_validate_lege_macro_faalt(self):
        with pytest.raises(ValueError):
            validate_steps([])

    def test_validate_te_veel_stappen_faalt(self):
        steps = [{"tool": "brain_search"} for _ in range(13)]
        with pytest.raises(ValueError):
            validate_steps(steps)

    def test_validate_stap_zonder_tool_faalt(self):
        with pytest.raises(ValueError):
            validate_steps([{"args": {}}])

    def test_validate_geen_skill_recursie(self):
        with pytest.raises(ValueError):
            validate_steps([{"tool": "skill_use", "args": {}}])
        with pytest.raises(ValueError):
            validate_steps([{"tool": "use_skill"}])

    def test_validate_args_moet_object_zijn(self):
        with pytest.raises(ValueError):
            validate_steps([{"tool": "brain_search", "args": "fout"}])

    def test_validate_geldige_macro_ok(self):
        validate_steps([{"tool": "brain_search", "args": {"q": "x"}},
                        {"tool": "web_search"}])  # geen exception


class TestMacroExecution:
    def _macro(self, steps):
        return {"name": "demo", "kind": "macro", "steps": steps, "params": []}

    def test_param_templating_in_geneste_args(self):
        seen = {}

        def dispatch(tool, args):
            seen[tool] = args
            return '{"ok": true}'

        skill = self._macro([
            {"tool": "o365_mail_search",
             "args": {"q": "van {{params.wie}}", "opts": {"map": "{{params.map}}"},
                      "tags": ["{{params.wie}}", "vast"]}},
        ])
        out = execute_macro(skill, {"wie": "Jan", "map": "Inbox"}, dispatch)
        assert seen["o365_mail_search"]["q"] == "van Jan"
        assert seen["o365_mail_search"]["opts"]["map"] == "Inbox"   # genest dict
        assert seen["o365_mail_search"]["tags"] == ["Jan", "vast"]  # genest list
        assert out["steps_run"] == 1
        assert out["results"][0]["result"] == {"ok": True}  # JSON geparset

    def test_onbekende_tool_wordt_overgeslagen_niet_gecrasht(self):
        skill = self._macro([{"tool": "bestaat_niet"}, {"tool": "brain_search"}])
        out = execute_macro(skill, {}, lambda t, a: "{}", known_tools={"brain_search"})
        assert out["results"][0]["error"].startswith("tool niet beschikbaar")
        assert "result" in out["results"][1]

    def test_dispatch_fout_breekt_macro_niet(self):
        def dispatch(tool, args):
            raise RuntimeError("kapot")

        out = execute_macro(self._macro([{"tool": "brain_search"}]), {}, dispatch)
        assert out["steps_run"] == 1
        assert "RuntimeError" in out["results"][0]["error"]


class TestRenderForPrompt:
    def test_render_slaat_uitgeschakelde_over_en_markeert_type(self):
        skills = [
            {"name": "a", "kind": "macro", "description": "doe iets", "enabled": True},
            {"name": "b", "kind": "workflow", "description": "werkwijze",
             "trigger": "bij mail", "enabled": True},
            {"name": "c", "kind": "workflow", "description": "uit", "enabled": False},
            {"name": "d", "kind": "workflow", "description": "team", "enabled": True,
             "shared": True},
        ]
        out = render_for_prompt(skills)
        assert "a · ⚙ uitvoerbaar" in out
        assert "trigger: bij mail" in out
        assert "- c " not in out  # disabled skill 'c' weggelaten
        assert "[team]" in out


# --------------------------------------------------------------------------
# TaskManager — levenscyclus, owner-isolatie, annuleren, team-routing
# --------------------------------------------------------------------------
def _wait(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestTaskManager:
    def test_taak_draait_en_eindigt_met_resultaat(self):
        tm = TaskManager(lambda task, sp, sc, ctx: f"klaar: {task['goal']}", brain=None)
        tid = tm.submit("zoek blauw", owner="span-brain")
        assert _wait(lambda: tm.get(tid)["status"] == "done")
        t = tm.get(tid)
        assert t["result"] == "klaar: zoek blauw"
        assert t["percent"] == 100

    def test_runner_fout_geeft_error_status_zonder_crash(self):
        def boom(task, sp, sc, ctx):
            raise ValueError("nee")

        tm = TaskManager(boom, brain=None)
        tid = tm.submit("x")
        assert _wait(lambda: tm.get(tid)["status"] == "error")
        assert "ValueError" in tm.get(tid)["result"]

    def test_owner_isolatie_in_list_en_active_count(self):
        # runner blijft hangen tot annulering -> taken blijven 'active' voor de telling
        def hold(task, sp, sc, ctx):
            _wait(sc, timeout=2.0)
            return "ok"

        tm = TaskManager(hold, brain=None)
        a = tm.submit("a", owner="brain-a")
        b = tm.submit("b", owner="brain-b")
        assert _wait(lambda: tm.get(a)["status"] == "running")

        ids_a = {t["id"] for t in tm.list(owner="brain-a")}
        assert ids_a == {a}                              # B ziet A's taak niet
        assert {t["id"] for t in tm.list(owner="brain-b")} == {b}
        assert {t["id"] for t in tm.list(owner=None)} == {a, b}   # intern: alles
        assert tm.active_count(owner="brain-a") == 1
        assert tm.active_count(owner="brain-b") == 1
        tm.shutdown()

    def test_lege_owner_is_niet_van_iedereen(self):
        tm = TaskManager(lambda task, sp, sc, ctx: "ok", brain=None)
        tid = tm.submit("geen-owner")  # owner=""
        assert _wait(lambda: tm.get(tid)["status"] == "done")
        assert tm.list(owner="brain-a") == []       # niet zichtbaar voor een gebruiker
        assert {t["id"] for t in tm.list(owner=None)} == {tid}  # wel in de interne lijst

    def test_annuleren_wordt_door_runner_gezien(self):
        def hold(task, sp, sc, ctx):
            _wait(sc, timeout=2.0)
            return "gestopt"

        tm = TaskManager(hold, brain=None)
        tid = tm.submit("lang")
        assert _wait(lambda: tm.get(tid)["status"] == "running")
        assert tm.cancel(tid) is True
        assert _wait(lambda: tm.get(tid)["status"] == "cancelled")

    def test_team_vlag_kiest_team_runner(self):
        tm = TaskManager(lambda task, sp, sc, ctx: "solo",
                         brain=None,
                         team_runner=lambda task, sp, sc, ctx: "team")
        solo = tm.submit("a")
        team = tm.submit("b", team=True)
        assert _wait(lambda: tm.get(solo)["status"] == "done")
        assert _wait(lambda: tm.get(team)["status"] == "done")
        assert tm.get(solo)["result"] == "solo"
        assert tm.get(team)["result"] == "team"

    def test_progress_callback_zet_label_en_percent(self):
        def runner(task, set_progress, sc, ctx):
            set_progress("bezig…", 42)
            return "ok"

        tm = TaskManager(runner, brain=None)
        tid = tm.submit("x")
        assert _wait(lambda: tm.get(tid)["status"] == "done")
        # eindigt op 100, maar het tussenlabel is bewaard in de stappen
        assert "bezig…" in tm.get(tid)["steps"]


# --------------------------------------------------------------------------
# AgentInbox — per-gebruiker zichtbaarheid + goedkeur-isolatie
# --------------------------------------------------------------------------
class TestAgentInboxIsolation:
    def test_snapshot_filtert_op_owner_plus_systeem(self):
        ib = AgentInbox()
        ib.add(kind="action", title="A-mail", owner="brain-a", origin="agent")
        ib.add(kind="action", title="B-mail", owner="brain-b", origin="agent")
        ib.add(kind="notify", title="systeem")  # owner="" -> voor iedereen

        titles_a = {i["title"] for i in ib.snapshot("brain-a")}
        assert titles_a == {"A-mail", "systeem"}      # eigen + systeem, niet B
        assert {i["title"] for i in ib.snapshot("brain-b")} == {"B-mail", "systeem"}
        assert len(ib.snapshot(None)) == 3            # intern: alles

    def test_open_count_is_per_owner(self):
        ib = AgentInbox()
        ib.add(kind="action", title="A", owner="brain-a", origin="agent")
        ib.add(kind="action", title="B", owner="brain-b", origin="agent")
        assert ib.open_count("brain-a") == 1
        assert ib.open_count("brain-b") == 1
        assert ib.open_count(None) == 2

    def test_approvable_by_blokkeert_andermans_actie(self):
        ib = AgentInbox()
        owned = ib.get(ib.add(kind="action", title="A", owner="brain-a", origin="agent"))
        system = ib.get(ib.add(kind="notify", title="sys"))  # owner=""
        assert AgentInbox.approvable_by(owned, "brain-a") is True
        assert AgentInbox.approvable_by(owned, "brain-b") is False   # de hele fix
        assert AgentInbox.approvable_by(system, "brain-b") is True   # systeem mag iedereen


# --------------------------------------------------------------------------
# ToolBox — gequeue'de acties krijgen de eigenaar-tag mee
# --------------------------------------------------------------------------
class TestToolboxOwnerTag:
    def test_owner_uit_brain_database(self):
        brain = MagicMock(database="span-brain")
        tb = ToolBox(brain=brain, fragments=MagicMock(), session_id="s")
        assert tb._owner == "span-brain"

    def test_gequeue_actie_draagt_owner(self):
        brain = MagicMock(database="brain-a")
        inbox = AgentInbox()
        tb = ToolBox(brain=brain, fragments=MagicMock(), session_id="s", inbox=inbox)
        res = tb._tool_mcp_propose_server("server", "https://x.example", "reden")
        assert "proposed" in res
        item = inbox.get(res["proposed"])
        assert item["owner"] == "brain-a"      # getagd met de spawn-gebruiker
        assert item["origin"] == "agent"
        # en dus niet goed te keuren door een andere gebruiker
        assert AgentInbox.approvable_by(item, "brain-b") is False
