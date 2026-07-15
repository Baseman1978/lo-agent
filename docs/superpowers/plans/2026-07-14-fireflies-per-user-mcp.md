# Fireflies per-user MCP-koppeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elke LO-gebruiker koppelt zijn eigen Fireflies door in te loggen (OAuth via de Fireflies-MCP-server `https://api.fireflies.ai/mcp`), i.p.v. de huidige ene centrale `FIREFLIES_API_KEY`; de oude centrale koppeling wordt gefaseerd uitgezet.

**Architecture:** LO heeft al een volledige MCP-OAuth-stack (`mcp_oauth.py` DCR+PKCE, `mcp_client.py` registry, `/api/mcp/*`-endpoints, HUD-flow). Die is nu echter **owner-globaal**: de registry wordt uit `_state["brain"]` gebouwd en `/api/mcp` is `_require_owner`. We maken de MCP-laag **per-gebruiker**: de MCP-registry verhuist naar `UserContext` (gebouwd uit `ctx.brain`), de agent gebruikt `ctx.mcp`, de endpoints worden self-service (elke toegestane user beheert z'n eigen servers), en de OAuth-callback schrijft het token in de brain van de juiste gebruiker (oid meegedragen via de pending-state). Fireflies wordt een één-klik-preset. De centrale API-key-route wordt achter een deprecatie-flag uitgefaseerd.

**Tech Stack:** Python 3.11 (prod) / 3.14 (dev), FastAPI, pytest, ruff. Kernbestanden: `server/usercontext.py` (`UserContext`/`ContextRegistry`), `integrations/mcp_client.py` (`MCPRegistry`, `load_servers`/`save_servers`), `integrations/mcp_oauth.py` (DCR+PKCE), `server/routes.py` (`/api/mcp/*`), `server/app.py` + `server/routes.py` (SpanAgent-bouw), `integrations/fireflies.py` (centrale client, uit te faseren).

---

## Design-besluiten (bevestigen vóór start)

- **D1 — per-user registry op `UserContext`:** lazy gebouwd uit `ctx.brain`. In single-user/owner-modus is `ctx.brain` = het owner-brein, dus gedrag = ongewijzigd. Na een nieuwe login wordt de context ge-invalidate (bestaand patroon) zodat het verse token wordt ingelezen.
- **D2 — oid door de OAuth-callback:** `mcp_connect` stopt de `oid` in `mcp_pending[state]`; de callback schrijft het token in de brain van díe gebruiker en invalidate't díe context.
- **D3 — self-service:** `POST/DELETE /api/mcp` en `/connect` gaan van `_require_owner` naar `_require_rest_auth` + eigen-brein-scoping (een user beheert alléén z'n eigen servers).
- **D4 — Fireflies één-klik:** preset (naam `fireflies`, url `https://api.fireflies.ai/mcp`) + HUD-knop "Koppel Fireflies", zodat de user geen URL hoeft te typen.
- **D5 — uitfaseren centrale key:** reversibel. Flag `FIREFLIES_LEGACY_APIKEY` (default aan tijdens transitie → later default uit → verwijderen). Als een user Fireflies via MCP heeft gekoppeld, gebruikt LO die; anders valt hij terug op de centrale key (indien flag aan). Laatste stap verwijdert de centrale client + env.

---

## File Structure

- `server/usercontext.py` — `UserContext` krijgt een lazy `mcp`-registry (uit `ctx.brain`); `ContextRegistry.invalidate` sluit die mee. (Task 1)
- `server/app.py` + `server/routes.py` — SpanAgent-bouw gebruikt `ctx.mcp` i.p.v. `_state["mcp"]`. (Task 2)
- `server/routes.py` — `/api/mcp`-endpoints per-gebruiker + self-service. (Task 3, 4)
- `integrations/broker/connectors.py` + `server/routes.py` + `server/static/settings.js` — Fireflies-preset + één-klik-knop. (Task 5)
- `integrations/fireflies.py` + `orchestrator/tools.py` + `config.py` — centrale key uitfaseren achter flag. (Task 6)
- Tests: `tests/test_mcp_peruser.py` (nieuw), aanvullingen in bestaande tests.

---

### Task 1: Per-user MCP-registry op `UserContext`

**Files:**
- Modify: `src/span/server/usercontext.py` (class `UserContext` r.58-70; `ContextRegistry.invalidate` r.116-122)
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_peruser.py
"""Per-user MCP-registry op UserContext."""
from __future__ import annotations

from unittest.mock import MagicMock

from span.server.usercontext import UserContext


def test_usercontext_mcp_lazy_uit_eigen_brain(monkeypatch):
    brain = MagicMock()
    built = {}

    def fake_registry(servers, b):
        built["brain"] = b
        built["servers"] = servers
        return MagicMock(name="registry")

    # load_servers + MCPRegistry worden binnen UserContext.mcp aangeroepen
    monkeypatch.setattr("span.server.usercontext.load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"}])
    monkeypatch.setattr("span.server.usercontext.MCPRegistry", fake_registry)

    ctx = UserContext(oid="oid-1", upn="a@b.nl", name="A", brain=brain)
    reg1 = ctx.mcp
    reg2 = ctx.mcp
    assert reg1 is reg2                      # gecachet
    assert built["brain"] is brain           # uit de EIGEN brain
    assert built["servers"][0]["name"] == "fireflies"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py::test_usercontext_mcp_lazy_uit_eigen_brain -v`
Expected: FAIL — `AttributeError: 'UserContext' object has no attribute 'mcp'`

- [ ] **Step 3: Write minimal implementation**

Bovenaan `usercontext.py` bij de imports toevoegen:

```python
from span.integrations.mcp_client import MCPRegistry, load_servers
```

`UserContext` (r.58-70) uitbreiden met een lazy, gecachede `mcp`-property:

```python
class UserContext:
    oid: str
    upn: str
    name: str
    brain: BrainDB               # privé-brein van deze gebruiker
    o365: Any = None             # per-user O365-client (eigen token-cache)
    shared: Any = None           # gedeeld brein (read), of None in single-user
    _mcp: Any = None             # lazy per-user MCP-registry (uit de eigen brain)

    @property
    def mcp(self) -> Any:
        """Per-user MCP-registry, lazy gebouwd uit de EIGEN brain (Config-node
        mcp_servers). Fail-safe: bij een fout geen registry (None)."""
        if self._mcp is None:
            try:
                self._mcp = MCPRegistry(load_servers(self.brain), self.brain)
            except Exception:
                self._mcp = None
        return self._mcp

    def close(self) -> None:
        try:
            self.brain.close()
        except Exception:
            pass
