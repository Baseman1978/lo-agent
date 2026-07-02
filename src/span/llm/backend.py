"""Chat-backends achter één interface.

De provider-naad van Span. Vandaag: ORQ.AI (OpenAI-compatibel). Straks komt
hier de Claude Agent SDK naast (abonnement-credit) via dezelfde ChatBackend-
vorm, zodat beide naast elkaar bestaan en je per env-flag schakelt
(SPAN_CHAT_BACKEND=orq|sdk) zonder de rest van Span aan te raken.

Contract dat elke backend MOET leveren (chat):
  - geen on_text  -> geef een message-object terug met .content (str|None) en
    .tool_calls (lijst|None; elk .id/.type/.function.name/.function.arguments).
  - met on_text   -> stream elke tekst-delta naar on_text, geef daarna hetzelfde
    message-object terug.
Embeddings horen NIET bij de chat-backend (de SDK levert die niet) — die blijven
op de OpenAI/ORQ-client in LLMClient.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Callable, Protocol

from openai import BadRequestError

# Sommige nieuwere modellen (o.a. claude-opus-4-8) weigeren de temperature-
# parameter (400). We onthouden ze en laten 'temperature' voortaan weg.
_NO_TEMPERATURE: set[str] = set()


def _rejects_temperature(model: str) -> bool:
    return model in _NO_TEMPERATURE or "opus-4-8" in (model or "")


class ChatBackend(Protocol):
    name: str
    def chat(self, messages: list[dict[str, Any]], *, model: str,
             tools: list[dict[str, Any]] | None = None, temperature: float = 0.4,
             max_tokens: int = 4096, on_text: Callable[[str], None] | None = None) -> Any: ...


class OrqChatBackend:
    """Chat via de OpenAI-compatibele ORQ-gateway (het huidige gedrag)."""
    name = "orq"

    def __init__(self, client: Any):
        self._client = client

    def chat(self, messages, *, model, tools=None, temperature=0.4,
             max_tokens=4096, on_text=None):
        kwargs: dict[str, Any] = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if on_text is None:
            return self._create(kwargs).choices[0].message
        return self._chat_stream(kwargs, on_text)

    def _create(self, kwargs: dict[str, Any], *, stream: bool = False) -> Any:
        k = dict(kwargs)
        if _rejects_temperature(k.get("model", "")):
            k.pop("temperature", None)
        try:
            return self._client.chat.completions.create(stream=stream, **k)
        except BadRequestError as exc:
            if "temperature" in str(exc).lower() and "temperature" in k:
                _NO_TEMPERATURE.add(k["model"])
                k.pop("temperature", None)
                try:
                    return self._client.chat.completions.create(stream=stream, **k)
                except BadRequestError as exc2:
                    return self._retry_without_mcp_tools(k, stream, exc2)
            return self._retry_without_mcp_tools(k, stream, exc)

    def _retry_without_mcp_tools(self, k: dict[str, Any], stream: bool,
                                 exc: BadRequestError) -> Any:
        """Fail-soft: één afgekeurde MCP-toolspec mag de beurt niet doden.

        Remote MCP-servers (Notion, Fireflies, …) kunnen op elk moment hun
        schema's wijzigen; wijst de gateway de request daarom af (400), dan
        proberen we één keer opnieuw zónder de MCP-tools. De kern-tools
        blijven werken en de beheerder ziet in de log wat eruit viel
        (productie-uitval 2026-07-02)."""
        tools = k.get("tools") or []
        rest = [t for t in tools
                if not t.get("function", {}).get("name", "").startswith("mcp__")]
        if len(rest) == len(tools):
            raise exc  # geen MCP-tools om te strippen -> echte fout
        import logging
        logging.getLogger("uvicorn.error").warning(
            "modelaanroep 400 (%s) — retry zonder %d MCP-tool(s); "
            "controleer de MCP-schema's", str(exc)[:120], len(tools) - len(rest))
        k = dict(k)
        if rest:
            k["tools"] = rest
        else:
            k.pop("tools", None)
        return self._client.chat.completions.create(stream=stream, **k)

    def _chat_stream(self, kwargs: dict[str, Any], on_text: Callable[[str], None]) -> Any:
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        stream = self._create(kwargs, stream=True)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                on_text(delta.content)
            for tc in delta.tool_calls or []:
                slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        assembled = [
            SimpleNamespace(id=slot["id"], type="function",
                            function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]))
            for _, slot in sorted(tool_calls.items())
        ]
        return SimpleNamespace(content="".join(content_parts) or None,
                               tool_calls=assembled or None)


def select_backend(settings: Any, client: Any) -> ChatBackend:
    """Kies de chat-backend op basis van SPAN_CHAT_BACKEND (default 'orq').
    'sdk' = subscription-first (tekst via de Claude Agent SDK, tool-beurten via
    ORQ, failover naar ORQ bij auth/credit-fout). Valt veilig terug op pure ORQ
    als de SDK niet geïnstalleerd is."""
    orq = OrqChatBackend(client)
    choice = os.environ.get("SPAN_CHAT_BACKEND", "orq").strip().lower()
    if choice == "sdk":
        from span.llm.sdk_backend import SdkChatBackend, RoutedChatBackend, sdk_installed
        if sdk_installed():
            print("[llm] chat-backend: sdk+orq (subscription-first, ORQ-backup)", flush=True)
            return RoutedChatBackend(SdkChatBackend(settings), orq)
        print("[llm] SPAN_CHAT_BACKEND=sdk maar claude-agent-sdk ontbreekt — val terug op orq",
              flush=True)
    return orq
