"""A6 — WhatsApp-kanaal via de Meta Cloud API (officieel, geen BSP).

Dunne, wegneembare adapter op de bestaande agent-loop (spec §A6 + §5): geen
kernlogica in deze laag; weghalen = deze module + de webhook-router + drie
wiring-regels in app.py verwijderen. Onofficiële bibliotheken (whatsmeow,
Baileys) zijn verboden. Alleen nummers op WHATSAPP_ALLOWED_NUMBERS worden
bediend; al het andere wordt genegeerd + gelogd. WhatsApp-inhoud is untrusted
input — de guard/risk-governance draait ongewijzigd binnen elke agent.turn().
"""

from __future__ import annotations

import io
import time
from collections import OrderedDict
from typing import Any

import requests

from span.integrations.http import guarded_get, request_with_retry

_GRAPH = "https://graph.facebook.com/v21.0"
_MAX_MEDIA_BYTES = 20_000_000  # zelfde grens als de Telegram-voice-download (M11)
_MAX_TEXT = 4000               # Cloud API-limiet is 4096 tekens per text-body
_DEDUPE_MAX = 500              # Meta levert dubbel bij trage acks -> id-dedupe


class WhatsAppBridge:
    """Chat (laag 1) + spraakmemo's (laag 2). Webhook-gedreven: geen poll-loop."""

    def __init__(self, state: dict[str, Any]):
        self._state = state
        jc = state["settings"].jarvis
        self._token: str = jc.whatsapp_token
        self._phone_id: str = jc.whatsapp_phone_id
        self._allowed: frozenset[str] = jc.whatsapp_allowlist
        self._voice_reply: bool = jc.whatsapp_voice_reply
        self._agent = None
        self._session_id: str | None = None
        self._seen: OrderedDict[str, float] = OrderedDict()

    # -- cloud api ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def send_text(self, to: str, text: str) -> bool:
        """Stuur een tekstbericht; True alleen als álle chunks zijn aangekomen."""
        ok = True
        for chunk in [text[i:i + _MAX_TEXT] for i in range(0, len(text), _MAX_TEXT)] or [""]:
            resp = request_with_retry(
                lambda c=chunk: requests.post(
                    f"{_GRAPH}/{self._phone_id}/messages",
                    headers=self._headers(),
                    json={"messaging_product": "whatsapp", "to": to,
                          "type": "text", "text": {"body": c}},
                    timeout=30,
                ),
                idempotent=False,  # bericht sturen is niet-idempotent (geen blinde retry)
            )
            if not resp.ok:
                print(f"[whatsapp] send_text {resp.status_code}: {resp.text[:200]}",
                      flush=True)
                ok = False
        return ok

    def download_media(self, media_id: str) -> bytes:
        """media-id -> download-URL -> bytes (bounded). Beide hops via guarded_get:
        de media-URL komt uit een API-antwoord en is dus untrusted (egress-check
        vóór de request de deur uit gaat). Te groot of geen URL -> lege bytes."""
        meta = guarded_get(f"{_GRAPH}/{media_id}", headers=self._headers(), timeout=30)
        meta.raise_for_status()
        url = str((meta.json() or {}).get("url") or "")
        if not url:
            return b""
        r = guarded_get(url, headers=self._headers(), timeout=60, stream=True)
        r.raise_for_status()
        # bounded download (M11): geen onbegrensde body in het geheugen
        data = r.raw.read(_MAX_MEDIA_BYTES + 1, decode_content=True) or b""
        r.close()
        if len(data) > _MAX_MEDIA_BYTES:
            return b""  # te groot -> overslaan i.p.v. geheugen opblazen
        return data

    # -- gesprek ---------------------------------------------------------------

    def _ensure_agent(self):
        """Eén lazily gebouwde agent + sessie per bridge (patroon: TelegramBridge).
        De agent erft de volledige guard/risk-governance — deze laag voegt níéts
        aan governance toe en haalt er níéts vanaf."""
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
                telegram=self._state.get("telegram"),
                tool_retrieval=self._state.get("tool_retrieval", True),
                tool_retrieval_k=self._state.get("tool_retrieval_k", 24),
            )
            self._session_id = start_session(self._state["brain"])
            self._agent.begin(self._session_id)
        return self._agent

    def _is_duplicate(self, msg_id: str) -> bool:
        """True als dit wamid al verwerkt is (Meta redelivert bij trage acks)."""
        if not msg_id:
            return False
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = time.time()
        while len(self._seen) > _DEDUPE_MAX:
            self._seen.popitem(last=False)
        return False

    def handle_message(self, msg: dict[str, Any]) -> None:
        """Eén inkomend bericht -> agent-loop -> antwoord. Sync/blocking; de
        webhook draait dit via asyncio.to_thread NA de directe 200-ack."""
        sender = str(msg.get("from") or "")
        if sender not in self._allowed:
            # allowlist = vertrouwensgrens: negeren + loggen, nooit antwoorden
            print(f"[whatsapp] genegeerd: bericht van niet-toegestaan nummer "
                  f"{sender[:6]}…", flush=True)
            return
        if self._is_duplicate(str(msg.get("id") or "")):
            return
        mtype = str(msg.get("type") or "")
        voice_in = False
        if mtype == "text":
            text = str((msg.get("text") or {}).get("body") or "").strip()
        elif mtype == "audio":
            text = self._transcribe_voice(msg.get("audio") or {})
            voice_in = True
        else:
            print(f"[whatsapp] genegeerd: berichttype {mtype!r} wordt niet "
                  f"ondersteund", flush=True)
            return
        if not text:
            if voice_in:
                # spec §5: nooit stilletjes falen richting de gebruiker — als de
                # spraakmemo niet verwerkt kon worden (geen STT, lege download),
                # meld dat eerlijk i.p.v. het bericht te negeren
                self.send_text(sender, "Ik kan spraakmemo's nu niet verwerken — "
                                       "stuur het als tekst.")
            return
        agent = self._ensure_agent()
        answer = agent.turn(text)
        self.send_text(sender, answer)
        if voice_in and self._voice_reply:
            try:
                self.send_voice(sender, answer)
            except Exception as exc:
                # best-effort extraatje: tekst is al verstuurd, gesprek blijft heel
                print(f"[whatsapp] voice-antwoord mislukt: {exc}", flush=True)

    def _transcribe_voice(self, audio: dict[str, Any]) -> str:
        """Inkomende voice-note (audio/ogg) -> bestaande STT. faster-whisper
        decodeert ogg/opus rechtstreeks (PyAV), dus de bytes gaan er onbewerkt
        in — zelfde pad als de Telegram-voice-flow."""
        from span.server import stt
        if not stt.available():
            return ""
        media_id = str(audio.get("id") or "")
        if not media_id:
            return ""
        data = self.download_media(media_id)
        if not data:
            return ""
        t0 = time.perf_counter()
        text = stt.transcribe(data)
        # telemetrie is best-effort (record slikt zelf alle fouten, A1);
        # de WhatsApp-adapter meet zelf omdat hij stt DIRECT aanroept
        from span import telemetry
        telemetry.record("stt", (time.perf_counter() - t0) * 1000.0,
                         {"backend": stt.backend(), "channel": "whatsapp"})
        return text

    # -- laag 2 uit: voice-antwoord --------------------------------------------

    def send_voice(self, to: str, text: str) -> bool:
        """Antwoord als voice-note: TTS (WAV) -> OGG/Opus -> media-upload ->
        audio-bericht. Best-effort extraatje bovenop het tekst-antwoord."""
        from span.server import tts
        if not tts.available():
            return False
        t0 = time.perf_counter()
        wav = tts.synthesize(text)
        from span import telemetry
        telemetry.record("tts", (time.perf_counter() - t0) * 1000.0,
                         {"engine": tts.engine(), "channel": "whatsapp"})
        if not wav:
            return False
        ogg = _wav_to_ogg_opus(wav)
        media_id = self._upload_media(ogg)
        if not media_id:
            return False
        resp = request_with_retry(
            lambda: requests.post(
                f"{_GRAPH}/{self._phone_id}/messages",
                headers=self._headers(),
                json={"messaging_product": "whatsapp", "to": to,
                      "type": "audio", "audio": {"id": media_id}},
                timeout=30,
            ),
            idempotent=False,
        )
        if not resp.ok:
            print(f"[whatsapp] send_voice {resp.status_code}: {resp.text[:200]}",
                  flush=True)
        return bool(resp.ok)

    def _upload_media(self, ogg: bytes) -> str:
        """Upload OGG/Opus-bytes; geeft de media-id terug (leeg bij falen)."""
        resp = request_with_retry(
            lambda: requests.post(
                f"{_GRAPH}/{self._phone_id}/media",
                headers=self._headers(),
                files={"file": ("voice.ogg", ogg, "audio/ogg")},
                data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                timeout=60,
            ),
            idempotent=False,
        )
        if not resp.ok:
            print(f"[whatsapp] media-upload {resp.status_code}: {resp.text[:200]}",
                  flush=True)
            return ""
        return str((resp.json() or {}).get("id") or "")


def _wav_to_ogg_opus(wav: bytes) -> bytes:
    """WAV (uit tts.synthesize, altijd 16-bit PCM) -> OGG/Opus, in-proces via
    PyAV. Er zit geen ffmpeg-binary in de Docker-image, maar `av` (met libopus)
    is gepind in constraints.txt en komt mee met de [stt]-extra. Lazy import:
    in een kale dev-omgeving kan av ontbreken — tests mocken deze functie dan."""
    import av

    src = av.open(io.BytesIO(wav), format="wav")
    buf = io.BytesIO()
    out = av.open(buf, mode="w", format="ogg")
    stream = out.add_stream("libopus", rate=48000)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    for frame in src.decode(audio=0):
        for rf in resampler.resample(frame):
            for pkt in stream.encode(rf):
                out.mux(pkt)
    try:
        tail = resampler.resample(None)  # flush de resampler (PyAV >= 10)
    except Exception:
        tail = []
    for rf in tail:
        for pkt in stream.encode(rf):
            out.mux(pkt)
    for pkt in stream.encode(None):      # flush de encoder
        out.mux(pkt)
    out.close()
    src.close()
    return buf.getvalue()
