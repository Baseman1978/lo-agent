"""Telegram-bridge — Span op zak.

Zet TELEGRAM_BOT_TOKEN in .env (bot maken via @BotFather). Koppelen:
stuur de bot `/koppel <SPAN_AUTH_TOKEN>`. Daarna is dat de enige chat
die Span bedient; de dagstart wordt er 's ochtends heen gepusht.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import requests


class TelegramBridge:
    def __init__(self, token: str, state: dict[str, Any]):
        self._base = f"https://api.telegram.org/bot{token}"
        self._state = state
        self._offset = 0
        self._chat_id: str = self._load_chat_id()
        self._agent = None
        self._session_id: str | None = None
        rows = state["brain"].run(
            "MATCH (c:Config {id:'runtime'}) RETURN c.last_tg_daily AS d"
        )
        self._daily_pushed: str = (rows[0]["d"] if rows else None) or ""

    # -- pairing ------------------------------------------------------------

    def _load_chat_id(self) -> str:
        rows = self._state["brain"].run(
            "MATCH (c:Config {id:'runtime'}) RETURN c.telegram_chat_id AS cid"
        )
        return str(rows[0]["cid"]) if rows and rows[0]["cid"] else ""

    def _save_chat_id(self, chat_id: str) -> None:
        self._chat_id = chat_id
        self._state["brain"].run(
            "MERGE (c:Config {id:'runtime'}) SET c.telegram_chat_id = $cid", cid=chat_id
        )

    @property
    def linked(self) -> bool:
        return bool(self._chat_id)

    # -- telegram api ----------------------------------------------------------

    def _get_updates(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{self._base}/getUpdates",
            params={"offset": self._offset + 1, "timeout": 50},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    def send(self, text: str, chat_id: str = "") -> None:
        target = chat_id or self._chat_id
        if not target:
            return
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]:
            requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": target, "text": chunk},
                timeout=30,
            )

    # -- gesprek ------------------------------------------------------------

    def _ensure_agent(self):
        from span.memory.bootstrap import start_session
        from span.orchestrator.agent import SpanAgent

        if self._agent is None:
            self._agent = SpanAgent(
                self._state["settings"], self._state["brain"], self._state["llm"],
                self._state.get("work"), o365=self._state.get("o365"),
                asana=self._state.get("asana"), inbox=self._state.get("inbox"),
                autonomy=self._state.get("autonomy"),
                disabled_tools=self._state.get("disabled_tools"),
                fireflies=self._state.get("fireflies"),
            )
            self._session_id = start_session(self._state["brain"])
            self._agent.begin(self._session_id)
        return self._agent

    def _handle_text(self, chat_id: str, text: str) -> None:
        expected = os.environ.get("SPAN_AUTH_TOKEN", "").strip()

        if not self._chat_id:
            if text.startswith("/koppel"):
                supplied = text.removeprefix("/koppel").strip()
                if not expected or supplied == expected:
                    self._save_chat_id(chat_id)
                    self.send("Gekoppeld. Ik ben er, waar je ook bent. — Span", chat_id)
                else:
                    self.send("Onjuiste code. Gebruik: /koppel <SPAN_AUTH_TOKEN>", chat_id)
            else:
                self.send("Span hier. Koppel eerst: /koppel <SPAN_AUTH_TOKEN>", chat_id)
            return

        if chat_id != self._chat_id:
            return  # alleen de gekoppelde chat wordt bediend

        if text.strip() == "/end":
            if self._agent is not None:
                from span.evaluation.reflect import reflect_session
                from span.memory.fragments import FragmentStore
                self._agent.flush_recording()
                fragments = FragmentStore(self._state["brain"], self._state["llm"])
                result = reflect_session(
                    self._state["settings"], self._state["brain"], self._state["llm"],
                    fragments, self._session_id,
                )
                self.send("Sessie geëvalueerd: " + result["summary"])
                self._agent = None
                self._session_id = None
            else:
                self.send("Geen actieve sessie.")
            return

        agent = self._ensure_agent()
        answer = agent.turn(text)
        self.send(answer)

    # -- hoofd-loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                # dagstart pushen zodra die er voor vandaag is
                daily = self._state.get("daily")
                if (daily and self._chat_id
                        and daily.get("date") != self._daily_pushed):
                    self._daily_pushed = daily["date"]
                    self._state["brain"].run(
                        "MERGE (c:Config {id:'runtime'}) SET c.last_tg_daily = $d",
                        d=daily["date"],
                    )
                    await asyncio.to_thread(
                        self.send, "🌅 DAGSTART\n\n" + daily.get("spoken", "")
                    )
                updates = await asyncio.to_thread(self._get_updates)
                for upd in updates:
                    self._offset = max(self._offset, upd.get("update_id", 0))
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    if text and chat_id:
                        await asyncio.to_thread(self._handle_text, chat_id, text)
            except Exception:
                await asyncio.sleep(10)  # netwerk-hapering: rustig doorgaan
