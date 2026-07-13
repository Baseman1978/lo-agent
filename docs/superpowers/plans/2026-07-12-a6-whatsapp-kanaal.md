# A6 — WhatsApp-kanaal (laag 1 + 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LO is bereikbaar via WhatsApp op een eigen nummer: chat (laag 1) en spraakmemo's in/uit (laag 2) via de officiële Meta Cloud API, volledig testbaar met mocks vóór het testnummer er is.

**Architecture:** Een dunne, wegneembare adapter in twee nieuwe modules: `span/integrations/whatsapp.py` (de `WhatsAppBridge` — Cloud-API-client, allowlist, dedupe, agent-koppeling, voice via bestaande STT/TTS) en `span/server/whatsapp.py` (eigen `APIRouter` met de webhook: GET = hub.challenge-verificatie, POST = X-Hub-Signature-256 over de rauwe body, direct 200, afhandeling als achtergrondtaak). Geen kernlogica in de WhatsApp-laag: elke beurt loopt door de bestaande `SpanAgent.turn()` en erft daarmee ongewijzigd de guard/risk-governance; WhatsApp-inhoud is untrusted input en de allowlist (alleen Bas' nummer) is de vertrouwensgrens.

**Tech Stack:** Python 3, FastAPI, requests (via `span.integrations.http`), PyAV (al gepind in constraints.txt, alleen voor WAV→OGG/Opus), pytest. Geen nieuwe dependencies, geen achtergrond-pollingtaak (webhook-gedreven).

**Branch-basis:** bouw gewoon op `master` — `a1-telemetrie` is gemerged (PR #116, merge-commit 79a9990) en content-identiek aan master, dus `src/span/telemetry.py` bestaat daar. `src/span/telemetry.py` en `tests/test_telemetry.py` worden NIET aangeraakt; telemetrie wordt alleen geïmporteerd. Ook `src/span/orchestrator/agent.py` en `src/span/server/routes.py` blijven onaangeraakt — de WhatsApp-laag is een losse adapter.

---

## File Structure

- **Create** `src/span/integrations/whatsapp.py` — `WhatsAppBridge`: send_text, download_media (bounded, via egress-guard), allowlist + dedupe, agent-beheer, voice-note→STT, TTS→OGG/Opus→voice-note. Spiegel van `integrations/telegram.py`, maar zonder poll-loop.
- **Create** `src/span/server/whatsapp.py` — eigen `APIRouter` met `/api/webhooks/whatsapp` (GET + POST). Eigen module (precedent: `server/auth.py`) omdat `routes.py` al 1598 regels telt.
- **Create** `tests/test_whatsapp.py` — alles offline: handshake, signatures (echte HMAC over bytes), allowlist, tekst-flow, voice-flow; alle Cloud-API-calls gemockt.
- **Modify** `src/span/config.py` — `JarvisConfig`-velden + properties (r.30-66) en het jarvis-blok in `load_settings()` (r.146-154).
- **Modify** `src/span/safety/egress.py` — `graph.facebook.com` + `lookaside.fbsbx.com` in `_ALLOWED` (r.16-25).
- **Modify** `src/span/server/app.py` — import (r.39), lifespan-blok (na r.160), `include_router` (r.207-208).
- **Modify** `.env.example` — zes nieuwe variabelen met commentaar (alleen NAMEN, geen waarden).
- **Modify** `tests/test_config.py` — nieuwe WHATSAPP_*-vars in de `clean_env`-fixture (r.10-15) + config-tests.

---

## Task 1: Config — JarvisConfig-velden + whatsapp_enabled + .env.example

**Files:**
- Modify: `src/span/config.py` (r.30-66 `JarvisConfig`, r.146-154 `load_settings`)
- Modify: `.env.example`
- Test: `tests/test_config.py` (fixture r.10-15 + nieuwe tests onderaan)

- [ ] **Step 1: Write the failing test**

Voeg eerst de zes nieuwe variabelen toe aan de `clean_env`-fixture in `tests/test_config.py` (anders lekt een echte `.env` erin). Vervang de lijst in r.10-15 door:

```python
    for var in [
        "ORQ_API_KEY", "ORQ_BASE_URL", "NEO4J_PASSWORD", "SPAN_EMBED_DIMS",
        "WORK_NEO4J_URI", "BRAIN_DB", "ASANA_TOKEN", "FIREFLIES_API_KEY",
        "TELEGRAM_BOT_TOKEN", "MS_CLIENT_ID",
        "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET", "WHATSAPP_ALLOWED_NUMBERS", "WHATSAPP_VOICE_REPLY",
    ]:
```

Voeg daarna onderaan `tests/test_config.py` toe:

```python
def test_whatsapp_default_uit(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    j = load_settings().jarvis
    assert not j.whatsapp_enabled
    assert j.whatsapp_allowlist == frozenset()
    assert not j.whatsapp_voice_reply


def test_whatsapp_config_volledig(monkeypatch):
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "12345")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setenv("WHATSAPP_ALLOWED_NUMBERS", "+31 6 12345678, 31687654321")
    monkeypatch.setenv("WHATSAPP_VOICE_REPLY", "1")
    j = load_settings().jarvis
    assert j.whatsapp_enabled
    assert j.whatsapp_allowlist == frozenset({"31612345678", "31687654321"})
    assert j.whatsapp_voice_reply


def test_whatsapp_zonder_allowlist_blijft_uit(monkeypatch):
    """Fail-closed: zonder toegestaan nummer gaat het kanaal niet aan."""
    monkeypatch.setenv("ORQ_API_KEY", "orq-test")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    monkeypatch.setenv("WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "12345")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    j = load_settings().jarvis
    assert not j.whatsapp_enabled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_whatsapp_config_volledig -v`
Expected: FAIL with `TypeError: JarvisConfig.__init__() got an unexpected keyword argument` of `AttributeError: 'JarvisConfig' object has no attribute 'whatsapp_enabled'`

- [ ] **Step 3: Write minimal implementation**

In `src/span/config.py`, voeg aan `JarvisConfig` (na het veld `telegram_bot_token: str = ""` op r.44) toe:

```python
    # A6 — WhatsApp Cloud API (officieel, geen BSP). Alle vijf kernvelden gezet
    # = kanaal aan; allowlist leeg = fail-closed uit.
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_allowed_numbers: str = ""   # komma-gescheiden wa_id's
    whatsapp_voice_reply: bool = False   # laag 2 uit: antwoord als voice-note terug
```

En na de bestaande property `telegram_enabled` (r.64-66):

```python
    @property
    def whatsapp_allowlist(self) -> frozenset[str]:
        """Toegestane wa_id's, genormaliseerd: '+31 6 12345678' -> '31612345678'."""
        out = set()
        for n in self.whatsapp_allowed_numbers.split(","):
            n = n.strip().lstrip("+").replace(" ", "")
            if n:
                out.add(n)
        return frozenset(out)

    @property
    def whatsapp_enabled(self) -> bool:
        """Alle kernvelden gezet: token+phone-id (versturen), verify-token+
        app-secret (webhook) én minstens één toegestaan nummer (fail-closed)."""
        return bool(self.whatsapp_token and self.whatsapp_phone_id
                    and self.whatsapp_verify_token and self.whatsapp_app_secret
                    and self.whatsapp_allowlist)
```

In `load_settings()`, breid het `jarvis=JarvisConfig(...)`-blok (r.146-154) uit met, direct na de regel `telegram_bot_token=...`:

```python
            whatsapp_token=os.environ.get("WHATSAPP_TOKEN", "").strip(),
            whatsapp_phone_id=os.environ.get("WHATSAPP_PHONE_ID", "").strip(),
            whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip(),
            whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", "").strip(),
            whatsapp_allowed_numbers=os.environ.get("WHATSAPP_ALLOWED_NUMBERS", "").strip(),
            whatsapp_voice_reply=(os.environ.get("WHATSAPP_VOICE_REPLY", "").strip().lower()
                                  in ("1", "true", "yes", "on")),
```

Voeg onderaan `.env.example` toe (alleen namen, nooit waarden):

```
# A6 — WhatsApp-kanaal (Meta Cloud API, officieel; apart nummer voor LO).
# Alle vier de eerste velden + minstens één nummer gezet = kanaal aan.
# Permanent System User-token uit de Meta Business-app:
WHATSAPP_TOKEN=
# Phone number ID van LO's testnummer (Meta app-dashboard, WhatsApp > API Setup):
WHATSAPP_PHONE_ID=
# Zelfgekozen verify-token voor de webhook-handshake (Meta app-dashboard):
WHATSAPP_VERIFY_TOKEN=
# App Secret van de Meta-app — verifieert X-Hub-Signature-256 op de webhook:
WHATSAPP_APP_SECRET=
# Allowlist: alleen deze wa_id's worden bediend (komma-gescheiden, bv. 31612345678).
# Al het andere wordt genegeerd + gelogd. Leeg = kanaal blijft uit (fail-closed).
WHATSAPP_ALLOWED_NUMBERS=
# Laag 2: antwoord op een spraakmemo ook als voice-note terug (1 = aan, default uit).
WHATSAPP_VOICE_REPLY=
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (alle bestaande config-tests blijven groen; de drie nieuwe ook)

- [ ] **Step 5: Commit**

```bash
git add src/span/config.py .env.example tests/test_config.py
git commit -m "feat(config): A6 WhatsApp-config in JarvisConfig (fail-closed, allowlist genormaliseerd)"
```

---

## Task 2: Egress-allowlist — facebook-hosts toestaan

**Files:**
- Modify: `src/span/safety/egress.py` (r.16-25 `_ALLOWED`)
- Test: `tests/test_whatsapp.py` (nieuw bestand)

- [ ] **Step 1: Write the failing test**

Maak `tests/test_whatsapp.py` aan:

```python
"""A6 — WhatsApp-kanaal (laag 1+2): webhook, allowlist, chat- en voice-flow.

Alles draait offline: Cloud-API-calls, STT en TTS zijn gemockt. Signature-tests
rekenen een échte HMAC over de bytes-body. Dat dekt de spec-eis "testbaar met
mocks/fixtures vóór het Meta-testnummer er is".
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections import OrderedDict
from unittest.mock import MagicMock

import pytest


def test_egress_staat_facebook_hosts_toe():
    """Media-URLs wijzen naar lookaside.fbsbx.com; API-calls naar graph.facebook.com.
    Zonder deze hosts blokkeert guarded_get elke media-download."""
    from span.safety.egress import host_allowed
    assert host_allowed("graph.facebook.com")
    assert host_allowed("lookaside.fbsbx.com")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_egress_staat_facebook_hosts_toe -v`
Expected: FAIL with `assert False` (hosts staan nog niet in `_ALLOWED`)

- [ ] **Step 3: Write minimal implementation**

In `src/span/safety/egress.py`, voeg aan de `_ALLOWED`-set (r.16-25) twee regels toe, na `"api.telegram.org",`:

```python
    "graph.facebook.com",   # A6: WhatsApp Cloud API (messages/media)
    "lookaside.fbsbx.com",  # A6: WhatsApp media-download-URLs
```

> Bewuste afweging (omkeerbaarheids-klep): deze twee hosts staan statisch in `_ALLOWED`, ook met `whatsapp_enabled=False`. Dat is acceptabel omdat de egress-allowlist alleen bepaalt waar `guarded_get` héén mag, geen verkeer initieert: zonder actieve `WhatsAppBridge` (Task 10, achter de flag) bestaat er geen codepad dat deze hosts aanroept. Het alternatief — `host_allowed` config-afhankelijk maken of de hosts dynamisch in de lifespan registreren — zou de egress-guard stateful en laadvolgorde-gevoelig maken; een statische, in code gereviewde allowlist is als veiligheidsgrens juist te verkiezen. Verificatiestap 8 ("vars weg → alles terug naar af") gaat dus over het kanaal (route 404, geen bridge), niet over deze twee allowlist-regels; volledig terugdraaien = deze twee regels mee verwijderen (onderdeel van "adapter weghalen" uit de module-docstring).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py::test_egress_staat_facebook_hosts_toe -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/safety/egress.py tests/test_whatsapp.py
git commit -m "feat(egress): sta graph.facebook.com en lookaside.fbsbx.com toe voor A6"
```

---

## Task 3: WhatsAppBridge — send_text via de Cloud API

**Files:**
- Create: `src/span/integrations/whatsapp.py`
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

Append aan `tests/test_whatsapp.py` — eerst de test-helper die overal in dit bestand terugkomt (patroon: `SpanAgent.__new__` uit test_telemetry.py — geen echte Settings/Neo4j nodig):

```python
def _bridge(allowed=("31612345678",), voice_reply=False):
    """Bouw een WhatsAppBridge zonder Settings/Neo4j: attributen handmatig."""
    from span.integrations.whatsapp import WhatsAppBridge
    b = WhatsAppBridge.__new__(WhatsAppBridge)
    b._state = {}
    b._token = "wa-test-token"
    b._phone_id = "12345"
    b._allowed = frozenset(allowed)
    b._voice_reply = voice_reply
    b._agent = None
    b._session_id = None
    b._seen = OrderedDict()
    return b


class _Resp:
    """Minimal requests.Response-double."""
    def __init__(self, payload=None, ok=True, status_code=200):
        self._payload = payload or {}
        self.ok = ok
        self.status_code = status_code
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_send_text_chunkt_en_post(monkeypatch):
    import span.integrations.whatsapp as wa
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return _Resp()

    monkeypatch.setattr(wa.requests, "post", fake_post)
    b = _bridge()
    assert b.send_text("31612345678", "x" * 4500)  # > 4000 tekens -> 2 chunks
    assert len(calls) == 2
    url, headers, payload = calls[0]
    assert url == "https://graph.facebook.com/v21.0/12345/messages"
    assert headers["Authorization"] == "Bearer wa-test-token"
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "31612345678" and payload["type"] == "text"
    assert payload["text"]["body"] == "x" * 4000
    assert calls[1][2]["text"]["body"] == "x" * 500


def test_send_text_fout_geeft_false(monkeypatch):
    import span.integrations.whatsapp as wa
    monkeypatch.setattr(wa.requests, "post",
                        lambda *a, **k: _Resp(ok=False, status_code=400))
    assert not _bridge().send_text("31612345678", "hallo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_send_text_chunkt_en_post -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.integrations.whatsapp'`

- [ ] **Step 3: Write minimal implementation**

Maak `src/span/integrations/whatsapp.py` aan:

```python
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
```

> Note: drie imports in deze module-header worden pas later gebruikt: `guarded_get` in Task 4 (`download_media`), `time` in Task 5 (`_is_duplicate`) en `io` in Task 9 (`_wav_to_ogg_opus`). De CI draait `ruff check src tests` met F-regels aan (pyproject.toml r.48), dus F401 laat een per-taak gepushte Task 3-commit door de lint zakken. Wie per taak lint/pusht: voeg elk van deze imports pas toe in de taak waar hij gebruikt wordt (Task 4, 5 resp. 9) i.p.v. hier.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): WhatsAppBridge met send_text via Cloud API"
```

---

## Task 4: Bounded media-download via de egress-guard

**Files:**
- Modify: `src/span/integrations/whatsapp.py` (append aan de klasse)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append aan tests/test_whatsapp.py
class _RawStream:
    def __init__(self, data):
        self._data = data

    def read(self, n, decode_content=True):
        return self._data[:n]


class _StreamResp:
    def __init__(self, data):
        self.raw = _RawStream(data)
        self.ok = True

    def raise_for_status(self):
        pass

    def close(self):
        pass


def test_download_media_via_guard(monkeypatch):
    import span.integrations.whatsapp as wa
    seen_urls = []

    def fake_guarded_get(url, **kwargs):
        seen_urls.append(url)
        if url == "https://graph.facebook.com/v21.0/media-1":
            return _Resp({"url": "https://lookaside.fbsbx.com/whatsapp/x"})
        return _StreamResp(b"OggS-audio-bytes")

    monkeypatch.setattr(wa, "guarded_get", fake_guarded_get)
    b = _bridge()
    assert b.download_media("media-1") == b"OggS-audio-bytes"
    # beide hops lopen door de egress-guard (media-URL = untrusted API-antwoord)
    assert seen_urls == ["https://graph.facebook.com/v21.0/media-1",
                         "https://lookaside.fbsbx.com/whatsapp/x"]


def test_download_media_te_groot_is_leeg(monkeypatch):
    import span.integrations.whatsapp as wa

    def fake_guarded_get(url, **kwargs):
        if url.endswith("/media-1"):
            return _Resp({"url": "https://lookaside.fbsbx.com/whatsapp/x"})
        return _StreamResp(b"X" * 100)

    monkeypatch.setattr(wa, "guarded_get", fake_guarded_get)
    monkeypatch.setattr(wa, "_MAX_MEDIA_BYTES", 50)
    assert _bridge().download_media("media-1") == b""  # te groot -> overslaan
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_download_media_via_guard -v`
Expected: FAIL with `AttributeError: 'WhatsAppBridge' object has no attribute 'download_media'`

- [ ] **Step 3: Write minimal implementation**

Append aan de klasse in `src/span/integrations/whatsapp.py` (na `send_text`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): bounded media-download door de egress-guard"
```

---

## Task 5: Allowlist + dedupe + agent-koppeling (laag 1 — tekst)

**Files:**
- Modify: `src/span/integrations/whatsapp.py` (append aan de klasse)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append aan tests/test_whatsapp.py
def test_handle_message_tekst_flow(monkeypatch):
    b = _bridge()
    sent = []
    monkeypatch.setattr(b, "send_text", lambda to, text: (sent.append((to, text)), True)[1])
    agent = MagicMock()
    agent.turn.return_value = "hoi Bas"
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    msg = {"from": "31612345678", "id": "wamid.1", "type": "text",
           "text": {"body": "hallo"}}
    b.handle_message(msg)
    agent.turn.assert_called_once_with("hallo")
    assert sent == [("31612345678", "hoi Bas")]
    # dedupe: Meta levert dubbel bij trage acks -> zelfde wamid = geen tweede beurt
    b.handle_message(msg)
    agent.turn.assert_called_once()


def test_vreemd_nummer_genegeerd_en_gelogd(monkeypatch, capsys):
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    sender = MagicMock()
    monkeypatch.setattr(b, "send_text", sender)
    b.handle_message({"from": "49170000000", "id": "wamid.x", "type": "text",
                      "text": {"body": "negeer mij"}})
    agent.turn.assert_not_called()
    sender.assert_not_called()  # géén antwoord naar vreemde nummers
    assert "niet-toegestaan" in capsys.readouterr().out


def test_onbekend_berichttype_genegeerd(monkeypatch, capsys):
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    b.handle_message({"from": "31612345678", "id": "wamid.s", "type": "sticker"})
    agent.turn.assert_not_called()
    assert "sticker" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_handle_message_tekst_flow -v`
Expected: FAIL with `AttributeError: 'WhatsAppBridge' object has no attribute 'handle_message'`

- [ ] **Step 3: Write minimal implementation**

Append aan de klasse in `src/span/integrations/whatsapp.py`:

```python
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
        """Krijgt in Task 8 de echte STT-implementatie; tot dan geen audio-pad."""
        print("[whatsapp] genegeerd: spraakmemo's nog niet actief", flush=True)
        return ""
```

> Note: `send_voice` bestaat pas na Task 9 — het `voice_in and self._voice_reply`-pad is tot die tijd onbereikbaar omdat `_transcribe_voice` hier nog `""` retourneert (lege tekst -> eerlijke tekstmelding + return vóór de agent-beurt). Dit is bewuste taak-volgorde binnen dit plan, geen opengelaten gat: Task 8 en 9 vullen beide functies met echte code in.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): allowlist + wamid-dedupe + agent-koppeling (laag 1)"
```

---

## Task 6: Webhook GET — hub.challenge-verificatie

**Files:**
- Create: `src/span/server/whatsapp.py`
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

Testpatroon: route-handlers direct aanroepen met een request-double (geen TestClient — conventie van deze testsuite, zie test_observability.py/test_telemetry.py).

```python
# append aan tests/test_whatsapp.py
def _get_request(params):
    req = MagicMock()
    req.method = "GET"
    req.query_params = params
    return req


def test_webhook_get_handshake(monkeypatch):
    import span.server.whatsapp as wh
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-123")
    resp = asyncio.run(wh.whatsapp_webhook(_get_request({
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-123",
        "hub.challenge": "424242",
    })))
    assert resp.body == b"424242"
    assert resp.media_type == "text/plain"


def test_webhook_get_fout_token_is_403(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-123")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_get_request({
            "hub.mode": "subscribe",
            "hub.verify_token": "fout",
            "hub.challenge": "424242",
        })))
    assert exc.value.status_code == 403


def test_webhook_get_niet_geconfigureerd_is_404(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_get_request({"hub.mode": "subscribe"})))
    assert exc.value.status_code == 404  # fail-closed, zoals /api/webhooks/graph
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_webhook_get_handshake -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'span.server.whatsapp'`

- [ ] **Step 3: Write minimal implementation**

Maak `src/span/server/whatsapp.py` aan (eigen router-module — precedent `server/auth.py`; `routes.py` is met 1598 regels te groot om nog uit te breiden):

```python
"""A6 — WhatsApp-webhook (Meta Cloud API).

GET  = verificatie-handshake: Meta stuurt hub.mode/hub.verify_token/hub.challenge
       en verwacht de challenge plain terug (zelfde vorm als de validationToken-tak
       van /api/webhooks/graph).
POST = berichten (Task 7): X-Hub-Signature-256 over de RAUWE body, daarna direct
       200 — Meta's timeout is ~10s met redelivery en een agent-beurt duurt
       seconden, dus de afhandeling draait als achtergrondtaak.

De payload is untrusted input: hij wordt uitsluitend als user-bericht van een
allowlisted nummer behandeld, nooit als instructie voor deze laag. Auth is de
eigen verificatie (verify-token/signature) — bewust géén _require_rest_auth,
zoals de bestaande webhook-routes. Fail-closed: zonder env-config -> 404.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from span.server.state import _state

router = APIRouter()

# referenties op lopende achtergrondtaken (anders kan de GC ze opruimen)
_bg_tasks: set[asyncio.Task] = set()


def _verify_token() -> str:
    return os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()


def _app_secret() -> str:
    return os.environ.get("WHATSAPP_APP_SECRET", "").strip()


@router.api_route("/api/webhooks/whatsapp", methods=["GET", "POST"])
async def whatsapp_webhook(request: Request) -> Any:
    if request.method == "GET":
        expected = _verify_token()
        if not expected:
            raise HTTPException(status_code=404,
                                detail="WhatsApp-webhook niet geconfigureerd.")
        supplied = request.query_params.get("hub.verify_token", "")
        challenge = request.query_params.get("hub.challenge", "")
        if (request.query_params.get("hub.mode") == "subscribe"
                and hmac.compare_digest(supplied, expected)):
            return PlainTextResponse(challenge, media_type="text/plain")
        raise HTTPException(status_code=403, detail="Ongeldige verify-token.")

    return await _handle_post(request)


async def _handle_post(request: Request) -> dict[str, Any]:
    """Task 7 vult dit in; tot dan is POST niet geconfigureerd."""
    raise HTTPException(status_code=404,
                        detail="WhatsApp-webhook niet geconfigureerd.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/server/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): webhook GET hub.challenge-verificatie (fail-closed)"
```

---

## Task 7: Webhook POST — signature-check, directe 200, async afhandeling

**Files:**
- Modify: `src/span/server/whatsapp.py` (vervang `_handle_post`)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

De signature-tests rekenen een échte HMAC-SHA256 over de bytes-body — precies wat Meta doet.

```python
# append aan tests/test_whatsapp.py
def _post_request(body: bytes, secret="app-secret", sig=None):
    req = MagicMock()
    req.method = "POST"

    async def _body():
        return body

    req.body = _body
    if sig is None and secret is not None:
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req.headers = {"x-hub-signature-256": sig or ""}
    return req


def _wa_payload(msgs):
    return json.dumps({"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp", "messages": msgs,
    }}]}]}).encode()


