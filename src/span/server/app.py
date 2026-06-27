"""Span web-server — FastAPI + WebSocket streaming.

Eén proces serveert de JARVIS-UI (static) en de chat-API. Elke
WebSocket-verbinding is één sessie met een eigen SpanAgent; bij nette
afsluiting (end-bericht) draait de evaluatiecirkel.

Beveiliging: zet SPAN_AUTH_TOKEN in de omgeving. Zonder token weigert de
server alles behalve localhost-verkeer.

Dit bestand doet alleen de wiring: lifespan (achtergrondtaken + gedeelde
staat), de WebSocket-chat, en het mounten van de REST-routes (routes.py)
en de statics. Gedeelde staat en helpers staan in state.py.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from span import AGENT_NAME, __version__
from span.config import Settings, load_settings
from span.db.brain import BrainDB
from span.db.schema import init_schema
from span.db.work import WorkDB
from span.evaluation.reflect import reflect_session
from span.integrations import build_integrations
from span.jarvis.ambient import AgentInbox, ambient_watcher
from span.jarvis.daily import daily_scheduler
from span.llm.client import LLMClient
from span.memory.bootstrap import start_session
from span.memory.fragments import FragmentStore
from span.orchestrator.agent import SpanAgent
from span.server import auth, routes
from span.server.state import (
    SESSION_COOKIE, STATIC_DIR, _auth_token, _check_token, _effective_settings,
    _state, _ws_context, read_session,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    brain = BrainDB(settings)
    brain.verify()
    init_schema(brain, settings)  # idempotent — server is meteen bruikbaar
    # audit-sleutel zelfstandig regelen: verse installatie krijgt een eigen
    # gegenereerde sleutel in het state-volume (zero-touch); bestaande keten
    # blijft op z'n huidige sleutel staan (geen stille herschrijving)
    from span.safety.audit import ensure_audit_key
    print(f"[audit] sleutel-modus: {ensure_audit_key(brain)}", flush=True)
    work = None
    if settings.work:
        try:
            work = WorkDB(settings.work)
            work.verify()
        except Exception:
            work = None
    o365, asana, fireflies = build_integrations(settings)
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
    from span.safety.settings import load_security
    _state["security"] = load_security(brain)
    # MCP-registry: verbonden externe MCP-servers leveren extra tools
    from span.integrations.mcp_client import MCPRegistry, load_servers
    try:
        _state["mcp"] = MCPRegistry(load_servers(brain), brain)
    except Exception as exc:
        print(f"[mcp] registry-init mislukt: {exc}", flush=True)
        _state["mcp"] = None
    # WP-2 multi-user: alleen aan als expliciet ingeschakeld. Owner houdt zijn
    # bestaande brein; andere gebruikers krijgen brain-<oid>. Uit => globale staat.
    import os as _os
    owner_oid = _os.environ.get("SPAN_OWNER_OID", "").strip()
    multiuser = (_os.environ.get("SPAN_MULTIUSER", "").strip().lower()
                 in ("1", "true", "yes")) or bool(owner_oid)
    if multiuser:
        from span.server.usercontext import ContextRegistry
        _state["contexts"] = ContextRegistry(
            settings, build_o365=lambda oid: _state.get("o365"),
            owner_oid=owner_oid,
        )
        print(f"[multiuser] aan (owner={'ja' if owner_oid else 'nee'})", flush=True)
    else:
        _state["contexts"] = None
    if fireflies is not None:
        _state["fireflies"] = fireflies
    if not _auth_token():
        print("WAARSCHUWING: SPAN_AUTH_TOKEN niet gezet — alleen localhost toegestaan.")
    scheduler = asyncio.create_task(daily_scheduler(_state))
    watcher = asyncio.create_task(ambient_watcher(_state))
    telegram_task = None
    if settings.jarvis.telegram_enabled:
        from span.integrations.telegram import TelegramBridge
        _state["telegram"] = TelegramBridge(settings.jarvis.telegram_bot_token, _state)
        telegram_task = asyncio.create_task(_state["telegram"].run())
    yield
    tasks = [t for t in (scheduler, watcher, telegram_task) if t is not None]
    for t in tasks:
        t.cancel()
    # wacht tot de taken echt gestopt zijn vóór de db-verbinding dichtgaat
    await asyncio.gather(*tasks, return_exceptions=True)
    if _state.get("contexts") is not None:
        _state["contexts"].close_all()
    brain.close()
    if work:
        work.close()


app = FastAPI(title=f"{AGENT_NAME} API", version=__version__, lifespan=lifespan)


# M15: security-headers als vangnet onder de HUD (die innerHTML rendert).
# CSP staat self-scripts toe + de inline bootstrap; 'self'-connect dekt de
# WebSocket. Geen externe scriptbronnen -> een gemiste escape wordt geen
# token-diefstal. nosniff + geen referer-lek + clickjacking-slot.
_CSP = ("default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


app.include_router(auth.router)
app.include_router(routes.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    await ws.accept()
    client_host = ws.client.host if ws.client else None

    settings: Settings = _effective_settings()
    llm: LLMClient = _state["llm"]

    try:
        hello = json.loads(await ws.receive_text())
    except (json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=1002)
        return
    ws_forwarded = bool(ws.headers.get("x-forwarded-for") or ws.headers.get("x-real-ip"))
    # auth: Microsoft-sessie (cookie) óf de bearer-token in het hello-bericht
    session_ok = read_session(ws.cookies.get(SESSION_COOKIE, "")) is not None
    if not session_ok and not _check_token(
            str(hello.get("token", "")), client_host, forwarded=ws_forwarded):
        await ws.send_json({"type": "error", "error": "auth", "message": "Niet ingelogd."})
        await ws.close(code=4401)
        return

    # per-user context (WP-2): eigen brein/connector als multi-user aan staat,
    # anders de globale staat (owner houdt zijn bestaande brein)
    ctx = _ws_context(ws)
    brain: BrainDB = ctx.brain

    agent = SpanAgent(
        settings, brain, llm, _state["work"],
        o365=ctx.o365, asana=_state.get("asana"),
        inbox=_state["inbox"], autonomy=_state["autonomy"],
        disabled_tools=_state.get("disabled_tools"),
        fireflies=_state.get("fireflies"),
        mcp=_state.get("mcp"),
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
                    agent.set_location(float(msg["lat"]), float(msg["lon"]))
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

                # heterogene queue: zowel tekst-delta's als live 'memory_read'-
                # events (welk geheugen Span tijdens de beurt raadpleegt)
                queue: asyncio.Queue[dict | None] = asyncio.Queue()

                def on_text(chunk: str) -> None:
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"type": "delta", "text": chunk})

                def on_memory(ids, reason: str, query: str | None = None) -> None:
                    # vanuit de worker-thread veilig naar de loop; alleen opaque
                    # node-keys + kort label (geen gevoelige inhoud over de socket)
                    if not ids:
                        return
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, {
                            "type": "memory_read", "ids": list(ids),
                            "reason": reason, "query": query,
                        })
                    except Exception:
                        pass  # een WS-hapering mag de beurt nooit breken

                future = loop.run_in_executor(
                    None, lambda: agent.turn(text, on_text, on_memory))
                future.add_done_callback(
                    lambda _f: loop.call_soon_threadsafe(queue.put_nowait, None)
                )
                try:
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        await ws.send_json(item)
                    answer = await future
                except Exception as exc:
                    # beurt netjes laten aflopen (recorder schrijft nog), dan melden
                    await asyncio.wait([future])
                    if isinstance(exc, WebSocketDisconnect):
                        raise
                    await ws.send_json({
                        "type": "error", "error": "turn",
                        "message": f"Er ging iets mis in deze beurt: {exc}",
                    })
                    continue
                # geen losse eind-'touched' meer: de live leescascade dekt dit al
                # (de :TOUCHED-DB-edges blijven via agent._write_trace)
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
