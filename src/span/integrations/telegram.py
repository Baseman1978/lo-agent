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

from span import AGENT_NAME


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

    def send(self, text: str, chat_id: str = "") -> bool:
        """Stuur een bericht; True alleen als álle chunks zijn aangekomen."""
        target = chat_id or self._chat_id
        if not target:
            return False
        ok = True
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]:
            resp = requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": target, "text": chunk},
                timeout=30,
            )
            if not resp.ok:
                print(f"[telegram] sendMessage {resp.status_code}: {resp.text[:200]}",
                      flush=True)
                ok = False
        return ok

    def send_voice(self, text: str, chat_id: str = "") -> bool:
        """A5: antwoord als Telegram-voice-note (OGG/Opus via PyAV).

        Best-effort: bij élke fout (geen TTS, encode kapot, API-fout) False,
        zodat de aanroeper terugvalt op een gewoon tekstbericht — een
        voice-fout mag nooit een gesprek breken."""
        target = chat_id or self._chat_id
        if not target:
            return False
        if len(text) > 900:
            return False   # lange antwoorden lezen beter als tekst
        try:
            from span.server import tts
            if not tts.available():
                return False
            import time as _time
            from span import telemetry
            from span.integrations import audio as audiomod
            _t0 = _time.perf_counter()
            wav = tts.synthesize(text)
            telemetry.record("tts", (_time.perf_counter() - _t0) * 1000.0,
                             {"mode": "telegram"})
            if not wav:
                return False
            ogg = audiomod.wav_to_ogg_opus(wav)
            resp = requests.post(
                f"{self._base}/sendVoice",
                data={"chat_id": target},
                files={"voice": ("antwoord.ogg", ogg, "audio/ogg")},
                timeout=60,
            )
            if not resp.ok:
                print(f"[telegram] sendVoice {resp.status_code}: {resp.text[:200]}",
                      flush=True)
                return False
            return True
        except Exception as exc:
            print(f"[telegram] voice-antwoord mislukt: {exc}", flush=True)
            return False

    def send_inbox_item(self, item: dict[str, Any]) -> None:
        """F2.3 — stuur een actie-item met inline goedkeur/afwijs-knoppen, zodat
        Bas vanaf zijn telefoon kan goedkeuren. callback_data draagt het item-id."""
        if not self._chat_id:
            return
        iid = item["id"]
        text = f"✓ Ter goedkeuring\n{item['title']}\n{item.get('detail','')[:300]}"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Goedkeuren", "callback_data": f"approve:{iid}"},
            {"text": "✖ Afwijzen", "callback_data": f"reject:{iid}"},
        ]]}
        try:
            requests.post(f"{self._base}/sendMessage",
                          json={"chat_id": self._chat_id, "text": text,
                                "reply_markup": keyboard}, timeout=30)
        except Exception as exc:
            print(f"[telegram] inbox-item sturen mislukt: {exc}", flush=True)

    def _handle_callback(self, cb: dict[str, Any]) -> None:
        """Verwerk een knop-druk (callback_query) op een inbox-item."""
        data = cb.get("data") or ""
        cb_id = cb.get("id")
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat_id != self._chat_id or ":" not in data:
            return
        action, _, iid = data.partition(":")
        inbox = self._state.get("inbox")
        result = ""
        try:
            if inbox is None:
                result = "Geen inbox actief."
            elif action == "reject":
                inbox.resolve(int(iid), "rejected")
                result = "Afgewezen — niets uitgevoerd."
            elif action == "approve":
                claimed = inbox.claim(int(iid))
                if claimed is None:
                    result = "Al afgehandeld."
                else:
                    from span.jarvis.ambient import execute_approval
                    execute_approval(claimed, self._state.get("o365"),
                                     self._state.get("llm"),
                                     self._state["settings"].model_light,
                                     self._state.get("asana"),
                                     self._state.get("mcp"),
                                     self._state.get("brain"))
                    inbox.resolve(int(iid), "done")
                    result = "✅ Uitgevoerd."
        except Exception as exc:
            if inbox is not None:
                inbox.release(int(iid))
            result = f"Mislukt: {exc}"
        # bevestig de knop-druk (anders blijft Telegram 'laden' tonen)
        try:
            requests.post(f"{self._base}/answerCallbackQuery",
                          json={"callback_query_id": cb_id, "text": result}, timeout=15)
        except Exception:
            pass
        self.send(result, chat_id)

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
                telegram=self,  # de bridge zelf: telegram_notify werkt ook hier
                tool_retrieval=self._state.get("tool_retrieval", True),
                tool_retrieval_k=self._state.get("tool_retrieval_k", 24),
            )
            self._session_id = start_session(self._state["brain"])
            self._agent.begin(self._session_id)
        return self._agent

    def _handle_text(self, chat_id: str, text: str) -> None:
        expected = os.environ.get("SPAN_AUTH_TOKEN", "").strip()

        if not self._chat_id:
            if not expected:
                # fail-closed: zonder SPAN_AUTH_TOKEN geen pairing mogelijk
                self.send("Koppelen is uitgeschakeld: er is geen SPAN_AUTH_TOKEN "
                          "ingesteld op de server.", chat_id)
                return
            if text.startswith("/koppel"):
                import hmac
                supplied = text.removeprefix("/koppel").strip()
                if hmac.compare_digest(supplied, expected):
                    self._save_chat_id(chat_id)
                    self.send("Gekoppeld. Ik ben er, waar je ook bent. — " + AGENT_NAME, chat_id)
                else:
                    self.send("Onjuiste code. Gebruik: /koppel <SPAN_AUTH_TOKEN>", chat_id)
            else:
                self.send(AGENT_NAME + " hier. Koppel eerst: /koppel <SPAN_AUTH_TOKEN>", chat_id)
            return

        if chat_id != self._chat_id:
            return  # alleen de gekoppelde chat wordt bediend

        if text.strip() == "/login":
            self._start_o365_login()
            return

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

    def _start_o365_login(self) -> None:
        """O365 opnieuw koppelen vanaf de telefoon: device code via Telegram.
        De code invoeren gebeurt bij Microsoft zelf — er gaat geen wachtwoord
        of token door deze chat."""
        o365 = self._state.get("o365")
        if o365 is None:
            self.send("Microsoft 365 is niet geconfigureerd op de server.")
            return
        if o365.is_authenticated():
            self.send(f"Al ingelogd als {o365.account_name()}.")
            return
        try:
            flow = o365.start_device_flow()
        except Exception as exc:
            self.send(f"Kon geen login starten: {exc}")
            return
        self.send("🔐 " + flow.get("message", "Login gestart."))

        import threading

        def _complete() -> None:
            try:
                account = o365.complete_device_flow(flow)
                self._state["o365_authenticated"] = True
                self.send(f"Ingelogd als {account}. Mail, agenda en taken draaien weer.")
            except Exception as exc:
                self.send(f"Inloggen niet afgerond: {exc}")

        threading.Thread(target=_complete, daemon=True).start()

    def _push_new_inbox_items(self) -> None:
        """Stuur openstaande actie-items die nog niet als knop-bericht gingen."""
        if not self._chat_id:
            return
        inbox = self._state.get("inbox")
        if inbox is None:
            return
        pushed = getattr(self, "_pushed_items", None)
        if pushed is None:
            pushed = self._pushed_items = set()
        for item in inbox.snapshot():
            if (item["status"] == "open" and item["kind"] == "action"
                    and item["id"] not in pushed):
                pushed.add(item["id"])
                self.send_inbox_item(item)
        if len(pushed) > 200:
            self._pushed_items = set(list(pushed)[-100:])

    def _transcribe_voice(self, voice: dict[str, Any]) -> str:
        """Download een Telegram voice-note en transcribeer via server-Whisper."""
        from span.server import stt
        if not stt.available():
            return ""
        fid = voice.get("file_id")
        meta = requests.get(f"{self._base}/getFile",
                            params={"file_id": fid}, timeout=30).json()
        path = (meta.get("result") or {}).get("file_path")
        if not path:
            return ""
        token = self._base.rsplit("/bot", 1)[-1]
        # bounded download (M11): geen onbegrensde body in het geheugen
        max_bytes = 20_000_000
        r = requests.get(f"https://api.telegram.org/file/bot{token}/{path}",
                         timeout=60, stream=True)
        r.raise_for_status()
        audio = r.raw.read(max_bytes + 1, decode_content=True) or b""
        r.close()
        if len(audio) > max_bytes:
            return ""   # te groot -> overslaan i.p.v. geheugen opblazen
        return stt.transcribe(audio)

    # -- hoofd-loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                # dagstart pushen zodra die er voor vandaag is;
                # pas als 'gepusht' markeren wanneer de verzending écht slaagde
                daily = self._state.get("daily")
                if (daily and self._chat_id
                        and daily.get("date") != self._daily_pushed):
                    sent = await asyncio.to_thread(
                        self.send, "🌅 DAGSTART\n\n" + daily.get("spoken", "")
                    )
                    if sent:
                        self._daily_pushed = daily["date"]
                        self._state["brain"].run(
                            "MERGE (c:Config {id:'runtime'}) SET c.last_tg_daily = $d",
                            d=daily["date"],
                        )
                # nieuwe inbox-acties als knop-bericht pushen (F2.3)
                await asyncio.to_thread(self._push_new_inbox_items)
                updates = await asyncio.to_thread(self._get_updates)
                for upd in updates:
                    # offset meteen door: een kapot bericht mag de stroom niet
                    # blokkeren — maar de fout gaat wél terug naar de chat
                    self._offset = max(self._offset, upd.get("update_id", 0))
                    if upd.get("callback_query"):  # knop-druk op een inbox-item
                        try:
                            await asyncio.to_thread(self._handle_callback,
                                                    upd["callback_query"])
                        except Exception as exc:
                            print(f"[telegram] callback-fout: {exc}", flush=True)
                        continue
                    msg = upd.get("message") or {}
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    # voice-note -> server-Whisper -> als tekst behandelen (F2.3)
                    if msg.get("voice") and chat_id:
                        try:
                            text = await asyncio.to_thread(self._transcribe_voice, msg["voice"])
                        except Exception as exc:
                            print(f"[telegram] voice-fout: {exc}", flush=True)
                            text = ""
                    else:
                        text = (msg.get("text") or "").strip()
                    if not text or not chat_id:
                        continue
                    try:
                        await asyncio.to_thread(self._handle_text, chat_id, text)
                    except Exception as exc:
                        print(f"[telegram] bericht-fout: {type(exc).__name__}: {exc}",
                              flush=True)
                        try:
                            await asyncio.to_thread(
                                self.send,
                                f"Daar ging iets mis: {exc}. Probeer het nog eens.",
                                chat_id,
                            )
                        except Exception:
                            pass
            except Exception as exc:
                print(f"[telegram] poll-fout: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(10)  # netwerk-hapering: rustig doorgaan
