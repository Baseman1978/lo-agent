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
        # intern adres: bij autonomy=auto stuurt Span direct (geen exfiltratie-
        # vangnet). Extern adres zou nu altijd via de poort gaan (zie test_safety).
        result = tb.dispatch("o365_mail_send",
                             {"to": ["collega@lomans.nl"], "subject": "T", "body": "B"})
        assert "sent" in result
        o365.send_mail.assert_called_once()

    def test_resolve_en_dubbel_afhandelen(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        iid = inbox.add(kind="notify", title="t")
        assert inbox.resolve(iid, "done")["status"] == "done"
        assert inbox.open_count() == 0
        assert inbox.resolve(iid, "rejected") is None  # geen tweede transitie
        assert inbox.get(iid)["status"] == "done"  # status verandert niet meer

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
        # system-message is content-blocks (cache_control); pak de tekst
        content = agent._messages[0]["content"]
        return content[0]["text"] if isinstance(content, list) else content

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
        from datetime import datetime
        now = datetime(2026, 6, 12, 10, 0)  # vrijdag
        today = now.date().isoformat()  # vergelijk tegen now, niet de echte klok
        base = {"at": "09:30", "last_run": "", "run_date": "", "weekday": -1}
        assert _is_due({**base, "repeat": "daily"}, now)
        assert _is_due({**base, "repeat": "weekdays"}, now)
        assert _is_due({**base, "repeat": "weekly", "weekday": 4}, now)
        assert not _is_due({**base, "repeat": "weekly", "weekday": 2}, now)
        assert not _is_due({**base, "repeat": "daily", "at": "11:00"}, now)  # nog niet
        assert not _is_due({**base, "repeat": "daily",
                            "last_run": today}, now)  # al gedraaid
        assert _is_due({**base, "repeat": "once", "run_date": today}, now)

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


class TestZelflerendSysteem:
    def test_skill_herhaling_verhoogt_usage_count(self):
        from span.evaluation.reflect import reflect_session
        brain = MagicMock()
        brain.run.return_value = []
        fragments = MagicMock()
        fragments.session_fragments.return_value = [{"id": "mf-1", "content": "x"}]
        llm = MagicMock()
        llm.chat_json.return_value = {
            "summary": "s",
            "skills": [{"name": "ssh-agent-fix", "description": "d",
                        "trigger": "t", "body": "b", "source_ids": []}],
        }
        reflect_session(MagicMock(model_main="m"), brain, llm, fragments, "s-1")
        skill_query = next(q.args[0] for q in brain.run.call_args_list
                           if "MERGE (sk:Skill" in q.args[0])
        assert "ON MATCH SET sk.usage_count" in skill_query

    def test_orphan_sessies_worden_gereflecteerd(self):
        from span.jarvis.daily import reflect_orphan_sessions
        brain = MagicMock()
        brain.run.return_value = [{"id": "session-oud"}]
        state = {"brain": brain, "llm": MagicMock(), "inbox": None,
                 "settings": MagicMock()}
        with patch("span.evaluation.reflect.reflect_session",
                   return_value={"summary": "ok", "written": {"insights": ["i-1"]}}) as rs, \
             patch("span.memory.fragments.FragmentStore"):
            assert reflect_orphan_sessions(state) == 1
            rs.assert_called_once()

    def test_orphan_query_filtert_op_leeftijd_en_inhoud(self):
        from span.jarvis.daily import reflect_orphan_sessions
        brain = MagicMock()
        brain.run.return_value = []
        assert reflect_orphan_sessions({"brain": brain, "llm": MagicMock(),
                                        "settings": MagicMock()}) == 0
        q = brain.run.call_args.args[0]
        assert "s.ended IS NULL" in q and "PT3H" in q and "MemoryFragment" in q


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


class TestAuditFixes:
    """De zes risico's uit de multi-agent audit — elk fix heeft een test."""

    # Fix 4: superseded fragmenten worden niet meer teruggegeven
    def test_search_filtert_superseded(self):
        from span.memory.fragments import FragmentStore
        brain = MagicMock()
        brain.vector_search.return_value = [
            {"node": {"id": "mf-1", "type": "observation", "content": "a",
                      "superseded": True}, "score": 0.9},
            {"node": {"id": "mf-2", "type": "observation", "content": "b"}, "score": 0.8},
            {"node": {"id": "mf-3", "type": "observation", "content": "c"}, "score": 0.7},
        ]
        llm = MagicMock()
        llm.embed_one.return_value = [0.1]
        results = FragmentStore(brain, llm).search("vraag", k=2)
        assert [r["id"] for r in results] == ["mf-2", "mf-3"]

    def test_recent_query_sluit_superseded_uit(self):
        from span.memory.fragments import FragmentStore
        brain = MagicMock()
        brain.run.return_value = []
        FragmentStore(brain, MagicMock()).recent(k=5)
        assert "superseded IS NULL" in brain.run.call_args.args[0]

    # Fix 6: Telegram-pairing is fail-closed zonder SPAN_AUTH_TOKEN
    def _bridge(self):
        from span.integrations.telegram import TelegramBridge
        brain = MagicMock()
        brain.run.return_value = []
        bridge = TelegramBridge("tok", {"brain": brain})
        bridge.send = MagicMock()
        return bridge

    def test_pairing_geweigerd_zonder_auth_token(self):
        bridge = self._bridge()
        with patch.dict(os.environ, {"SPAN_AUTH_TOKEN": ""}):
            bridge._handle_text("123", "/koppel watdanook")
        assert not bridge.linked
        assert "uitgeschakeld" in bridge.send.call_args.args[0]

    def test_pairing_geweigerd_met_fout_token(self):
        bridge = self._bridge()
        with patch.dict(os.environ, {"SPAN_AUTH_TOKEN": "geheim"}):
            bridge._handle_text("123", "/koppel fout")
        assert not bridge.linked
        assert "Onjuiste" in bridge.send.call_args.args[0]

    def test_pairing_met_juist_token(self):
        bridge = self._bridge()
        with patch.dict(os.environ, {"SPAN_AUTH_TOKEN": "geheim"}):
            bridge._handle_text("123", "/koppel geheim")
        assert bridge.linked and bridge._chat_id == "123"

    # Fix 3: planner denkt in Nederlandse tijd, ook in een UTC-container
    def test_now_local_is_amsterdams(self):
        from span.jarvis.daily import now_local, today_local, TZ
        now = now_local()
        assert now.tzinfo is not None
        assert str(now.tzinfo) == "Europe/Amsterdam"
        assert today_local() == now.date().isoformat()
        assert TZ.key == "Europe/Amsterdam"

    # Fix 1: formele kennis krijgt embedding en is doorzoekbaar
    def test_reflect_geeft_insight_embedding(self):
        from span.evaluation.reflect import _write_formal_node
        brain = MagicMock()
        llm = MagicMock()
        llm.embed_one.return_value = [0.5, 0.5]
        _write_formal_node(brain, llm, "Insight", "insight-123-1",
                           {"content": "les", "session_id": "s"}, [])
        props = brain.run.call_args_list[0].kwargs["props"]
        assert props["embedding"] == [0.5, 0.5]

    def test_search_formal_combineert_en_sorteert(self):
        from span.memory.fragments import FragmentStore
        brain = MagicMock()
        def fake_vs(index, emb, k):
            if index == "insight_embedding":
                return [{"node": {"id": "insight-1", "content": "a"}, "score": 0.6}]
            if index == "mistake_embedding":
                return [{"node": {"id": "mistake-1", "content": "b",
                                  "lesson": "let op"}, "score": 0.9}]
            return []
        brain.vector_search.side_effect = fake_vs
        llm = MagicMock()
        llm.embed_one.return_value = [0.1]
        results = FragmentStore(brain, llm).search_formal("vraag", k=3)
        assert [r["id"] for r in results] == ["mistake-1", "insight-1"]
        assert results[0]["lesson"] == "let op"

    def test_bootstrap_rendert_inzichten_en_lessen(self):
        from span.memory.bootstrap import BootstrapContext, render_bootstrap
        ctx = BootstrapContext(
            identity={"name": "Span", "philosophy": "p", "origin": "o", "owner": "Bas"},
            protocols=[], quests=[], decisions=[], anti_patterns=[], soul=[], skills=[],
            insights=[{"id": "insight-1", "content": "Bas plant graag vooruit"}],
            lessons=[{"id": "mistake-1", "content": "Te snel gemaild",
                      "lesson": "Eerst checken"}],
        )
        out = render_bootstrap(ctx)
        assert "Inzichten" in out and "Bas plant graag vooruit" in out
        assert "Lessen uit fouten" in out and "Eerst checken" in out

    def test_store_insight_zelfde_schema_als_reflect(self):
        from span.jarvis.daily import _store_insight
        brain = MagicMock()
        llm = MagicMock()
        llm.embed_one.return_value = [0.2]
        _store_insight(brain, llm, "Weekreview: goede week", "weekreview")
        kwargs = brain.run.call_args.kwargs
        assert kwargs["content"] == "Weekreview: goede week"
        assert kwargs["embedding"] == [0.2]
        assert kwargs["id"].startswith("insight-")

    def test_brain_search_tool_levert_ook_formele_kennis(self):
        fragments = MagicMock()
        fragments.embed.return_value = [0.1]
        fragments.search.return_value = [{"id": "mf-1", "score": 0.6, "content": "x"}]
        fragments.search_formal.return_value = [
            {"id": "insight-1", "label": "Insight", "content": "y", "score": 0.7}]
        tb = ToolBox(brain=MagicMock(), fragments=fragments, session_id="s")
        out = json.loads(tb.dispatch("brain_search", {"query": "y"}))
        assert out["formal_knowledge"][0]["id"] == "insight-1"
        assert out["fragments"][0]["id"] == "mf-1"

    # Fix 5: CLI-inbox — afgewezen actie wordt niet uitgevoerd
    def test_cli_inbox_afwijzing_voert_niets_uit(self):
        from span.cli import _handle_inbox
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        inbox.add(kind="action", title="Mail naar Jan", action="mail_send",
                  payload={"to": "jan@x.nl", "subject": "s", "body": "b"})
        o365 = MagicMock()
        with patch("span.cli.console") as fake_console:
            fake_console.input.return_value = "n"
            _handle_inbox(inbox, o365, None, MagicMock(), MagicMock())
        o365.send_mail.assert_not_called()
        assert inbox.snapshot()[0]["status"] == "rejected"

    def test_cli_inbox_akkoord_voert_uit(self):
        from span.cli import _handle_inbox
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        inbox.add(kind="action", title="Mail naar Jan", action="mail_send",
                  payload={"to": "jan@x.nl", "subject": "s", "body": "b"})
        o365 = MagicMock()
        with patch("span.cli.console") as fake_console:
            fake_console.input.return_value = "j"
            _handle_inbox(inbox, o365, None, MagicMock(), MagicMock())
        o365.send_mail.assert_called_once_with("jan@x.nl", "s", "b")
        assert inbox.snapshot()[0]["status"] == "approved"


class TestO365Relogin:
    """Token-refresh kan niet voorbij Lomans' 8-uursbeleid; wel: detectie +
    her-login vanaf de telefoon via Telegram /login."""

    def _bridge(self, state_extra=None):
        from span.integrations.telegram import TelegramBridge
        brain = MagicMock()
        brain.run.return_value = [{"cid": "123", "d": ""}]
        state = {"brain": brain}
        state.update(state_extra or {})
        bridge = TelegramBridge("tok", state)
        bridge._chat_id = "123"
        bridge.send = MagicMock()
        return bridge

    def test_login_zonder_o365_geeft_nette_melding(self):
        bridge = self._bridge({"o365": None})
        bridge._handle_text("123", "/login")
        assert "niet geconfigureerd" in bridge.send.call_args.args[0]

    def test_login_al_ingelogd(self):
        o365 = MagicMock()
        o365.is_authenticated.return_value = True
        o365.account_name.return_value = "b.spaan@lomans.nl"
        bridge = self._bridge({"o365": o365})
        bridge._handle_text("123", "/login")
        assert "Al ingelogd" in bridge.send.call_args.args[0]
        o365.start_device_flow.assert_not_called()

    def test_login_stuurt_device_code_en_rondt_af(self):
        import time as _t
        o365 = MagicMock()
        o365.is_authenticated.return_value = False
        o365.start_device_flow.return_value = {"message": "Ga naar microsoft.com/devicelogin en voer code ABC123 in."}
        o365.complete_device_flow.return_value = "b.spaan@lomans.nl"
        bridge = self._bridge({"o365": o365})
        bridge._handle_text("123", "/login")
        for _ in range(50):  # wacht op de achtergrond-thread
            if bridge.send.call_count >= 2:
                break
            _t.sleep(0.02)
        sent = [c.args[0] for c in bridge.send.call_args_list]
        assert any("ABC123" in s for s in sent)
        assert any("Ingelogd als b.spaan@lomans.nl" in s for s in sent)


class TestVerbeterRonde:
    """Multi-agent audit-verbeterronde: goedkeuringspoort, brein-integriteit,
    geen stil dataverlies, weerbare clients."""

    # Fase 1 — atomaire claim: dubbele uitvoering structureel onmogelijk
    def test_claim_is_atomair(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        iid = inbox.add(kind="action", action="mail_send", title="x",
                        payload={"to": ["a"], "subject": "s", "body": "b"})
        assert inbox.claim(iid) is not None
        assert inbox.claim(iid) is None          # tweede claim faalt
        assert inbox.resolve(iid, "done") is not None
        assert inbox.resolve(iid, "done") is None  # tweede resolve faalt

    def test_release_zet_item_terug_op_open(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        iid = inbox.add(kind="action", action="mail_send", title="x")
        inbox.claim(iid)
        inbox.release(iid)
        assert inbox.get(iid)["status"] == "open"
        assert inbox.claim(iid) is not None  # opnieuw beschikbaar

    def test_agent_kan_eigen_actie_niet_goedkeuren(self):
        from span.jarvis.ambient import AgentInbox
        inbox = AgentInbox()
        o365 = MagicMock()
        tb = ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                     o365=o365, inbox=inbox, autonomy={"mail": "ask"})
        queued = json.loads(tb.dispatch("o365_mail_send",
                                        {"to": ["x@y.nl"], "subject": "S", "body": "B"}))
        result = json.loads(tb.dispatch("inbox_approve", {"item_id": queued["queued"]}))
        assert "error" in result
        o365.send_mail.assert_not_called()
        assert inbox.get(queued["queued"])["status"] == "open"  # blijft voor de HUD

    # Fase 2 — brein-integriteit
    def test_brain_cypher_gebruikt_read_sessie(self):
        brain = MagicMock()
        brain.run_read.return_value = [{"n": 1}]
        tb = ToolBox(brain=brain, fragments=MagicMock(), session_id="s")
        tb.dispatch("brain_cypher", {"query": "MATCH (n) RETURN count(n) AS n"})
        brain.run_read.assert_called_once()
        brain.run.assert_not_called()

    def test_mf_ids_botsen_niet_in_zelfde_milliseconde(self):
        from span.memory.fragments import new_mf_id
        ids = {new_mf_id("observation") for _ in range(50)}
        assert len(ids) == 50

    def test_reflect_ids_deterministisch_per_sessie(self):
        from span.evaluation.reflect import reflect_session
        brain = MagicMock()
        brain.vector_search.return_value = []  # geen near-duplicate
        # _valid_sources matcht mf-1 als bestaand; overige queries -> []
        brain.run.side_effect = lambda q, **kw: (
            [{"id": "mf-1"}] if "WHERE mf.id IN" in q else [])
        fragments = MagicMock()
        fragments.session_fragments.return_value = [{"id": "mf-1", "content": "x"}]
        llm = MagicMock()
        llm.embed_one.return_value = [0.1]
        llm.chat_json.return_value = {
            "summary": "s", "insights": [{"content": "A", "source_ids": ["mf-1"]}],
            "mistakes": [], "ideas": [], "quests": [], "skills": [],
            "protocol_updates": [],
        }
        out1 = reflect_session(MagicMock(), brain, llm, fragments, "session-777")
        out2 = reflect_session(MagicMock(), brain, llm, fragments, "session-777")
        assert out1["written"]["insights"] == out2["written"]["insights"] == ["insight-777-1"]

    def test_reflect_weigert_bronloze_insight(self):
        from span.evaluation.reflect import reflect_session
        brain = MagicMock()
        brain.vector_search.return_value = []  # geen near-duplicate
        brain.run.side_effect = lambda q, **kw: []  # geen enkel fragment bestaat
        fragments = MagicMock()
        fragments.session_fragments.return_value = [{"id": "mf-1", "content": "x"}]
        llm = MagicMock()
        llm.embed_one.return_value = [0.1]
        llm.chat_json.return_value = {
            "summary": "s",
            "insights": [{"content": "Verzonnen inzicht", "source_ids": ["mf-bestaat-niet"]}],
            "mistakes": [], "ideas": [{"content": "los idee", "source_ids": []}],
            "quests": [], "skills": [], "protocol_updates": [],
        }
        out = reflect_session(MagicMock(), brain, llm, fragments, "session-9")
        # Insight zonder geldige bron geweigerd; Idea (bron-loos toegestaan) wel
        assert "insights" not in out["written"]
        assert out["written"].get("ideas") == ["idea-9-1"]

    # Fase 3 — crons: overdue once-cron draait alsnog, markeren ná succes
    def test_once_cron_achterstallig_draait_alsnog(self):
        from span.jarvis.crons import _is_due
        from span.clock import now_local
        now = now_local().replace(hour=23, minute=59)
        cron = {"last_run": "", "at": "08:00", "repeat": "once",
                "run_date": "2020-01-01", "weekday": -1}
        assert _is_due(cron, now)

    def test_cron_blijft_staan_na_mislukte_execute(self):
        from span.jarvis import crons
        brain = MagicMock()
        cron_row = {"id": "cron-x", "text": "doe iets", "at": "00:00",
                    "repeat": "once", "run_date": "2020-01-01", "weekday": -1,
                    "mode": "execute", "last_run": ""}
        brain.run.side_effect = lambda q, **kw: (
            [{"n": 1}] if "attempts" in q else [])
        state = {"brain": brain, "inbox": None, "telegram": None}
        with patch.object(crons, "list_crons", return_value=[cron_row]), \
             patch.object(crons, "_execute", return_value="Uitvoering mislukt: kapot"), \
             patch.object(crons, "delete_cron") as dc:
            ran = crons.run_due_crons(state)
        assert ran == 0
        dc.assert_not_called()  # niet verwijderd: volgende tick een nieuwe poging

    # Fase 5 — retry-helper respecteert Retry-After
    def test_retry_helper_respecteert_retry_after(self):
        from span.integrations.http import request_with_retry
        throttled = MagicMock(status_code=429, headers={"Retry-After": "0"})
        ok = MagicMock(status_code=200, headers={})
        calls = iter([throttled, ok])
        with patch("span.integrations.http.time.sleep") as slp:
            resp = request_with_retry(lambda: next(calls))
        assert resp is ok
        slp.assert_called_once()

    # Settings: keys onafhankelijk — autonomy-POST raakt modellen niet aan
    def test_telegram_send_meldt_falen(self):
        from span.integrations.telegram import TelegramBridge
        brain = MagicMock()
        brain.run.return_value = []
        bridge = TelegramBridge("tok", {"brain": brain})
        bridge._chat_id = "123"
        bad = MagicMock(ok=False, status_code=429, text="throttle")
        with patch("span.integrations.telegram.requests.post", return_value=bad):
            assert bridge.send("hoi") is False
        good = MagicMock(ok=True, status_code=200)
        with patch("span.integrations.telegram.requests.post", return_value=good):
            assert bridge.send("hoi") is True


class TestGeheugenHygiene:
    def test_formal_dedup_hergebruikt_bestaande(self):
        from span.evaluation.reflect import _write_formal_node
        brain = MagicMock()
        brain.vector_search.return_value = [{"node": {"id": "insight-oud"}, "score": 0.97}]
        llm = MagicMock(); llm.embed_one.return_value = [0.1]
        out = _write_formal_node(brain, llm, "Insight", "insight-nieuw",
                                 {"content": "een bekend inzicht"}, ["mf-1"])
        assert out == "insight-oud"  # hergebruikt, niet dubbel geschreven
        assert not [c for c in brain.run.call_args_list if "MERGE (n:Insight" in str(c)]

    def test_formal_dedup_schrijft_nieuw_bij_lage_score(self):
        from span.evaluation.reflect import _write_formal_node
        brain = MagicMock()
        brain.vector_search.return_value = [{"node": {"id": "insight-x"}, "score": 0.4}]
        llm = MagicMock(); llm.embed_one.return_value = [0.1]
        out = _write_formal_node(brain, llm, "Insight", "insight-nieuw",
                                 {"content": "een nieuw inzicht"}, [])
        assert out == "insight-nieuw"
        assert [c for c in brain.run.call_args_list if "MERGE (n:Insight" in str(c)]


class TestPlanner:
    def test_make_plan_decomponeert(self):
        from span.orchestrator.planner import make_plan
        llm = MagicMock()
        llm.chat_json.return_value = {
            "haalbaar": True,
            "stappen": [{"titel": "Verzamel offertes", "klaar_als": "3 offertes binnen"},
                        {"titel": "Vergelijk prijzen", "klaar_als": "tabel klaar"}],
        }
        plan = make_plan(llm, "m", "Kies een leverancier")
        assert plan["haalbaar"] and len(plan["stappen"]) == 2
        assert plan["stappen"][0]["klaar_als"] == "3 offertes binnen"

    def test_make_plan_onhaalbaar(self):
        from span.orchestrator.planner import make_plan
        llm = MagicMock()
        llm.chat_json.return_value = {"haalbaar": False, "notitie": "te vaag"}
        assert make_plan(llm, "m", "doe iets")["haalbaar"] is False

    def test_plan_goal_tool_slaat_quest_op(self):
        from span.jarvis.ambient import AgentInbox
        brain = MagicMock()
        llm = MagicMock()
        llm.chat_json.return_value = {
            "haalbaar": True,
            "stappen": [{"titel": "Stap een", "klaar_als": "x"}]}
        tb = ToolBox(brain=brain, fragments=MagicMock(), session_id="s",
                     llm=llm, inbox=AgentInbox())
        out = json.loads(tb.dispatch("plan_goal", {"goal": "Een groot doel"}))
        assert out["planned"] is True and out["quest_id"].startswith("quest-plan-")
        # er is een Quest aangemaakt
        assert [c for c in brain.run.call_args_list if "CREATE (q:Quest" in str(c)]
