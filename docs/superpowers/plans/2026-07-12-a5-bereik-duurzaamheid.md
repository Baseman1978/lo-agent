# A5 — Bereik + duurzaamheid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LO is overal bruikbaar (mobiele spraak via HTTPS + iOS, Telegram-voice in én uit) en duurzaam te vertrouwen (bewezen restore-baarheid van het brein + nachtelijke integriteitscontrole op de audit-keten).

**Architecture:** Vier losse, kleine ingrepen op bestaande naden: (1) de QR-flow krijgt de publieke HTTPS-URL uit `SPAN_PUBLIC_URL` via `GET /api/netinfo` (mic vereist een secure context); (2) voice.js forceert het bestaande server-STT-pad wanneer `SpeechRecognition` ontbreekt (iOS/Safari), met een mp4-acceptatie op `/api/stt` omdat iOS `audio/mp4` levert; (3) de Telegram-bridge krijgt reply-in-kind: voice-note in → `sendVoice` uit, met WAV→OGG/Opus in-process via PyAV (geen ffmpeg-binary nodig) en tekst-fallback bij elke fout; (4) `verify_chain` (bestaat al in `span.safety.audit`) wordt een nachttaak in de bestaande `daily_scheduler`, melding via inbox + urgente Telegram-push alleen bij een breuk. De backup-drill wordt een handmatig draaibaar script dat RUNBOOK sectie A automatiseert.

**Tech Stack:** Python 3, FastAPI, pytest, vanilla JS (HUD-statics), PyAV (`av==17.1.0`, zit al in het image via faster-whisper), bash (drill-script). Geen nieuwe dependencies, geen Dockerfile-wijziging.

---

## File Structure

- **Create** `tests/test_bereik.py` — alle A5-Python-tests behalve de chaincheck (netinfo, stt-mp4, audio-conversie, Telegram-voice). Self-contained, geen conftest (repo-conventie).
- **Create** `src/span/integrations/audio.py` — `wav_to_ogg_opus(wav_bytes)` via PyAV; eigen module zodat telegram.py klein blijft en de formaat-kennis herbruikbaar is.
- **Create** `scripts/neo4j-restore-drill.sh` — geautomatiseerde herstel-drill (community-pad), zelfde bash-stijl als `scripts/neo4j-backup.sh`.
- **Modify** `src/span/server/routes.py` — `GET /api/netinfo` (r1249-1266): veld `public_url`; `POST /api/stt` (r924-929): mp4/m4a-magic-bytes toestaan.
- **Modify** `src/span/server/static/settings.js` — QR-handler (r210-223): public_url eerst, LAN-http als fallback met mic-waarschuwing.
- **Modify** `src/span/server/static/voice.js` — `!SR`-blok (r554-557): niet returnen maar server-STT forceren.
- **Modify** `src/span/server/static/index.html` — cache-busting `?v=76` bumpen (r7-8, r500-506) bij elke JS-wijziging.
- **Modify** `src/span/integrations/telegram.py` — `send_voice()`, `_incoming_text()`, `_handle_text(..., as_voice=False)` (r206-208, r314-328).
- **Modify** `src/span/jarvis/daily.py` — `chain_check(state)`, `chaincheck_enabled()`, `CHAINCHECK_TIME = "03:45"`, scheduler-haak naast consolidate (r589-594).
- **Modify** `docs/RUNBOOK-restore.md` — verwijzing naar het drill-script + sectie D (drill-checklist, incl. restic/rsync-laag).
- **Test** `tests/test_bereik.py` (nieuw), `tests/test_safety.py` (chaincheck-tests naast `_fake_audit_brain` r271-289).

**Verboden terrein (andere sessie):** `src/span/telemetry.py` en `tests/test_telemetry.py` — alleen importeren, nooit wijzigen.

**Feature-flags in dit plan (consequent, SPAN_TELEMETRY-stijl: default áán, uitzetten met off/0/false/no):**
- `SPAN_TG_VOICE_REPLY` — voice-antwoord op een voice-note. Default aan, want het pad is volledig best-effort met tekst-fallback; de flag is de kill-switch.
- `SPAN_CHAINCHECK` — nachtelijke ketencontrole. Default aan; kill-switch voor grote breinen of debug.

---

## Task 1: `GET /api/netinfo` levert `public_url`

