"""Span web-server — FastAPI + WebSocket streaming.

Eén proces serveert de JARVIS-UI (static) en de chat-API. Elke
WebSocket-verbinding is één sessie met een eigen SpanAgent; bij nette
afsluiting (end-bericht) draait de evaluatiecirkel.

Beveiliging: zet SPAN_AUTH_TOKEN in de omgeving. Zonder token weigert de
server alles behalve localhost-verkeer.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from span import AGENT_NAME, __version__
from span.config import Settings, load_settings
from span.db.brain import BrainDB
from span.db.schema import init_schema
from span.db.work import WorkDB
from span.evaluation.reflect import reflect_session
from span.integrations import build_integrations
from span.jarvis.ambient import AgentInbox, ambient_watcher
from span.jarvis.briefing import build_briefing
from span.jarvis.daily import briefing_time, daily_scheduler, generate_daily, set_briefing_time
from span.llm.client import LLMClient
from span.memory.bootstrap import start_session
from span.memory.fragments import FragmentStore
from span.orchestrator.agent import SpanAgent

STATIC_DIR = Path(__file__).parent / "static"

_state: dict[str, Any] = {}


def _auth_token() -> str:
    return os.environ.get("SPAN_AUTH_TOKEN", "").strip()


def _is_local(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def _check_token(token: str, client_host: str | None) -> bool:
    expected = _auth_token()
    if expected:
        return token == expected
    return _is_local(client_host)  # geen token gezet: alleen lokaal toegestaan


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    brain = BrainDB(settings)
    brain.verify()
    init_schema(brain, settings)  # idempotent — server is meteen bruikbaar
    work = None
    if settings.work:
        try:
            work = WorkDB(settings.work)
            work.verify()
        except Exception:
            work = None
    o365, asana = build_integrations(settings)
    overrides = brain.run(
        "MATCH (c:Config {id:'runtime'}) "
        "RETURN c.model_main AS model_main, c.model_light AS model_light, "
        "       c.autonomy_mail AS autonomy_mail, c.autonomy_event AS autonomy_event, "
        "       c.triage_rules AS triage_rules, c.disabled_tools AS disabled_tools"
    )
    cfg = overrides[0] if overrides else {}
    _state.update(
        settings=settings,
        brain=brain,
        llm=LLMClient(settings),
        work=work,
        o365=o365,
        asana=asana,
        o365_flow=None,
        model_overrides=cfg,
        autonomy={
            "mail": cfg.get("autonomy_mail") or "ask",
            "event": cfg.get("autonomy_event") or "ask",
        },
        inbox=AgentInbox(),
        triage_rules=cfg.get("triage_rules") or "",
        disabled_tools=set(cfg.get("disabled_tools") or []),
    )
    ff_key = os.environ.get("FIREFLIES_API_KEY", "").strip()
    if ff_key:
        from span.integrations.fireflies import FirefliesClient
        _state["fireflies"] = FirefliesClient(ff_key)
    if not _auth_token():
        print("WAARSCHUWING: SPAN_AUTH_TOKEN niet gezet — alleen localhost toegestaan.")
    scheduler = asyncio.create_task(daily_scheduler(_state))
    watcher = asyncio.create_task(ambient_watcher(_state))
    telegram_task = None
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if tg_token:
        from span.integrations.telegram import TelegramBridge
        _state["telegram"] = TelegramBridge(tg_token, _state)
        telegram_task = asyncio.create_task(_state["telegram"].run())
    yield
    scheduler.cancel()
    watcher.cancel()
    if telegram_task:
        telegram_task.cancel()
    brain.close()
    if work:
        work.close()


app = FastAPI(title=f"{AGENT_NAME} API", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _require_rest_auth(request: Request) -> None:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    client_host = request.client.host if request.client else None
    if not _check_token(token, client_host):
        raise HTTPException(status_code=401, detail="Ongeldige of ontbrekende token.")


@app.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]
    counts = {}
    for label in ["MemoryFragment", "Insight", "Mistake", "Idea", "Quest", "Skill", "Protocol", "Session"]:
        counts[label] = brain.run(f"MATCH (n:{label}) RETURN count(n) AS n")[0]["n"]
    return {"agent": AGENT_NAME, "version": __version__, "counts": counts}


@app.get("/api/memory")
async def memory(request: Request, q: str = Query(...), k: int = Query(8, le=25)) -> list[dict]:
    _require_rest_auth(request)
    fragments = FragmentStore(_state["brain"], _state["llm"])
    return await asyncio.to_thread(fragments.search, q, k)


def _effective_settings() -> Settings:
    """Basis-settings + runtime model-overrides (instellingenpagina)."""
    base: Settings = _state["settings"]
    ov = _state.get("model_overrides") or {}
    main = (ov.get("model_main") or "").strip() if ov.get("model_main") else ""
    light = (ov.get("model_light") or "").strip() if ov.get("model_light") else ""
    if not main and not light:
        return base
    return replace(
        base,
        model_main=main or base.model_main,
        model_light=light or base.model_light,
    )


@app.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    base: Settings = _state["settings"]
    eff = _effective_settings()
    o365 = _state.get("o365")
    return {
        "model_main": eff.model_main,
        "model_light": eff.model_light,
        "defaults": {"model_main": base.model_main, "model_light": base.model_light},
        "o365": {
            "configured": o365 is not None,
            "authenticated": bool(o365) and await asyncio.to_thread(o365.is_authenticated),
            "account": o365.account_name() if o365 else None,
        },
        "asana": {"configured": _state.get("asana") is not None},
        "work_db": _state.get("work") is not None,
        "briefing_time": await asyncio.to_thread(briefing_time, _state["brain"]),
        "autonomy": dict(_state["autonomy"]),
        "triage_rules": await asyncio.to_thread(
            lambda: ((_state["brain"].run(
                "MATCH (c:Config {id:'runtime'}) RETURN c.triage_rules AS r"
            ) or [{}])[0].get("r")) or ""
        ),
        "tools": _tools_overview(),
        "telegram": {
            "configured": _state.get("telegram") is not None,
            "linked": bool(_state.get("telegram")) and _state["telegram"].linked,
        },
        "fireflies": {"configured": _state.get("fireflies") is not None},
        "system_prompt": await asyncio.to_thread(
            lambda: ((_state["brain"].run(
                "MATCH (c:Config {id:'runtime'}) RETURN c.system_prompt AS sp"
            ) or [{}])[0].get("sp")) or ""
        ),
        "system_prompt_default": __import__(
            "span.orchestrator.agent", fromlist=["BASE_PROMPT"]).BASE_PROMPT,
    }


def _tools_overview() -> list[dict[str, Any]]:
    """Alle tools met groep, lees/schrijf en status — voor de permissie-tab."""
    from span.orchestrator.tools import TOOL_META
    disabled = _state.get("disabled_tools") or set()
    available_groups = {
        "Brein": True, "Briefing": True, "Agent Inbox": True,
        "O365 Mail": _state.get("o365") is not None,
        "O365 Agenda": _state.get("o365") is not None,
        "O365 To Do": _state.get("o365") is not None,
        "Asana": _state.get("asana") is not None,
        "Werkdata": _state.get("work") is not None,
        "Weer": True,
        "Fireflies": _state.get("fireflies") is not None,
        "Planning": True,
    }
    return [
        {"name": name, "group": group, "access": access,
         "enabled": name not in disabled,
         "available": available_groups.get(group, True)}
        for name, (group, access) in TOOL_META.items()
    ]


@app.post("/api/settings")
async def save_settings(request: Request) -> dict[str, Any]:
    """Model-overrides opslaan (leeg = terug naar default uit .env)."""
    _require_rest_auth(request)
    body = await request.json()
    if "autonomy" in body:
        new = body["autonomy"] or {}
        for key in ("mail", "event"):
            if new.get(key) in ("ask", "auto"):
                _state["autonomy"][key] = new[key]
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) "
            "SET c.autonomy_mail = $m, c.autonomy_event = $e",
            m=_state["autonomy"]["mail"], e=_state["autonomy"]["event"],
        )
        if len(body) == 1:
            return {"saved": True, "autonomy": dict(_state["autonomy"])}
    if "disabled_tools" in body:
        from span.orchestrator.tools import TOOL_META
        disabled = [t for t in (body["disabled_tools"] or []) if t in TOOL_META]
        _state["disabled_tools"] = set(disabled)
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.disabled_tools = $d", d=disabled,
        )
        if len(body) == 1:
            return {"saved": True, "disabled_tools": disabled}
    if "system_prompt" in body:
        sp = str(body["system_prompt"])[:8000].strip()
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.system_prompt = $sp",
            sp=sp or None,  # leeg = terug naar de ingebouwde standaard
        )
        if len(body) == 1:
            return {"saved": True, "custom": bool(sp)}
    if "triage_rules" in body:
        rules = str(body["triage_rules"])[:2000]
        _state["triage_rules"] = rules
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.triage_rules = $r", r=rules,
        )
        if len(body) == 1:
            return {"saved": True}
    if "briefing_time" in body:
        try:
            saved = await asyncio.to_thread(
                set_briefing_time, _state["brain"], str(body["briefing_time"])
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if len(body) == 1:
            return {"saved": True, "briefing_time": saved}
    main = str(body.get("model_main", "")).strip()
    light = str(body.get("model_light", "")).strip()
    base: Settings = _state["settings"]
    if main == base.model_main:
        main = ""  # default expliciet gekozen → geen override
    if light == base.model_light:
        light = ""
    brain: BrainDB = _state["brain"]
    await asyncio.to_thread(
        brain.run,
        "MERGE (c:Config {id:'runtime'}) "
        "SET c.model_main = $main, c.model_light = $light, c.updated = datetime()",
        main=main or None,
        light=light or None,
    )
    _state["model_overrides"] = {"model_main": main or None, "model_light": light or None}
    eff = _effective_settings()
    return {"saved": True, "model_main": eff.model_main, "model_light": eff.model_light}


@app.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    llm: LLMClient = _state["llm"]
    available = await asyncio.to_thread(llm.list_models)
    eff = _effective_settings()
    for m in (eff.model_main, eff.model_light):
        if m not in available:
            available.insert(0, m)
    return {"models": available}


GRAPH_LABELS = ["Identity", "MemoryFragment", "Insight", "Mistake", "Idea",
                "Quest", "QuestStep", "Skill", "Protocol", "Session", "Entity",
                "Meeting", "Document"]


@app.get("/api/graph")
async def graph(request: Request, limit: int = Query(250, le=600)) -> dict[str, Any]:
    """Het brein als graph: nodes + links voor het 3D-hologram in de HUD."""
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]

    def fetch() -> dict[str, Any]:
        nodes = brain.run(
            """
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels)
            WITH n ORDER BY coalesce(n.created, n.started, datetime('2000-01-01')) DESC
            LIMIT $limit
            RETURN elementId(n) AS id, labels(n)[0] AS type, coalesce(n.id, '') AS key,
                   left(coalesce(n.title, n.name, n.content, n.summary, n.body, n.id, ''), 70) AS label
            """,
            labels=GRAPH_LABELS,
            limit=limit,
        )
        ids = [n["id"] for n in nodes]
        links = brain.run(
            """
            MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND elementId(b) IN $ids
            RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS rel
            LIMIT 1500
            """,
            ids=ids,
        )
        return {"nodes": nodes, "links": links}

    return await asyncio.to_thread(fetch)


def _audit(action: str, detail: str) -> None:
    """Audit-log in het brein: wat heeft Span namens Bas gedaan."""
    try:
        _state["brain"].run(
            "CREATE (:Action {type: $type, detail: $detail, at: datetime()})",
            type=action, detail=detail[:300],
        )
    except Exception:
        pass


@app.get("/api/inbox")
async def inbox_list(request: Request) -> dict[str, Any]:
    """Agent Inbox: voorgenomen acties + ambient meldingen."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    return {"items": inbox.snapshot(), "open": inbox.open_count()}


