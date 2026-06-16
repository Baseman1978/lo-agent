"""SDK-backend: routing (tekst->SDK, tools->ORQ, failover->ORQ) + helpers.
Geen token/netwerk: de SDK-laag wordt gemockt."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from span.llm.sdk_backend import (
    RoutedChatBackend, SdkUnavailable, is_failover_error, _split_messages, _map_model,
)


def test_failover_detectie():
    assert is_failover_error("Failed to authenticate. API Error: 401 Invalid bearer token")
    assert is_failover_error("insufficient credit for this month")
    assert is_failover_error("429 rate limit exceeded")
    assert not is_failover_error("gewoon een normaal antwoord over de zee")


def test_split_messages():
    sys, prompt = _split_messages([
        {"role": "system", "content": "Je bent Span."},
        {"role": "user", "content": "Hoi"},
        {"role": "assistant", "content": "Hallo"},
        {"role": "user", "content": "Hoe gaat het?"},
    ])
    assert sys == "Je bent Span."
    assert "Hoi" in prompt and "Hoe gaat het?" in prompt and "assistent eerder" in prompt


def test_map_model():
    assert _map_model("anthropic/claude-opus-4-8") == "opus"
    assert _map_model("aws/eu.anthropic.claude-haiku-4-5") == "haiku"
    assert _map_model("aws/eu.anthropic.claude-sonnet-4-5") == "sonnet"
    assert _map_model("iets-onbekends") == "sonnet"


def test_tool_beurt_gaat_naar_orq():
    sdk = MagicMock(); orq = MagicMock()
    orq.chat.return_value = SimpleNamespace(content="via orq", tool_calls=[1])
    r = RoutedChatBackend(sdk, orq)
    out = r.chat([{"role": "user", "content": "x"}], model="m", tools=[{"x": 1}])
    assert out.content == "via orq"
    sdk.chat.assert_not_called()          # tools -> nooit naar de SDK
    orq.chat.assert_called_once()


def test_tekst_beurt_gaat_naar_sdk():
    sdk = MagicMock(); orq = MagicMock()
    sdk.chat.return_value = SimpleNamespace(content="via sdk", tool_calls=None)
    r = RoutedChatBackend(sdk, orq)
    out = r.chat([{"role": "user", "content": "vertel iets"}], model="m")
    assert out.content == "via sdk"
    sdk.chat.assert_called_once()
    orq.chat.assert_not_called()


def test_failover_naar_orq_bij_sdk_fout():
    sdk = MagicMock(); orq = MagicMock()
    sdk.chat.side_effect = SdkUnavailable("401 invalid bearer")
    orq.chat.return_value = SimpleNamespace(content="orq-backup", tool_calls=None)
    r = RoutedChatBackend(sdk, orq)
    out = r.chat([{"role": "user", "content": "vertel iets"}], model="m")
    assert out.content == "orq-backup"     # transparant teruggevallen
    orq.chat.assert_called_once()