def test_webhook_post_geldige_signature_verwerkt_async(monkeypatch):
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    handled = []
    bridge = MagicMock()
    bridge.handle_message.side_effect = lambda m: handled.append(m)
    st._state["whatsapp"] = bridge
    try:
        body = _wa_payload([{"from": "31612345678", "id": "wamid.1",
                             "type": "text", "text": {"body": "hallo"}}])

        async def _run():
            out = await wh.whatsapp_webhook(_post_request(body))
            # de route ackt direct; geef de achtergrondtaken de kans om binnen
            # deze event-loop af te ronden
            await asyncio.gather(*list(wh._bg_tasks))
            return out

        out = asyncio.run(_run())
        assert out == {"received": 1}
        assert handled and handled[0]["id"] == "wamid.1"
    finally:
        st._state.pop("whatsapp", None)


def test_webhook_post_bridge_fout_stuurt_eerlijke_melding(monkeypatch):
    """Spec §5: een exception in handle_message wordt niet stil gedropt — de
    allowlisted afzender krijgt een eerlijke foutmelding via send_text."""
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    bridge = MagicMock()
    bridge._allowed = frozenset({"31612345678"})
    bridge.handle_message.side_effect = RuntimeError("boem")
    st._state["whatsapp"] = bridge
    try:
        body = _wa_payload([{"from": "31612345678", "id": "wamid.e1",
                             "type": "text", "text": {"body": "hallo"}}])

        async def _run():
            out = await wh.whatsapp_webhook(_post_request(body))
            await asyncio.gather(*list(wh._bg_tasks))
            return out

        assert asyncio.run(_run()) == {"received": 1}
        bridge.send_text.assert_called_once()
        to, text = bridge.send_text.call_args.args
        assert to == "31612345678"
        assert "mis" in text  # eerlijke melding, geen stille drop
    finally:
        st._state.pop("whatsapp", None)


