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
from span.jarvis.daily import (
    briefing_time,
    generate_daily,
    meeting_prep_lead,
    quiet_hours_active,
    quiet_window,
    set_briefing_time,
    set_meeting_prep_lead,
    set_quiet_end,
    set_quiet_start,
)
from span.llm.client import LLMClient
from span.memory.fragments import FragmentStore
from span.server.state import (
    GRAPH_LABELS, STATIC_DIR, _audit, _effective_settings, _request_context,
    _is_owner, _require_owner, _require_rest_auth, _state, _tools_overview,
)

router = APIRouter()


@router.get("/")
async def index(request: Request) -> Any:
    # Web-login aan en nog geen Microsoft-sessie? -> meteen naar de login.
    from span.server.state import _session_user
    settings = _state.get("settings")
    if (settings is not None and settings.jarvis.web_login_enabled
            and _session_user(request) is None):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/auth/login", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    brain: BrainDB = _request_context(request).brain

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
    ctx = _request_context(request)
    fragments = FragmentStore(ctx.brain, _state["llm"],
                              decay_mode=_state["settings"].decay_mode,
                              extra_brains=[ctx.shared] if ctx.shared else None)
    return await asyncio.to_thread(fragments.search, q, k)


@router.post("/api/share")
async def share_memory(request: Request) -> dict[str, Any]:
    """Deel een knoop (Insight/Skill/Protocol/Idea/Fragment) met het team
    (kopie naar brain-shared). Iedereen op de allowlist mag delen (WP-3)."""
    _require_rest_auth(request)
    ctx = _request_context(request)
    if getattr(ctx, "shared", None) is None:
        raise HTTPException(status_code=400, detail="Gedeeld geheugen niet actief.")
    body = await request.json()
    node_id = (body.get("id") or "").strip()
    if not node_id:
        raise HTTPException(status_code=422, detail="Knoop-id vereist.")
    from span.memory.sharing import share_node
    try:
        res = await asyncio.to_thread(
            share_node, ctx.brain, ctx.shared, node_id, getattr(ctx, "upn", ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await asyncio.to_thread(_audit, "memory_share", f"{res['label']} {node_id}",
                            getattr(_request_context(request), "upn", ""))
    return {"shared": True, **res}


@router.post("/api/unshare")
async def unshare_memory(request: Request) -> dict[str, Any]:
    """Trek een gedeelde knoop terug uit brain-shared."""
    _require_rest_auth(request)
    ctx = _request_context(request)
    if getattr(ctx, "shared", None) is None:
        raise HTTPException(status_code=400, detail="Gedeeld geheugen niet actief.")
    body = await request.json()
    node_id = (body.get("id") or "").strip()
    if not node_id:
        raise HTTPException(status_code=422, detail="Knoop-id vereist.")
    from span.memory.sharing import unshare_node
    res = await asyncio.to_thread(unshare_node, ctx.shared, node_id)
    await asyncio.to_thread(_audit, "memory_unshare", node_id,
                            getattr(_request_context(request), "upn", ""))
    return res


@router.get("/api/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    base: Settings = _state["settings"]
    eff = _effective_settings()
    o365 = _request_context(request).o365
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
        "quiet_hours": await asyncio.to_thread(
            lambda: dict(zip(("start", "end"), quiet_window(_state["brain"])))
        ),
        "meeting_prep_lead": await asyncio.to_thread(meeting_prep_lead, _state["brain"]),
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
        # C6: de HUD toont beheer-tabs alleen aan de owner; de schrijfroutes
        # zelf blijven server-side op _require_owner zitten
        "is_owner": _is_owner(request),
    }


@router.post("/api/settings")
async def save_settings(request: Request) -> dict[str, Any]:
    """Instellingen opslaan. Elke key wordt onafhankelijk verwerkt; alleen
    keys die in de body staan worden aangeraakt (een autonomy-POST kan dus
    nooit per ongeluk de model-overrides wissen)."""
    _require_owner(request)
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
        disabled = [t for t in (body["disabled_tools"] or [])
                    if t in TOOL_META or (isinstance(t, str) and t.startswith("mcp__"))]
        _state["disabled_tools"] = set(disabled)
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.disabled_tools = $d", d=disabled,
        )
        result["disabled_tools"] = disabled

    if "integration_perms" in body:
        raw = body["integration_perms"] or {}
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="integration_perms: object verwacht.")
        clean: dict[str, dict[str, bool]] = {}
        for key, val in raw.items():
            if not isinstance(key, str) or not isinstance(val, dict):
                continue
            clean[key[:80]] = {"read": bool(val.get("read", True)),
                               "write": bool(val.get("write", True))}
        _state["integration_perms"] = clean
        import json as _json
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.integration_perms = $p",
            p=_json.dumps(clean),
        )
        dicht = [k for k, v in clean.items() if not (v["read"] and v["write"])]
        await asyncio.to_thread(_audit, "integration_perms",
                                ("beperkt: " + ", ".join(sorted(dicht))) if dicht else "alles open",
                                getattr(_request_context(request), "upn", ""))
        result["integration_perms"] = clean

    if "tts_engine" in body:
        from span.server import tts
        eng = str(body["tts_engine"] or "").strip().lower()
        if eng not in ("", "elevenlabs", "xtts", "piper"):
            raise HTTPException(status_code=422, detail="Onbekende spraakbron.")
        tts.set_engine_override(eng)
        await asyncio.to_thread(
            _state["brain"].run,
            "MERGE (c:Config {id:'runtime'}) SET c.tts_engine = $e", e=eng,
        )
        await asyncio.to_thread(_audit, "settings_tts_engine", eng or "auto",
                                getattr(_request_context(request), "upn", ""))
        result["tts_engine"] = eng

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

    if "quiet_start" in body or "quiet_end" in body:
        try:
            if "quiet_start" in body:
                await asyncio.to_thread(
                    set_quiet_start, _state["brain"], str(body["quiet_start"])
                )
            if "quiet_end" in body:
                await asyncio.to_thread(
                    set_quiet_end, _state["brain"], str(body["quiet_end"])
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        start, end = await asyncio.to_thread(quiet_window, _state["brain"])
        result["quiet_hours"] = {"start": start, "end": end}

    if "meeting_prep_lead" in body:
        try:
            result["meeting_prep_lead"] = await asyncio.to_thread(
                set_meeting_prep_lead, _state["brain"], body["meeting_prep_lead"]
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
    """Het brein als graph: nodes + links voor de NEBULA-scene in de HUD.
    since = aantal dagen terug (0 = alles). Formele/kern-labels blijven altijd
    zichtbaar zodat het venster het skelet van het brein niet wegfiltert."""
    _require_rest_auth(request)
    brain: BrainDB = _request_context(request).brain

    _NODE_RETURN = ("elementId(n) AS id, labels(n)[0] AS type, coalesce(n.id, '') AS key, "
                    "left(coalesce(n.title, n.name, n.content, n.summary, n.body, n.id, ''), 70) AS label")

    # de 'structurele kern' = de betekenisvolle groepjes (entiteiten, inzichten,
    # quests, skills, protocollen). Die horen altijd zichtbaar te zijn, niet
    # weggedrukt te worden door een bulk-import van losse fragmenten.
    core_labels = ["Identity", "Protocol", "Quest", "QuestStep", "Skill",
                   "Insight", "Mistake", "Idea", "Entity", "Meeting", "Document"]

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
    """Agent Inbox: voorgenomen acties + ambient meldingen (per gebruiker geïsoleerd)."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    owner = getattr(_request_context(request).brain, "database", "")
    # 'open' = alle open items (de lijst toont ze allemaal); 'attention' = alleen
    # de echte to-do's (action/needs_reply/choice), die de HUD-badge voeden.
    return {"items": inbox.snapshot(owner), "open": inbox.open_count(owner),
            "attention": inbox.attention_count(owner)}


@router.post("/api/inbox/{item_id}/approve")
async def inbox_approve(request: Request, item_id: int) -> dict[str, Any]:
    """Keur een actie goed en voer hem uit; needs_reply = concept laten schrijven."""
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    ctx = _request_context(request)  # per-user brein + gedeeld brein voor share_memory
    owner = getattr(ctx.brain, "database", "")
    pre = inbox.get(item_id)  # eigenaar-check vóór de claim: B keurt A's actie niet goed
    if pre is None or not inbox.approvable_by(pre, owner):
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    if pre.get("kind") == "choice":
        # een keuze-item kent geen generiek "goedkeuren" — er moet een van de
        # opties gekozen worden (POST /api/inbox/{id}/choose)
        raise HTTPException(status_code=400,
                            detail="Dit item vraagt een keuze — kies een van de opties.")
    item = inbox.claim(item_id)  # atomair: dubbelklik kan nooit twee keer uitvoeren
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    from span.jarvis.ambient import execute_approval
    try:
        result = await asyncio.to_thread(
            execute_approval, item, _state.get("o365"),
            _state["llm"], _effective_settings().model_light, _state.get("asana"),
            _state.get("mcp"), getattr(ctx, "brain", None) or _state["brain"],
            getattr(ctx, "shared", None), getattr(ctx, "upn", ""),
            _broker_dispatch(ctx),   # WP-A3: goedgekeurde native writes echt uitvoeren
        )
        if item.get("action") == "mcp_add":
            await asyncio.to_thread(_rebuild_mcp)
    except Exception:
        inbox.release(item_id)  # mislukt: item blijft open voor een nieuwe poging
        raise
    await asyncio.to_thread(_audit, item["action"] or item["kind"], item["title"],
                            getattr(ctx, "upn", ""))
    inbox.resolve(item_id, "done")
    from span.jarvis.feedback import feedback_action, record_feedback
    await asyncio.to_thread(record_feedback, _state["brain"],
                            item["kind"], feedback_action(item), "approved")
    return {"approved": True, "result": result}


@router.post("/api/inbox/{item_id}/reject")
async def inbox_reject(request: Request, item_id: int) -> dict[str, Any]:
    _require_rest_auth(request)
    inbox: AgentInbox = _state["inbox"]
    owner = getattr(_request_context(request).brain, "database", "")
    pre = inbox.get(item_id)  # zelfde eigenaar-check als bij approve
    if pre is None or not inbox.approvable_by(pre, owner):
        raise HTTPException(status_code=404, detail="Item niet gevonden.")
    item = inbox.resolve(item_id, "rejected")
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden.")
    from span.jarvis.feedback import feedback_action, record_feedback
    await asyncio.to_thread(record_feedback, _state["brain"],
                            item["kind"], feedback_action(item), "rejected")
    return {"rejected": True}


@router.post("/api/inbox/{item_id}/choose")
async def inbox_choose(request: Request, item_id: int) -> dict[str, Any]:
    """Keuze-item afhandelen (bv. tegenspraak in het geheugen): de gekozen
    versie blijft, de andere opties worden gearchiveerd (superseded) zodat
    de recall ze niet meer ophaalt."""
    _require_rest_auth(request)
    body = await request.json()
    pick = str(body.get("pick") or "")
    inbox: AgentInbox = _state["inbox"]
    ctx = _request_context(request)
    owner = getattr(ctx.brain, "database", "")
    pre = inbox.get(item_id)  # zelfde eigenaar-check als bij approve
    if pre is None or not inbox.approvable_by(pre, owner):
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    if pre.get("kind") != "choice":
        raise HTTPException(status_code=400, detail="Dit item is geen keuze-item.")
    option_ids = [o.get("id") for o in (pre.get("payload") or {}).get("options") or []]
    if pick not in option_ids:
        raise HTTPException(status_code=400, detail="Onbekende keuze.")
    item = inbox.claim(item_id)  # atomair: dubbelklik kiest nooit twee keer
    if item is None:
        raise HTTPException(status_code=404, detail="Item niet gevonden of al afgehandeld.")
    losers = [i for i in option_ids if i != pick]
    brain = getattr(ctx, "brain", None) or _state["brain"]

    def _resolve_contradiction() -> None:
        brain.run(
            "MATCH (mf:MemoryFragment {id: $id}) "
            "SET mf.contradiction_flagged = null",
            id=pick,
        )
        brain.run(
            "UNWIND $ids AS i MATCH (mf:MemoryFragment {id: i}) "
            "SET mf.superseded = true, mf.superseded_by = $winner, "
            "    mf.contradiction_flagged = null",
            ids=losers, winner=pick,
        )

    try:
        await asyncio.to_thread(_resolve_contradiction)
    except Exception:
        inbox.release(item_id)  # mislukt: item blijft open voor een nieuwe poging
        raise
    await asyncio.to_thread(_audit, item.get("action") or "choice", item["title"],
                            getattr(ctx, "upn", ""))
    inbox.resolve(item_id, "done")
    from span.jarvis.feedback import feedback_action, record_feedback
    await asyncio.to_thread(record_feedback, _state["brain"],
                            item["kind"], feedback_action(item), "approved")
    return {"chosen": pick, "superseded": losers}


# -- PROACTIEF SPREKEN: aankondigingen-wachtrij + agenda-aanwezigheid --------
@router.get("/api/announcements")
async def announcements_list(request: Request) -> dict[str, Any]:
    """Open uit-te-spreken items voor PROACTIEF SPREKEN (id, type, text).

    Tijdens de stille uren blijft de lijst leeg — de items zelf blijven staan
    (het spreken wacht, er gaat niets verloren). Zo zit de quiet-hours-regel op
    dezelfde plek als voor Telegram, server-side."""
    _require_rest_auth(request)
    q = _state.get("announcements")
    if q is None:
        return {"items": []}
    if await asyncio.to_thread(quiet_hours_active, _state["brain"]):
        return {"items": []}
    return {"items": q.open_items()}


@router.post("/api/announcements/{item_id}/spoken")
async def announcements_spoken(request: Request, item_id: int) -> dict[str, Any]:
    """Markeer een aankondiging als uitgesproken; hij verdwijnt uit de queue
    zodat hij nooit twee keer klinkt."""
    _require_rest_auth(request)
    q = _state.get("announcements")
    if q is None:
        raise HTTPException(status_code=404, detail="Geen aankondigingen-wachtrij.")
    return {"spoken": q.mark_spoken(int(item_id))}


@router.get("/api/presence/meeting_now")
async def presence_meeting_now(request: Request) -> dict[str, Any]:
    """Loopt er NU een agenda-afspraak MET minstens één andere genodigde?

    blocking=true bij een echte meeting/call (andere attendees). Een solo-blok
    (geen andere genodigden) of geen event → blocking=false. Faalt zacht naar
    false als O365 er niet is; de client leunt dan op mic-VAD + DND."""
    _require_rest_auth(request)
    o365 = _request_context(request).o365
    if o365 is None:
        return {"blocking": False}
    try:
        blocking = await asyncio.to_thread(o365.meeting_now)
    except Exception:
        return {"blocking": False}
    return {"blocking": bool(blocking)}


# -- Integration Broker: catalogus + acties (onder LO's governance) ---------
@router.get("/api/integrations/permissions")
async def integrations_permissions(request: Request) -> dict[str, Any]:
    """Rechtenoverzicht voor de HUD: per integratie(groep) de tools met hun
    read/write-aard, de aan/uit-stand per actie (disabled_tools) en de
    lees/schrijf-toestemming (integration_perms)."""
    _require_rest_auth(request)
    from span.orchestrator.tools import TOOL_META
    from span.safety.risk import mcp_capability
    perms = _state.get("integration_perms") or {}
    disabled = _state.get("disabled_tools") or set()
    groups: dict[str, dict[str, Any]] = {}
    for name, (grp, rw) in TOOL_META.items():
        g = groups.setdefault(grp, {"key": grp, "label": grp, "tools": []})
        g["tools"].append({"name": name, "rw": rw, "enabled": name not in disabled})
    mcp = _state.get("mcp")
    if mcp is not None:
        for full in mcp.tool_names():
            parts = full.split("__")
            server = parts[1] if len(parts) >= 3 else "?"
            key = f"mcp:{server}"
            g = groups.setdefault(key, {"key": key, "label": f"{server} (MCP)",
                                        "tools": []})
            g["tools"].append({"name": full, "rw": mcp_capability(parts[-1]),
                               "enabled": full not in disabled})
    out = []
    for key in sorted(groups, key=str.lower):
        g = groups[key]
        p = perms.get(g["key"]) or {}
        g["read"] = bool(p.get("read", True))
        g["write"] = bool(p.get("write", True))
        g["tools"].sort(key=lambda t: (t["rw"], t["name"]))
        out.append(g)
    return {"integrations": out, "is_owner": _is_owner(request)}


@router.get("/api/integrations/catalog")
async def integrations_catalog(request: Request, category: str = Query(""),
                               capability: str = Query(""), q: str = Query("")) -> dict[str, Any]:
    _require_rest_auth(request)
    broker = _state.get("broker")
    if broker is None:
        return {"connectors": []}
    ctx = _request_context(request)
    items = broker.catalog(ctx, category=category or None,
                           capability=capability or None, query=q or None)
    # mcp-connectors: markeer 'connected' als er een ingelogde MCP-server met
    # dezelfde URL gekoppeld is (unificatie met de bestaande MCP-koppeling)
    try:
        from span.integrations.mcp_client import load_servers
        servers = await asyncio.to_thread(load_servers, _state["brain"])
        logged = {(s.get("url") or "").rstrip("/") for s in servers if s.get("token")}
        for c in items:
            if c.get("provider") == "mcp" and (c.get("mcp_url") or "").rstrip("/") in logged:
                c["connected"] = True
    except Exception:
        pass
    # api_key-connectors: connected = de bijbehorende client bestaat (sleutel gezet)
    for c in items:
        if c.get("auth") == "api_key" and _state.get(c.get("id")) is not None:
            c["connected"] = True
    return {"connectors": items}


@router.get("/api/integrations/{cid}/actions")
async def integrations_actions(request: Request, cid: str) -> dict[str, Any]:
    _require_rest_auth(request)
    broker = _state.get("broker")
    detail = broker.connector(cid, _request_context(request)) if broker else None
    if detail is None:
        raise HTTPException(status_code=404, detail="Connector niet gevonden.")
    actions = broker.actions(cid) or []
    # MCP-connector: toon de LIVE tools van de gekoppelde server (die zitten niet
    # in de statische spec — ze komen van de server ná login). De agent heeft ze al.
    if detail.get("provider") == "mcp":
        reg = _state.get("mcp")
        prefix = f"mcp__{cid}__"
        live: list[dict[str, Any]] = []
        try:
            for spec in (reg.tool_specs() if reg is not None else []):
                fn = spec.get("function", {})
                name = fn.get("name", "")
                if not name.startswith(prefix):
                    continue
                short = name[len(prefix):]
                from span.safety.risk import mcp_capability
                cap = mcp_capability(short)   # zelfde read/write-bron als de risico-poort
                live.append({"id": name, "name": short,
                             "description": (fn.get("description") or "")[:200],
                             "capability": cap,
                             "approval": "never" if cap == "read" else "on_write",
                             "risk": detail.get("risk", "medium")})
        except Exception:
            live = []
        if live:
            actions = sorted(live, key=lambda a: a["name"])
            detail["connected"] = True
    return {"connector": detail, "actions": actions}


@router.post("/api/integrations/{cid}/{aid}/preview")
async def integrations_preview(request: Request, cid: str, aid: str) -> dict[str, Any]:
    _require_rest_auth(request)
    broker = _state.get("broker")
    payload = await _json_body(request)
    prev = broker.preview(cid, aid, payload) if broker else None
    if prev is None:
        raise HTTPException(status_code=404, detail="Connector of actie niet gevonden.")
    return prev


def _broker_dispatch(ctx: Any):
    """Bouw een dispatch (LO's gegatete tool-uitvoerder) voor een broker-actie,
    zodat een native/tool-gebonden actie via de bestaande risico-poort loopt."""
    from span.orchestrator.tools import ToolBox
    brain = ctx.brain
    tb = ToolBox(
        brain, FragmentStore(brain, _state["llm"]), "broker", _state.get("work"),
        o365=getattr(ctx, "o365", None), asana=_state.get("asana"),
        inbox=_state.get("inbox"), autonomy=_state.get("autonomy") or {},
        llm=_state["llm"], light_model=_effective_settings().model_light,
        disabled=_state.get("disabled_tools"), perms=_state.get("integration_perms"),
        fireflies=_state.get("fireflies"),
        telegram=_state.get("telegram"),
        security=_state.get("security"), mcp=_state.get("mcp"),
        shared=getattr(ctx, "shared", None))
    return tb.dispatch


@router.post("/api/integrations/{cid}/{aid}/run")
async def integrations_run(request: Request, cid: str, aid: str) -> dict[str, Any]:
    _require_rest_auth(request)
    broker = _state.get("broker")
    if broker is None:
        raise HTTPException(status_code=503, detail="Broker niet beschikbaar.")
    ctx = _request_context(request)
    owner = getattr(ctx.brain, "database", "")
    payload = await _json_body(request)
    return await asyncio.to_thread(
        broker.run, cid, aid, payload, ctx,
        inbox=_state.get("inbox"), owner=owner,
        audit=lambda a, d: _audit(a, d, getattr(ctx, "upn", "")),
        dispatch=_broker_dispatch(ctx))


_APIKEY_CONNECTORS = ("asana", "fireflies")


def _rebuild_apikey(cid: str) -> bool:
    """(Her)bouw een api_key-integratie uit de opgeslagen sleutel (of env-fallback)."""
    from span.integrations.credentials import get_key
    brain = _state["brain"]
    settings = _state["settings"]
    key = get_key(brain, cid)
    try:
        if cid == "asana":
            from span.integrations.asana import AsanaClient
            tok = key or settings.jarvis.asana_token
            _state["asana"] = (AsanaClient(
                token=tok, workspace_gid=getattr(settings.jarvis, "asana_workspace", ""))
                if tok else None)
            return _state["asana"] is not None
        if cid == "fireflies":
            from span.integrations.fireflies import FirefliesClient
            k = key or settings.jarvis.fireflies_api_key
            _state["fireflies"] = FirefliesClient(k) if k else None
            return _state["fireflies"] is not None
    except Exception:
        _state[cid] = None
        return False
    return False


@router.post("/api/integrations/{cid}/key")
async def integrations_set_key(request: Request, cid: str) -> dict[str, Any]:
    """Sla een API-sleutel op (in het brein) en bouw de client meteen — geen
    .env of herstart nodig. De sleutel gaat nooit terug naar de frontend."""
    _require_owner(request)
    if cid not in _APIKEY_CONNECTORS:
        raise HTTPException(status_code=400, detail="Deze integratie gebruikt geen API-sleutel.")
    key = (await _json_body(request)).get("key") or ""
    if not str(key).strip():
        raise HTTPException(status_code=422, detail="Geen sleutel opgegeven.")
    from span.integrations.credentials import save_key
    await asyncio.to_thread(save_key, _state["brain"], cid, str(key).strip())
    ok = await asyncio.to_thread(_rebuild_apikey, cid)
    await asyncio.to_thread(_audit, f"integration_key:{cid}", "API-sleutel opgeslagen via UI",
                            getattr(_request_context(request), "upn", ""))
    return {"saved": True, "connected": ok}


@router.delete("/api/integrations/{cid}/key")
async def integrations_delete_key(request: Request, cid: str) -> dict[str, Any]:
    _require_owner(request)
    if cid not in _APIKEY_CONNECTORS:
        raise HTTPException(status_code=400, detail="Deze integratie gebruikt geen API-sleutel.")
    from span.integrations.credentials import delete_key
    await asyncio.to_thread(delete_key, _state["brain"], cid)
    ok = await asyncio.to_thread(_rebuild_apikey, cid)  # val terug op env of None
    return {"deleted": True, "connected": ok}


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@router.get("/api/provenance/{key}")
async def provenance(request: Request, key: str) -> dict[str, Any]:
    """F3.5 — 'waarom weet je dit?': de bron-keten van een formele node of
    fragment (DISTILLED_FROM/FROM_SESSION/MENTIONS), voor de HUD."""
    _require_rest_auth(request)
    brain: BrainDB = _request_context(request).brain

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


# Property-namen die NOOIT in een export mogen (secrets + embeddings). De
# Config-node bewaart integratie-sleutels + MCP OAuth-tokens; een backup is
# platte JSON en mag die niet lekken (audit P0-2).
_SECRET_PROP_HINTS = ("token", "refresh", "secret", "password", "api_key", "apikey", "_key")
_SECRET_PROP_EXACT = {"embedding", "integration_keys", "mcp_servers"}


def _is_secret_prop(key: str) -> bool:
    k = (key or "").lower()
    return key in _SECRET_PROP_EXACT or any(h in k for h in _SECRET_PROP_HINTS)


@router.get("/api/backup")
async def backup(request: Request) -> Any:
    """Brein-export als JSON-download (zonder embeddings én zonder secrets)."""
    _require_owner(request)
    brain: BrainDB = _request_context(request).brain

    def dump() -> dict[str, Any]:
        raw_nodes = brain.run(
            "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, "
            "properties(n) AS props"
        )
        nodes = []
        for n in raw_nodes:  # embeddings + secrets eruit (groot/herleidbaar/gevoelig)
            props = {k: str(v) if not isinstance(v, (str, int, float, bool, list, type(None))) else v
                     for k, v in (n["props"] or {}).items() if not _is_secret_prop(k)}
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
    _require_owner(request)
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
    await asyncio.to_thread(_audit, "document_ingest", f"{filename} ({result['chunks']} delen)",
                            getattr(_request_context(request), "upn", ""))
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
        _t0 = time.perf_counter()
        text = await asyncio.to_thread(stt.transcribe, audio)
        from span import telemetry
        telemetry.record("stt", (time.perf_counter() - _t0) * 1000.0,
                         {"backend": stt.backend()})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcriptie mislukt: {exc}")
    import logging
    logging.getLogger("uvicorn.error").info("STT transcript ontvangen (%d tekens)", len(text))
    return {"text": text}


@router.get("/api/stt/status")
async def stt_status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    from span.server import stt
    return {"available": stt.available(), "model": stt.MODEL_NAME}


@router.post("/api/tts")
async def text_to_speech(request: Request) -> Any:
    """Tekst → gesproken audio (WAV) via server-side Piper. De HUD haalt dit
    per zin op zodat barge-in schoon werkt."""
    _require_rest_auth(request)
    from span.server import tts
    if not tts.available():
        raise HTTPException(status_code=501, detail="Server-TTS niet beschikbaar.")
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Lege tekst.")
    text = text[:1200]  # cap per fragment

    def _num(key, lo, hi):
        v = body.get(key)
        if v is None:
            return None
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return None

    spk = body.get("speaker_id")
    try:
        spk = int(spk) if spk is not None else None
    except (TypeError, ValueError):
        spk = None
    # speaker mag een naam zijn (XTTS) of nummer (Piper)
    speaker = body.get("speaker")
    if speaker is not None:
        speaker = str(speaker)[:80]
    try:
        audio = await asyncio.to_thread(
            tts.synthesize, text,
            speaker=speaker,
            speaker_id=spk,
            length_scale=_num("length_scale", 0.5, 2.0),
            noise_scale=_num("noise_scale", 0.0, 1.5),
            noise_w_scale=_num("noise_w_scale", 0.0, 1.5),
            volume=_num("volume", 0.1, 2.0),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS mislukt: {exc}")
    from fastapi.responses import Response
    return Response(content=audio, media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/tts_stream")
async def tts_stream(request: Request) -> Any:
    """Streamt audio (ruwe PCM16 @ 24kHz) door van de XTTS-GPU-service terwijl die
    genereert — eerste klank ~0,2s. Alleen bij de XTTS-backend."""
    _require_rest_auth(request)
    from span.server import tts as ttsmod
    if not ttsmod.XTTS_URL:
        raise HTTPException(status_code=501, detail="Streaming niet beschikbaar.")
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Lege tekst.")
    payload: dict[str, Any] = {"text": text[:1200], "language": "nl"}
    spk = body.get("speaker")
    if spk:
        payload["speaker"] = str(spk)[:80]
    import httpx
    from fastapi.responses import StreamingResponse

    async def gen():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", ttsmod.XTTS_URL + "/tts_stream",
                                     json=payload) as r:
                if r.status_code != 200:
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk

    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"X-Sample-Rate": "24000", "Cache-Control": "no-store"})


@router.get("/api/tts/status")
async def tts_status(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    from span.server import tts
    info = {"available": tts.available()}
    if info["available"]:
        info.update(tts.voice_info())
        # streamen kan alleen via XTTS; alleen relevant als die ook actief is
        info["streaming"] = bool(tts.XTTS_URL) and tts.engine() == "xtts"
        # keuzemenu: welke bronnen zijn er + wat is de beheerder-keuze
        info["engines"] = tts.engines_available()
        info["engine_override"] = tts._ENGINE_OVERRIDE
        info["is_owner"] = _is_owner(request)   # engine-keuze is server-breed
    return info


@router.get("/api/skills")
async def skills_list(request: Request) -> Any:
    _require_rest_auth(request)
    ctx = _request_context(request)
    from span.memory import skills as sk
    from span.orchestrator.tools import TOOL_META
    items = await asyncio.to_thread(sk.list_skills, ctx.brain, ctx.shared, True)
    # ook de beschikbare tools meegeven voor de macro-bouwer
    tools = sorted(name for name, (_grp, _rw) in TOOL_META.items()
                   if not name.startswith("skill_"))
    return {"skills": items, "tools": tools}


@router.post("/api/skills")
async def skills_upsert(request: Request) -> Any:
    _require_rest_auth(request)
    ctx = _request_context(request)
    from span.memory import skills as sk
    body = await request.json()
    try:
        res = await asyncio.to_thread(
            lambda: sk.upsert_skill(
                ctx.brain,
                name=body.get("name", ""), description=body.get("description", ""),
                trigger=body.get("trigger", ""), kind=body.get("kind", "workflow"),
                body=body.get("body", ""), steps=body.get("steps"),
                params=body.get("params"), author="user",
                enabled=bool(body.get("enabled", True))))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await asyncio.to_thread(_audit, "skill_upsert", res.get("name", ""),
                            getattr(_request_context(request), "upn", ""))
    return res


@router.post("/api/skills/{name}/enable")
async def skills_enable(request: Request, name: str) -> Any:
    _require_rest_auth(request)
    ctx = _request_context(request)
    from span.memory import skills as sk
    body = await request.json()
    en = bool(body.get("enabled", True))
    ok = await asyncio.to_thread(sk.set_enabled, ctx.brain, name, en)
    if not ok:
        raise HTTPException(status_code=404, detail="Skill niet gevonden.")
    return {"name": name, "enabled": en}


@router.delete("/api/skills/{name}")
async def skills_delete(request: Request, name: str) -> Any:
    _require_rest_auth(request)
    ctx = _request_context(request)
    from span.memory import skills as sk
    ok = await asyncio.to_thread(sk.delete_skill, ctx.brain, name)
    if not ok:
        raise HTTPException(status_code=404, detail="Skill niet gevonden.")
    return {"deleted": name}


@router.get("/api/tasks")
async def tasks_list(request: Request) -> Any:
    _require_rest_auth(request)
    tm = _state.get("tasks")
    if tm is None:
        return {"tasks": [], "active": 0}
    owner = getattr(_request_context(request).brain, "database", "")
    items = tm.list(owner=owner)[:25]
    return {"active": tm.active_count(owner=owner),
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"],
                       "progress": t["progress"], "percent": t.get("percent", 0),
                       "team": t.get("team", False), "steps": t["steps"],
                       "result": t["result"], "updated": t["updated"]} for t in items]}


@router.post("/api/tasks/{tid}/cancel")
async def tasks_cancel(request: Request, tid: int) -> Any:
    _require_rest_auth(request)
    tm = _state.get("tasks")
    if tm is None:
        raise HTTPException(status_code=404, detail="Geen taken.")
    owner = getattr(_request_context(request).brain, "database", "")
    t = tm.get(int(tid))
    if t is None or (t.get("owner") or "") not in ("", owner):
        raise HTTPException(status_code=404, detail="Taak niet gevonden.")
    return {"cancelled": tm.cancel(int(tid))}


@router.post("/api/fireflies/sync")
async def fireflies_sync(request: Request, deep: bool = Query(False)) -> dict[str, Any]:
    """Handmatige sync: meetings → brein, actiepunten → Agent Inbox.
    deep=true verwerkt de volledige historie (idempotent)."""
    _require_rest_auth(request)
    if _state.get("fireflies") is None:
        raise HTTPException(status_code=400, detail="Fireflies niet geconfigureerd.")
    from span.jarvis.meetings import sync_meetings
    return await asyncio.to_thread(sync_meetings, _state, 8, deep)


# C3: procesbegin voor uptime; module-import valt samen met serverstart.
_STARTED = time.monotonic()


@router.get("/livez")
async def livez() -> dict[str, Any]:
    """Liveness-probe (bewust zonder auth en zonder DB): de container-healthcheck
    en een load-balancer dragen geen token; 'het proces draait' is hier genoeg.
    Minimale payload — geen versie of details op de unauth-surface."""
    return {"status": "ok", "uptime_s": int(time.monotonic() - _STARTED)}


@router.get("/readyz")
async def readyz() -> Any:
    """Readiness-probe (zonder auth): 503 zolang het brein niet bereikbaar is,
    zodat een proxy/orchestrator verkeer kan wegleiden i.p.v. 500's serveren."""
    brain = _state.get("brain")
    try:
        if brain is None:
            raise RuntimeError("brein nog niet geladen")
        await asyncio.to_thread(brain.run, "RETURN 1 AS ok")
    except Exception:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return {"status": "ready"}


@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    _require_rest_auth(request)
    ctx = _request_context(request)
    brain_ok = True
    try:
        await asyncio.to_thread(ctx.brain.run, "RETURN 1 AS ok")
    except Exception:
        brain_ok = False
    o365 = ctx.o365
    return {
        "brain": brain_ok,
        "o365": bool(o365) and await asyncio.to_thread(o365.is_authenticated),
        "asana": _state.get("asana") is not None,
        "inbox_open": _state["inbox"].open_count(getattr(ctx.brain, "database", "")),
        "version": __version__,
        "uptime_s": int(time.monotonic() - _STARTED),
    }


@router.get("/api/meetings")
async def meetings_list(request: Request) -> dict[str, Any]:
    """Recente Fireflies-meetings voor het HUD-paneel (volledig-weergave)."""
    _require_rest_auth(request)
    ctx = _request_context(request)
    rows = await asyncio.to_thread(
        ctx.brain.run,
        "MATCH (m:Meeting) RETURN m.title AS title, m.date AS date, "
        "m.duration_min AS duration_min, m.participants AS participants "
        "ORDER BY coalesce(m.date, '') DESC LIMIT 8")
    return {"configured": _state.get("fireflies") is not None, "meetings": rows}


@router.get("/api/jarvis/daily")
async def jarvis_daily(request: Request, force: bool = Query(False)) -> dict[str, Any]:
    """De dagstart van vandaag; genereert hem alsnog als de scheduler nog
    niet geweest is (of bij force=true)."""
    _require_rest_auth(request)
    ctx = _request_context(request)
    from span.jarvis.daily import today_local
    cached = _state.get("daily")
    if force or not cached or cached.get("date") != today_local():
        _state["daily"] = await asyncio.to_thread(
            generate_daily, ctx.brain, _state["llm"],
            ctx.o365, _state.get("asana"),
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
    ctx = _request_context(request)
    mcp = _state.get("mcp")
    data = await asyncio.to_thread(
        build_briefing, ctx.brain, ctx.o365, _state.get("asana"), "Bas", mcp
    )
    o365 = ctx.o365
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
    _require_owner(request)
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
    _require_owner(request)
    from span.integrations.mcp_client import load_servers, save_servers
    servers = [s for s in await asyncio.to_thread(load_servers, _state["brain"])
               if s["name"] != name]
    await asyncio.to_thread(save_servers, _state["brain"], servers)
    await asyncio.to_thread(_rebuild_mcp)
    return {"deleted": name}


def _callback_uri(request: Request) -> str:
    # De OAuth-redirect moet een stabiele, publieke https-URL zijn. Achter een
    # reverse proxy (Cosmos/Cloudflare) is request.base_url onbetrouwbaar: uvicorn
    # vertrouwt de X-Forwarded-headers niet, dus scheme wordt 'http'. Bovendien is
    # een redirect_uri uit de Host-header afleiden een host-header-injection-risico.
    # Daarom expliciet via SPAN_PUBLIC_URL; alleen lokaal terugvallen op de request.
    base = os.environ.get("SPAN_PUBLIC_URL", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/api/mcp/oauth/callback"


@router.post("/api/mcp/{name}/connect")
async def mcp_connect(request: Request, name: str) -> dict[str, Any]:
    """Start de OAuth-login: discover + dynamische registratie + PKCE.
    Geeft de authorize-URL terug; Bas opent die en logt in."""
    _require_owner(request)
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
        resp = getattr(exc, "response", None)
        body = ""
        if resp is not None:
            try:
                body = resp.text
            except Exception:
                body = ""
        if "invalid_redirect_uri" in body:
            # loopback-only DCR (bv. Asana): server accepteert geen publieke
            # callback -> een gehoste app als LO kan hier niet via MCP-login koppelen
            raise HTTPException(status_code=422, detail=(
                f"'{name}' accepteert alleen lokale (localhost) redirect-URI's voor "
                "dynamische registratie. Een gehoste app als LO kan hier niet via "
                "MCP-login koppelen — gebruik een eigen OAuth-app of een API-sleutel."))
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
        # auto-skill: maak een werkwijze-skill uit de tools van deze server, zodat
        # de integratie meteen 'actief' is bij het opstarten (best effort)
        try:
            from span.integrations.broker.autoskill import sync_mcp_skill
            from span.integrations.broker.connectors import get_connector
            reg = _state.get("mcp")
            conn = get_connector(pending["name"])
            dn = conn.name if conn is not None else pending["name"]
            if reg is not None:
                sync_mcp_skill(_state["brain"], pending["name"], dn, reg.tool_specs())
        except Exception:
            pass
        return pending["name"]

    try:
        name = await asyncio.to_thread(finish)
    except Exception as exc:
        return PlainTextResponse(f"Token-uitwisseling mislukt: {exc}", status_code=502)
    return PlainTextResponse(
        f"MCP-server '{name}' is gekoppeld. Je kunt dit tabblad sluiten en terug "
        f"naar {AGENT_NAME} — de tools verschijnen bij een nieuwe sessie.")


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