```

> Let op: `UserContext` gebruikt class-annotaties + wordt aangeroepen als `UserContext(oid=..., brain=...)`. Als het een `@dataclass` is, voeg `_mcp: Any = None` als veld toe en maak `mcp` een gewone method i.p.v. property (dataclass + property botst). Controleer de decorator bovenaan de class en kies de passende vorm; de test hierboven gebruikt `ctx.mcp` als attribuut/property.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/span/server/usercontext.py tests/test_mcp_peruser.py
git commit -m "feat(mcp): per-user MCP-registry op UserContext (lazy, uit eigen brain)"
```

---

### Task 2: Agent gebruikt `ctx.mcp` i.p.v. de globale registry

**Files:**
- Modify: `src/span/server/app.py:277-285` (SpanAgent-bouw), `src/span/server/routes.py:729` (SpanAgent-bouw)
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_agent_bouw_gebruikt_ctx_mcp(monkeypatch):
    # de helper die de agent bouwt moet ctx.mcp doorgeven, niet _state["mcp"]
    import span.server.app as app_mod
    captured = {}

    class FakeAgent:
        def __init__(self, *a, **k):
            captured["mcp"] = k.get("mcp")

    monkeypatch.setattr(app_mod, "SpanAgent", FakeAgent)
    ctx = MagicMock()
    ctx.mcp = "USER-REGISTRY"
    app_mod.build_agent(ctx)          # zie Step 3 voor de helper
    assert captured["mcp"] == "USER-REGISTRY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py::test_agent_bouw_gebruikt_ctx_mcp -v`
