"""LLM-client via ORQ.AI router (OpenAI-compatible).

Chat (met tool-calling) en embeddings lopen allebei door de ORQ gateway,
zodat modelkeuze, kosten en logging op één plek zitten.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from openai import OpenAI

from span.config import Settings
# de chat-laag zit nu achter een verwisselbare backend (ORQ nu, SDK straks).
# _NO_TEMPERATURE/_rejects_temperature hier re-exporteren voor backwards-compat.
from span.llm.backend import (  # noqa: F401
    ChatBackend, OrqChatBackend, select_backend, _NO_TEMPERATURE, _rejects_temperature,
)


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = OpenAI(
            api_key=settings.orq_api_key,
            base_url=settings.orq_base_url,
        )
        # chat -> verwisselbare backend; embeddings blijven op deze OpenAI-client
        self._chat_backend: ChatBackend = select_backend(settings, self._client)

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
        """Eén chat-completion call via de actieve backend. Geeft het message-
        object terug (.content + eventuele .tool_calls). Met on_text wordt
        gestreamd: elke tekst-delta gaat direct naar de callback."""
        return self._chat_backend.chat(
            messages, model=model or self._settings.model_main,
            tools=tools, temperature=temperature, max_tokens=max_tokens, on_text=on_text,
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
