"""Integration Broker — register, catalogus, adapters en het approval-pad."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from span.integrations.broker.broker import IntegrationBroker, build_broker
from span.integrations.broker.connectors import (
    get_action, get_connector, list_connectors, needs_approval, Action,
)
from span.integrations.broker.adapters.native import NativeAdapter
from span.integrations.broker.adapters.mcp import MCPAdapter
from span.jarvis.ambient import AgentInbox


# -- register ---------------------------------------------------------------
class TestRegistry:
    def test_seed_laadt_en_validate_ok(self):
        # import valideert de seed; demo-connector moet bestaan
        assert get_connector("demo") is not None
        assert get_connector("bestaat-niet") is None

    def test_filters(self):
        assert any(c.id == "gmail" for c in list_connectors(category="email"))
        assert all("write" in c.capabilities for c in list_connectors(capability="write"))
        assert any(c.id == "notion" for c in list_connectors(query="notion"))
        assert list_connectors(query="zzzzz") == []

    def test_get_action(self):
        pair = get_action("demo", "echo")
        assert pair is not None and pair[1].id == "echo"
        assert get_action("demo", "nope") is None
        assert get_action("nope", "echo") is None

    def test_needs_approval_beleid(self):
        never = Action(id="r", name="r", capability="read", approval="never")
        onwrite_w = Action(id="w", name="w", capability="write", approval="on_write")
        onwrite_r = Action(id="r2", name="r2", capability="read", approval="on_write")
        always = Action(id="a", name="a", capability="read", approval="always")
        assert needs_approval(never) is False
        assert needs_approval(onwrite_w) is True
        assert needs_approval(onwrite_r) is False   # read valt niet onder on_write
        assert needs_approval(always) is True


# -- catalogus + connected-vlag --------------------------------------------
class TestCatalog:
    def test_catalog_markeert_mock_als_connected(self):
        b = build_broker()
        cat = b.catalog(ctx=None)
        demo = next(c for c in cat if c["id"] == "demo")
        assert demo["connected"] is True
        assert "actions" not in demo and demo["action_count"] >= 1

    def test_native_graph_connected_hangt_af_van_o365(self):
        b = build_broker()
        teams_zonder = next(c for c in b.catalog(ctx=SimpleNamespace(o365=None))
                            if c["id"] == "ms_teams")
        teams_met = next(c for c in b.catalog(ctx=SimpleNamespace(o365=object()))
                         if c["id"] == "ms_teams")
        assert teams_zonder["connected"] is False
        assert teams_met["connected"] is True


# -- uitvoer + approval-pad -------------------------------------------------
class TestRun:
    def test_read_actie_draait_direct_en_audit(self):
        b = build_broker()
        seen = []
        out = b.run("demo", "echo", {"text": "hoi"}, ctx=None,
                    audit=lambda a, d: seen.append(a))
        assert out["status"] == "succeeded"
        assert out["result"] == {"echo": "hoi"}
        assert seen == ["integration:demo.echo"]

    def test_write_actie_gaat_naar_de_inbox_met_owner(self):
        b = build_broker()
        inbox = AgentInbox()
        out = b.run("demo", "create_note", {"title": "x"}, ctx=None,
                    inbox=inbox, owner="brain-a")
        assert out["status"] == "requires_approval"
        item = inbox.get(out["queued"])
        assert item["action"] == "integration_run"
        assert item["origin"] == "agent"
        assert item["owner"] == "brain-a"                     # per-user isolatie
        assert AgentInbox.approvable_by(item, "brain-b") is False
        # payload bevat genoeg om ná goedkeuring uit te voeren
        assert item["payload"]["connector"] == "demo"

    def test_run_approved_voert_mock_uit(self):
        b = build_broker()
        res = b.run_approved({"connector": "demo", "action": "create_note",
                              "payload": {"title": "Verslag"}})
        assert res["status"] == "succeeded"
        assert res["result"]["created"] is True
        assert res["result"]["title"] == "Verslag"

    def test_write_zonder_inbox_weigert(self):
        b = build_broker()
        out = b.run("demo", "create_note", {"title": "x"}, ctx=None)
        assert "error" in out

    def test_onbekende_actie(self):
        b = build_broker()
        assert "error" in b.run("demo", "nope", {}, ctx=None)


# -- adapters ---------------------------------------------------------------
class TestAdapters:
    def test_native_zonder_tool_binding(self):
        a = NativeAdapter()
        c = get_connector("google_calendar")            # oauth2, geen tool
        act = Action(id="x", name="x", capability="read", tool="")
        with pytest.raises(NotImplementedError):
            a.run(c, act, {}, ctx=None, dispatch=lambda t, ar: "{}")

    def test_native_met_tool_delegeert_naar_dispatch(self):
        a = NativeAdapter()
        c = get_connector("ms_teams")
        act = c.actions[0]                               # search -> o365_teams_search
        called = {}

        def dispatch(tool, args):
            called["tool"] = tool
            return '{"data": ["m1"]}'

        res = a.run(c, act, {"query": "offerte"}, ctx=None, dispatch=dispatch)
        assert called["tool"] == "o365_teams_search"
        assert res == {"data": ["m1"]}

    def test_mcp_adapter_verwijst_naar_fase3(self):
        a = MCPAdapter()
        c = get_connector("notion")
        with pytest.raises(NotImplementedError):
            a.run(c, c.actions[0] if c.actions else Action(id="x", name="x"),
                  {}, ctx=None)