def test_webhook_post_foute_signature_is_401(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    body = _wa_payload([])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(
            _post_request(body, sig="sha256=" + "0" * 64)))
    assert exc.value.status_code == 401


def test_webhook_post_zonder_secret_is_404(monkeypatch):
    import span.server.whatsapp as wh
    from fastapi import HTTPException
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wh.whatsapp_webhook(_post_request(b"{}")))
    assert exc.value.status_code == 404


def test_webhook_post_zonder_bridge_geeft_200(monkeypatch):
    """Geldige signature maar kanaal (nog) niet actief -> toch 200, anders
    blijft Meta redelivery doen."""
    import span.server.whatsapp as wh
    from span.server import state as st
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    st._state.pop("whatsapp", None)
    body = _wa_payload([{"from": "49170000000", "id": "wamid.z",
                         "type": "text", "text": {"body": "x"}}])
    out = asyncio.run(wh.whatsapp_webhook(_post_request(body)))
    assert out == {"received": 1}


def test_webhook_post_status_updates_zijn_ok(monkeypatch):
    """Delivery/read-statussen bevatten geen messages -> received: 0, geen fout."""
    import span.server.whatsapp as wh
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    body = json.dumps({"entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.1", "status": "delivered"}],
    }}]}]}).encode()
    out = asyncio.run(wh.whatsapp_webhook(_post_request(body)))
    assert out == {"received": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_webhook_post_geldige_signature_verwerkt_async -v`
Expected: FAIL with `HTTPException` (status 404 uit de Task 6-placeholder i.p.v. `{"received": 1}`)

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/server/whatsapp.py` de hele `_handle_post`-placeholder door:

```python
async def _run_message(bridge: Any, msg: dict[str, Any]) -> None:
    """Eén bericht door de (sync/blocking) bridge, zonder de 200-ack te blokkeren.
    Per-bericht try/except: één kapot bericht mag de rest nooit meeslepen.
    Spec §5: geen stille drops — na een fout krijgt de allowlisted afzender een
    eerlijke foutmelding; alleen server-side loggen is niet genoeg."""
    try:
        await asyncio.to_thread(bridge.handle_message, msg)
    except Exception as exc:
        print(f"[whatsapp] bericht-fout: {type(exc).__name__}: {exc}", flush=True)
        sender = str(msg.get("from") or "")
        if sender in getattr(bridge, "_allowed", frozenset()):
            # best-effort: de foutmelding zelf mag nooit een nieuwe crash geven
            try:
                await asyncio.to_thread(
                    bridge.send_text, sender,
                    "Er ging iets mis bij het verwerken van je bericht — "
                    "probeer het zo nog eens.")
            except Exception as exc2:
                print(f"[whatsapp] foutmelding versturen mislukt: {exc2}",
                      flush=True)


async def _handle_post(request: Request) -> dict[str, Any]:
    secret = _app_secret()
    if not secret:
        raise HTTPException(status_code=404,
                            detail="WhatsApp-webhook niet geconfigureerd.")
    # signature MOET over de rauwe bytes — pas daarna JSON-parsen
    raw = await request.body()
    supplied = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Ongeldige signature.")
    try:
        import json as _json
        body = _json.loads(raw or b"{}")
    except Exception:
        body = {}
    messages: list[dict[str, Any]] = []
    for entry in (body.get("entry") or []):
        for change in (entry.get("changes") or []):
            messages.extend((change.get("value") or {}).get("messages") or [])
    bridge = _state.get("whatsapp")
    if bridge is not None:
        for msg in messages:
            task = asyncio.create_task(_run_message(bridge, msg))
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)
    # ALTIJD 200 op een geldige signature (ook genegeerde afzenders/typen),
    # anders blijft Meta hetzelfde bericht opnieuw aanleveren
    return {"received": len(messages)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/server/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): webhook POST met X-Hub-Signature-256 en async afhandeling"
```

---

## Task 8: Laag 2 in — spraakmemo's via bestaande STT (+ telemetrie)

**Files:**
- Modify: `src/span/integrations/whatsapp.py` (vervang de `_transcribe_voice`-placeholder)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append aan tests/test_whatsapp.py
def test_voice_note_flow_met_stt_telemetrie(monkeypatch, tmp_path):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))

    import span.server.stt as stt
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe",
                        lambda audio, language="nl": "wat staat er vandaag op de agenda")

    b = _bridge()
    monkeypatch.setattr(b, "download_media", lambda mid: b"OggS-opus-bytes")
    sent = []
    monkeypatch.setattr(b, "send_text", lambda to, text: (sent.append(text), True)[1])
    agent = MagicMock()
    agent.turn.return_value = "drie afspraken vandaag"
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)

    b.handle_message({"from": "31612345678", "id": "wamid.v1", "type": "audio",
                      "audio": {"id": "media-9", "voice": True}})
    agent.turn.assert_called_once_with("wat staat er vandaag op de agenda")
    assert sent == ["drie afspraken vandaag"]

    import span.telemetry as tel
    seg = tel.aggregate()["segments"]
    assert seg["stt"]["count"] == 1  # kanaal-meting: {"channel": "whatsapp"}


