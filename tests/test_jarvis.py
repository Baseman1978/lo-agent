"""JARVIS-laag: toolbox-zichtbaarheid, briefing en Asana-client — zonder netwerk."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from span.config import JarvisConfig
from span.integrations.asana import AsanaClient
from span.jarvis.briefing import build_briefing, _greeting
from span.orchestrator.tools import ToolBox, O365_TOOLS, ASANA_TOOLS


def _tool_names(toolbox: ToolBox) -> set[str]:
    return {t["function"]["name"] for t in toolbox.specs()}


class TestToolboxVisibility:
    def test_zonder_integraties_geen_jarvis_tools(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s")
        names = _tool_names(tb)
        assert not (names & O365_TOOLS)
        assert not (names & ASANA_TOOLS)
        assert "jarvis_briefing" not in names
        assert "brain_search" in names

    def test_met_o365_komen_o365_tools_en_briefing(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=MagicMock())
        names = _tool_names(tb)
        assert O365_TOOLS <= names
        assert "jarvis_briefing" in names
        assert not (names & ASANA_TOOLS)

    def test_met_asana_komen_asana_tools(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     asana=MagicMock())
        assert ASANA_TOOLS <= _tool_names(tb)

    def test_tool_zonder_client_geeft_nette_fout(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s")
        result = tb.dispatch("o365_calendar", {})
        assert "MS_CLIENT_ID" in result


class TestBriefing:
    def test_greeting_dagdelen(self):
        from datetime import datetime
        assert _greeting(datetime(2026, 6, 11, 8)) == "Goedemorgen"
        assert _greeting(datetime(2026, 6, 11, 14)) == "Goedemiddag"
        assert _greeting(datetime(2026, 6, 11, 21)) == "Goedenavond"
        assert _greeting(datetime(2026, 6, 11, 3)) == "Goedenacht"

    def test_briefing_zonder_integraties(self):
        brain = MagicMock()
        brain.run.return_value = [{"id": "quest-1", "title": "Test", "status": "open"}]
        b = build_briefing(brain)
        assert "calendar" not in b and "asana" not in b
        assert b["quests"][0]["id"] == "quest-1"
        assert b["errors"] == {}

    def test_briefing_faalt_zacht_per_bron(self):
        brain = MagicMock()
        brain.run.return_value = []
        o365 = MagicMock()
        o365.calendar.side_effect = RuntimeError("graph down")
        o365.inbox.return_value = [{"subject": "hoi", "unread": True},
                                   {"subject": "oud", "unread": False}]
        o365.todo_tasks.return_value = []
        b = build_briefing(brain, o365=o365)
        assert b["calendar"] == []
        assert "calendar" in b["errors"]
        assert len(b["mail"]) == 2
        assert b["unread_mail"] == [{"subject": "hoi", "unread": True}]


class TestAsanaClient:
    def _client(self) -> AsanaClient:
        client = AsanaClient(token="x", workspace_gid="ws-1")
        client._me_gid = "me-1"
        return client

    def test_create_task_payload(self):
        client = self._client()
        with patch.object(client, "_request") as req:
            req.return_value = {"gid": "t-1", "name": "Doe iets", "permalink_url": "u"}
            out = client.create_task("Doe iets", due_on="2026-06-12")
        payload = req.call_args.args[2]
        assert payload["assignee"] == "me-1"
        assert payload["workspace"] == "ws-1"
        assert payload["due_on"] == "2026-06-12"
        assert "notes" not in payload and "projects" not in payload
        assert out["created"] and out["gid"] == "t-1"

    def test_complete_task(self):
        client = self._client()
        with patch.object(client, "_request") as req:
            req.return_value = {"name": "Klaar"}
            out = client.complete_task("t-9")
        assert req.call_args.args[0] == "PUT"
        assert req.call_args.args[1] == "/tasks/t-9"
        assert out["completed"] is True

    def test_slim_taakvorm(self):
        slim = AsanaClient._slim(
            {"gid": "1", "name": "n", "due_on": "2026-06-13",
             "projects": [{"name": "P"}], "permalink_url": "u"}
        )
        assert slim == {"gid": "1", "name": "n", "due": "2026-06-13",
                        "projects": ["P"], "url": "u"}


class TestAgentInbox:
    def test_actie_wacht_op_goedkeuring(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=MagicMock(), inbox=inbox, autonomy={"mail": "ask"})
        result = tb.dispatch("o365_mail_send",
                             {"to": ["x@y.nl"], "subject": "Test", "body": "Hoi"})
        assert "queued" in result
        assert inbox.open_count() == 1
        item = inbox.snapshot()[0]
        assert item["action"] == "mail_send"
        assert item["payload"]["to"] == ["x@y.nl"]

    def test_autonoom_verstuurt_direct(self):
        from span.jarvis.ambient import AgentInbox
        o365 = MagicMock()
        o365.send_mail.return_value = {"sent": True}
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=o365, inbox=AgentInbox(), autonomy={"mail": "auto"})
        result = tb.dispatch("o365_mail_send",
                             {"to": ["x@y.nl"], "subject": "T", "body": "B"})
        assert "sent" in result
        o365.send_mail.assert_called_once()

    def test_resolve_en_dubbel_afhandelen(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        iid = inbox.add(kind="notify", title="t")
        assert inbox.resolve(iid, "done")["status"] == "done"
        assert inbox.open_count() == 0
        again = inbox.resolve(iid, "rejected")
        assert again["status"] == "done"  # status verandert niet meer

    def test_triage_faalt_zacht(self):
        from span.jarvis.ambient import triage_message
        llm = MagicMock()
        llm.chat_json.side_effect = RuntimeError("down")
        out = triage_message(llm, None, {"subject": "Hallo"})
        assert out["action"] == "notify"

    def test_triage_normaliseert_onzin(self):
        from span.jarvis.ambient import triage_message
        llm = MagicMock()
        llm.chat_json.return_value = {"action": "spam", "summary": "x"}
        assert triage_message(llm, None, {})["action"] == "notify"

    def test_injectie_wordt_nooit_automatisch_verwerkt(self):
        from span.jarvis.ambient import triage_message
        llm = MagicMock()
        llm.chat_json.return_value = {"action": "needs_reply", "summary": "s",
                                      "injection": True}
        out = triage_message(llm, None, {"subject": "x"})
        assert out["action"] == "notify"  # gedegradeerd naar melding
        assert out["urgency"] == "high"
        assert "injectie" in out["summary"].lower()


class TestInboxTools:
    def _toolbox_met_item(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        inbox.add(kind="action", action="mail_send", title="Mail aan X",
                  payload={"to": ["x@y.nl"], "subject": "S", "body": "B"})
        o365 = MagicMock()
        o365.send_mail.return_value = {"sent": True}
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=o365, inbox=inbox, autonomy={"mail": "ask"})
        return tb, inbox, o365

    def test_inbox_open_toont_open_items(self):
        tb, _, _ = self._toolbox_met_item()
        result = json.loads(tb.dispatch("inbox_open", {}))
        assert len(result) == 1 and result[0]["title"] == "Mail aan X"

    def test_inbox_approve_voert_uit(self):
        tb, inbox, o365 = self._toolbox_met_item()
        item_id = inbox.snapshot()[0]["id"]
        result = json.loads(tb.dispatch("inbox_approve", {"item_id": item_id}))
        assert result["approved"] is True
        o365.send_mail.assert_called_once_with(["x@y.nl"], "S", "B")
        assert inbox.open_count() == 0

    def test_inbox_reject(self):
        tb, inbox, o365 = self._toolbox_met_item()
        item_id = inbox.snapshot()[0]["id"]
        result = json.loads(tb.dispatch("inbox_reject", {"item_id": item_id}))
        assert result["rejected"] is True
        o365.send_mail.assert_not_called()

    def test_inbox_tools_verborgen_zonder_inbox(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s")
        names = _tool_names(tb)
        assert "inbox_approve" not in names


class TestToolPermissies:
    def test_disabled_tool_verdwijnt_en_weigert(self):
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=MagicMock(), disabled={"o365_mail_send"})
        assert "o365_mail_send" not in _tool_names(tb)
        result = tb.dispatch("o365_mail_send", {"to": ["x"], "subject": "s", "body": "b"})
        assert "uitgeschakeld" in result

    def test_meta_dekt_alle_specs(self):
        from span.orchestrator.tools import TOOL_META, TOOL_SPECS
        spec_names = {t["function"]["name"] for t in TOOL_SPECS}
        assert spec_names == set(TOOL_META.keys())


class TestSystemPromptOverride:
    def _agent_system(self, brain_rows):
        from span.orchestrator.agent import SpanAgent
        brain = MagicMock()
        llm = MagicMock()
        agent = SpanAgent(MagicMock(model_light="m"), brain, llm)
        ctx = MagicMock()
        ctx.identity = {"name": "Span", "owner": "Bas"}
        with patch("span.orchestrator.agent.load_bootstrap", return_value=ctx), \
             patch("span.orchestrator.agent.render_bootstrap", return_value="<<CTX>>"):
            brain.run.return_value = brain_rows
            agent.begin("s-1")
        return agent._messages[0]["content"]

    def test_default_prompt_zonder_override(self):
        system = self._agent_system([{"sp": None}])
        assert "kennispartner van Bas" in system and "<<CTX>>" in system

    def test_override_vervangt_en_vult_plekhouders(self):
        system = self._agent_system(
            [{"sp": "Jij bent {name}, butler van {owner}.\n{bootstrap}"}])
        assert system == "Jij bent Span, butler van Bas.\n<<CTX>>"


class TestDocumenten:
    def test_chunker_overlapt_en_begrenst(self):
        from span.jarvis.documents import chunk_text, CHUNK_SIZE, MAX_CHUNKS
        text = "Dit is een testzin die vaak herhaald wordt. " * 2000
        chunks = chunk_text(text)
        assert 0 < len(chunks) <= MAX_CHUNKS
        assert all(len(c) <= CHUNK_SIZE + 100 for c in chunks)

    def test_extract_txt_en_onbekend_type(self):
        from span.jarvis.documents import extract_text
        assert extract_text("notitie.txt", "hallo wereld".encode()) == "hallo wereld"
        with pytest.raises(ValueError):
            extract_text("foto.png", b"...")

    def test_ingest_schrijft_document_chunks_en_entiteiten(self, tmp_path):
        from span.jarvis import documents
        brain = MagicMock()
        llm = MagicMock()
        llm.chat_json.return_value = {
            "summary": "Samenvatting van het verslag.",
            "entities": [{"name": "Project X", "etype": "project"}],
        }
        state = {"brain": brain, "llm": llm,
                 "settings": MagicMock(model_light="m")}
        with patch("span.memory.fragments.FragmentStore") as FS, \
             patch("span.memory.bootstrap.start_session", return_value="s-1"), \
             patch.object(documents, "DOCS_DIR", tmp_path):
            FS.return_value.write.return_value = "mf-1"
            out = documents.ingest_document(
                state, "verslag.txt",
                ("Belangrijk besluit over koelinstallatie. " * 50).encode())
        assert out["chunks"] >= 1
        assert out["summary"].startswith("Samenvatting")
        queries = [c.args[0] for c in brain.run.call_args_list]
        assert any("CREATE (:Document" in q for q in queries)
        assert any("MENTIONS" in q for q in queries)  # entiteit gekoppeld
        # bestanden op schijf: origineel + markdown
        folder = next(tmp_path.iterdir())
        names = sorted(p.name for p in folder.iterdir())
        assert names == ["verslag.md", "verslag.txt"]


class TestCrons:
    def test_create_valideert(self):
        from span.jarvis.crons import create_cron
        brain = MagicMock()
        out = create_cron(brain, "Offerte checken", "09:00", "once")
        assert out["created"] and out["id"].startswith("cron-")
        with pytest.raises(ValueError):
            create_cron(brain, "x", "09:00", "elke-dag")
        with pytest.raises(ValueError):
            create_cron(brain, "x", "09:00", "weekly")  # weekday ontbreekt

    def test_due_logica(self):
        from span.jarvis.crons import _is_due
        from datetime import datetime, date
        now = datetime(2026, 6, 12, 10, 0)  # vrijdag
        base = {"at": "09:30", "last_run": "", "run_date": "", "weekday": -1}
        assert _is_due({**base, "repeat": "daily"}, now)
        assert _is_due({**base, "repeat": "weekdays"}, now)
        assert _is_due({**base, "repeat": "weekly", "weekday": 4}, now)
        assert not _is_due({**base, "repeat": "weekly", "weekday": 2}, now)
        assert not _is_due({**base, "repeat": "daily", "at": "11:00"}, now)  # nog niet
        assert not _is_due({**base, "repeat": "daily",
                            "last_run": date.today().isoformat()}, now)  # al gedraaid
        assert _is_due({**base, "repeat": "once",
                        "run_date": date.today().isoformat()}, now)

    def test_remind_naar_inbox_en_once_verwijderd(self):
        from span.jarvis.ambient import AgentInbox
        from span.jarvis import crons
        from datetime import date
        brain = MagicMock()
        brain.run.side_effect = [
            [{"id": "cron-1", "text": "Bel Jan", "at": "00:01", "repeat": "once",
              "run_date": date.today().isoformat(), "weekday": -1,
              "mode": "remind", "last_run": ""}],   # list_crons
            [{"n": 1}],                              # delete
        ]
        inbox = AgentInbox()
        state = {"brain": brain, "inbox": inbox}
        assert crons.run_due_crons(state) == 1
        assert inbox.snapshot()[0]["detail"] == "Bel Jan"


class TestTriageRegelsTools:
    def test_get_en_set_via_brein(self):
        brain = MagicMock()
        brain.run.return_value = [{"r": "Facturen negeren."}]
        tb = ToolBox(brain=brain, fragments=MagicMock(), session_id="s")
        out = json.loads(tb.dispatch("triage_rules_get", {}))
        assert out["rules"] == "Facturen negeren."
        out = json.loads(tb.dispatch("triage_rules_set",
                                     {"rules": "Alles van Martijn is urgent."}))
        assert out["saved"] is True
        set_call = brain.run.call_args
        assert "SET c.triage_rules" in set_call.args[0]
        assert set_call.kwargs["r"] == "Alles van Martijn is urgent."


class TestTriageRegels:
    def test_eigen_regels_in_prompt(self):
        from span.jarvis.ambient import triage_message
        llm = MagicMock()
        llm.chat_json.return_value = {"action": "ignore", "summary": "x"}
        triage_message(llm, None, {"subject": "Factuur"}, rules="Facturen altijd negeren.")
        system = llm.chat_json.call_args.args[0][0]["content"]
        assert "Facturen altijd negeren." in system


class TestTouchedTracking:
    def test_brain_search_registreert_touched(self):
        fragments = MagicMock()
        fragments.search.return_value = [
            {"id": "mf-1", "score": 0.8}, {"id": "mf-2", "score": 0.3},
        ]
        tb = ToolBox(brain=MagicMock(), fragments=fragments, session_id="s")
        tb.dispatch("brain_search", {"query": "test"})
        assert tb.touched == ["mf-1"]  # alleen boven de drempel


class TestDaily:
    def test_set_briefing_time_valideert(self):
        from span.jarvis.daily import set_briefing_time
        brain = MagicMock()
        assert set_briefing_time(brain, "06:45") == "06:45"
        assert set_briefing_time(brain, "  ") == "07:00"  # leeg = default
        with pytest.raises(ValueError):
            set_briefing_time(brain, "kwart over zeven")

    def test_generate_daily_met_gesproken_tekst(self):
        from span.jarvis.daily import generate_daily
        brain = MagicMock()
        brain.run.return_value = []
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content="Goedemorgen Bas, rustige dag.")
        out = generate_daily(brain, llm)
        assert out["spoken"] == "Goedemorgen Bas, rustige dag."
        assert "briefing" in out and out["date"]

    def test_generate_daily_faalt_zacht_zonder_llm(self):
        from span.jarvis.daily import generate_daily
        brain = MagicMock()
        brain.run.return_value = []
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("orq down")
        out = generate_daily(brain, llm)
        assert out["spoken"]  # valt terug op greeting


class TestProactief:
    def test_overlappende_afspraken_gedetecteerd(self):
        from span.jarvis.briefing import _overlaps
        events = [
            {"subject": "A", "start": "2026-06-12T10:00:00", "end": "2026-06-12T11:00:00"},
            {"subject": "B", "start": "2026-06-12T10:30:00", "end": "2026-06-12T11:30:00"},
            {"subject": "C", "start": "2026-06-12T12:00:00", "end": "2026-06-12T13:00:00"},
            {"subject": "D", "start": "", "end": "", "all_day": True},
        ]
        conflicts = _overlaps(events)
        assert conflicts == ["A overlapt met B"]

    def test_consolidatie_markeert_duplicaten_en_maakt_insights(self):
        from span.jarvis.daily import consolidate_memory
        brain = MagicMock()
        brain.run.return_value = [
            {"id": f"mf-{i}", "type": "observation", "content": f"feit {i}"}
            for i in range(12)
        ]
        llm = MagicMock()
        llm.chat_json.return_value = {
            "duplicates": [["mf-1", "mf-2", "mf-99"]],  # mf-99 onbekend → genegeerd
            "insights": [{"title": "Patroon", "body": "Bas werkt graag 's ochtends."}],
        }
        result = consolidate_memory(brain, llm)
        assert result["duplicates"] == 1 and result["insights"] == 1

    def test_consolidatie_slaat_over_bij_weinig_fragmenten(self):
        from span.jarvis.daily import consolidate_memory
        brain = MagicMock()
        brain.run.return_value = [{"id": "mf-1", "type": "x", "content": "y"}]
        llm = MagicMock()
        assert consolidate_memory(brain, llm)["duplicates"] == 0
        llm.chat_json.assert_not_called()

    def test_meeting_prep_bevat_geheugen(self):
        from span.jarvis.ambient import build_meeting_prep
        state = {"brain": MagicMock(), "llm": MagicMock()}
        with patch("span.memory.fragments.FragmentStore") as FS:
            FS.return_value.search.return_value = [
                {"content": "Vorige keer besloten: koelvermogen herzien", "score": 0.7},
            ]
            prep = build_meeting_prep(state, {
                "subject": "Overleg project X", "start": "2026-06-12T14:00:00",
                "organizer": "Jan", "location": "Amersfoort",
            })
        assert "14:00" in prep and "Overleg project X" in prep
        assert "koelvermogen" in prep


class TestWeerEnMeetings:
    def test_weather_tool_gebruikt_user_location(self):
        with patch("span.integrations.weather.forecast") as fc:
            fc.return_value = {"locatie": "x"}
            tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                         user_location={"lat": 52.1, "lon": 5.1})
            tb.dispatch("weather", {})
            assert fc.call_args.args[:2] == (52.1, 5.1)

    def test_weather_tool_fallback_amersfoort(self):
        from span.integrations.weather import DEFAULT_LAT
        with patch("span.integrations.weather.forecast") as fc:
            fc.return_value = {}
            tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s")
            tb.dispatch("weather", {})
            assert fc.call_args.args[0] == DEFAULT_LAT

    def test_deelnemer_filter_skipt_meetings_zonder_bas(self):
        from span.jarvis.ambient import AgentInbox
        from span.jarvis.meetings import sync_meetings
        ff = MagicMock()
        ff.recent_transcripts.return_value = [
            {"id": "t-zonder", "title": "Andermans overleg", "date": "2026-06-12",
             "duration_min": 30, "participants": ["jan@x.nl", "piet@x.nl"],
             "overview": "x", "action_items": "", "bullets": ""},
            {"id": "t-met", "title": "Met Bas", "date": "2026-06-12",
             "duration_min": 30, "participants": ["b.spaan@lomans.nl", "jan@x.nl"],
             "overview": "y", "action_items": "", "bullets": ""},
            {"id": "t-eigen", "title": "Eigen opname", "date": "2026-06-12",
             "duration_min": 5, "participants": [],
             "overview": "z", "action_items": "", "bullets": ""},
        ]
        brain = MagicMock()
        brain.run.side_effect = lambda q, **kw: (
            [{"f": "b.spaan@lomans.nl"}] if "ff_filter" in q
            else [] if "RETURN m.ff_id" in q else [])
        state = {"fireflies": ff, "brain": brain, "llm": MagicMock(),
                 "inbox": AgentInbox(), "asana": None,
                 "settings": MagicMock(model_light="m")}
        with patch("span.memory.fragments.FragmentStore"), \
             patch("span.memory.bootstrap.start_session", return_value="s-1"):
            result = sync_meetings(state)
        assert result["new"] == 2  # met-Bas + eigen opname; andermans geskipt

    def test_meetings_sync_idempotent_en_taken_naar_inbox(self):
        from span.jarvis.ambient import AgentInbox
        from span.jarvis.meetings import sync_meetings
        ff = MagicMock()
        ff.recent_transcripts.return_value = [
            {"id": "t1", "title": "Bouwoverleg", "date": "2026-06-12",
             "duration_min": 30, "participants": ["Bas"],
             "overview": "Besproken: planning.", "action_items": "Bas: offerte sturen",
             "bullets": ""},
            {"id": "t-bekend", "title": "Oud", "date": "", "duration_min": 0,
             "participants": [], "overview": "x", "action_items": "", "bullets": ""},
        ]
        brain = MagicMock()
        brain.run.return_value = [{"id": "t-bekend"}]  # al gesynct
        llm = MagicMock()
        llm.chat_json.return_value = {"tasks": [{"name": "Offerte sturen", "notes": "", "due": ""}]}
        inbox = AgentInbox()
        state = {"fireflies": ff, "brain": brain, "llm": llm, "inbox": inbox,
                 "asana": MagicMock(), "settings": MagicMock(model_light="m")}
        with patch("span.memory.fragments.FragmentStore"), \
             patch("span.memory.bootstrap.start_session", return_value="s-1"):
            result = sync_meetings(state)
        assert result == {"new": 1, "tasks": 1}
        item = inbox.snapshot()[0]
        assert item["action"] == "asana_task"
        assert item["payload"]["name"] == "Offerte sturen"

    def test_approve_asana_task_maakt_taak(self):
        from span.jarvis.ambient import execute_approval
        asana = MagicMock()
        asana.create_task.return_value = {"created": True}
        item = {"action": "asana_task", "kind": "action",
                "payload": {"name": "Offerte", "notes": "n", "due_on": ""}}
        out = execute_approval(item, None, asana=asana)
        assert out["created"]
        asana.create_task.assert_called_once_with(name="Offerte", notes="n", due_on="")


class TestJarvisConfig:
    def test_o365_default_aan_via_publieke_client(self):
        from span.config import MS_PUBLIC_CLIENT_ID
        cfg = JarvisConfig()
        assert cfg.o365_enabled
        assert cfg.ms_client_id == MS_PUBLIC_CLIENT_ID
        assert not cfg.asana_enabled

    def test_enabled_vlaggen(self):
        cfg = JarvisConfig(ms_client_id="abc", asana_token="tok")
        assert cfg.o365_enabled and cfg.asana_enabled
        assert cfg.ms_tenant_id == "common"