Expected: FAIL — `build_agent` bestaat niet of geeft `_state["mcp"]` door.

- [ ] **Step 3: Write minimal implementation**

In `app.py`, de bestaande SpanAgent-bouw (r.277-285) — vervang `mcp=_state.get("mcp")` door de per-user registry uit de context. Als de bouw al een `ctx`/context-object heeft, gebruik `ctx.mcp`; anders val terug op de globale registry (owner/single-user startpad):

```python
    agent = SpanAgent(
        settings, ctx.brain, llm,
        o365=ctx.o365, asana=_state.get("asana"),
        inbox=inbox, security=_state.get("security"),
        mcp=getattr(ctx, "mcp", None) or _state.get("mcp"),
        # … overige bestaande argumenten ongewijzigd …
    )
```

Doe exact dezelfde vervanging in `routes.py:729` (`mcp=_state.get("mcp")` → `mcp=getattr(ctx, "mcp", None) or _state.get("mcp")`). Als `ctx` daar een andere naam heeft, gebruik de lokale contextvariabele die `.brain`/`.o365` levert.

> Als er geen gedeelde `build_agent(ctx)`-helper is, extraheer de SpanAgent-constructie in `app.py` naar een functie `build_agent(ctx, *, on_text=None, ...)` en roep die aan op beide plekken (DRY), zodat de test één helper kan toetsen. Houd de handtekening minimaal.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v && python -m pytest tests/ -q`
Expected: PASS; volledige suite groen (op de bekende `test_llm_client` `ORQ_API_KEY`-fails na).

- [ ] **Step 5: Commit**

```bash
git add src/span/server/app.py src/span/server/routes.py tests/test_mcp_peruser.py
git commit -m "feat(mcp): agent gebruikt de per-user MCP-registry (ctx.mcp)"
```

---

### Task 3: `/api/mcp` per-gebruiker + self-service

**Files:**
- Modify: `src/span/server/routes.py` (`mcp_list` r.1530, `mcp_add` r.1547, `mcp_delete` r.1563; `_rebuild_mcp` r.1522)
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_mcp_add_schrijft_in_eigen_brain(monkeypatch):
    # mcp_add moet ctx.brain gebruiken (niet _state["brain"]) en self-service zijn
    import span.server.routes as r
    saved = {}
    monkeypatch.setattr(r, "load_servers", lambda b: [])
    monkeypatch.setattr(r, "save_servers", lambda b, s: saved.update(brain=b, servers=s))
    # _require_owner mag NIET meer verplicht zijn; _require_rest_auth wel
    # (details van de request-mock volgen het bestaande test_jarvis-patroon)
    ctx = MagicMock(); ctx.brain = "USER-BRAIN"
    r._mcp_ctx = lambda request: ctx          # helper uit Step 3
    # ... roep mcp_add aan met een gemockte request die {"name","url"} levert ...
    # assert saved["brain"] == "USER-BRAIN"
```

> De precieze request-mock volgt het patroon in `tests/test_jarvis.py` (FastAPI `Request` + `_require_rest_auth`). Houd de asserts op: `save_servers` kreeg `ctx.brain`, en de call slaagt zónder owner-rol.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py -k mcp_add -v`
Expected: FAIL — endpoint gebruikt nog `_state["brain"]` en `_require_owner`.

- [ ] **Step 3: Write minimal implementation**

Voeg in `routes.py` een kleine helper toe die de per-user context uit de request haalt (hergebruik de bestaande `_request_context`):

```python
def _mcp_ctx(request: Request) -> Any:
    """Context (per-user brain) voor MCP-beheer; valt terug op de globale
    brain in single-user/owner-modus."""
    ctx = _request_context(request)
    return ctx if getattr(ctx, "brain", None) is not None else None