def test_voice_note_zonder_stt_wordt_eerlijk_gemeld(monkeypatch):
    """Spec §5: geen stille drops — zonder STT volgt geen agent-beurt, maar de
    afzender krijgt wél een eerlijk tekstantwoord (geen genegeerde spraakmemo)."""
    import span.server.stt as stt
    monkeypatch.setattr(stt, "available", lambda: False)
    b = _bridge()
    agent = MagicMock()
    monkeypatch.setattr(b, "_ensure_agent", lambda: agent)
    sent = []
    monkeypatch.setattr(b, "send_text",
                        lambda to, text: (sent.append((to, text)), True)[1])
    b.handle_message({"from": "31612345678", "id": "wamid.v2", "type": "audio",
                      "audio": {"id": "media-10"}})
    agent.turn.assert_not_called()  # geen STT -> geen beurt, geen crash
    assert sent == [("31612345678", "Ik kan spraakmemo's nu niet verwerken — "
                                    "stuur het als tekst.")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_voice_note_flow_met_stt_telemetrie -v`
Expected: FAIL — `agent.turn` niet aangeroepen (de placeholder retourneert nog `""`)

- [ ] **Step 3: Write minimal implementation**

Vervang in `src/span/integrations/whatsapp.py` de `_transcribe_voice`-placeholder uit Task 5 volledig door:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): inkomende spraakmemo's via bestaande STT (laag 2 in)"
```

---

## Task 9: Laag 2 uit — voice-antwoord via TTS + OGG/Opus-transcode (achter WHATSAPP_VOICE_REPLY)

**Files:**
- Modify: `src/span/integrations/whatsapp.py` (append: `_wav_to_ogg_opus`, `_upload_media`, `send_voice`)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append aan tests/test_whatsapp.py
def test_send_voice_upload_en_audio_bericht(monkeypatch, tmp_path):
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    import span.integrations.whatsapp as wa
    import span.server.tts as ttsmod
    monkeypatch.setattr(ttsmod, "available", lambda: True)
    monkeypatch.setattr(ttsmod, "engine", lambda: "piper")
    monkeypatch.setattr(ttsmod, "synthesize", lambda text, **kw: b"RIFF-wav-bytes")
    # transcode gemockt: `av` zit alleen in de [stt]-omgeving (Docker-image)
    monkeypatch.setattr(wa, "_wav_to_ogg_opus", lambda wav: b"OggS-opus")

    posts = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        return _Resp({"id": "media-42"} if url.endswith("/media")
                     else {"messages": [{"id": "wamid.out"}]})

    monkeypatch.setattr(wa.requests, "post", fake_post)
    b = _bridge()
    assert b.send_voice("31612345678", "drie afspraken vandaag")
    (up_url, up_kw), (msg_url, msg_kw) = posts
    assert up_url == "https://graph.facebook.com/v21.0/12345/media"
    assert up_kw["files"]["file"] == ("voice.ogg", b"OggS-opus", "audio/ogg")
    assert up_kw["data"]["messaging_product"] == "whatsapp"
    assert msg_url.endswith("/12345/messages")
    assert msg_kw["json"]["type"] == "audio"
    assert msg_kw["json"]["audio"] == {"id": "media-42"}


def test_voice_reply_alleen_achter_flag(monkeypatch):
    import span.server.stt as stt
    monkeypatch.setattr(stt, "available", lambda: True)
    monkeypatch.setattr(stt, "backend", lambda: "cpu-local")
    monkeypatch.setattr(stt, "transcribe", lambda audio, language="nl": "hoi")
    for flag, verwacht in ((True, 1), (False, 0)):
        b = _bridge(voice_reply=flag)
        monkeypatch.setattr(b, "download_media", lambda mid: b"OggS")
        monkeypatch.setattr(b, "send_text", lambda to, text: True)
        voiced = []
        monkeypatch.setattr(b, "send_voice", lambda to, text: (voiced.append(to), True)[1])
        agent = MagicMock()
        agent.turn.return_value = "antwoord"
        monkeypatch.setattr(b, "_ensure_agent", lambda a=agent: a)
        b.handle_message({"from": "31612345678", "id": f"wamid.f{flag}",
                          "type": "audio", "audio": {"id": "m1"}})
        assert len(voiced) == verwacht  # default UIT: alleen expliciet aan


def test_wav_naar_ogg_opus_echte_transcode():
    pytest.importorskip("av")  # alleen gegarandeerd in de [stt]-omgeving (Docker)
    import io as _io
    import math
    import struct
    import wave
    from span.integrations.whatsapp import _wav_to_ogg_opus
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(struct.pack(
            "<" + "h" * 2205,
            *(int(8000 * math.sin(i / 10.0)) for i in range(2205))))
    ogg = _wav_to_ogg_opus(buf.getvalue())
    assert ogg.startswith(b"OggS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_send_voice_upload_en_audio_bericht -v`
Expected: FAIL with `AttributeError: ... has no attribute '_wav_to_ogg_opus'` (of `send_voice`)

- [ ] **Step 3: Write minimal implementation**

Append aan `src/span/integrations/whatsapp.py` — binnen de klasse (na `_transcribe_voice`):

```python
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
```

En op module-niveau (onder de klasse):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS (`test_wav_naar_ogg_opus_echte_transcode` mag SKIPPED zijn in een dev-omgeving zonder `av`; in de Docker-image moet hij PASSEN)

- [ ] **Step 5: Commit**

```bash
git add src/span/integrations/whatsapp.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): voice-antwoord via TTS + PyAV OGG/Opus-transcode (WHATSAPP_VOICE_REPLY, default uit)"
```

---

## Task 10: Wiring — router mounten + bridge in de lifespan (achter whatsapp_enabled)

**Files:**
- Modify: `src/span/server/app.py` (r.39 import, na r.160 lifespan, r.207-208 include_router)
- Test: `tests/test_whatsapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append aan tests/test_whatsapp.py
def test_webhook_route_gemount_in_app():
    """De router hangt in de FastAPI-app (import voert de lifespan NIET uit)."""
    from span.server.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/webhooks/whatsapp" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whatsapp.py::test_webhook_route_gemount_in_app -v`
Expected: FAIL with `AssertionError` (route nog niet gemount)

- [ ] **Step 3: Write minimal implementation**

Drie kleine wijzigingen in `src/span/server/app.py`:

1. Import (r.39) — vervang:

```python
from span.server import auth, routes
```

door:

```python
from span.server import auth, routes
from span.server import whatsapp as whatsapp_routes
```

2. Lifespan — direct ná het Telegram-blok (r.156-160, de regel `telegram_task = asyncio.create_task(_state["telegram"].run())`) toevoegen:

```python
    if settings.jarvis.whatsapp_enabled:
        # A6: webhook-gedreven — geen achtergrondtaak, alleen de bridge in _state
        from span.integrations.whatsapp import WhatsAppBridge
        _state["whatsapp"] = WhatsAppBridge(_state)
        print("[whatsapp] kanaal actief (allowlist: "
              f"{len(settings.jarvis.whatsapp_allowlist)} nummer(s))", flush=True)
```

(Er hoeft niets bij de teardown: geen taak om te cancelen.)

3. Router mounten — vervang r.207-208:

```python
app.include_router(auth.router)
app.include_router(routes.router)
```

door:

```python
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(whatsapp_routes.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_whatsapp.py -v`
Expected: PASS

- [ ] **Step 5: Volledige regressie-sweep**

Run: `python -m pytest tests/ -q`
Expected: PASS (inclusief de A1-tests: test_telemetry.py en test_observability.py blijven onaangeraakt groen)

- [ ] **Step 6: Commit**

```bash
git add src/span/server/app.py tests/test_whatsapp.py
git commit -m "feat(whatsapp): router + bridge-wiring achter whatsapp_enabled"
```

---

## Task 11: Livegang — Meta-setup (Bas-acties) + deploy-verificatie

**Files:** geen repo-bestanden — Modify (buiten repo): `~/nova/.env` op z390, de Meta-dashboard-configuratie en de cloudflared-config op de z390-host.

**Geen TDD-code — dit is een configuratie/deploy-taak.** Alles hierboven is offline getest; deze taak maakt het live zodra het testnummer er is. De spec-file (`docs/superpowers/specs/2026-07-12-highend-fase-ab-design.md`) wordt bewust NIET aangepast.

- [ ] **Step 1 (Bas-actie): Meta-app + testnummer**

Het aanmaken van de Business-app en het starten van de **business-verificatie** horen als het goed is al op dag 1 gestart te zijn, parallel aan Tasks 1-10 (spec §4: "start op dag 1"; doorlooptijd weken — vereist voor hogere tiers en fase-C Calling) — zie "Afhankelijkheden & volgorde". Zo niet: doe dat nu alsnog eerst. Daarna in het [Meta for Developers-dashboard](https://developers.facebook.com): product "WhatsApp" toevoegen → het testnummer (of LO's eigen nummer na OTP-verificatie van de prepaid/VoIP-SIM) koppelen. Noteer de **Phone number ID** en maak een **System User-token** (permanent, scope `whatsapp_business_messaging`). Voeg Bas' eigen nummer toe als toegestaan test-ontvanger.

- [ ] **Step 2: Env-vars op z390 zetten**

In `~/nova/.env` (nooit in git; `chmod 600 .env` na het wijzigen): `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` (zelf genereren: `python -c "import secrets;print(secrets.token_urlsafe(32))"`), `WHATSAPP_APP_SECRET` (uit de Meta-app: Instellingen → Basis), `WHATSAPP_ALLOWED_NUMBERS` (alleen Bas' wa_id, bv. `316xxxxxxxx`), optioneel `WHATSAPP_VOICE_REPLY=1`.

- [ ] **Step 3 (Bas-actie): Webhook publiek maken via cloudflared**

De cloudflared-config leeft op de z390-host, buiten deze repo. Publiceer het pad `/api/webhooks/whatsapp` op een publieke hostname (bv. `agent.famspaan.nl`) via de bestaande tunnel, **zonder Cloudflare Access-policy op dit pad** (Meta kan niet door een Access-login; de route beveiligt zichzelf met verify-token + signature). De rest van de app blijft achter de bestaande bescherming.

- [ ] **Step 4: Deploy + webhook registreren**

Volg het bestaande deploy-patroon (compose bewaren, `git fetch && git reset --hard origin/master`, compose terugzetten, `chmod 600 .env`, `docker compose up -d --build span`, `sleep 25`, `curl readyz`; `export MSYS_NO_PATHCONV=1` bij Windows-git-bash). Registreer daarna in het Meta-dashboard (WhatsApp → Configuration) de callback-URL `https://<hostname>/api/webhooks/whatsapp` + het verify-token en abonneer op het `messages`-veld. Meta doet de GET-handshake; het dashboard toont groen als Task 6 zijn werk doet.

- [ ] **Step 5: End-to-end verifiëren**

Zie "Handmatige verificatie" hieronder. Controleer ook de container-logs (`docker logs span | grep whatsapp`) op de allowlist-melding bij opstart en op `[whatsapp] genegeerd:`-regels bij een testbericht vanaf een vreemd nummer.

---

## Afhankelijkheden & volgorde

- **Bouwbasis:** `master`. `a1-telemetrie` is gemerged (PR #116, merge-commit 79a9990) en content-identiek aan master, dus `span.telemetry` (Task 8/9 importeren het) bestaat daar gewoon. Geen rebase-stap nodig.
- **Niet aanraken:** `src/span/telemetry.py`, `tests/test_telemetry.py`, `src/span/orchestrator/agent.py`, `src/span/server/routes.py`, `tests/test_observability.py` en de spec-file. Dit plan wijzigt geen van deze bestanden — de WhatsApp-laag blijft een losse, wegneembare adapter.
- **Dag-1 Bas-actie (parallel aan Tasks 1-10):** maak de Meta-app aan en start de business-verificatie METEEN (spec §4: "start op dag 1"; doorlooptijd weken) — zie Task 11, Step 1. Zo loopt die wachttijd naast het bouwwerk in plaats van erna.
- **Taakvolgorde:** Task 1 → 2 → 3 → 4 → 5 (bridge compleet voor tekst) → 6 → 7 (webhook) → 8 → 9 (voice) → 10 (wiring) → 11 (livegang). Task 2 kan desnoods parallel aan 1; al het andere is sequentieel (elke taak bouwt op typen/functies uit de vorige).
- **Task 11 wacht op Bas:** testnummer/SIM, de (op dag 1 gestarte) business-verificatie en het cloudflared-pad zijn Bas-acties buiten de repo. Tasks 1-10 zijn er niet van afhankelijk — alles draait offline met mocks (spec-eis).
- **Bewust NIET in dit plan (fase B/C):** proactieve berichten/templates buiten het 24-uurs-venster (B5), een `whatsapp_notify`-agent-tool (B5; krijgt dan een expliciete `TOOL_RISK`-tier zoals `telegram_notify`), WhatsApp Calling (fase C), streaming-STT (B1).

## Handmatige verificatie

Na Task 11, met LO live op z390:

1. **Handshake:** het Meta-dashboard toont de webhook als geverifieerd (groene status na de GET-handshake).
2. **Chat (laag 1):** stuur vanaf Bas' nummer "hoe laat is mijn eerste afspraak morgen?" → binnen ~15 s een inhoudelijk antwoord van LO in WhatsApp; het antwoord toont dat de agent-loop (agenda-tool) draaide, niet een kaal echo-antwoord.
3. **Allowlist:** stuur vanaf een ander nummer een bericht → géén antwoord; `docker logs span` toont `[whatsapp] genegeerd: bericht van niet-toegestaan nummer …`; het Meta-dashboard toont géén webhook-failures (200 werd teruggegeven).
4. **Dedupe:** in de logs geen dubbele agent-beurten voor hetzelfde wamid (Meta's redelivery wordt geslikt).
5. **Voice in (laag 2):** stuur een spraakmemo "wat staat er vandaag op de agenda" → LO antwoordt met tekst die bewijst dat de transcriptie klopte.
6. **Voice uit (achter flag):** met `WHATSAPP_VOICE_REPLY=1` en herstart: op een spraakmemo volgt naast tekst óók een afspeelbare voice-note in LO's stem (OGG/Opus wordt door WhatsApp als voice-bericht gerenderd, niet als bestandsbijlage).
7. **Telemetrie:** `curl -s -H "Authorization: Bearer <SPAN_AUTH_TOKEN>" https://nova.famspaan.nl/api/telemetry | jq '.segments'` toont `stt`- en `tts`-metingen; de JSONL-regels bevatten `"channel": "whatsapp"`.
8. **Wegneembaarheid (beleidsklep):** verwijder de WHATSAPP_*-vars uit `.env` en herstart → `/api/webhooks/whatsapp` geeft weer 404, de rest van LO draait ongewijzigd door. (De twee facebook-hosts blijven bewust statisch in de egress-allowlist staan — zie de afweging bij Task 2; zonder actieve bridge roept niets ze aan.)

## Self-Review (uitgevoerd door de plan-auteur)

**Spec-dekking (§A6 + §5):**
- Officiële Cloud API, direct, geen BSP; geen onofficiële bibliotheken → alle calls naar `graph.facebook.com/v21.0`, alleen `requests`/PyAV. ✓
- Laag 1: webhook → bestaande agent-loop → antwoord via Cloud API → Tasks 5/6/7. Zelfde governance als Telegram: beurt via ongewijzigde `SpanAgent.turn()`. ✓
- Laag 2: voice-note → media-download → bestaande STT (Task 8); antwoord optioneel als voice-note via TTS + OGG/Opus (Task 9, achter flag, default uit). ✓
- Untrusted input: signature over rauwe body, payload alleen als user-bericht, allowlist = vertrouwensgrens, media-URL door de egress-guard. ✓
- Dunne wegneembare adapter: twee nieuwe modules + drie wiring-regels; verwijderen raakt geen kernlogica (verificatiestap 8). ✓
- Alles achter config: WHATSAPP_TOKEN / WHATSAPP_PHONE_ID / WHATSAPP_VERIFY_TOKEN / WHATSAPP_ALLOWED_NUMBERS (spec) + WHATSAPP_APP_SECRET (nodig voor de door Meta verplichte signature-check) + WHATSAPP_VOICE_REPLY (spec: "optioneel"). Fail-closed zonder config. ✓
- Alleen Bas' nummer; anderen negeren + loggen → Task 5 + verificatiestap 3. ✓
- Testbaar vóór het testnummer: alle Cloud-API/STT/TTS-calls gemockt, signatures met echte HMAC — Tasks 1-10 draaien volledig offline. ✓

**Placeholder-scan:** geen TBD/TODO. De `_transcribe_voice`-stub in Task 5 is expliciet gemarkeerde taak-volgorde die Task 8 volledig invult — beide kanten tonen echte code.

**Type-consistentie:** `send_text(to, text) -> bool`, `send_voice(to, text) -> bool`, `download_media(media_id) -> bytes`, `handle_message(msg: dict) -> None`, `_wav_to_ogg_opus(wav: bytes) -> bytes` — identiek gebruikt in Tasks 3-10 en in alle tests. `whatsapp_allowlist: frozenset[str]` matcht `b._allowed` in de test-helper.

**Robuustheid:** een telemetrie-fout kan geen beurt breken (A1-`record` slikt alles); een voice-antwoord-fout laat het al verstuurde tekst-antwoord staan; één kapot bericht sleept via de per-bericht try/except in `_run_message` de rest niet mee én levert de allowlisted afzender een eerlijke foutmelding op (spec §5: geen stille drops); een spraakmemo zonder werkende STT of met lege media-download krijgt eveneens een eerlijk tekstantwoord i.p.v. genegeerd te worden; de webhook ackt altijd 200 op geldige signatures zodat Meta niet blijft redeliveren.

**Scope:** alleen laag 1+2. Proactief versturen, templates, Calling en agent-tools zijn expliciet fase B/C. ✓