**Files:**
- Modify: `src/span/server/routes.py` (r1249-1266, functie `netinfo`)
- Test: `tests/test_bereik.py` (nieuw bestand)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bereik.py
"""A5 — bereik + duurzaamheid: QR/HTTPS, iOS-STT, Telegram-voice."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def test_netinfo_geeft_public_url(monkeypatch):
    """De QR-flow heeft een HTTPS-bron nodig: SPAN_PUBLIC_URL via netinfo."""
    import span.server.routes as routes
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setenv("SPAN_PUBLIC_URL", "https://nova.famspaan.nl/")
    monkeypatch.setenv("SPAN_LAN_HOST", "192.168.2.10")
    out = asyncio.run(routes.netinfo(MagicMock()))
    assert out["public_url"] == "https://nova.famspaan.nl"  # slash gestript
    assert out["lan_ip"] == "192.168.2.10"
    assert out["hint"] == ""


def test_netinfo_zonder_public_url_houdt_lan_fallback(monkeypatch):
    """In Docker zonder SPAN_PUBLIC_URL: leeg lan_ip + de bestaande hint."""
    import span.server.routes as routes
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.delenv("SPAN_PUBLIC_URL", raising=False)
    monkeypatch.setenv("SPAN_LAN_HOST", "172.17.0.2")  # container-adres
    out = asyncio.run(routes.netinfo(MagicMock()))
    assert out["public_url"] == ""
    assert out["lan_ip"] == ""          # container-IP is niet bruikbaar
    assert "ipconfig" in out["hint"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bereik.py::test_netinfo_geeft_public_url -v`
Expected: FAIL with `KeyError: 'public_url'`

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/server/routes.py` de volledige `netinfo`-functie (r1249-1266) door:

```python
@router.get("/api/netinfo")
async def netinfo(request: Request) -> dict[str, Any]:
    """Adres voor de QR-code (Span op je telefoon). Voorkeur: de publieke
    HTTPS-URL (SPAN_PUBLIC_URL) — alleen op https werkt de microfoon op
    mobiel (secure-context-eis van de browser). LAN-IP blijft de fallback
    voor puur-lokaal gebruik zonder reverse proxy."""
    _require_rest_auth(request)
    import socket
    public_url = os.environ.get("SPAN_PUBLIC_URL", "").strip().rstrip("/")
    lan_ip = os.environ.get("SPAN_LAN_HOST", "").strip()
    if not lan_ip:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
    # in Docker is het gedetecteerde adres het container-IP — niet bruikbaar
    in_container = lan_ip.startswith("172.") or lan_ip.startswith("10.0.")
    hint = ""
    if in_container and not public_url:
        hint = "vul het LAN-IP van deze pc in (ipconfig)"
    return {"lan_ip": "" if in_container else lan_ip, "port": 8472,
            "public_url": public_url, "hint": hint}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bereik.py -v`
Expected: PASS (beide tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_bereik.py
git commit -m "feat(netinfo): public_url uit SPAN_PUBLIC_URL als HTTPS-bron voor de QR"
```

---

## Task 2: QR-flow wijst naar HTTPS (mic op mobiel)

**Files:**
- Modify: `src/span/server/static/settings.js` (r210-223, de `set-qr-make`-handler)
- Modify: `src/span/server/static/index.html` (r7-8 en r500-506: `?v=76` → `?v=77`)

> Geen pytest — dit is statische HUD-code zonder JS-testharnas in dit repo; verificatie in de browser (Step 3) en op prod (Task 11). Dit volgt het A1-precedent voor niet-TDD-stappen.

- [ ] **Step 1: Herschrijf de QR-handler**

Vervang in `src/span/server/static/settings.js` het volledige blok (r210-223):

```js
  /* -- QR-code: Span op je telefoon ------------------------------------ */
  $("set-qr-make").onclick = () => {
    const ip = $("set-lan-ip").value.trim();
    if (!ip) { $("qr-note").textContent = "Vul eerst het LAN-IP van deze pc in (ipconfig)."; return; }
    localStorage.setItem("span_lan_ip", ip);
    const url = `http://${ip}:8472/?token=${encodeURIComponent(localStorage.getItem("span_token") || "")}`;
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    const box = $("qr-box");
    box.innerHTML = qr.createImgTag(5, 8);
    box.classList.remove("hidden");
    $("qr-note").textContent = `Scan met je telefoon (zelfde wifi): ${url}`;
  };
```

door:

```js
  /* -- QR-code: Span op je telefoon ------------------------------------
     Voorkeur: de publieke HTTPS-URL (SPAN_PUBLIC_URL via /api/netinfo) —
     alleen op https geeft de mobiele browser microfoon-toegang. LAN-http
     blijft de fallback, mét waarschuwing dat de mic dan niet werkt. */
  $("set-qr-make").onclick = async () => {
    const token = encodeURIComponent(localStorage.getItem("span_token") || "");
    let url = "", note = "";
    try {
      const nRes = await fetch("/api/netinfo", { headers: SPAN.authHeaders() });
      if (nRes.ok) {
        const n = await nRes.json();
        if (n.public_url) url = `${n.public_url}/?token=${token}`;
      }
    } catch (e) { /* stil: LAN-fallback hieronder */ }
    if (!url) {
      const ip = $("set-lan-ip").value.trim();
      if (!ip) { $("qr-note").textContent = "Vul eerst het LAN-IP van deze pc in (ipconfig)."; return; }
      localStorage.setItem("span_lan_ip", ip);
      url = `http://${ip}:8472/?token=${token}`;
      note = " Let op: via http werkt de microfoon op je telefoon niet " +
        "(browsers eisen https) — typen werkt wel.";
    }
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    const box = $("qr-box");
    box.innerHTML = qr.createImgTag(5, 8);
    box.classList.remove("hidden");
    $("qr-note").textContent = `Scan met je telefoon: ${url}` + note;
  };
```

- [ ] **Step 2: Bump de cache-busting-versie**

In `src/span/server/static/index.html`: alle negen voorkomens van `?v=76` (r7-8: css, r500-506: js) vervangen door `?v=77` — anders zien mobiele clients de fix niet.

- [ ] **Step 3: Verifieer in de browser**

Start lokaal (het bestaande run-recept), open de HUD → instellingen → "toon QR-code":
- Mét `SPAN_PUBLIC_URL` gezet: QR-URL begint met `https://` en de note toont géén mic-waarschuwing.
- Zonder `SPAN_PUBLIC_URL` en met een ingevuld LAN-IP: QR-URL is `http://<ip>:8472/?token=…` en de note bevat de mic-waarschuwing.
- Token-consumptie aan de telefoonkant is ongewijzigd (jarvis.js r19-30 leest `?token=` en doet `history.replaceState`).

- [ ] **Step 4: Commit**

```bash
git add src/span/server/static/settings.js src/span/server/static/index.html
git commit -m "fix(hud): QR wijst naar HTTPS public_url — mic werkt op mobiel"
```

---

## Task 3: `/api/stt` accepteert iOS mp4/m4a

iOS Safari ondersteunt `audio/webm` niet in MediaRecorder en levert `audio/mp4` (AAC). De magic-byte-allowlist (routes.py r924-929) weigert dat nu met 415 — zonder deze fix is de voice.js-fix (Task 4) op iPhone alsnog dood. PyAV/faster-whisper decodeert mp4/AAC prima.

**Files:**
- Modify: `src/span/server/routes.py` (r924-929, in `speech_to_text`)
- Test: `tests/test_bereik.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_bereik.py
def _stt_request(payload: bytes):
    req = MagicMock()

    async def _body():
        return payload
    req.body = _body
    return req


def test_stt_accepteert_ios_mp4(monkeypatch):
    """iOS Safari MediaRecorder levert audio/mp4 ('ftyp' op offset 4)."""
    import span.server.routes as routes
    import span.server.stt as stt
    monkeypatch.setenv("SPAN_TELEMETRY", "off")   # geen jsonl-schrijfsel in tests
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe", lambda audio, language="nl": "hallo vanaf de iphone")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000
    out = asyncio.run(routes.speech_to_text(_stt_request(mp4)))
    assert out["text"] == "hallo vanaf de iphone"


def test_stt_weigert_onbekend_formaat_nog_steeds(monkeypatch):
    """De allowlist blijft dicht voor niet-audio (M12)."""
    from fastapi import HTTPException
    import span.server.routes as routes
    import span.server.stt as stt
    monkeypatch.setenv("SPAN_TELEMETRY", "off")
    monkeypatch.setattr(routes, "_require_rest_auth", lambda request: None)
    monkeypatch.setattr(stt, "available", lambda: True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.speech_to_text(_stt_request(b"\x00" * 2000)))
    assert exc.value.status_code == 415
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bereik.py::test_stt_accepteert_ios_mp4 -v`
Expected: FAIL — `HTTPException` 415 ("Onbekend audioformaat.") in plaats van een transcript

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/server/routes.py` het allowlist-blok (r924-929):

```python
    head = audio[:4]
    if not (head.startswith(b"\x1aE\xdf\xa3")      # EBML (webm/mkv)
            or head == b"OggS"                       # ogg/opus
            or head == b"RIFF"                       # wav
            or head[:3] == b"ID3" or head[:2] == b"\xff\xfb"):  # mp3
        raise HTTPException(status_code=415, detail="Onbekend audioformaat.")
```

door:

```python
    head = audio[:4]
    if not (head.startswith(b"\x1aE\xdf\xa3")      # EBML (webm/mkv)
            or head == b"OggS"                       # ogg/opus
            or head == b"RIFF"                       # wav
            or audio[4:8] == b"ftyp"                 # mp4/m4a (iOS Safari, AAC)
            or head[:3] == b"ID3" or head[:2] == b"\xff\xfb"):  # mp3
        raise HTTPException(status_code=415, detail="Onbekend audioformaat.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bereik.py -v`
Expected: PASS (alle tests tot nu toe)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_bereik.py
git commit -m "fix(stt): accepteer iOS mp4/m4a op /api/stt (ftyp magic bytes)"
```

---

## Task 4: voice.js zonder SpeechRecognition → server-STT (iOS/Safari)

De `return` op r556 verlaat de hele IIFE: álles daarna (server-STT-machinerie r689-773, mic/wake-knoppen r780-791) wordt nooit gedefinieerd. De fix: bij `!SR` niet stoppen maar het bestaande server-STT-pad forceren — `setVoiceMode()` (r680) heeft daar al de juiste aftakking voor (`if (serverSTT) { ensureSegmentLoop(); return; }`), en `buildRecognizer()` (het enige `new SR()`) wordt dan nooit geraakt.

**Files:**
- Modify: `src/span/server/static/voice.js` (r554-557)
- Modify: `src/span/server/static/index.html` (`?v=77` → `?v=78`)

> Geen pytest — statische HUD-code; verificatie in de browser (Step 3) en op een echte iPhone (Task 11).

- [ ] **Step 1: Vervang het `!SR`-blok**

Vervang in `src/span/server/static/voice.js` (r554-557):

```js
  if (!SR) {
    $("mic").style.display = "none"; $("wake").style.display = "none";
    return;
  }
```

door:

```js
  if (!SR) {
    // iOS/Safari: geen (webkit)SpeechRecognition. Spraak blijft werken via
    // het bestaande server-STT-pad (MediaRecorder -> /api/stt); alleen als
    // óók opnemen onmogelijk is (geen MediaRecorder, of http zonder secure
    // context -> geen mediaDevices) verdwijnen de knoppen echt.
    if (!window.MediaRecorder || !navigator.mediaDevices) {
      $("mic").style.display = "none"; $("wake").style.display = "none";
      return;
    }
    localStorage.setItem("span_stt", "server");  // r689 pikt dit op
  }
```

> Waarom via localStorage en niet een variabele: `let serverSTT` staat pas op r689 (temporal dead zone — eerder toewijzen gooit een ReferenceError) en `switchToServerSTT()` gebruikt exact dezelfde localStorage-sleutel; dit is dus het bestaande mechanisme, geen nieuw. Op iOS levert MediaRecorder `audio/mp4`; het Blob-label "audio/webm" in `onSegmentDone()` (r751) is irrelevant omdat de server op magic bytes keurt (Task 3).

- [ ] **Step 2: Bump de cache-busting-versie**

In `src/span/server/static/index.html`: alle voorkomens van `?v=77` vervangen door `?v=78`.

- [ ] **Step 3: Verifieer in de browser**

- Desktop-Chrome (heeft SR): gedrag ongewijzigd — gespreksmodus start de browser-recognizer.
- Browser zonder SR (Safari op macOS/iOS, of Firefox): de 🎙- en ◉-knoppen blijven zichtbaar; 🎙 aanzetten start de segment-loop en na een gesproken zin verschijnt de transcriptie via `/api/stt` (netwerk-tab: POST met 200).

- [ ] **Step 4: Commit**

```bash
git add src/span/server/static/voice.js src/span/server/static/index.html
git commit -m "fix(voice): iOS/Safari zonder SpeechRecognition valt terug op server-STT i.p.v. mic te verbergen"
```

---

## Task 5: Audio-helper — WAV → OGG/Opus via PyAV

Telegram's `sendVoice` wil OGG/Opus. Er zit bewust géén ffmpeg-binary in het image; PyAV (`av==17.1.0`, al aanwezig als faster-whisper-dependency, gepind in `constraints.txt` r8) heeft de ffmpeg-libs aan boord.

**Files:**
- Create: `src/span/integrations/audio.py`
- Test: `tests/test_bereik.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_bereik.py
def test_wav_naar_ogg_opus():
    """WAV (PCM16, 22.05 kHz zoals Piper) -> OGG/Opus-bytes voor sendVoice."""
    pytest.importorskip("av")  # PyAV zit in het image; lokaal evt. niet
    from span.integrations.audio import wav_to_ogg_opus
    from span.server.tts import _wav
    pcm = b"\x00\x00" * 22050          # 1 seconde stilte, 16-bit mono
    ogg = wav_to_ogg_opus(_wav(pcm, 22050))
    assert ogg[:4] == b"OggS"
    assert len(ogg) > 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bereik.py::test_wav_naar_ogg_opus -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.integrations.audio'` (of SKIP als `av` lokaal ontbreekt — dan draait deze test in de container, zie Step 4)

- [ ] **Step 3: Write minimal implementation**

```python
# src/span/integrations/audio.py
"""Audio-conversie voor integraties (A5 — Telegram voice-notes).