@app.post("/api/inbox/{item_id}/approve")
async def inbox_approve(request: Request, item_id: int) -> dict[str, Any]:
    """Keur een actie goed en voer hem uit; needs_reply = concept laten schrijven."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    item = inbox.get(item_id)
    if item is None or item["status"] != "open":
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    from span.jarvis.ambient import execute_approval
    result = await asyncio.to_thread(
        execute_approval, item, _state.get("o365"),
        _state["llm"], _effective_settings().model_light, _state.get("asana"),
    )
    _audit(item["action"] or item["kind"], item["title"])
    inbox.resolve(item_id, "done")
    return {"approved": True, "result": result}


@app.post("/api/inbox/{item_id}/reject")
async def inbox_reject(request: Request, item_id: int) -> dict[str, Any]:
    _require_rest_auth(request)
    item = _state["inbox"].resolve(item_id, "rejected")
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden.")
    return {"rejected": True}


@app.get("/api/backup")
async def backup(request: Request) -> Any:
    """Brein-export als JSON-download (zonder embeddings)."""
    from fastapi.responses import JSONResponse
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]

    def dump() -> dict[str, Any]:
        raw_nodes = brain.run(
            "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, "
            "properties(n) AS props"
        )
        nodes = []
        for n in raw_nodes:  # embeddings eruit: groot en herleidbaar
            props = {k: str(v) if not isinstance(v, (str, int, float, bool, list, type(None))) else v
                     for k, v in (n["props"] or {}).items() if k != "embedding"}
            nodes.append({"id": n["id"], "labels": n["labels"], "props": props})
        rels = brain.run(
            "MATCH (a)-[r]->(b) RETURN elementId(a) AS source, type(r) AS type, "
            "elementId(b) AS target"
        )
        from span.jarvis.daily import now_local
        return {"exported": now_local().isoformat(timespec="seconds"),
                "nodes": nodes, "relationships": rels}

    data = await asyncio.to_thread(dump)
    return JSONResponse(
        data,
        headers={"Content-Disposition": "attachment; filename=span-brein-backup.json"},
    )


@app.post("/api/documents")
async def upload_document(request: Request, filename: str = Query(...)) -> dict[str, Any]:
    """Document (pdf/docx/txt/md) het geheugen in: chunks + samenvatting."""
    _require_rest_auth(request)
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=422, detail="Leeg bestand.")
    from span.jarvis.documents import ingest_document
    try:
        result = await asyncio.to_thread(ingest_document, _state, filename, raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit("document_ingest", f"{filename} ({result['chunks']} delen)")
    return result


@app.post("/api/stt")
async def speech_to_text(request: Request) -> dict[str, Any]:
    """Audio-segment (webm/opus) → tekst via lokale Whisper.
    Fallback voor browsers waarvan de spraakdienst geblokkeerd is."""
    _require_rest_auth(request)
    from span.server import stt
    if not stt.available():
        raise HTTPException(status_code=501, detail="Server-STT niet geïnstalleerd.")
    audio = await request.body()
    if len(audio) < 1000:
        raise HTTPException(status_code=422, detail="Audio te kort of leeg.")
    if len(audio) > 10_000_000:
        raise HTTPException(status_code=413, detail="Audio te groot (max 10 MB).")
    try:
        text = await asyncio.to_thread(stt.transcribe, audio)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcriptie mislukt: {exc}")
    return {"text": text}


@app.get("/api/stt/status")
async def stt_status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    from span.server import stt
    return {"available": stt.available(), "model": stt.MODEL_NAME}


@app.post("/api/fireflies/sync")
async def fireflies_sync(request: Request, deep: bool = Query(False)) -> dict[str, Any]:
    """Handmatige sync: meetings → brein, actiepunten → Agent Inbox.
    deep=true verwerkt de volledige historie (idempotent)."""
    _require_rest_auth(request)
    if _state.get("fireflies") is None:
        raise HTTPException(status_code=400, detail="Fireflies niet geconfigureerd.")
    from span.jarvis.meetings import sync_meetings
    return await asyncio.to_thread(sync_meetings, _state, 8, deep)


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    brain_ok = True
    try:
        await asyncio.to_thread(_state["brain"].run, "RETURN 1 AS ok")
    except Exception:
        brain_ok = False
    o365 = _state.get("o365")
    return {
        "brain": brain_ok,
        "o365": bool(o365) and await asyncio.to_thread(o365.is_authenticated),
        "asana": _state.get("asana") is not None,
        "inbox_open": _state["inbox"].open_count(),
    }


@app.get("/api/jarvis/daily")
async def jarvis_daily(request: Request, force: bool = Query(False)) -> dict[str, Any]:
    """De dagstart van vandaag; genereert hem alsnog als de scheduler nog
    niet geweest is (of bij force=true)."""
    _require_rest_auth(request)
    from span.jarvis.daily import today_local
    cached = _state.get("daily")
    if force or not cached or cached.get("date") != today_local():
        _state["daily"] = await asyncio.to_thread(
            generate_daily, _state["brain"], _state["llm"],
            _state.get("o365"), _state.get("asana"),
            _effective_settings().model_light,
        )
        _state["daily"]["date"] = today_local()
    return _state["daily"]


@app.get("/api/netinfo")
async def netinfo(request: Request) -> dict[str, Any]:
    """LAN-adres voor de QR-code (Span op je telefoon, zelfde netwerk)."""
    _require_rest_auth(request)
    import socket
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
    return {"lan_ip": "" if in_container else lan_ip, "port": 8472,
            "hint": "vul het LAN-IP van deze pc in (ipconfig)" if in_container else ""}


@app.get("/api/jarvis/briefing")
async def jarvis_briefing(request: Request) -> dict[str, Any]:
    """Briefing + paneel-data voor de HUD: agenda, mail, taken, quests."""
    _require_rest_auth(request)
    data = await asyncio.to_thread(
        build_briefing, _state["brain"], _state.get("o365"), _state.get("asana")
    )
    o365 = _state.get("o365")
    data["integrations"] = {
        "o365": bool(o365) and await asyncio.to_thread(o365.is_authenticated) if o365 else False,
        "asana": _state.get("asana") is not None,
    }
    return data


@app.post("/api/auth/o365/start")
async def o365_auth_start(request: Request) -> dict[str, Any]:
    """Start de device code flow; een achtergrondtaak wacht de login af."""
    _require_rest_auth(request)
    o365 = _state.get("o365")
    if o365 is None:
        raise HTTPException(status_code=400, detail="O365 niet geconfigureerd (MS_CLIENT_ID).")
    flow = await asyncio.to_thread(o365.start_device_flow)
    _state["o365_flow"] = {"status": "pending", "account": None, "error": None}

    def _complete() -> None:
        try:
            account = o365.complete_device_flow(flow)
            _state["o365_flow"] = {"status": "done", "account": account, "error": None}
        except Exception as exc:
            _state["o365_flow"] = {"status": "error", "account": None, "error": str(exc)}

    asyncio.get_running_loop().run_in_executor(None, _complete)
    return {
        "user_code": flow["user_code"],
        "verification_uri": flow["verification_uri"],
        "message": flow.get("message", ""),
    }


@app.post("/api/auth/o365/logout")
async def o365_logout(request: Request) -> dict[str, Any]:
    """Ontkoppel het gekoppelde Microsoft-account (bv. verkeerd account)."""
    _require_rest_auth(request)
    o365 = _state.get("o365")
    if o365 is None:
        raise HTTPException(status_code=400, detail="O365 niet geconfigureerd.")
    name = await asyncio.to_thread(o365.logout)
    _state["o365_flow"] = None
    return {"logged_out": True, "account": name}


@app.get("/api/auth/o365/status")
async def o365_auth_status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    o365 = _state.get("o365")
    if o365 is None:
        return {"configured": False, "authenticated": False}
    authenticated = await asyncio.to_thread(o365.is_authenticated)
    return {
        "configured": True,
        "authenticated": authenticated,
        "account": o365.account_name(),
        "flow": _state.get("o365_flow"),
    }


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    await ws.accept()
    client_host = ws.client.host if ws.client else None

    settings: Settings = _effective_settings()
    brain: BrainDB = _state["brain"]
    llm: LLMClient = _state["llm"]

    try:
        hello = json.loads(await ws.receive_text())
    except (json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=1002)
        return
    if not _check_token(str(hello.get("token", "")), client_host):
        await ws.send_json({"type": "error", "error": "auth", "message": "Token ongeldig."})
        await ws.close(code=4401)
        return

    agent = SpanAgent(
        settings, brain, llm, _state["work"],
        o365=_state.get("o365"), asana=_state.get("asana"),
        inbox=_state["inbox"], autonomy=_state["autonomy"],
        disabled_tools=_state.get("disabled_tools"),
        fireflies=_state.get("fireflies"),
    )
    session_id: str | None = None
    loop = asyncio.get_running_loop()

    try:
        await ws.send_json({"type": "ready", "agent": AGENT_NAME})
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "location":
                try:
                    agent.user_location = {"lat": float(msg["lat"]), "lon": float(msg["lon"])}
                    if agent._toolbox is not None:  # sessie loopt al: direct bijwerken
                        agent._toolbox._user_location = agent.user_location
                except (KeyError, TypeError, ValueError):
                    pass
                continue

            if msg.get("type") == "user":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                if session_id is None:
                    session_id = await asyncio.to_thread(start_session, brain)
                    ctx = await asyncio.to_thread(agent.begin, session_id, text)
                    await ws.send_json(
                        {
                            "type": "session",
                            "session_id": session_id,
                            "protocols": len(ctx.protocols),
                            "quests": len(ctx.quests),
                            "relevant": len(ctx.relevant),
                        }
                    )

                queue: asyncio.Queue[str | None] = asyncio.Queue()

                def on_text(chunk: str) -> None:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)

                future = loop.run_in_executor(None, lambda: agent.turn(text, on_text))
                future.add_done_callback(
                    lambda _f: loop.call_soon_threadsafe(queue.put_nowait, None)
                )
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    await ws.send_json({"type": "delta", "text": chunk})
                answer = await future
                if agent.last_touched:
                    await ws.send_json({"type": "touched", "ids": agent.last_touched})
                await ws.send_json({"type": "done", "answer": answer})

            elif msg.get("type") == "end":
                if session_id is None:
                    await ws.send_json({"type": "summary", "summary": "Lege sessie.", "written": {}})
                else:
                    await asyncio.to_thread(agent.flush_recording)
                    fragments = FragmentStore(brain, llm)
                    result = await asyncio.to_thread(
                        reflect_session, settings, brain, llm, fragments, session_id
                    )
                    await ws.send_json(
                        {"type": "summary", "summary": result["summary"], "written": result["written"]}
                    )
                    session_id = None
                break
    except WebSocketDisconnect:
        pass  # fragmenten blijven bewaard; evaluatie kan later via CLI: span reflect
    finally:
        try:
            await ws.close()
        except Exception:
            pass
