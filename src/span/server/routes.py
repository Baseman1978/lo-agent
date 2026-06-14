"""REST-endpoints van de Span-server.

Alle HTTP-routes (de WebSocket-chat zit in app.py). Gebruikt de gedeelde
`_state` en helpers uit state.py; geregistreerd op de app via
`app.include_router(router)` in app.py.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

# M14: gedeelde sub-dicts in _state (m.n. mcp_pending) worden vanuit executor-
# threads gemuteerd -> serialiseer setdefault/pop. M9: pending OAuth-state heeft
# een TTL zodat een achtergebleven code/state niet eindeloop geldig blijft.
_PENDING_LOCK = threading.Lock()
_PENDING_TTL = 600.0  # seconden

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from span import AGENT_NAME, __version__
from span.config import Settings
from span.db.brain import BrainDB
from span.jarvis.ambient import AgentInbox
from span.jarvis.briefing import build_briefing
from span.jarvis.daily import briefing_time, generate_daily, set_briefing_time
from span.llm.client import LLMClient
from span.memory.fragments import FragmentStore
from span.server.state import (
    GRAPH_LABELS, STATIC_DIR, _audit, _effective_settings, _require_rest_auth,
    _state, _tools_overview,
)

router = APIRouter()


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]

    def _counts() -> dict[str, int]:
        rows = brain.run(
            "UNWIND $labels AS label "
            "CALL { WITH label MATCH (n) WHERE label IN labels(n) "
            "RETURN count(n) AS n } "
            "RETURN label, n",
            labels=["MemoryFragment", "Insight", "Mistake", "Idea", "Quest",
                    "Skill", "Protocol", "Session"],
        )
        return {r["label"]: r["n"] for r in rows}

    counts = await asyncio.to_thread(_counts)
    return {"agent": AGENT_NAME, "version": __version__, "counts": counts}


@router.get("/api/memory")
async def memory(request: Request, q: str = Query(...), k: int = Query(8, le=25)) -> list[dict]:
    _require_rest_auth(request)
    fragments = FragmentStore(_state["brain"], _state["llm"],
                              decay_mode=_state["settings"].decay_mode)
    return await asyncio.to_thread(fragments.search, q, k)


@router.get("/api/settings")
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
        "security": _state.get("security") or {},
    }


@router.post("/api/settings")
async def save_settings(request: Request) -> dict[str, Any]:
    """Instellingen opslaan. Elke key wordt onafhankelijk verwerkt; alleen
    keys die in de body staan worden aangeraakt (een autonomy-POST kan dus
    nooit per ongeluk de model-overrides wissen)."""
    _require_rest_auth(request)
    body = await request.json()
    result: dict[str, Any] = {"saved": True}

    if "security" in body:
        from span.safety.settings import save_security
        sec = await asyncio.to_thread(save_security, _state["brain"], body["security"] or {})
        _state["security"] = sec  # geldt voor nieuwe sessies + de watcher
        result["security"] = sec

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
        result["autonomy"] = dict(_state["autonomy"])

    if "disabled_tools" in body:
        from span.orchestrator.tools import TOOL_META
        disabled = [t for t in (body["disabled_tools"] or []) if t in TOOL_META]
        _state["disabled_tools"] = set(disabled)
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.disabled_tools = $d", d=disabled,
        )
        result["disabled_tools"] = disabled

    if "system_prompt" in body:
        sp = str(body["system_prompt"])[:8000].strip()
        if sp and "{bootstrap}" not in sp:
            # zonder deze placeholder verliest Span zijn hele geheugen-context
            sp += "\n\n{bootstrap}"
            result["system_prompt_note"] = ("{bootstrap} ontbrak en is automatisch "
                                            "toegevoegd — anders start Span zonder geheugen.")
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.system_prompt = $sp",
            sp=sp or None,  # leeg = terug naar de ingebouwde standaard
        )
        result["custom"] = bool(sp)

    if "triage_rules" in body:
        rules = str(body["triage_rules"])[:2000]
        _state["triage_rules"] = rules
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.triage_rules = $r", r=rules,
        )

    if "briefing_time" in body:
        try:
            result["briefing_time"] = await asyncio.to_thread(
                set_briefing_time, _state["brain"], str(body["briefing_time"])
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    if "model_main" in body or "model_light" in body:
        main = str(body.get("model_main", "")).strip()
        light = str(body.get("model_light", "")).strip()
        base: Settings = _state["settings"]
        if main == base.model_main:
            main = ""  # default expliciet gekozen → geen override
        if light == base.model_light:
            light = ""
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) "
            "SET c.model_main = $main, c.model_light = $light, c.updated = datetime()",
            main=main or None,
            light=light or None,
        )
        _state["model_overrides"] = {"model_main": main or None,
                                     "model_light": light or None}
        eff = _effective_settings()
        result["model_main"] = eff.model_main
        result["model_light"] = eff.model_light

    return result


@router.get("/api/models")
async def models(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    llm: LLMClient = _state["llm"]
    available = await asyncio.to_thread(llm.list_models)
    eff = _effective_settings()
    for m in (eff.model_main, eff.model_light):
        if m not in available:
            available.insert(0, m)
    return {"models": available}


@router.get("/api/graph")
async def graph(request: Request, limit: int = Query(250, le=600),
                since: int = Query(0, ge=0, le=3650)) -> dict[str, Any]:
    """Het brein als graph: nodes + links voor het 3D-hologram in de HUD.
    since = aantal dagen terug (0 = alles). Formele/kern-labels blijven altijd
    zichtbaar zodat het venster het skelet van het brein niet wegfiltert."""
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]
    # labels die altijd zichtbaar blijven, ook buiten het tijdvenster
    always = ["Identity", "Quest", "QuestStep", "Protocol", "Skill", "Insight"]

    _NODE_RETURN = ("elementId(n) AS id, labels(n)[0] AS type, coalesce(n.id, '') AS key, "
                    "left(coalesce(n.title, n.name, n.content, n.summary, n.body, n.id, ''), 70) AS label")

    # de 'structurele kern' = de betekenisvolle groepjes (entiteiten, inzichten,
    # quests, skills, protocollen). Die horen altijd zichtbaar te zijn, niet
    # weggedrukt te worden door een bulk-import van losse fragmenten.
    core_labels = ["Identity", "Protocol", "Quest", "QuestStep", "Skill",
                   "Insight", "Mistake", "Idea", "Entity", "Meeting"]

    # max ~60% van het budget voor de kern, de rest voor recente fragmenten;
    # de hubs (hoogste relatie-graad) eerst zodat de betekenisvolle groepjes
    # bovenaan staan en niet door losse entiteiten worden volgemaakt
    core_limit = max(1, int(limit * 0.6))

    def fetch() -> dict[str, Any]:
        # 1a) structurele kern eerst (op relatie-rijkdom: hubs bovenaan)
        core = brain.run(
            f"""
            MATCH (n) WHERE any(l IN labels(n) WHERE l IN $core)
            OPTIONAL MATCH (n)-[r]-()
            WITH n, count(r) AS deg
            WHERE deg > 0
            ORDER BY deg DESC, coalesce(n.created, n.started, datetime('2000-01-01')) DESC
            LIMIT $core_limit
            RETURN {_NODE_RETURN}
            """,
            core=core_labels, core_limit=core_limit,
        )
        seen = {n["id"] for n in core}
        # 1b) recente fragmenten vullen de rest aan (binnen het tijdvenster)
        room = max(0, limit - len(core))
        recent = brain.run(
            f"""
            MATCH (n:MemoryFragment)
              WHERE ($since = 0
                     OR coalesce(n.created, datetime('2000-01-01'))
                        >= datetime() - duration({{days: $since}}))
            WITH n ORDER BY coalesce(n.created, datetime('2000-01-01')) DESC
            LIMIT $room
            RETURN {_NODE_RETURN}
            """,
            since=since, room=room,
        ) if room else []
        seeds = list(core)
        for n in recent:
            if n["id"] not in seen:
                seen.add(n["id"]); seeds.append(n)
        seed_ids = [n["id"] for n in seeds]
        # 2) anker-nodes erbij: de buren van de seeds (Session/Document/Entity/…)
        #    zodat fragmenten rond hun bron clusteren en er lijnen zichtbaar zijn
        extra = brain.run(
            f"""
            MATCH (a)-[]-(n) WHERE elementId(a) IN $ids
              AND any(l IN labels(n) WHERE l IN $labels)
            WITH DISTINCT n LIMIT $extra
            RETURN {_NODE_RETURN}
            """,
            ids=seed_ids, labels=GRAPH_LABELS, extra=min(limit, 150),
        )
        seen = set(seed_ids)
        nodes = list(seeds)
        for n in extra:
            if n["id"] not in seen:
                seen.add(n["id"]); nodes.append(n)
        # totaal begrenzen (ARM64-software-WebGL blijft licht)
        nodes = nodes[:min(limit + 150, 450)]
        ids = [n["id"] for n in nodes]
        links = brain.run(
            """
            MATCH (a)-[r]->(b)
            WHERE elementId(a) IN $ids AND elementId(b) IN $ids
            RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS rel
            LIMIT 2500
            """,
            ids=ids,
        )
        return {"nodes": nodes, "links": links}

    return await asyncio.to_thread(fetch)


@router.get("/api/inbox")
async def inbox_list(request: Request) -> dict[str, Any]:
    """Agent Inbox: voorgenomen acties + ambient meldingen."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    return {"items": inbox.snapshot(), "open": inbox.open_count()}