WAV (16-bit PCM) -> OGG/Opus, in-process via PyAV: de ffmpeg-libs zitten in
de av-wheel, dus er is bewust géén ffmpeg-binary in het Docker-image nodig.
Telegram's sendVoice accepteert alleen OGG/Opus als echte voice-note."""
from __future__ import annotations

import io


def wav_to_ogg_opus(wav_bytes: bytes, bitrate: int = 32_000) -> bytes:
    """Zet een WAV-bestand om naar OGG/Opus-bytes.

    Raise't bij kapotte input of een ontbrekende opus-encoder — de aanroeper
    (TelegramBridge.send_voice) vangt dat en valt terug op een tekstbericht."""
    import av

    src = av.open(io.BytesIO(wav_bytes), format="wav")
    buf = io.BytesIO()
    out = av.open(buf, mode="w", format="ogg")
    try:
        stream = out.add_stream("libopus", rate=48000)
        stream.bit_rate = bitrate
        # Opus eist 48 kHz; Piper/XTTS leveren 22.05/24 kHz -> expliciet
        # resamplen (mono is genoeg voor spraak)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        for frame in src.decode(audio=0):
            for rf in resampler.resample(frame):
                for packet in stream.encode(rf):
                    out.mux(packet)
        for rf in resampler.resample(None):     # resampler leegtrekken
            for packet in stream.encode(rf):
                out.mux(packet)
        for packet in stream.encode(None):      # encoder leegtrekken
            out.mux(packet)
    finally:
        out.close()
        src.close()
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bereik.py::test_wav_naar_ogg_opus -v`
Expected: PASS (of SKIP zonder lokale `av` — dan verifiëren in de container: `docker compose exec span python -m pytest tests/test_bereik.py::test_wav_naar_ogg_opus -v`)

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/audio.py tests/test_bereik.py
git commit -m "feat(audio): WAV naar OGG/Opus via PyAV voor Telegram-voice"
```

---

## Task 6: `TelegramBridge.send_voice` — antwoord als voice-note

**Files:**
- Modify: `src/span/integrations/telegram.py` (nieuwe methode ná `send`, r77)
- Test: `tests/test_bereik.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_bereik.py
class TestTelegramVoice:
    """Telegram voice-uit: sendVoice met OGG/Opus, altijd met tekst-fallback."""

    def _bridge(self):
        from span.integrations.telegram import TelegramBridge
        brain = MagicMock()
        # __init__ leest telegram_chat_id ('cid') en last_tg_daily ('d')
        brain.run.return_value = [{"cid": "123", "d": ""}]
        return TelegramBridge("tok", {"brain": brain})

    def test_send_voice_stuurt_ogg_multipart(self, monkeypatch):
        import span.integrations.telegram as tgmod
        import span.integrations.audio as audiomod
        import span.server.tts as tts
        monkeypatch.setenv("SPAN_TELEMETRY", "off")
        monkeypatch.setattr(tts, "available", lambda: True)
        monkeypatch.setattr(tts, "synthesize", lambda text, **kw: b"RIFF....WAVE")
        monkeypatch.setattr(audiomod, "wav_to_ogg_opus",
                            lambda wav, bitrate=32_000: b"OggSfake")
        posted = {}

        def fake_post(url, **kw):
            posted["url"] = url
            posted.update(kw)
            resp = MagicMock()
            resp.ok = True
            return resp

        monkeypatch.setattr(tgmod.requests, "post", fake_post)
        bridge = self._bridge()
        assert bridge.send_voice("hoi bas") is True
        assert posted["url"].endswith("/sendVoice")
        assert posted["data"] == {"chat_id": "123"}
        assert posted["files"]["voice"][1] == b"OggSfake"

    def test_send_voice_faalt_zacht_zonder_tts(self, monkeypatch):
        import span.server.tts as tts
        monkeypatch.setattr(tts, "available", lambda: False)
        bridge = self._bridge()
        assert bridge.send_voice("hoi") is False   # aanroeper valt terug op tekst

    def test_send_voice_weigert_lange_teksten(self, monkeypatch):
        import span.server.tts as tts
        monkeypatch.setattr(tts, "available", lambda: True)
        bridge = self._bridge()
        assert bridge.send_voice("x" * 1000) is False  # lang antwoord leest beter als tekst
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bereik.py::TestTelegramVoice -v`
Expected: FAIL with `AttributeError: 'TelegramBridge' object has no attribute 'send_voice'`

