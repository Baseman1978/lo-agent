# tests/test_mcp_peruser.py
"""Per-user MCP-registry op UserContext."""
from __future__ import annotations

from unittest.mock import MagicMock

from span.server.usercontext import UserContext


def test_usercontext_mcp_lazy_uit_eigen_brain(monkeypatch):
    brain = MagicMock()
    built = {}

    def fake_registry(servers, b):
        built["brain"] = b
        built["servers"] = servers
        return MagicMock(name="registry")

    monkeypatch.setattr("span.server.usercontext.load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"}])
    monkeypatch.setattr("span.server.usercontext.MCPRegistry", fake_registry)

    ctx = UserContext(oid="oid-1", upn="a@b.nl", name="A", brain=brain)
    reg1 = ctx.mcp
    reg2 = ctx.mcp
    assert reg1 is reg2                      # gecachet
    assert built["brain"] is brain           # uit de EIGEN brain
    assert built["servers"][0]["name"] == "fireflies"


def test_usercontext_mcp_failsafe_geeft_none(monkeypatch):
    def boom(_b):
        raise RuntimeError("brain onbereikbaar")

    monkeypatch.setattr("span.server.usercontext.load_servers", boom)

    ctx = UserContext(oid="oid-2", upn="c@d.nl", name="C", brain=MagicMock())
    assert ctx.mcp is None                   # fail-safe: geen crash


def test_agent_bouw_gebruikt_ctx_mcp(monkeypatch):
    import span.server.app as app_mod
    from unittest.mock import MagicMock
    captured = {}

    class FakeAgent:
        def __init__(self, *a, **k):
            captured["mcp"] = k.get("mcp")

    monkeypatch.setattr(app_mod, "SpanAgent", FakeAgent)
    # gedeelde services die de helper uit _state trekt
    for key in ("settings", "llm", "work", "inbox", "autonomy"):
        monkeypatch.setitem(app_mod._state, key, MagicMock())
    ctx = MagicMock()
    ctx.mcp = "USER-REGISTRY"
    app_mod.build_agent(ctx)
    assert captured["mcp"] == "USER-REGISTRY"


def _make_async(value):
    """Bouw een async functie die `value` teruggeeft (voor request.json)."""
    async def _inner(*_a, **_k):
        return value
    return _inner


def test_mcp_add_schrijft_in_eigen_brain(monkeypatch):
    import asyncio

    import span.server.routes as r
    saved = {}
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)
    monkeypatch.setattr(r, "load_servers", lambda b: [])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: saved.update(brain=b, servers=s))
    ctx = MagicMock()
    ctx.brain = "USER-BRAIN"
    ctx.oid = "oid-1"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    req = MagicMock()
    req.json = _make_async({"name": "fireflies",
                            "url": "https://api.fireflies.ai/mcp"})
    result = asyncio.run(r.mcp_add(req))
    assert result == {"added": "fireflies"}
    assert saved["brain"] == "USER-BRAIN"
    assert saved["servers"][-1]["name"] == "fireflies"


def test_mcp_add_is_self_service(monkeypatch):
    """add mag niet meer op _require_owner gaten: owner-guard raises, maar add
    slaagt toch (het gate't nu op _require_rest_auth)."""
    import asyncio

    import span.server.routes as r
    saved = {}
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)

    def _boom(_req):
        raise AssertionError("mcp_add mag _require_owner niet aanroepen")

    monkeypatch.setattr(r, "_require_owner", _boom)
    monkeypatch.setattr(r, "load_servers", lambda b: [])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: saved.update(brain=b, servers=s))
    ctx = MagicMock()
    ctx.brain = "USER-BRAIN"
    ctx.oid = "oid-2"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    req = MagicMock()
    req.json = _make_async({"name": "fireflies",
                            "url": "https://api.fireflies.ai/mcp"})
    result = asyncio.run(r.mcp_add(req))
    assert result == {"added": "fireflies"}


def test_mcp_delete_invalidateert_per_user(monkeypatch):
    """delete gebruikt de eigen brain en invalideert de context via oid."""
    import asyncio

    import span.server.routes as r
    saved = {}
    invalidated = {}
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)
    monkeypatch.setattr(r, "load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"},
                                   {"name": "keep", "url": "https://y"}])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: saved.update(brain=b, servers=s))
    monkeypatch.setattr(r, "_invalidate_ctx",
                        lambda oid: invalidated.update(oid=oid))
    ctx = MagicMock()
    ctx.brain = "USER-BRAIN"
    ctx.oid = "oid-3"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    req = MagicMock()
    result = asyncio.run(r.mcp_delete(req, "fireflies"))
    assert result == {"deleted": "fireflies"}
    assert saved["brain"] == "USER-BRAIN"
    assert [s["name"] for s in saved["servers"]] == ["keep"]
    assert invalidated["oid"] == "oid-3"


def test_invalidate_ctx_gebruikt_contexts_registry(monkeypatch):
    """_invalidate_ctx roept reg.invalidate(oid) op de ContextRegistry aan."""
    import span.server.routes as r
    reg = MagicMock()
    monkeypatch.setitem(r._state, "contexts", reg)
    r._invalidate_ctx("oid-9")
    reg.invalidate.assert_called_once_with("oid-9")


def test_invalidate_ctx_zonder_registry_valt_terug_op_rebuild(monkeypatch):
    """Geen ContextRegistry (single-user) -> herbouw globale registry."""
    import span.server.routes as r
    called = {}
    monkeypatch.setitem(r._state, "contexts", None)
    monkeypatch.setattr(r, "_rebuild_mcp", lambda: called.update(hit=True))
    r._invalidate_ctx("")
    assert called.get("hit") is True
