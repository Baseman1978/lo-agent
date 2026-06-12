"""LLM-client via ORQ.AI router (OpenAI-compatible).

Chat (met tool-calling) en embeddings lopen allebei door de ORQ gateway,
zodat modelkeuze, kosten en logging op één plek zitten.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

from openai import OpenAI

from span.config import Settings


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = OpenAI(
            api_key=settings.orq_api_key,
            base_url=settings.orq_base_url,
        )

    # -- chat -----------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        on_text: Callable[[str], None] | None = None,
    ) -> Any:
        """Eén chat-completion call. Geeft het message-object terug
        (inclusief eventuele tool_calls).

        Met on_text wordt gestreamd: elke tekst-delta gaat direct naar de
        callback, het volledige message-object komt daarna terug."""
        kwargs: dict[str, Any] = {
            "model": model or self._settings.model_main,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if on_text is None:
            response = self._client.chat.completions.create(**kwargs)
            return response.choices[0].message
        return self._chat_stream(kwargs, on_text)

    def _chat_stream(self, kwargs: dict[str, Any], on_text: Callable[[str], None]) -> Any:
        """Streamt deltas naar on_text en bouwt het message-object zelf op
        (zelfde vorm als het niet-gestreamde object: .content, .tool_calls)."""
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        stream = self._client.chat.completions.create(stream=True, **kwargs)
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
                slot = tool_calls.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        assembled = [
            SimpleNamespace(
                id=slot["id"],
                type="function",
                function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]),
            )
            for _, slot in sorted(tool_calls.items())
        ]
        return SimpleNamespace(
            content="".join(content_parts) or None,
            tool_calls=assembled or None,
        )

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Chat-call die strikt JSON terug moet geven. Strips eventuele
        markdown-codefences en parset; bij parsefout één retry met feedback."""
        msgs = list(messages)
        for attempt in range(2):
            message = self.chat(
                msgs, model=model or self._settings.model_light,
                temperature=temperature, max_tokens=max_tokens,
            )
            text = (message.content or "").strip()
            try:
                return _parse_json_block(text)
            except ValueError as exc:
                if attempt == 1:
                    raise
                msgs = msgs + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"Dat was geen geldige JSON ({exc}). "
                        "Antwoord met uitsluitend één JSON-object, geen tekst eromheen.",
                    },
                ]
        raise RuntimeError("unreachable")

    def list_models(self) -> list[str]:
        """Beschikbare modellen via de ORQ-router; leeg bij fout."""
        try:
            return sorted(m.id for m in self._client.models.list())
        except Exception:
            return []

    # -- embeddings -----------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._settings.embed_model,
            input=texts,
            dimensions=self._settings.embed_dims,
        )
        return [item.embedding for item in response.data]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def _parse_json_block(text: str) -> dict[str, Any]:
    """Parse JSON, ook als het model het in ```json fences zet."""
    candidate = text
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        candidate = "\n".join(lines)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("geen JSON-object gevonden")
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise ValueError("verwachtte een JSON-object, geen array of scalar")
    return parsed