- [ ] **Step 3: Write minimal implementation**

Voeg in `src/span/integrations/telegram.py` direct ná de `send`-methode (r77) toe:

```python
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
```

> Let op: `sendVoice` is multipart (`files=`), afwijkend van de JSON-posts elders in dit bestand — dat is de Bot-API-eis voor bestandsuploads. De telemetrie-import is lazy en `record` slikt zelf alle fouten (A1-contract); geen extra try/except nodig rond die regel.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bereik.py::TestTelegramVoice -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/telegram.py tests/test_bereik.py
git commit -m "feat(telegram): send_voice — antwoord als OGG/Opus voice-note met zachte fallback"
```

---

## Task 7: Reply-in-kind + nette melding bij mislukte transcriptie

Voice-in bestaat al (`_transcribe_voice`, r258-279) maar faalt stil (`return ""` = bericht genegeerd) en de run-loop weet niet dat de input een voice-note was. We factoren de tekst-extractie uit de run-loop naar een testbare methode en geven een `as_voice`-vlag door aan `_handle_text`.

**Files:**
- Modify: `src/span/integrations/telegram.py` (`_handle_text` r161 + r206-208, run-loop r314-328, nieuwe `_incoming_text`)
- Test: `tests/test_bereik.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_bereik.py (binnen TestTelegramVoice)
    def test_voice_note_krijgt_voice_antwoord(self, monkeypatch):
        monkeypatch.setenv("SPAN_TG_VOICE_REPLY", "on")
        bridge = self._bridge()
        agent = MagicMock()
        agent.turn.return_value = "antwoord"
        bridge._agent = agent                     # _ensure_agent geeft deze terug
        bridge.send = MagicMock()
        bridge.send_voice = MagicMock(return_value=True)
        bridge._handle_text("123", "hoi", True)   # as_voice=True
        bridge.send_voice.assert_called_once_with("antwoord")
        bridge.send.assert_not_called()

    def test_voice_antwoord_valt_terug_op_tekst(self, monkeypatch):
        monkeypatch.setenv("SPAN_TG_VOICE_REPLY", "on")
        bridge = self._bridge()
        agent = MagicMock()
        agent.turn.return_value = "antwoord"
        bridge._agent = agent
        bridge.send = MagicMock()
        bridge.send_voice = MagicMock(return_value=False)  # encode/API kapot
        bridge._handle_text("123", "hoi", True)
        bridge.send.assert_called_once_with("antwoord")

    def test_flag_off_geeft_altijd_tekst(self, monkeypatch):
        monkeypatch.setenv("SPAN_TG_VOICE_REPLY", "off")
        bridge = self._bridge()
        agent = MagicMock()
        agent.turn.return_value = "antwoord"
        bridge._agent = agent
        bridge.send = MagicMock()
        bridge.send_voice = MagicMock()
        bridge._handle_text("123", "hoi", True)
        bridge.send_voice.assert_not_called()
        bridge.send.assert_called_once_with("antwoord")

    def test_mislukte_transcriptie_geeft_nette_melding(self, monkeypatch):
        bridge = self._bridge()
        bridge.send = MagicMock()
        monkeypatch.setattr(bridge, "_transcribe_voice", lambda voice: "")
        text, was_voice = bridge._incoming_text({"voice": {"file_id": "f1"}}, "123")
        assert text == "" and was_voice is True
        assert "spraakbericht" in bridge.send.call_args.args[0]

    def test_gewone_tekst_blijft_gewoon_tekst(self):
        bridge = self._bridge()
        text, was_voice = bridge._incoming_text({"text": " hoi "}, "123")
        assert text == "hoi" and was_voice is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bereik.py::TestTelegramVoice -v`
Expected: FAIL — `_handle_text` accepteert geen derde argument en `_incoming_text` bestaat niet

- [ ] **Step 3: Write minimal implementation**

**(a)** Flag-helper, module-niveau in `src/span/integrations/telegram.py` (onder de imports, ná r17):

```python
def _voice_reply_enabled() -> bool:
    """A5-flag SPAN_TG_VOICE_REPLY: voice-antwoord op een voice-note.
    Default aan (het pad is best-effort met tekst-fallback); kill-switch
    in dezelfde stijl als SPAN_TELEMETRY."""
    val = os.environ.get("SPAN_TG_VOICE_REPLY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}
```

**(b)** Signatuur + staart van `_handle_text` (r161 en r206-208). Signatuur wordt:

```python
    def _handle_text(self, chat_id: str, text: str, as_voice: bool = False) -> None:
```

en de laatste drie regels van de methode:

```python
        agent = self._ensure_agent()
        answer = agent.turn(text)
        self.send(answer)
```

worden:

```python
        agent = self._ensure_agent()
        answer = agent.turn(text)
        # reply-in-kind: een ingesproken vraag krijgt een ingesproken antwoord;
        # elke fout in het voice-pad valt geruisloos terug op tekst
        if as_voice and _voice_reply_enabled() and self.send_voice(answer):
            return
        self.send(answer)
```

**(c)** Nieuwe methode ná `_transcribe_voice` (r279):

```python
    def _incoming_text(self, msg: dict[str, Any], chat_id: str) -> tuple[str, bool]:
        """Tekst uit een update halen; voice-notes gaan door server-Whisper.
        Lege transcriptie -> nette melding i.p.v. de oude stille drop.
        Geeft (tekst, was_voice) terug."""
        if msg.get("voice") and chat_id:
            try:
                text = self._transcribe_voice(msg["voice"])
            except Exception as exc:
                print(f"[telegram] voice-fout: {exc}", flush=True)
                text = ""
            if not text:
                self.send("Ik kon je spraakbericht niet omzetten naar tekst "
                          "(opname te kort, te groot, of server-STT niet "
                          "beschikbaar). Typ je vraag, of probeer het opnieuw.",
                          chat_id)
            return text, True
        return (msg.get("text") or "").strip(), False
```

**(d)** Run-loop (r314-328): vervang het hele voice/tekst-blok

```python
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
```

door:

```python
                    msg = upd.get("message") or {}
                    chat_id = str((msg.get("chat") or {}).get("id", ""))
                    # voice-note -> server-Whisper; A5: melding bij mislukking
                    # en de was_voice-vlag stuurt reply-in-kind
                    text, was_voice = await asyncio.to_thread(
                        self._incoming_text, msg, chat_id)
                    if not text or not chat_id:
                        continue
                    try:
                        await asyncio.to_thread(self._handle_text, chat_id, text,
                                                was_voice)
```

- [ ] **Step 4: Run test to verify it passes (incl. regressie op de bestaande Telegram-tests)**

Run: `python -m pytest tests/test_bereik.py -v`
Expected: PASS

Run: `python -m pytest tests/test_jarvis.py -q`
Expected: PASS — de bestaande pairing-tests roepen `_handle_text(chat_id, text)` met twee argumenten aan; het default-argument houdt ze groen

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/telegram.py tests/test_bereik.py
git commit -m "feat(telegram): reply-in-kind voor voice-notes + melding bij mislukte transcriptie"
```

---

## Task 8: `chain_check` — integriteitscontrole met melding

`verify_chain(brain)` bestaat al (`src/span/safety/audit.py` r129-155) maar wordt nergens periodiek aangeroepen. We bouwen een compacte, sync en testbare wrapper in daily.py (de scheduling-haak komt in Task 9); alleen een breuk levert een melding op — succes is log-only, conform het consolidatie-precedent (daily.py r517-522).

**Files:**
- Modify: `src/span/jarvis/daily.py` (`import os` bij de imports r10-13; functies + constante bij r450-455)
- Test: `tests/test_safety.py` (naast `_fake_audit_brain`, r271-289)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_safety.py (MagicMock en pytest staan al in de imports)
# -- A5 nachtelijke ketencontrole -------------------------------------------

def test_chain_check_ok_is_stil(monkeypatch):
    from span.jarvis.daily import chain_check
    from span.safety import audit
    monkeypatch.setenv("SPAN_AUDIT_HMAC_KEY", "geheim-buiten-het-brein")
    b, store = _fake_audit_brain()
    audit.record_action(b, "mail_send", "naar jan")
    inbox = MagicMock()
    result = chain_check({"brain": b, "inbox": inbox, "telegram": None})
    assert result["ok"] is True and result["count"] == 1
    inbox.add.assert_not_called()   # succes = log-only, geen inbox-ruis


def test_chain_check_breuk_meldt_inbox_en_telegram(monkeypatch):
    from span.jarvis.daily import chain_check
    from span.safety import audit
    monkeypatch.setenv("SPAN_AUDIT_HMAC_KEY", "geheim-buiten-het-brein")
    b, store = _fake_audit_brain()
    audit.record_action(b, "mail_send", "naar jan")
    store[0]["detail"] = "naar dief@evil.com"     # tampering
    inbox = MagicMock()
    tg = MagicMock()
    tg.linked = True
    result = chain_check({"brain": b, "inbox": inbox, "telegram": tg})
    assert result["ok"] is False and result["broken_at"] == 1
    assert inbox.add.call_args.kwargs["urgency"] == "high"
    assert "seq 1" in inbox.add.call_args.kwargs["detail"]
    tg.send.assert_called_once()   # urgent=True breekt door de stille uren


def test_chaincheck_flag(monkeypatch):
    from span.jarvis import daily
    monkeypatch.setenv("SPAN_CHAINCHECK", "off")
    assert daily.chaincheck_enabled() is False
    monkeypatch.setenv("SPAN_CHAINCHECK", "on")
    assert daily.chaincheck_enabled() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_safety.py::test_chain_check_ok_is_stil -v`
Expected: FAIL with `ImportError: cannot import name 'chain_check' from 'span.jarvis.daily'`

- [ ] **Step 3: Write minimal implementation**

**(a)** Bovenin `src/span/jarvis/daily.py` (r10-13) `import os` toevoegen — dat ontbreekt daar nu:

```python
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Any
```

**(b)** Bij de bestaande tijd-constantes (r450-451, naast `CONSOLIDATE_TIME`):

```python
EVENING_TIME = "17:15"
CONSOLIDATE_TIME = "03:30"
# A5: ná de nachtdump (cron 03:00, legt neo4j ~1 min plat) en de consolidatie
CHAINCHECK_TIME = "03:45"


def chaincheck_enabled() -> bool:
    """A5-flag SPAN_CHAINCHECK: nachtelijke integriteitscontrole op de
    audit-keten. Default aan; kill-switch in SPAN_TELEMETRY-stijl."""
    val = os.environ.get("SPAN_CHAINCHECK", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


def chain_check(state: dict[str, Any]) -> dict[str, Any]:
    """A5: herbereken de audit-hashketen (verify_chain leest ALLE Action-nodes
    in één query — daarom 's nachts, via to_thread). Afwijking = inbox-item
    (high) + urgente Telegram-push; succes = alleen een logregel elders."""
    from span.safety.audit import verify_chain
    result = verify_chain(state["brain"])
    if not result.get("ok"):
        detail = (f"Breuk bij seq {result.get('broken_at')}: "
                  f"{result.get('reason', 'onbekend')} "
                  f"({result.get('count', 0)} records gecontroleerd). "
                  "Iemand of iets heeft de actie-historie gewijzigd — "
                  "controleer scripts/reanchor_audit.py en de server-toegang.")
        inbox = state.get("inbox")
        if inbox is not None:
            inbox.add(kind="notify", title="Audit-keten gebroken (integriteit)",
                      detail=detail, urgency="high")
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            send_respecting_quiet(tg, "🛑 AUDIT-KETEN GEBROKEN\n\n" + detail,
                                  state["brain"], urgent=True)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_safety.py -q`
Expected: PASS (nieuwe tests én de bestaande audit-tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/jarvis/daily.py tests/test_safety.py
git commit -m "feat(safety): chain_check — ketencontrole met inbox- en Telegram-melding bij breuk"
```

---

## Task 9: Scheduler-haak — chaincheck als nachttaak (03:45)

**Files:**
- Modify: `src/span/jarvis/daily.py` (in `daily_scheduler`: taak-coroutine naast `do_consolidate` r512-533, aanroep naast r591-592)

> Geen nieuwe unit-test: de while-lus van `daily_scheduler` is bewust niet direct getest (repo-conventie); de meldlogica zit in `chain_check` (Task 8) en het due/run_task-patroon is bestaand, beproefd gedrag. Step 2 borgt regressievrijheid.

- [ ] **Step 1: Voeg de taak-coroutine en de due-aanroep toe**

**(a)** In `daily_scheduler`, direct ná de bestaande `do_consolidate` (r533):

```python
    async def do_chaincheck() -> None:
        # zwaar (leest alle Action-nodes) -> via to_thread; run_task regelt
        # mark-after-success (c.last_chaincheck) en de attempt-cap
        result = await asyncio.to_thread(chain_check, state)
        log(f"chaincheck: ok={result.get('ok')} count={result.get('count')}")
```

**(b)** In de while-lus, direct ná de consolidate-aanroep (r591-592):

```python
            if due(CONSOLIDATE_TIME, "consolidate", now):
                await run_task("consolidate", do_consolidate)
            if chaincheck_enabled() and due(CHAINCHECK_TIME, "chaincheck", now):
                await run_task("chaincheck", do_chaincheck)
```

> `run_task` geeft gratis: mark-after-success (Config-node `c.last_chaincheck`), max `MAX_ATTEMPTS = 3` pogingen per dag en een high-urgency inbox-item als het blijft mislukken. De flag-check staat vóór `due()` zodat een uitgezette check ook geen brein-query doet.

- [ ] **Step 2: Run the full test suite (no regression)**

Run: `python -m pytest -q`
Expected: PASS

Run: `ruff check src tests`
Expected: schoon (E402/E501/E701/E702 staan in de ignore-lijst; late imports zijn repo-idioom)

- [ ] **Step 3: Commit**

```bash
git add src/span/jarvis/daily.py
git commit -m "feat(scheduler): nachtelijke chaincheck om 03:45 achter SPAN_CHAINCHECK"
```

---

## Task 10: Backup-drill — script + checklist

RUNBOOK sectie A (r14-34) is de gedocumenteerde drill, maar gebruikt `CREATE DATABASE restoredrill` — dat bestaat niet op de community-editie waar prod sinds C1 op draait (RUNBOOK r84-87). Het script doet daarom het community-pad: dump laden in een wegwerp-container op een wegwerp-volume, nodes tellen, vergelijken met productie, alles opruimen. Enterprise blijft naar RUNBOOK sectie A verwijzen. De off-host `.enc`-kopie krijgt in de checklist een échte restore-test (decrypt met de sleutel + zelfde drill-script), zodat sleutel én kopie samen bewezen worden. De restic/rsync-laag (homelab-breed, buiten dit repo) wordt hier alleen op versheid gecontroleerd; een echte restic/rsync-restore-steekproef valt expliciet buiten A5-scope en landt in het homelab-onderhoud (backrest).

**Files:**
- Create: `scripts/neo4j-restore-drill.sh`
- Modify: `docs/RUNBOOK-restore.md` (verwijzing in sectie A + nieuwe sectie D)

- [ ] **Step 1: Schrijf het drill-script**

```bash
#!/usr/bin/env bash
# Herstel-DRILL voor het Neo4j-brein (A5) — bewijst dat de jongste dump écht
# terug te zetten is, ZONDER de productie-database aan te raken.
#
# Community-editie (prod sinds C1): dump laden in een wegwerp-container op een
# wegwerp-volume, nodes tellen, vergelijken met productie, alles opruimen.
# Enterprise: volg docs/RUNBOOK-restore.md sectie A (restoredrill in-container).
#
# Handmatig draaien (maandelijks, NIET tussen 03:00-04:00 — nachtdump en
# nachttaken), zie RUNBOOK sectie D:
#   bash scripts/neo4j-restore-drill.sh
# Exit 0 = geslaagd; 1 = mislukt; 2 = editie niet ondersteund door dit script.
set -uo pipefail

DIR="${NOVA_BACKUP_DIR:-$HOME/nova-backups/neo4j}"
CT="${NOVA_NEO4J_CONTAINER:-nova-neo4j}"
DB="${NOVA_DRILL_DB:-span-brain}"
MIN_RATIO="${NOVA_DRILL_MIN_RATIO:-90}"   # drill-count >= 90% van prod = slagen
ts() { date +%Y-%m-%dT%H:%M:%S; }
T0="$(date +%s)"

DUMP="$(ls -1t "$DIR/${DB}-"*.dump 2>/dev/null | head -1)"
if [ -z "$DUMP" ]; then echo "[$(ts)] FOUT: geen ${DB}-dump in $DIR" >&2; exit 1; fi
DUMP_AGE_H=$(( ( $(date +%s) - $(stat -c %Y "$DUMP") ) / 3600 ))

# Het prod-wachtwoord blijft BINNEN de container: cypher-shell leest
# NEO4J_USERNAME/NEO4J_PASSWORD uit zijn omgeving, dus het geheim komt nooit
# als docker-exec-argument in de proceslijst van de host terecht. En bewust
# nergens `set -x` in dit script — dat zou de expansie alsnog loggen.
prod_cypher() {  # $1 = database, $2 = query -> laatste plain-regel van de output
  docker exec "$CT" sh -c \
    'NEO4J_USERNAME=neo4j NEO4J_PASSWORD="${NEO4J_AUTH#*/}" cypher-shell -d "$1" --format plain "$2"' \
    sh "$1" "$2" 2>/dev/null | tail -n 1 | tr -d '"\r'
}

EDITION="$(prod_cypher system "CALL dbms.components() YIELD edition RETURN edition;")"
if [ "$EDITION" != "community" ]; then
  echo "[$(ts)] editie '$EDITION': gebruik RUNBOOK-restore.md sectie A (restoredrill)" >&2
  exit 2
fi
PROD_N="$(prod_cypher "$DB" "MATCH (n) RETURN count(n);")"
PROD_N="${PROD_N:-0}"

IMG="$(docker inspect --format '{{.Config.Image}}' "$CT")"
TMP="$(mktemp -d)"
cleanup() {
  docker rm -f nova-drill >/dev/null 2>&1
  docker volume rm nova-drill-data >/dev/null 2>&1
  rm -rf "$TMP"
}
trap cleanup EXIT

# dump laden als default-db 'neo4j' op een vers wegwerp-volume (--entrypoint
# omzeilt de privilege-drop van de image, zie scripts/neo4j-backup.sh r45-48)
cp "$DUMP" "$TMP/neo4j.dump"
docker volume create nova-drill-data >/dev/null
if ! docker run --rm --user root --entrypoint neo4j-admin \
       -v nova-drill-data:/data -v "$TMP":/backups "$IMG" \
       database load neo4j --from-path=/backups --overwrite-destination=true; then
  echo "[$(ts)] FOUT: dump laden mislukt ($DUMP)" >&2; exit 1
fi
docker run -d --name nova-drill -e NEO4J_AUTH=neo4j/drill-tijdelijk \
  -v nova-drill-data:/data "$IMG" >/dev/null

DRILL_N=""
for _ in $(seq 1 60); do   # max ~2 min wachten tot de wegwerp-server op is
  DRILL_N="$(docker exec nova-drill cypher-shell -u neo4j -p drill-tijdelijk \
             --format plain "MATCH (n) RETURN count(n);" 2>/dev/null \
             | tail -n 1 | tr -d '"\r')"
  [ -n "$DRILL_N" ] && break
  sleep 2
done
if [ -z "$DRILL_N" ]; then echo "[$(ts)] FOUT: wegwerp-server kwam niet op" >&2; exit 1; fi

RTO=$(( $(date +%s) - T0 ))
echo "[$(ts)] drill: $DRILL_N nodes hersteld uit $(basename "$DUMP") (productie: $PROD_N)"
echo "[$(ts)] RTO: ${RTO}s (drill-duur) — RPO: max ${DUMP_AGE_H}u (leeftijd jongste dump)"
if [ "$DRILL_N" -eq 0 ]; then echo "[$(ts)] DRILL MISLUKT: 0 nodes" >&2; exit 1; fi
if [ "$PROD_N" -gt 0 ] && [ $(( DRILL_N * 100 )) -lt $(( PROD_N * MIN_RATIO )) ]; then
  echo "[$(ts)] DRILL MISLUKT: hersteld aantal < ${MIN_RATIO}% van productie" >&2
  exit 1
fi
echo "[$(ts)] DRILL GESLAAGD"
```

> Het wegwerp-wachtwoord `drill-tijdelijk` beschermt niets (container zonder gepubliceerde poorten, leeft minuten en wordt door de trap opgeruimd) — het échte geheim (`NEO4J_AUTH` van prod) verlaat de container nooit: `prod_cypher` expandeert het pas binnenin (`sh -c` + cypher-shells `NEO4J_PASSWORD`-env-var), dus het staat niet in de host-proceslijst en wordt nergens geprint.

- [ ] **Step 2: Syntax-check (het "failing test"-equivalent voor bash)**

Run: `bash -n scripts/neo4j-restore-drill.sh`
Expected: exit 0, geen output. (Draai ook `shellcheck` als die lokaal beschikbaar is; volg bij meldingen de `# shellcheck disable`-stijl van neo4j-backup.sh alleen met reden.)

- [ ] **Step 3: Update het RUNBOOK**

**(a)** In `docs/RUNBOOK-restore.md`, direct onder de kop van sectie A (na r18), deze regels toevoegen:

```markdown
> Geautomatiseerd (community, prod sinds C1): `bash scripts/neo4j-restore-drill.sh`
> — doet onderstaande in een wegwerp-container en print RTO/RPO. Onderstaande
> handmatige variant geldt voor de Enterprise-editie (`restoredrill`-database).
```

**(b)** Onderaan het bestand een nieuwe sectie toevoegen:

````markdown
## D. Drill-checklist (maandelijks, ~15 min)

1. [ ] Drill draaien op de z390 (niet tussen 03:00-04:00, dan draaien de
       nachtdump en de nachttaken): `bash ~/nova/scripts/neo4j-restore-drill.sh`
       → eindigt met `DRILL GESLAAGD`; noteer de RTO/RPO-regel in de tabel.
2. [ ] Off-host-kopie vers: `ssh -p 55 Bas_Spaan@192.168.3.6 "ls -lt nova-backups/neo4j | head -3"`
       → jongste `.enc` is van vannacht.
3. [ ] Off-host-kopie écht terug te zetten (restore-test — bewijst sleutel én
       kopie in één keer; parameters uit scripts/neo4j-backup.sh r108-109):

   ```bash
   mkdir -p /tmp/enc-drill
   NEWEST="$(ssh -p 55 Bas_Spaan@192.168.3.6 'ls -1t nova-backups/neo4j/span-brain-*.dump.enc | head -1')"
   scp -P 55 "Bas_Spaan@192.168.3.6:$NEWEST" /tmp/enc-drill/
   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
     -in "/tmp/enc-drill/$(basename "$NEWEST")" \
     -out "/tmp/enc-drill/$(basename "$NEWEST" .enc)" \
     -pass "file:$HOME/.secrets/nova-backup-key"
   NOVA_BACKUP_DIR=/tmp/enc-drill bash ~/nova/scripts/neo4j-restore-drill.sh
   rm -rf /tmp/enc-drill
   ```

   → zelfde `DRILL GESLAAGD` als punt 1, maar nu vanaf de versleutelde
   off-host kopie.
4. [ ] Sleutel-kopie: naast `~/.secrets/nova-backup-key` op de z390 (punt 3
       bewijst dat die werkt) staat er een kopie in de wachtwoordmanager.
5. [ ] Homelab-lagen buiten dit repo: backrest/restic-snapshot (03:00) en de
       dagelijkse rsync naar de externe USB (01:00) zijn < 48u oud. Een echte
       restic/rsync-restore-steekproef (backrest-UI → restore, of
       `restic check --read-data-subset=2%`) valt buiten A5-scope en hoort
       bij het homelab-onderhoud — hier alleen versheid.

| datum | RTO (drill-duur) | RPO (dump-leeftijd) | resultaat |
|-------|------------------|---------------------|-----------|
|       |                  |                     |           |
````

- [ ] **Step 4: Commit**

```bash
git add scripts/neo4j-restore-drill.sh docs/RUNBOOK-restore.md
git commit -m "feat(ops): geautomatiseerde neo4j-restore-drill + drill-checklist in het RUNBOOK"
```

---

## Task 11: Deploy naar z390 + prod-verificatie

**Geen TDD-code — dit is een deploy/verifieer-taak op z390** (A1-precedent, Task 8 daar). De inhoudelijke checks staan in "## Handmatige verificatie" hieronder; dit is de volgorde.

**Files:**
- Modify: `docs/RUNBOOK-restore.md` (RTO/RPO-tabel in sectie D, Step 4)
- Modify (op z390, buiten git): `~/nova/.env` (alleen `SPAN_PUBLIC_URL` controleren/zetten, Step 1)

- [ ] **Step 0: CI-gate — merge alleen op groen (les uit PR #110)**

PR openen vanaf `a5-bereik`, wachten tot zowel de **push-run** als de **PR-run** van de CI groen is, dan pas mergen naar master — nooit mergen op rood of op een nog lopende run.

- [ ] **Step 1: Controleer `SPAN_PUBLIC_URL` in `~/nova/.env` op de z390**

De QR-fix leunt erop. Moet de publieke HTTPS-URL zijn (nu `https://nova.famspaan.nl`, na de migratie `https://agent.famspaan.nl`):

```bash
ssh z390
cd ~/nova
# alleen deze ene regel bekijken — nooit `cat .env` (daar staan echte secrets in);
# de publieke URL zelf is geen geheim
grep -q '^SPAN_PUBLIC_URL=' .env || echo 'SPAN_PUBLIC_URL=https://nova.famspaan.nl' >> .env
grep '^SPAN_PUBLIC_URL=' .env   # verwacht: SPAN_PUBLIC_URL=https://nova.famspaan.nl
chmod 600 .env
```

- [ ] **Step 2: Deploy volgens het bestaande recept (zelfde patroon als A1/A2/A4)**

```bash
cd ~/nova
export MSYS_NO_PATHCONV=1                      # alleen nodig bij Windows-git-bash
cp docker-compose.yml /tmp/compose.z390.bak    # server-compose bewaren (lokale afwijkingen)
git fetch origin
git reset --hard origin/master                 # ná de merge uit Step 0
cp /tmp/compose.z390.bak docker-compose.yml    # compose terugzetten
chmod 600 .env
docker compose up -d --build span
sleep 25
curl -fsS http://localhost:8472/readyz         # verwacht: ready
```

- [ ] **Step 3: Draai de A5-tests één keer in het echte image**

Run: `docker compose exec span python -m pytest tests/test_bereik.py -q`
Expected: PASS — nu draait óók `test_wav_naar_ogg_opus` echt (PyAV met libopus zit in het image). Faalt de opus-encode hier, dan is het alternatief `apt-get install -y ffmpeg` in de Dockerfile + subprocess — maar dat is een bewuste vervolgkeuze, niet stil doorvoeren.

- [ ] **Step 4: Loop "## Handmatige verificatie" hieronder af en noteer het drill-resultaat**

Na een geslaagde drill de RTO/RPO-regel invullen in de tabel van RUNBOOK sectie D en committen:

```bash
git add docs/RUNBOOK-restore.md
git commit -m "docs(ops): eerste drill-resultaat (RTO/RPO) vastgelegd"
```

---

## Afhankelijkheden & volgorde

**Voordat dit plan start:**
- **A1 (telemetrie) moet af en gemerged zijn.** Verificatie: `src/span/telemetry.py` bestaat en `routes.py` bevat `telemetry.record(...)`-aanroepen in `/api/stt` en `/api/tts` (bij het schrijven van dit plan is dat in de werkboom op `master` al zo). Zit A1 nog op de branch `a1-telemetrie`, baseer de A5-branch dáárop — routes.py wordt door beide geraakt en dit voorkomt de merge-conflicten. Task 6 gebruikt A1's `telemetry.record("tts", ..., {"mode": "telegram"})` voor de tts-meting.
- **Branch:** `a5-bereik` (naamconventie: a1-telemetrie, wp-*, lo-*). `src/span/telemetry.py` en `tests/test_telemetry.py` zijn verboden terrein — alleen importeren.
- **Op z390 aanwezig:** `SPAN_PUBLIC_URL` in de .env (Task 11 Step 1), `TELEGRAM_BOT_TOKEN` (bestaat al), de backup-cron van 03:00 (bestaat al — daarom chaincheck om 03:45 en de drill nooit tussen 03:00-04:00, de nachtdump legt neo4j ~1 min plat).

**Volgorde binnen het plan (sequentieel uitvoeren):**
1. Task 1 → Task 2: de QR-handler consumeert het `public_url`-veld uit Task 1.
2. Task 3 → Task 4: zonder mp4-acceptatie is de iOS-opname uit Task 4 dood (415).
3. Task 5 → Task 6 → Task 7: audio-helper vóór `send_voice`, `send_voice` vóór reply-in-kind.
4. Task 8 → Task 9: eerst de testbare functie, dan de scheduler-haak.
5. Task 10 staat los (kan parallel); Task 11 als laatste.

**Bewuste keuzes (niet heropenen tijdens de uitvoering):**
- OGG/Opus via PyAV in-process, géén ffmpeg-binary in de Dockerfile (image blijft ~70 MB kleiner; `av==17.1.0` zit er al in via faster-whisper, `constraints.txt` r8; `PIP_CONSTRAINT` borgt de pin).
- Flags `SPAN_TG_VOICE_REPLY` en `SPAN_CHAINCHECK` default áán met kill-switch (SPAN_TELEMETRY-stijl): beide paden zijn best-effort en kunnen een gesprek/beurt per constructie niet breken; de spec schrijft geen default voor, dus dit is de expliciete keuze.
- Omkeerbaarheid van de flag-loze wijzigingen (invulling van de "elke fase-A-wijziging achter een feature-flag"-klep): Tasks 3 en 4 zijn gedrags-verbredende bugfixes zonder bestaand gedrag dat verloren gaat (de STT-allowlist wordt alleen ruimer; het `!SR`-pad was voorheen dood) — een eigen flag voegt daar alleen complexiteit toe, terugdraaien is git-revert + cache-bust. Tasks 1-2 zijn omkeerbaar door `SPAN_PUBLIC_URL` leeg te laten of te verwijderen: de QR-flow valt dan terug op het oude LAN-gedrag; dat wég-laten ís het omkeermechanisme.
- Buiten scope (fase B/C — niets van bouwen): streaming-STT, wake-word-verbeteringen, GPU-schakelaars, WhatsApp, nieuwe UI-panelen.

---

## Handmatige verificatie (wat Bas op prod moet zien/horen)

1. **QR/HTTPS:** HUD op de pc → instellingen → "toon QR-code" → de note toont een `https://nova.famspaan.nl/?token=…`-URL (geen mic-waarschuwing). iPhone: scan → HUD laadt, 🎙-knop is zichtbaar (verdween voorheen), mic-permissie-popup verschijnt.
2. **iOS-spraak:** op de iPhone (Safari) gespreksmodus aanzetten, een zin spreken → transcriptie verschijnt en LO antwoordt. Eerste zin mag traag zijn (Whisper-model laadt eenmalig). In de serverlog: `STT transcript ontvangen`. Op `GET /api/telemetry` (owner): het `stt`-segment telt op.
3. **Telegram voice-in + uit:** spraakbericht naar de bot sturen → antwoord komt terug als **voice-note** (afspeelbaar, Opus). Lange vraag ("schrijf vijf alinea's over …") → antwoord komt als tekst (bewust: >900 tekens). Op `/api/telemetry`: `tts`-metingen met `{"mode": "telegram"}`.
4. **Telegram nette fout:** een voice-note van <1 seconde sturen (of STT tijdelijk onbeschikbaar maken): de bot antwoordt met de "kon je spraakbericht niet omzetten"-melding in plaats van stilte.
5. **Kill-switches:** `SPAN_TG_VOICE_REPLY=off` in de .env + herstart → voice-note-vraag krijgt weer een tekstantwoord. Daarna terugzetten.
6. **Chaincheck:** de ochtend na de deploy in de serverlog: `[scheduler 03:45] chaincheck: ok=True count=<n>` en de Config-node heeft `c.last_chaincheck` op vandaag. (Een echte breuk niet op prod simuleren — dat is het werk van `tests/test_safety.py`.)
7. **Backup-drill:** `bash ~/nova/scripts/neo4j-restore-drill.sh` op de z390 (overdag) → eindigt met `DRILL GESLAAGD` + RTO/RPO-regel; productie-`span-brain` en de app blijven ondertussen gewoon bereikbaar; `docker ps` toont na afloop géén `nova-drill` meer en `docker volume ls` géén `nova-drill-data`. Resultaat in de RUNBOOK-tabel zetten.

---

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (A5-blok):**
- QR-flow naar HTTPS (settings.js ~r215 wees naar LAN-HTTP → mic geblokkeerd) → Tasks 1+2. ✓
- `if(!SR)return`-bug (voice.js ~r554) sluit iOS/Safari uit; mic-opname zonder webkitSpeechRecognition via het bestaande /api/stt-pad → Task 4, plus de noodzakelijke mp4-acceptatie (Task 3) zonder welke de fix op iPhone alsnog dood is. ✓
- Telegram-voice in (voice-note → /api/stt): bestond al; Task 7 repareert de stille drop en voegt de was_voice-vlag toe. Uit (antwoord als sendVoice, OGG/Opus): Tasks 5+6, PyAV i.p.v. een ffmpeg-binary. ✓
- Backup-drill (script/checklist, handmatig draaibaar mag van de spec): Task 10 — script voor de neo4j-keten; de off-host `.enc`-kopie wordt in de checklist echt test-ontsleuteld en gedrild (punt 3); de restic/rsync-laag wordt op versheid gecontroleerd en een echte restore-steekproef daarvan is expliciet buiten A5-scope geplaatst (homelab-onderhoud/backrest — die lagen leven buiten dit repo). ✓
- verify_chain als dagelijkse nachttaak met melding via de bestaande inbox/notify: Tasks 8+9 (inbox high + urgente Telegram-push, alleen bij breuk; log-only bij succes). ✓

**Placeholder-scan:** geen TBD/TODO; elke code-stap toont echte, complete code. De verwijzing naar RUNBOOK sectie A voor Enterprise (Task 10) is een verwijzing naar een bestaande, volledige procedure — geen placeholder.

**Type-consistentie:** `send_voice(text, chat_id="") -> bool` gelijk in Task 6 (definitie) en 7 (aanroep); `_handle_text(chat_id, text, as_voice=False)` compatibel met de bestaande 2-args-aanroepen in test_jarvis.py; `chain_check(state) -> dict` matcht `verify_chain`'s retourvorm (`ok`/`count`/`broken_at`/`reason`); `wav_to_ogg_opus(wav_bytes, bitrate=32_000) -> bytes` gelijk in Task 5 (definitie) en Task 6 (mock + aanroep); flags overal in SPAN_TELEMETRY-stijl.

**Nooit-een-gesprek-breken:** send_voice slikt alles en geeft False (→ tekst-fallback in `_handle_text`); telemetry.record is best-effort (A1-contract); chain_check draait in `run_task` (mark-after-success, attempt-cap, inbox-melding bij opgeven); netinfo/QR-paden zijn read-only.

**Bestandsgroottes (CLAUDE.md <500):** nieuwe logica in eigen bestanden (audio.py, test_bereik.py, drill-script); telegram.py (+~55) en daily.py (+~40) zijn bestaande overschrijders die licht groeien — conform het repo-idioom.

**Secrets:** alleen env-var-NAMEN (SPAN_PUBLIC_URL, SPAN_TG_VOICE_REPLY, SPAN_CHAINCHECK, SPAN_TELEMETRY, TELEGRAM_BOT_TOKEN, SPAN_AUTH_TOKEN, NOVA_BACKUP_*); geen waarden (de publieke HTTPS-URL in Task 11 Step 1 is geen geheim), en het drill-script print het prod-wachtwoord nergens én houdt het binnen de container (`prod_cypher` via cypher-shells `NEO4J_PASSWORD`-env-var — niet als docker-exec-argument in de host-proceslijst; geen `set -x`).

**Mocks:** alle muterende integratie-calls in tests gemockt — Telegram via `monkeypatch.setattr(tgmod.requests, "post", fake_post)` en `bridge.send`/`bridge.send_voice = MagicMock()`; TTS/STT via module-attribuut-monkeypatches; geen enkele echte API-call in de suite.
