"""Rechten per integratie: lezen/schrijven-toggles + per-actie aan/uit."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from span.orchestrator.tools import ToolBox


def _tb(perms=None, mcp=None):
    return ToolBox(brain=MagicMock(), fragments=MagicMock(), session_id="s",
                   perms=perms, mcp=mcp)


def test_zonder_perms_alles_toegestaan():
    tb = _tb()
    assert tb._perm_allowed("o365_mail_inbox") is True
    assert tb._perm_allowed("o365_mail_send") is True
    assert tb._perm_allowed("onbekende_tool") is True   # geen meta -> ongemoeid


def test_lezen_uit_blokkeert_leestools_van_de_groep():
    tb = _tb(perms={"O365 Mail": {"read": False, "write": True}})
    out = json.loads(tb.dispatch("o365_mail_inbox", {}))
    assert "Geen toestemming" in out["error"] and "lezen" in out["error"]
    # andere groepen ongemoeid
    assert tb._perm_allowed("jarvis_briefing") is True


def test_schrijven_uit_blokkeert_alleen_schrijftools():
    tb = _tb(perms={"O365 Mail": {"read": True, "write": False}})
    assert tb._perm_allowed("o365_mail_inbox") is True    # lezen mag nog
    out = json.loads(tb.dispatch("o365_mail_send", {"to": "x"}))
    assert "Geen toestemming" in out["error"] and "schrijven" in out["error"]


def test_specs_verbergen_geblokkeerde_tools():
    tb = _tb(perms={"O365 Mail": {"read": False, "write": False}})
    namen = {t["function"]["name"] for t in tb.specs()}
    assert "o365_mail_send" not in namen
    assert "o365_mail_inbox" not in namen
    assert "brain_search" in namen                        # andere groep blijft


def test_mcp_server_perms_via_mcp_prefix():
    reg = MagicMock()
    reg.tool_specs.return_value = [{
        "type": "function",
        "function": {"name": "mcp__notion__notion-search", "description": "x",
                     "parameters": {"type": "object", "properties": {}}}}]
    tb = _tb(perms={"mcp:notion": {"read": False, "write": True}}, mcp=reg)
    namen = {t["function"]["name"] for t in tb.specs()}
    assert "mcp__notion__notion-search" not in namen      # search = read -> dicht
    out = json.loads(tb.dispatch("mcp__notion__notion-search", {}))
    assert "Geen toestemming" in out["error"]


def test_mcp_schrijftool_volgt_write_toggle():
    reg = MagicMock()
    reg.tool_specs.return_value = []
    tb = _tb(perms={"mcp:notion": {"read": True, "write": False}}, mcp=reg)
    out = json.loads(tb.dispatch("mcp__notion__notion-create-pages", {}))
    assert "Geen toestemming" in out["error"] and "schrijven" in out["error"]
    reg.call.assert_not_called()