@router.post("/api/inbox/{item_id}/approve")
async def inbox_approve(request: Request, item_id: int) -> dict[str, Any]:
    """Keur een actie goed en voer hem uit; needs_reply = concept laten schrijven."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    item = inbox.claim(item_id)  # atomair: dubbelklik kan nooit twee keer uitvoeren
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    from span.jarvis.ambient import execute_approval
    try:
        result = await asyncio.to_thread(
            execute_approval, item, _state.get("o365"),
            _state["llm"], _effective_settings().model_light, _state.get("asana"),
            _state.get("mcp"), _state["brain"],
        )
        if item.get("action") == "mcp_add":
            await asyncio.to_thread(_rebuild_mcp)
    except Exception:
        inbox.release(item_id)  # mislukt: item blijft open voor een nieuwe poging
        raise
    await asyncio.to_thread(_audit, item["action"] or item["kind"], item["title"])
    inbox.resolve(item_id, "done")
    from span.jarvis.feedback import record_feedback
    await asyncio.to_thread(record_feedback, _state["brain"],
                            item["kind"], item.get("action", ""), "approved")
    return {"approved": True, "result": result}


@router.post("/api/inbox/{item_id}/reject")
async def inbox_reject(request: Request, item_id: int) -> dict[str, Any]:
    _require_rest_auth(request)
    item = _state["inbox"].resolve(item_id, "rejected")
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden.")
    from span.jarvis.feedback import record_feedback
    await asyncio.to_thread(record_feedback, _state["brain"],
                            item["kind"], item.get("action", ""), "rejected")
    return {"rejected": True}


@router.get("/api/provenance/{key}")
async def provenance(request: Request, key: str) -> dict[str, Any]:
    """F3.5 — 'waarom weet je dit?': de bron-keten van een formele node of
    fragment (DISTILLED_FROM/FROM_SESSION/MENTIONS), voor de HUD."""
    _require_rest_auth(request)
    brain: BrainDB = _state["brain"]

    def fetch() -> dict[str, Any]:
        node = brain.run(
            "MATCH (n {id:$key}) RETURN labels(n)[0] AS type, "
            "coalesce(n.content, n.title, n.name, n.id) AS label LIMIT 1", key=key)
        if not node:
            return {"found": False}
        sources = brain.run(
            "MATCH (n {id:$key})-[r:DISTILLED_FROM|FORMALIZED_FROM]->(src) "
            "RETURN src.id AS id, labels(src)[0] AS type, "
            "left(coalesce(src.content,''),120) AS snippet", key=key)
        session = brain.run(
            "MATCH (n {id:$key})-[:FROM_SESSION]->(s:Session) "
            "RETURN s.id AS id, s.summary AS summary LIMIT 1", key=key)
        entities = brain.run(
            "MATCH (n {id:$key})-[:MENTIONS]->(e:Entity) RETURN e.name AS name", key=key)
        return {"found": True, "node": node[0], "sources": sources,
                "session": session[0] if session else None,
                "entities": [e["name"] for e in entities]}

    return await asyncio.to_thread(fetch)


@router.get("/api/backup")
async def backup(request: Request) -> Any:
    """Brein-export als JSON-download (zonder embeddings)."""
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


@router.post("/api/documents")
async def upload_document(request: Request, filename: str = Query(...),
                          scope: str = Query("algemeen")) -> dict[str, Any]:
    """Document (pdf/docx/txt/md) het geheugen in: chunks + samenvatting.
    scope (algemeen|werk|prive) scheidt werk- van privé-kennis (M18)."""
    _require_rest_auth(request)
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=422, detail="Leeg bestand.")
    from span.jarvis.documents import ingest_document, MAX_BYTES as DOC_MAX
    if len(raw) > DOC_MAX:   # M13: één bron voor de grens (documents.MAX_BYTES)
        raise HTTPException(status_code=413,
                            detail=f"Bestand groter dan {DOC_MAX // 1_000_000} MB.")
    try:
        result = await asyncio.to_thread(ingest_document, _state, filename, raw, scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await asyncio.to_thread(_audit, "document_ingest", f"{filename} ({result['chunks']} delen)")
    return result


@router.post("/api/stt")
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
    # M12: ruwe bytes gaan naar de ffmpeg/Whisper-decoder -> alleen bekende
    # audio-containers toelaten (magic bytes), 415 bij iets anders
    head = audio[:4]
    if not (head.startswith(b"\x1aE\xdf\xa3")      # EBML (webm/mkv)
            or head == b"OggS"                       # ogg/opus
            or head == b"RIFF"                       # wav
            or head[:3] == b"ID3" or head[:2] == b"\xff\xfb"):  # mp3
        raise HTTPException(status_code=415, detail="Onbekend audioformaat.")
    try:
        text = await asyncio.to_thread(stt.transcribe, audio)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcriptie mislukt: {exc}")
    import logging
    logging.getLogger("uvicorn.error").info("STT transcript: %r", text)
    return {"text": text}


@router.get("/api/stt/status")
async def stt_status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    from span.server import stt
    return {"available": stt.available(), "model": stt.MODEL_NAME}


@router.post("/api/fireflies/sync")
async def fireflies_sync(request: Request, deep: bool = Query(False)) -> dict[str, Any]:
    """Handmatige sync: meetings → brein, actiepunten → Agent Inbox.
    deep=true verwerkt de volledige historie (idempotent)."""
    _require_rest_auth(request)
    if _state.get("fireflies") is None:
        raise HTTPException(status_code=400, detail="Fireflies niet geconfigureerd.")
    from span.jarvis.meetings import sync_meetings
    return await asyncio.to_thread(sync_meetings, _state, 8, deep)


@router.get("/api/health")
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


@router.get("/api/jarvis/daily")
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


@router.get("/api/netinfo")
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


@router.get("/api/jarvis/briefing")
async def jarvis_briefing(request: Request) -> dict[str, Any]:
    """Briefing + paneel-data voor de HUD: agenda, mail, taken, quests."""
    _require_rest_auth(request)
    mcp = _state.get("mcp")
    data = await asyncio.to_thread(
        build_briefing, _state["brain"], _state.get("o365"), _state.get("asana"), "Bas", mcp
    )
    o365 = _state.get("o365")
    o365_auth = bool(o365) and await asyncio.to_thread(o365.is_authenticated) if o365 else False
    # M365 telt als "verbonden" als de directe O365 ingelogd is OF een MCP-server
    # de m365-tools levert (dan vullen de panelen via MCP)
    mcp_m365 = bool(mcp) and any("m365_mail_list" in n for n in mcp.tool_names())
    data["integrations"] = {
        "o365": o365_auth or mcp_m365,
        "asana": _state.get("asana") is not None,
    }
    return data


@router.post("/api/auth/o365/start")
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


@router.post("/api/auth/o365/logout")
async def o365_logout(request: Request) -> dict[str, Any]:
    """Ontkoppel het gekoppelde Microsoft-account (bv. verkeerd account)."""
    _require_rest_auth(request)
    o365 = _state.get("o365")
    if o365 is None:
        raise HTTPException(status_code=400, detail="O365 niet geconfigureerd.")
    name = await asyncio.to_thread(o365.logout)
    _state["o365_flow"] = None
    return {"logged_out": True, "account": name}


@router.get("/api/auth/o365/status")
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


def _ical_token() -> str:
    return (os.environ.get("SPAN_ICAL_TOKEN", "").strip()
            or os.environ.get("SPAN_AUTH_TOKEN", "").strip())


@router.get("/api/ical")
async def ical_feed(token: str = Query("")) -> PlainTextResponse:
    """iCal-feed (F2.6) van Spans geplande crons — abonneerbaar in je agenda.
    Auth via ?token= (agenda-apps sturen geen header). Bevat alleen titels +
    tijden, geen gevoelige inhoud."""
    import hmac as _hmac
    expected = _ical_token()
    if not expected or not _hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Ongeldige feed-token.")

    def build() -> str:
        from span.jarvis.crons import list_crons
        crons = list_crons(_state["brain"])
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Span//Cron//NL"]
        for c in crons:
            at = (c.get("at") or "09:00").replace(":", "") + "00"
            # eenvoudige dagelijkse/eenmalige weergave; DTSTART zonder datum =
            # floating time, agenda's tonen het als terugkerend tijdstip
            uid = f"{c.get('id','cron')}@span"
            summ = (c.get("text") or "Span-taak").replace("\n", " ")[:120]
            lines += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:Span: {summ}",
                      f"DTSTART:20260101T{at}", "RRULE:FREQ=DAILY", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)

    body = await asyncio.to_thread(build)
    return PlainTextResponse(body, media_type="text/calendar")


@router.api_route("/api/webhooks/graph", methods=["GET", "POST"])
async def graph_webhook(request: Request) -> Any:
    """F4.5 — Microsoft Graph change notifications (vereist een publieke tunnel).
    Handshake: Graph stuurt ?validationToken= en verwacht die plain terug.
    Notificatie: geverifieerd via clientState (SPAN_GRAPH_CLIENTSTATE) en GEBRUIKT
    ALLEEN ALS TRIGGER — de payload is untrusted en wordt nooit als instructie
    uitgevoerd; hij zet enkel een melding klaar zodat de watcher een read-only
    Graph-pull doet."""
    token = request.query_params.get("validationToken")
    if token is not None:  # Graph-handshake bij het aanmaken van de subscription
        return PlainTextResponse(token, media_type="text/plain")
    secret = os.environ.get("SPAN_GRAPH_CLIENTSTATE", "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="Graph-webhook niet geconfigureerd.")
    try:
        import json as _json
        body = _json.loads(await request.body() or b"{}")
    except Exception:
        body = {}
    valid = [n for n in (body.get("value") or [])
             if n.get("clientState") == secret]
    if not valid:
        raise HTTPException(status_code=401, detail="Ongeldige clientState.")
    # trigger, geen instructie: alleen melden dat er iets veranderde
    _state["inbox"].add(kind="notify", title="Graph-melding",
                        detail=f"{len(valid)} wijziging(en) gesignaleerd — "
                               "Span haalt de details read-only op.")
    return {"received": len(valid)}


def _rebuild_mcp() -> None:
    from span.integrations.mcp_client import MCPRegistry, load_servers
    try:
        _state["mcp"] = MCPRegistry(load_servers(_state["brain"]), _state["brain"])
    except Exception as exc:
        print(f"[mcp] herbouw mislukt: {exc}", flush=True)


@router.get("/api/mcp")
async def mcp_list(request: Request) -> dict[str, Any]:
    """Gekoppelde MCP-servers + status (zonder tokens te lekken)."""
    _require_rest_auth(request)
    from span.integrations.mcp_client import load_servers
    servers = await asyncio.to_thread(load_servers, _state["brain"])
    reg = _state.get("mcp")
    connected = set()
    if reg is not None:
        connected = {n.split("__")[1] for n in reg.tool_names()}
    return {"servers": [
        {"name": s["name"], "url": s["url"],
         "connected": bool(s.get("token")) and s["name"] in connected,
         "logged_in": bool(s.get("token"))}
        for s in servers]}


@router.post("/api/mcp")
async def mcp_add(request: Request) -> dict[str, Any]:
    """Voeg een MCP-server toe (naam + url). Inloggen gaat via /connect."""
    _require_rest_auth(request)
    from span.integrations.mcp_client import load_servers, save_servers
    body = await request.json()
    name = (body.get("name") or "").strip()
    url = (body.get("url") or "").strip()
    if not name or not url.startswith("http"):
        raise HTTPException(status_code=422, detail="Naam en geldige https-URL vereist.")
    servers = await asyncio.to_thread(load_servers, _state["brain"])
    servers = [s for s in servers if s["name"] != name] + [{"name": name, "url": url}]
    await asyncio.to_thread(save_servers, _state["brain"], servers)
    return {"added": name}


@router.delete("/api/mcp/{name}")
async def mcp_delete(request: Request, name: str) -> dict[str, Any]:
    _require_rest_auth(request)
    from span.integrations.mcp_client import load_servers, save_servers
    servers = [s for s in await asyncio.to_thread(load_servers, _state["brain"])
               if s["name"] != name]
    await asyncio.to_thread(save_servers, _state["brain"], servers)
    await asyncio.to_thread(_rebuild_mcp)
    return {"deleted": name}


def _callback_uri(request: Request) -> str:
    # redirect terug naar deze server; host uit de request (browser van Bas)
    return str(request.base_url).rstrip("/") + "/api/mcp/oauth/callback"


@router.post("/api/mcp/{name}/connect")
async def mcp_connect(request: Request, name: str) -> dict[str, Any]:
    """Start de OAuth-login: discover + dynamische registratie + PKCE.
    Geeft de authorize-URL terug; Bas opent die en logt in."""
    _require_rest_auth(request)
    from span.integrations import mcp_oauth as ox
    from span.integrations.mcp_client import load_servers
    servers = await asyncio.to_thread(load_servers, _state["brain"])
    server = next((s for s in servers if s["name"] == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP-server niet gevonden.")
    redirect_uri = _callback_uri(request)
    # bewust koppelen -> de MCP-host mag uitgaand verkeer ontvangen (egress-poort)
    from urllib.parse import urlparse as _up
    from span.safety.egress import allow_host
    allow_host(_up(server["url"]).hostname or "")

    def prep() -> dict[str, Any]:
        meta = ox.discover(server["url"])
        reg = ox.register_client(meta, redirect_uri)
        verifier, challenge = ox.make_pkce()
        import secrets as _s
        state = _s.token_urlsafe(16)
        url = ox.authorize_url(meta, reg["client_id"], redirect_uri, challenge, state)
        with _PENDING_LOCK:
            pend = _state.setdefault("mcp_pending", {})
            # verlopen pogingen opruimen (M9)
            for st in [k for k, v in pend.items() if time.time() - v.get("ts", 0) > _PENDING_TTL]:
                pend.pop(st, None)
            pend[state] = {
                "name": name, "meta": meta, "client_id": reg["client_id"],
                "verifier": verifier, "redirect_uri": redirect_uri,
                "ts": time.time(),
            }
        return {"authorize_url": url}

    try:
        return await asyncio.to_thread(prep)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OAuth-start mislukt: {exc}")


@router.get("/api/mcp/oauth/callback")
async def mcp_oauth_callback(code: str = Query(""), state: str = Query("")) -> Any:
    """OAuth-redirect: wissel de code in voor tokens en sla ze op."""
    from span.integrations import mcp_oauth as ox
    from span.integrations.mcp_client import load_servers, save_servers
    with _PENDING_LOCK:
        pending = (_state.get("mcp_pending") or {}).pop(state, None)
    if not pending or not code:
        return PlainTextResponse("Ongeldige of verlopen login-poging.", status_code=400)
    if time.time() - pending.get("ts", 0) > _PENDING_TTL:   # M9: verlopen state
        return PlainTextResponse("Login-poging verlopen — start opnieuw.", status_code=400)

    def finish() -> str:
        tok = ox.exchange_code(pending["meta"], pending["client_id"], code,
                               pending["verifier"], pending["redirect_uri"])
        servers = load_servers(_state["brain"])
        for s in servers:
            if s["name"] == pending["name"]:
                s["token"] = tok.get("access_token", "")
                s["refresh"] = tok.get("refresh_token", "")
                s["client_id"] = pending["client_id"]
                s["token_endpoint"] = pending["meta"].get("token_endpoint", "")
        save_servers(_state["brain"], servers)
        _rebuild_mcp()
        return pending["name"]

    try:
        name = await asyncio.to_thread(finish)
    except Exception as exc:
        return PlainTextResponse(f"Token-uitwisseling mislukt: {exc}", status_code=502)
    return PlainTextResponse(
        f"MCP-server '{name}' is gekoppeld. Je kunt dit tabblad sluiten en terug "
        "naar Span — de tools verschijnen bij een nieuwe sessie.")


@router.post("/api/inbound")
async def inbound(request: Request) -> dict[str, Any]:
    """Generiek inbound-webhook (F2.6/feature 74): externe systemen (CI,
    monitoring, domotica) sturen een ondertekend bericht -> belandt als melding
    in de Agent Inbox. HMAC-SHA256 over de body in header X-Span-Signature;
    secret = SPAN_INBOUND_SECRET. Zonder secret is het endpoint uit."""
    import hashlib
    import hmac as _hmac
    secret = os.environ.get("SPAN_INBOUND_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="Inbound niet geconfigureerd.")
    raw = await request.body()
    sig = request.headers.get("x-span-signature", "")
    expected = _hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Ongeldige signatuur.")
    try:
        import json as _json
        payload = _json.loads(raw or b"{}")
    except Exception:
        payload = {}
    title = str(payload.get("title") or "Externe melding")[:120]
    detail = str(payload.get("detail") or "")[:500]
    _state["inbox"].add(kind="notify", title=f"⇲ {title}", detail=detail,
                        urgency=payload.get("urgency", "normal"))
    return {"received": True}