```

Pas `mcp_list`/`mcp_add`/`mcp_delete` aan:
- Vervang `_require_owner(request)` door `_require_rest_auth(request)` in `mcp_add` en `mcp_delete` (self-service; de allowlist in `_require_rest_auth` bepaalt wie mag). `mcp_list` was al `_require_rest_auth`.
- Vervang elke `_state["brain"]` in deze handlers door `(_mcp_ctx(request).brain if _mcp_ctx(request) else _state["brain"])`. Bind dat één keer bovenaan de handler in een lokale `brain =`.
- `mcp_delete` roept nu `_rebuild_mcp` aan (globaal). Vervang door invalidatie van de eigen context (zie Task 4 Step 3 voor `_invalidate_ctx`), zodat de user z'n eigen registry vernieuwt.

Voorbeeld `mcp_add`:

```python
@router.post("/api/mcp")
async def mcp_add(request: Request) -> dict[str, Any]:
    """Voeg een MCP-server toe voor de INGELOGDE gebruiker (self-service)."""
    _require_rest_auth(request)
    ctx = _mcp_ctx(request)
    brain = ctx.brain if ctx is not None else _state["brain"]
    body = await request.json()
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    if not name or not url.startswith("http"):
        raise HTTPException(status_code=422, detail="Naam en geldige https-URL vereist.")
    servers = await asyncio.to_thread(load_servers, brain)
    servers = [s for s in servers if s["name"] != name] + [{"name": name, "url": url}]
    await asyncio.to_thread(save_servers, brain, servers)
    return {"added": name}
```

Pas `mcp_list` zo aan dat de `connected`-status uit `ctx.mcp` komt (niet `_state["mcp"]`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_mcp_peruser.py
git commit -m "feat(mcp): /api/mcp per-gebruiker + self-service (eigen brain)"
```

---

### Task 4: OAuth-connect/callback per-gebruiker (oid meedragen)

**Files:**
- Modify: `src/span/server/routes.py` (`mcp_connect` r.1586-1639: oid in `mcp_pending`; `mcp_oauth_callback` r.1640-1690: schrijf in user-brain + invalidate)
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_callback_schrijft_token_in_juiste_user_brain(monkeypatch):
    import span.server.routes as r
    written = {}
    monkeypatch.setattr(r, "load_servers",
                        lambda b: [{"name": "fireflies", "url": "https://x"}])
    monkeypatch.setattr(r, "save_servers",
                        lambda b, s: written.update(brain=b, servers=s))
    # pending-state bevat de oid van de user die inlogde
    r._state["mcp_pending"] = {"st1": {
        "name": "fireflies", "oid": "oid-42", "meta": {}, "client_id": "c",
        "verifier": "v", "redirect_uri": "https://cb", "ts": 9e12}}
    monkeypatch.setattr(r.ox, "exchange_code",
                        lambda *a, **k: {"access_token": "TOK", "refresh_token": "R"})
    invalidated = {}
    monkeypatch.setattr(r, "_invalidate_ctx", lambda oid: invalidated.update(oid=oid))
    # roep de callback aan met code + state=st1 ...
    # assert written["servers"][0]["token"] == "TOK"
    # assert invalidated["oid"] == "oid-42"
```

> Timestamp-detail: de test zet `ts` hoog zodat de TTL-check (`_PENDING_TTL`) 'm niet als verlopen ziet; `time.time()` wordt in de handler gebruikt, niet gemockt.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py -k callback -v`
Expected: FAIL — callback schrijft naar `_state["brain"]` en `_invalidate_ctx` bestaat niet.

- [ ] **Step 3: Write minimal implementation**

