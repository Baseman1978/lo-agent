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


def test_connect_stopt_oid_in_pending(monkeypatch):
    """connect is self-service en bewaart de oid in de pending-state, zodat de
    callback het token in de juiste user-brain kan schrijven."""
    import asyncio

    import span.server.routes as r
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)

    def _boom(_req):
        raise AssertionError("mcp_connect mag _require_owner niet aanroepen")

    monkeypatch.setattr(r, "_require_owner", _boom)
    ctx = MagicMock()
    ctx.brain = "USER-BRAIN"
    ctx.oid = "oid-7"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    monkeypatch.setattr(r, "load_servers",
                        lambda b: [{"name": "fireflies",
                                    "url": "https://api.fireflies.ai/mcp"}])
    # ox en allow_host worden LOKAAL geïmporteerd -> patch de bronmodules
    monkeypatch.setattr("span.safety.egress.allow_host", lambda h: None)
    import span.integrations.mcp_oauth as ox_mod
    monkeypatch.setattr(ox_mod, "discover",
                        lambda url: {"authorization_endpoint": "https://a",
                                     "token_endpoint": "https://t"})
    monkeypatch.setattr(ox_mod, "register_client",
                        lambda meta, ru: {"client_id": "c1"})
    monkeypatch.setattr(ox_mod, "make_pkce", lambda: ("v", "c"))
    monkeypatch.setattr(ox_mod, "authorize_url",
                        lambda *a, **k: "https://auth?x=1")
    monkeypatch.setenv("SPAN_PUBLIC_URL", "https://lo.example")
    monkeypatch.setitem(r._state, "mcp_pending", {})  # schoon + auto-herstel
    req = MagicMock()
    result = asyncio.run(r.mcp_connect(req, "fireflies"))
    assert result == {"authorize_url": "https://auth?x=1"}
    pend = r._state["mcp_pending"]
    assert len(pend) == 1
    entry = next(iter(pend.values()))
    assert entry["oid"] == "oid-7"
    assert entry["name"] == "fireflies"


def test_connect_zonder_ctx_valt_terug_op_globale_brain(monkeypatch):
    """Single-user (geen per-user ctx): lege oid, servers uit _state['brain']."""
    import asyncio

    import span.server.routes as r
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: None)
    seen = {}
    monkeypatch.setitem(r._state, "brain", "GLOBAL-BRAIN")

    def fake_load(b):
        seen["brain"] = b
        return [{"name": "fireflies", "url": "https://api.fireflies.ai/mcp"}]

    monkeypatch.setattr(r, "load_servers", fake_load)
    monkeypatch.setattr("span.safety.egress.allow_host", lambda h: None)
    import span.integrations.mcp_oauth as ox_mod
    monkeypatch.setattr(ox_mod, "discover", lambda url: {"token_endpoint": "https://t"})
    monkeypatch.setattr(ox_mod, "register_client",
                        lambda meta, ru: {"client_id": "c1"})
    monkeypatch.setattr(ox_mod, "make_pkce", lambda: ("v", "c"))
    monkeypatch.setattr(ox_mod, "authorize_url", lambda *a, **k: "https://auth")
    monkeypatch.setenv("SPAN_PUBLIC_URL", "https://lo.example")
    monkeypatch.setitem(r._state, "mcp_pending", {})
    req = MagicMock()
    asyncio.run(r.mcp_connect(req, "fireflies"))
    assert seen["brain"] == "GLOBAL-BRAIN"
    entry = next(iter(r._state["mcp_pending"].values()))
    assert entry["oid"] == ""


def test_callback_schrijft_token_in_juiste_user_brain(monkeypatch):
    """callback resolvet de brain via de oid uit pending, schrijft het token
    daarin en invalideert de user-context (niet de globale rebuild)."""
    import asyncio
    import time as _t

    import span.server.routes as r
    written = {}
    monkeypatch.setattr(r, "load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"}])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: written.update(brain=b, servers=s))
    invalidated = {}
    monkeypatch.setattr(r, "_invalidate_ctx",
                        lambda oid: invalidated.update(oid=oid))
    user_ctx = MagicMock()
    user_ctx.brain = "BRAIN-42"
    registry = MagicMock()
    registry.get.return_value = user_ctx
    monkeypatch.setitem(r._state, "contexts", registry)
    monkeypatch.setitem(r._state, "mcp", None)  # auto-skill-blok: no-op
    monkeypatch.setitem(r._state, "mcp_pending", {"st1": {
        "name": "fireflies", "oid": "oid-42",
        "meta": {"token_endpoint": "https://t"},
        "client_id": "c", "verifier": "v", "redirect_uri": "https://cb",
        "ts": _t.time()}})
    # exchange_code wordt LOKAAL geïmporteerd als ox — patch de bronmodule
    import span.integrations.mcp_oauth as ox_mod
    monkeypatch.setattr(ox_mod, "exchange_code",
                        lambda *a, **k: {"access_token": "TOK",
                                         "refresh_token": "R"})
    resp = asyncio.run(r.mcp_oauth_callback(code="abc", state="st1"))
    assert resp.status_code == 200
    registry.get.assert_called_once_with("oid-42")  # pending-oid stuurt de brain
    assert written["brain"] == "BRAIN-42"
    assert written["servers"][0]["token"] == "TOK"
    assert written["servers"][0]["refresh"] == "R"
    assert invalidated["oid"] == "oid-42"


