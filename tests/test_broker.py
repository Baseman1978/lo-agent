"""Integration Broker — register, catalogus, adapters en het approval-pad."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from span.integrations.broker.broker import IntegrationBroker, build_broker
from span.integrations.broker.connectors import (
    get_action, get_connector, list_connectors, needs_approval, Action,
)
from span.integrations.broker.adapters.native import NativeAdapter
from span.integrations.broker.adapters.mcp import MCPAdapter
from span.integrations.broker.adapters.nango import NangoAdapter
import span.integrations.broker.adapters.nango as nango_mod
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


class TestApprovedWrite:
    """WP-A3: goedgekeurde native writes moeten écht uitvoeren + eerlijke status."""

    def test_run_approved_met_dispatch_voert_uit(self):
        b = build_broker()
        cap = {}

        def dispatch(tool, args):
            cap["tool"] = tool; cap["args"] = args
            return '{"created": true}'

        r = b.run_approved({"connector": "asana", "action": "task_create",
                            "payload": {"name": "Nieuwe taak"}}, dispatch=dispatch)
        assert r["status"] == "succeeded"
        assert cap["tool"] == "asana_task_create"
        assert cap["args"] == {"name": "Nieuwe taak"}

    def test_run_approved_zonder_dispatch_meldt_failed(self):
        b = build_broker()
        r = b.run_approved({"connector": "asana", "action": "task_create",
                            "payload": {"name": "X"}})
        assert r["status"] == "failed"          # geen valse 'succeeded' meer
        assert "error" in r["result"]


class TestAutoSkill:
    def test_build_body_splitst_read_write_en_filtert_server(self):
        from span.integrations.broker.autoskill import build_body
        specs = [
            {"function": {"name": "mcp__fireflies__fireflies_search", "description": "Zoek meetings"}},
            {"function": {"name": "mcp__fireflies__fireflies_update_meeting_title", "description": "Wijzig titel"}},
            {"function": {"name": "mcp__other__x", "description": "andere server"}},
        ]
        body = build_body("Fireflies", "fireflies", specs)
        assert "fireflies_search" in body and "Lezen" in body
        assert "fireflies_update_meeting_title" in body and "Schrijven" in body
        assert "mcp__other" not in body and "andere server" not in body
        assert build_body("X", "leeg", []) is None   # geen tools -> geen skill

    def test_sync_mcp_skill_upsert_met_body(self):
        from span.integrations.broker import autoskill
        cap = {}
        brain = MagicMock()
        brain.run.side_effect = lambda q, **kw: cap.update(kw) or []
        specs = [{"function": {"name": "mcp__notion__notion-search", "description": "Zoek"}}]
        out = autoskill.sync_mcp_skill(brain, "notion", "Notion", specs)
        assert out == "notion"
        assert "notion-search" in cap.get("body", "")
        assert cap.get("enabled") is True and cap.get("author") == "agent"


class TestNangoAdapter:
    def test_uit_zonder_env(self):
        a = NangoAdapter(host="", secret="")
        assert a.enabled is False
        c = get_connector("github")
        assert a.is_connected(c, None) is False
        assert "error" in a.run(c, c.actions[0], {}, ctx=None)   # nette melding, geen crash

    def test_is_connected_bevraagt_nango(self, monkeypatch):
        a = NangoAdapter(host="http://nango:3003", secret="s")
        cap = {}

        def fake_get(url, **kw):
            cap["url"] = url; cap["kw"] = kw
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr(nango_mod, "requests", SimpleNamespace(get=fake_get))
        c = get_connector("github")
        assert a.is_connected(c, SimpleNamespace(oid="u1", brain=None)) is True
        assert cap["url"].endswith("/connection/u1")
        assert cap["kw"]["params"]["provider_config_key"] == "github"
        assert cap["kw"]["headers"]["Authorization"] == "Bearer s"

    def test_run_proxyt_via_nango(self, monkeypatch):
        a = NangoAdapter(host="http://nango:3003", secret="s")
        cap = {}

        class R:
            status_code = 200
            def json(self): return {"repos": [1, 2]}
            def raise_for_status(self): pass

        def fake_request(method, url, **kw):
            cap.update(method=method, url=url, kw=kw); return R()

        monkeypatch.setattr(nango_mod, "requests", SimpleNamespace(request=fake_request))
        c = get_connector("github")
        out = a.run(c, c.actions[0], {"per_page": 5}, ctx=SimpleNamespace(oid="u1", brain=None))
        assert cap["method"] == "GET"
        assert cap["url"].endswith("/proxy/user/repos")
        assert cap["kw"]["headers"]["Connection-Id"] == "u1"
        assert cap["kw"]["headers"]["Provider-Config-Key"] == "github"
        assert cap["kw"]["params"] == {"per_page": 5}
        assert out["data"] == {"repos": [1, 2]}