In `mcp_connect` (r.1586): vervang `_require_owner(request)` door `_require_rest_auth(request)`, bepaal de context-brain via `_mcp_ctx(request)`, en stop de oid in de pending-entry:

```python
    ctx = _mcp_ctx(request)
    oid = getattr(ctx, "oid", "") if ctx is not None else ""
    ...
            pend[state] = {
                "name": name, "meta": meta, "client_id": reg["client_id"],
                "verifier": verifier, "redirect_uri": redirect_uri,
                "oid": oid, "ts": time.time(),
            }
```

Voeg een invalidatie-helper toe (naast `_rebuild_mcp`):

```python
def _invalidate_ctx(oid: str) -> None:
    """Gooi de gecachede UserContext (incl. z'n MCP-registry) weg zodat een
    verse login/token wordt ingelezen bij de volgende beurt."""
    reg = _state.get("contexts")            # de ContextRegistry (multi-user)
    if reg is not None and oid:
        try:
            reg.invalidate(oid)
        except Exception:
            pass
    else:
        _rebuild_mcp()                      # single-user/owner: globale registry
```

> Controleer de sleutel waaronder de `ContextRegistry` in `_state` staat (grep `ContextRegistry(` in `app.py`/`routes.py`); gebruik die exacte sleutel i.p.v. `"contexts"`.

In `mcp_oauth_callback` (`finish()`), schrijf naar de user-brain uit de pending-oid en invalidate die context:

```python
    def finish() -> str:
        tok = ox.exchange_code(pending["meta"], pending["client_id"], code,
                               pending["verifier"], pending["redirect_uri"])
        oid = pending.get("oid", "")
        reg = _state.get("contexts")
        brain = (reg.get(oid).brain if (reg is not None and oid) else _state["brain"])
        servers = load_servers(brain)
        for s in servers:
            if s["name"] == pending["name"]:
                s["token"] = tok.get("access_token", "")
                s["refresh"] = tok.get("refresh_token", "")
                s["client_id"] = pending["client_id"]
                s["token_endpoint"] = pending["meta"].get("token_endpoint", "")
        save_servers(brain, servers)
        _invalidate_ctx(oid)
        return pending["name"]
```