def test_callback_zonder_oid_valt_terug_op_globale_brain(monkeypatch):
    """Oude/single-user pending zonder oid -> _state['brain'] + invalidatie
    met lege oid (die zelf terugvalt op _rebuild_mcp)."""
    import asyncio
    import time as _t

    import span.server.routes as r
    written = {}
    monkeypatch.setattr(r, "load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"}])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: written.update(brain=b, servers=s))
    invalidated = {}
    monkeypatch.setattr(r, "_invalidate_ctx",
                        lambda oid: invalidated.update(oid=oid))
    monkeypatch.setitem(r._state, "contexts", None)
    monkeypatch.setitem(r._state, "brain", "GLOBAL-BRAIN")
    monkeypatch.setitem(r._state, "mcp", None)
    monkeypatch.setitem(r._state, "mcp_pending", {"st2": {
        "name": "fireflies", "meta": {"token_endpoint": "https://t"},
        "client_id": "c", "verifier": "v", "redirect_uri": "https://cb",
        "ts": _t.time()}})
    import span.integrations.mcp_oauth as ox_mod
    monkeypatch.setattr(ox_mod, "exchange_code",
                        lambda *a, **k: {"access_token": "TOK"})
    asyncio.run(r.mcp_oauth_callback(code="abc", state="st2"))
    assert written["brain"] == "GLOBAL-BRAIN"
    assert invalidated["oid"] == ""


def test_callback_502_lekt_geen_exceptiondetail(monkeypatch):
    """Fout tijdens brain-resolutie -> 502 met generieke tekst; het ruwe
    exception-detail (bv. Neo4j-connectiegegevens) mag NIET naar de browser."""
    import asyncio
    import time as _t

    import span.server.routes as r
    registry = MagicMock()
    registry.get.side_effect = RuntimeError("db down")
    monkeypatch.setitem(r._state, "contexts", registry)
    monkeypatch.setitem(r._state, "mcp_pending", {"st3": {
        "name": "fireflies", "oid": "oid-13",
        "meta": {"token_endpoint": "https://t"},
        "client_id": "c", "verifier": "v", "redirect_uri": "https://cb",
        "ts": _t.time()}})
    import span.integrations.mcp_oauth as ox_mod
    monkeypatch.setattr(ox_mod, "exchange_code",
                        lambda *a, **k: {"access_token": "TOK"})
    resp = asyncio.run(r.mcp_oauth_callback(code="abc", state="st3"))
    assert resp.status_code == 502
    assert b"db down" not in resp.body


def test_fireflies_preset_voegt_juiste_url_toe(monkeypatch):
    import asyncio

    import span.server.routes as r
    saved = {}
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)
    monkeypatch.setattr(r, "load_servers", lambda b: [])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: saved.update(brain=b, servers=s))
    ctx = MagicMock()
    ctx.brain = "B"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    out = asyncio.run(r.mcp_add_fireflies(MagicMock()))
    assert saved["servers"][-1]["name"] == "fireflies"
    assert saved["servers"][-1]["url"] == "https://api.fireflies.ai/mcp"
    assert saved["brain"] == "B"
    assert out["added"] == "fireflies"


def test_fireflies_preset_idempotent(monkeypatch):
    import asyncio

    import span.server.routes as r
    saved = {"called": False}
    monkeypatch.setattr(r, "_require_rest_auth", lambda req: None)
    monkeypatch.setattr(
        r, "load_servers",
        lambda b: [{"name": "fireflies", "url": "https://api.fireflies.ai/mcp"}])
    monkeypatch.setattr(r, "save_servers", lambda b, s: saved.update(called=True))
    ctx = MagicMock()
    ctx.brain = "B"
    monkeypatch.setattr(r, "_mcp_ctx", lambda req: ctx)
    out = asyncio.run(r.mcp_add_fireflies(MagicMock()))
    assert saved["called"] is False          # geen dubbele registratie
    assert out["added"] == "fireflies"
