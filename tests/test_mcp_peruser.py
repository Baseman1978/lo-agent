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
