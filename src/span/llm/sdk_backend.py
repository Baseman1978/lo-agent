"""Claude Agent SDK als chat-backend (abonnement-credit), met ORQ als backup.

WP-5b van docs/werkplan-sdk-transitie.md. Stand:
- Tekst-generatie (geen tools) kan via de SDK op het Claude-abonnement.
- Tool-beurten gaan (nog) via ORQ: de SDK draait z'n eigen agentic loop, dus de
  tool-loop-met-veiligheidshooks is een aparte, grotere stap (WP-5c). Tot dan
  routeren we tool-beurten transparant naar ORQ.
- Auth/credit-fout op de SDK -> automatische failover naar ORQ (detectie uit de
  spike: de SDK gooit geen exception maar geeft ResultMessage(is_error=True) +
  een auth/credit-melding).

Auth: de SDK pakt CLAUDE_CODE_OAUTH_TOKEN (abonnement). Zet GEEN ANTHROPIC_API_KEY
in de env van dit proces — die wint altijd en dan raakt de credit nooit aan.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable


class SdkUnavailable(RuntimeError):
    """SDK kon de beurt niet leveren (auth/credit/fout) -> val terug op ORQ."""


def sdk_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("claude_agent_sdk") is not None


# trefwoorden die op een auth-/credit-probleem wijzen in de (synthetische)
# SDK-foutmelding -> trigger voor de failover naar ORQ
_FAILOVER_HINTS = ("authenticate", "unauthorized", "401", "invalid bearer",
                   "credit", "quota", "rate limit", "429", "insufficient")


def is_failover_error(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _FAILOVER_HINTS)


class SdkChatBackend:
    """Tekst-chat via de Claude Agent SDK op het abonnement. Geen tools."""
    name = "sdk"

    def __init__(self, settings: Any):
        self._settings = settings

    def chat(self, messages, *, model, tools=None, temperature=0.4,
             max_tokens=4096, on_text=None):
        if tools:
            # de SDK voert tools zelf uit i.p.v. tool_calls terug te geven;
            # tool-beurten horen (nog) bij ORQ -> laat de router dat afhandelen
            raise SdkUnavailable("tool-beurten lopen via ORQ (WP-5c volgt)")
        system, prompt = _split_messages(messages)
        try:
            return asyncio.run(self._run(system, prompt, model, on_text))
        except SdkUnavailable:
            raise
        except Exception as exc:  # import-/CLI-/verbindingsfout -> failover
            raise SdkUnavailable(f"SDK-fout: {type(exc).__name__}: {exc}") from exc

    async def _run(self, system: str, prompt: str, model: str, on_text):
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        opts = ClaudeAgentOptions(
            system_prompt=system or None,
            model=_map_model(model),
            max_turns=1,
            include_partial_messages=True,
        )
        parts: list[str] = []
        async with ClaudeSDKClient(options=opts) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                name = type(msg).__name__
                if name == "ResultMessage" and getattr(msg, "is_error", False):
                    raise SdkUnavailable("SDK ResultMessage is_error (auth/credit?)")
                blocks = getattr(msg, "content", None)
                if isinstance(blocks, list):
                    for b in blocks:
                        txt = getattr(b, "text", None)
                        err = getattr(msg, "error", None)
                        if err and is_failover_error(str(err) + " " + (txt or "")):
                            raise SdkUnavailable(f"SDK auth/credit-fout: {err}")
                        if txt:
                            parts.append(txt)
                            if on_text:
                                on_text(txt)
        return SimpleNamespace(content="".join(parts) or None, tool_calls=None)


class RoutedChatBackend:
    """Subscription-first: tekst via de SDK, tool-beurten via ORQ, en bij een
    SDK-fout transparant terugvallen op ORQ. Eén ChatBackend naar buiten toe."""
    name = "sdk+orq"

    def __init__(self, sdk: SdkChatBackend, orq: Any):
        self._sdk = sdk
        self._orq = orq

    def chat(self, messages, *, model, tools=None, temperature=0.4,
             max_tokens=4096, on_text=None):
        if tools:  # tool-beurt -> ORQ (tot WP-5c)
            return self._orq.chat(messages, model=model, tools=tools,
                                   temperature=temperature, max_tokens=max_tokens, on_text=on_text)
        try:
            return self._sdk.chat(messages, model=model, temperature=temperature,
                                  max_tokens=max_tokens, on_text=on_text)
        except SdkUnavailable as exc:
            print(f"[llm] SDK niet beschikbaar ({exc}) -> failover naar ORQ", flush=True)
            return self._orq.chat(messages, model=model, temperature=temperature,
                                  max_tokens=max_tokens, on_text=on_text)


def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Plat de OpenAI-messages naar (system_prompt, gebruikers-prompt)."""
    system_parts, convo = [], []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):  # cache_control-blokken e.d.
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        content = content or ""
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            convo.append(f"[assistent eerder] {content}")
        else:
            convo.append(content)
    return "\n\n".join(system_parts).strip(), "\n\n".join(convo).strip()


def _map_model(model: str) -> str:
    """ORQ/Bedrock-prefixed id -> kale Anthropic-alias voor de SDK."""
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    return "sonnet"