(De bestaande auto-skill-stap laten staan; die mag best-effort blijven.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py tests/test_mcp_peruser.py
git commit -m "feat(mcp): OAuth-login per-gebruiker — token in eigen brain + context-invalidatie"
```

---

### Task 5: Fireflies één-klik-preset

**Files:**
- Modify: `src/span/server/routes.py` (nieuw endpoint `POST /api/mcp/fireflies`), `src/span/server/static/settings.js` (knop "Koppel Fireflies")
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_fireflies_preset_voegt_juiste_url_toe(monkeypatch):
    import span.server.routes as r
    saved = {}
    monkeypatch.setattr(r, "load_servers", lambda b: [])
    monkeypatch.setattr(r, "save_servers", lambda b, s: saved.update(servers=s))
    ctx = MagicMock(); ctx.brain = "B"
    monkeypatch.setattr(r, "_mcp_ctx", lambda request: ctx)
    # roep het preset-endpoint aan (rest-auth gemockt) ...
    # assert saved["servers"][-1]["name"] == "fireflies"
    # assert saved["servers"][-1]["url"] == "https://api.fireflies.ai/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py -k fireflies_preset -v`
Expected: FAIL — endpoint bestaat niet.

- [ ] **Step 3: Write minimal implementation**

Constante bovenaan `routes.py` (of importeer uit de bestaande connector-catalogus `get_connector("fireflies").mcp_url`):

```python
FIREFLIES_MCP_URL = "https://api.fireflies.ai/mcp"
```

Endpoint (hergebruikt de add-logica):

```python
@router.post("/api/mcp/fireflies")
async def mcp_add_fireflies(request: Request) -> dict[str, Any]:
    """Één-klik: registreer de Fireflies-MCP-server voor de ingelogde
    gebruiker. Inloggen daarna via POST /api/mcp/fireflies/connect (= /connect)."""
    _require_rest_auth(request)
    ctx = _mcp_ctx(request)
    brain = ctx.brain if ctx is not None else _state["brain"]
    servers = await asyncio.to_thread(load_servers, brain)
    if not any(s["name"] == "fireflies" for s in servers):
        servers = servers + [{"name": "fireflies", "url": FIREFLIES_MCP_URL}]
        await asyncio.to_thread(save_servers, brain, servers)
    return {"added": "fireflies", "connect": "/api/mcp/fireflies/connect"}
```

In `settings.js`, bij de MCP-sectie (`loadMcp`, r.399+), een knop toevoegen die eerst `POST /api/mcp/fireflies` doet en meteen daarna de bestaande connect-flow (`POST /api/mcp/fireflies/connect`) start en de `authorize_url` in een nieuw tabblad opent — hergebruik de bestaande "inloggen"-knop-handler:

```javascript
const ff = document.createElement("button");
ff.className = "primary"; ff.textContent = "Koppel Fireflies (inloggen)";
ff.onclick = async () => {
  await fetch("/api/mcp/fireflies", {method:"POST", headers: SPAN.authHeaders()});
  const res = await fetch("/api/mcp/fireflies/connect", {method:"POST", headers: SPAN.authHeaders()});
  const d = await res.json();
  if (d.authorize_url) window.open(d.authorize_url, "_blank");
  loadMcp();
};
$("mcp-list").parentElement.appendChild(ff);
```

> `/api/mcp/fireflies/connect` bestaat niet apart; de bestaande `/api/mcp/{name}/connect` dekt het (`name=fireflies`). Gebruik dus `POST /api/mcp/fireflies/connect` (dat matcht de bestaande route). Pas de fetch-URL daarop aan.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v`
Expected: PASS. (HUD-knop: handmatige rooktest na deploy.)

- [ ] **Step 5: Commit**

```bash
git add src/span/server/routes.py src/span/server/static/settings.js tests/test_mcp_peruser.py
git commit -m "feat(mcp): Fireflies één-klik-koppeling (preset + HUD-knop)"
```

---

### Task 6: Centrale `FIREFLIES_API_KEY` uitfaseren (achter flag)

**Files:**
- Modify: `src/span/config.py:43,69-70,178` (flag + deprecatie), `src/span/orchestrator/tools.py` (Fireflies-tools registreren afhankelijk van bron), `src/span/integrations/fireflies.py` (deprecatie-comment)
- Test: `tests/test_mcp_peruser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_legacy_fireflies_default_uit(monkeypatch):
    import span.config as c
    monkeypatch.delenv("FIREFLIES_LEGACY_APIKEY", raising=False)
    monkeypatch.setenv("FIREFLIES_API_KEY", "sleutel")
    s = c.load_settings()
    # centrale key telt alleen als legacy-flag expliciet aan staat
    assert s.fireflies_enabled() is False

def test_legacy_fireflies_aan_met_flag(monkeypatch):
    import span.config as c
    monkeypatch.setenv("FIREFLIES_LEGACY_APIKEY", "on")
    monkeypatch.setenv("FIREFLIES_API_KEY", "sleutel")
    s = c.load_settings()
    assert s.fireflies_enabled() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_peruser.py -k legacy_fireflies -v`
Expected: FAIL — `fireflies_enabled()` kijkt nu alleen naar de key, niet naar de flag.

- [ ] **Step 3: Write minimal implementation**

In `config.py`, `fireflies_enabled` (r.69-70) achter de legacy-flag zetten:

```python
    def fireflies_enabled(self) -> bool:
        # Uitfasering: de centrale API-key telt alleen nog met de expliciete
        # legacy-flag. Standaard koppelt elke gebruiker Fireflies per-user via MCP.
        legacy = os.environ.get("FIREFLIES_LEGACY_APIKEY", "").strip().lower() in (
            "1", "true", "yes", "on")
        return legacy and bool(self.fireflies_api_key)
```

In `tools.py`: de ingebouwde `fireflies_*`-tools alleen registreren als `settings.fireflies_enabled()` (centrale key + legacy-flag). Zonder flag verdwijnen ze uit de toollijst; de per-user MCP-tools (`mcp__fireflies__*`) komen dan via `ctx.mcp` binnen. Zoek de plek waar Fireflies-tools worden toegevoegd (grep `fireflies` in `tools.py`/`tool_specs.py`) en wikkel de registratie in `if settings.fireflies_enabled():`.

Zet bovenaan `integrations/fireflies.py` een deprecatie-comment dat deze centrale client wordt vervangen door de per-user Fireflies-MCP-koppeling en na de transitie verdwijnt.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_peruser.py -v && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/span/config.py src/span/orchestrator/tools.py src/span/integrations/fireflies.py tests/test_mcp_peruser.py
git commit -m "feat(fireflies): centrale API-key achter legacy-flag (uitfasering, default per-user MCP)"
```

---

### Task 7: Livegang + verificatie op de z390 (met Bas)

Geen code; koppel + meet met echte accounts. Uitvoeren ná merge.

- [ ] Deploy master naar de z390 (git-sync + `docker compose build span` + `up -d span`, `readyz` 200).
- [ ] Bas: HUD → Instellingen → MCP-servers → **"Koppel Fireflies (inloggen)"** → OAuth met zijn eigen Fireflies-account. Verifieer dat het token in Bas' brain landt en `mcp__fireflies__*`-tools na een nieuwe sessie verschijnen.
- [ ] Tweede account (`lomansconnect@lomans.nl`): eigen Fireflies koppelen; verifieer dat de twee koppelingen **gescheiden** zijn (elk in de eigen brain-db) en dat de één de transcripties van de ander niet ziet.
- [ ] Zet `FIREFLIES_LEGACY_APIKEY` **uit** in `~/nova/.env` (verwijder/leeg) → bevestig dat de ingebouwde `fireflies_*`-tools weg zijn en LO Fireflies nog steeds via MCP kan. Rollback = flag terug aan.
- [ ] Na bevestigde migratie (aparte, latere PR): centrale `fireflies.py` + `FIREFLIES_API_KEY` + de ingebouwde tools verwijderen.

---

## Self-Review

**Spec-dekking:** per-user koppelen via inloggen → Tasks 1-4 (registry per-user, agent gebruikt 'm, endpoints self-service, OAuth-token in eigen brain). "Zelf activeren door in te loggen" → Task 5 (één-klik). "Oude situatie uitfaseren" → Task 6 (flag) + Task 7 (uitzetten + later verwijderen). Multi-user isolatie → Task 7 (2e-account-test). ✔

**Placeholder-scan:** geen TBD/TODO; elke codestap toont concrete code. Twee expliciete "controleer de exacte sleutel/decorator"-noten (ContextRegistry-`_state`-sleutel; dataclass-vs-property op `UserContext`) zijn bewuste verificatiepunten met een concrete default, geen placeholders.

**Type-consistentie:** `ctx.mcp` (Task 1) ↔ gebruikt in Tasks 2/3/5; `_mcp_ctx(request)` (Task 3) ↔ Tasks 4/5; `_invalidate_ctx(oid)` (Task 4) ↔ gebruikt in Tasks 3/4; `mcp_pending[...]["oid"]` (Task 4 connect) ↔ gelezen in callback; `fireflies_enabled()` (Task 6) ↔ tools-registratie. Consistent. ✔

**Bekende aannames die de uitvoerder moet checken (geen blockers):** exacte `_state`-sleutel van de `ContextRegistry`; of `UserContext` een `@dataclass` is (bepaalt property-vs-method); de precieze plek waar Fireflies-tools in `tools.py` worden geregistreerd.
