"""LLM-client: temperature-afhandeling voor modellen die 'm weigeren."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import BadRequestError

from span.config import load_settings
from span.llm import client as C
from span.llm.client import LLMClient, _rejects_temperature


def _reply(text="OK"):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text, tool_calls=None))])


def _llm():
    with patch.object(C, "OpenAI"):
        return LLMClient(load_settings())


def test_opus_4_8_herkend_als_geen_temperature():
    assert _rejects_temperature("anthropic/claude-opus-4-8") is True
    assert _rejects_temperature("aws/eu.anthropic.claude-sonnet-4-5") is False


def test_opus_laat_temperature_weg():
    llm = _llm()
    create = llm._client.chat.completions.create
    create.return_value = _reply()
    llm.chat([{"role": "user", "content": "hoi"}], model="anthropic/claude-opus-4-8")
    assert "temperature" not in create.call_args.kwargs


def test_sonnet_stuurt_temperature_wel():
    llm = _llm()
    create = llm._client.chat.completions.create
    create.return_value = _reply()
    llm.chat([{"role": "user", "content": "hoi"}], model="aws/sonnet", temperature=0.4)
    assert create.call_args.kwargs.get("temperature") == 0.4


def test_leert_van_400_en_retryt_zonder_temperature():
    C._NO_TEMPERATURE.discard("vendor/nieuw-model")
    llm = _llm()
    create = llm._client.chat.completions.create
    err = BadRequestError(
        "Error code: 400 - `temperature` is deprecated for this model.",
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None)
    create.side_effect = [err, _reply("OK")]
    msg = llm.chat([{"role": "user", "content": "hoi"}], model="vendor/nieuw-model")
    assert msg.content == "OK"
    assert create.call_count == 2
    assert "temperature" not in create.call_args.kwargs   # retry zonder temperature
    assert "vendor/nieuw-model" in C._NO_TEMPERATURE       # onthouden


# -- fail-soft: 400 door een kapotte MCP-toolspec (productie-uitval 2026-07-02)

def _err400(msg="Error code: 400 - {'error': 'Invalid request body'}"):
    return BadRequestError(
        msg, response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None)


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": "x",
            "parameters": {"type": "object", "properties": {}}}}


def test_400_retryt_zonder_mcp_tools_en_houdt_kern_tools():
    llm = _llm()
    create = llm._client.chat.completions.create
    create.side_effect = [_err400(), _reply("OK")]
    msg = llm.chat([{"role": "user", "content": "hoi"}], model="aws/sonnet",
                   tools=[_tool("memory_search"), _tool("mcp__notion__notion-search")])
    assert msg.content == "OK"
    assert create.call_count == 2
    namen = [t["function"]["name"] for t in create.call_args.kwargs.get("tools", [])]
    assert namen == ["memory_search"]   # kern-tool blijft, MCP-tool eruit


def test_400_zonder_mcp_tools_blijft_echte_fout():
    import pytest
    llm = _llm()
    create = llm._client.chat.completions.create
    create.side_effect = _err400()
    with pytest.raises(BadRequestError):
        llm.chat([{"role": "user", "content": "hoi"}], model="aws/sonnet",
                 tools=[_tool("memory_search")])
    assert create.call_count == 1       # geen zinloze retry


def test_temperature_400_en_daarna_mcp_400_cascade():
    llm = _llm()
    create = llm._client.chat.completions.create
    create.side_effect = [
        _err400("Error code: 400 - `temperature` is deprecated."),
        _err400(),                       # retry zonder temperature faalt ook
        _reply("OK"),                    # retry zonder MCP-tools slaagt
    ]
    msg = llm.chat([{"role": "user", "content": "hoi"}], model="vendor/model-x",
                   temperature=0.4, tools=[_tool("mcp__notion__notion-fetch")])
    assert msg.content == "OK"
    assert create.call_count == 3
    laatste = create.call_args.kwargs
    assert "temperature" not in laatste
    assert "tools" not in laatste        # enige tool was MCP -> helemaal weg
