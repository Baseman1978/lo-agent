"""Autonome dagstart — elke ochtend staat de briefing klaar, ongevraagd.

Een achtergrondtaak genereert op het ingestelde tijdstip (Config-node,
default 07:00) de briefing plus een spreekbare samenvatting (licht model).
De HUD haalt hem op via /api/jarvis/daily en leest hem één keer voor.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

from span.db.brain import BrainDB
from span.jarvis.briefing import build_briefing
from span.llm.client import LLMClient

DEFAULT_TIME = "07:00"

SPOKEN_PROMPT = """Je bent Span, de JARVIS van Bas. Hieronder de ruwe dagstart-data (JSON).
Schrijf een gesproken ochtendbriefing in het Nederlands: 3 tot 6 zinnen,
warm maar zakelijk, JARVIS-toon. Noem alleen wat er echt is: afspraken met
tijden, agenda-conflicten, urgente mail, onbeantwoorde vragen (followups),
taken met deadlines, stilgevallen quests (stale). Niets verzinnen;
lege onderdelen oversla je. Sluit af met één concrete focus-suggestie.
Antwoord met uitsluitend de gesproken tekst."""

EVENING_PROMPT = """Je bent Span, de JARVIS van Bas. Hieronder de stand van zaken (JSON).
Schrijf een korte gesproken dagafsluiting in het Nederlands: 2 tot 4 zinnen.
Wat bleef vandaag liggen (onbeantwoorde vragen, open taken), en wat is morgen
de eerste afspraak. Sluit warm af. Antwoord met uitsluitend de gesproken tekst."""

CONSOLIDATE_PROMPT = """Je bent het slaap-subsysteem van een AI-agent met een Neo4j-geheugen.
Hieronder recente MemoryFragments (JSON: id, type, content). Doe twee dingen:
1. duplicates: groepen ids die hetzelfde feit beschrijven (eerste id = te bewaren origineel)
2. insights: maximaal 3 patronen die uit meerdere fragmenten samen oprijzen
3. contradictions: paren fragmenten die elkaar tegenspreken (zelfde onderwerp, strijdige inhoud)

Wees streng: alleen echte duplicaten, echte patronen, echte tegenspraken. Antwoord met uitsluitend JSON:
{"duplicates": [["mf-houden", "mf-dubbel", ...]], "insights": [{"title": "<kort>", "body": "<het patroon>"}], "contradictions": [{"ids": ["mf-a", "mf-b"], "issue": "<wat botst>"}]}"""


def briefing_time(brain: BrainDB) -> str:
    rows = brain.run("MATCH (c:Config {id:'runtime'}) RETURN c.briefing_time AS t")
    t = (rows[0]["t"] if rows else None) or DEFAULT_TIME
    return t


def set_briefing_time(brain: BrainDB, value: str) -> str:
    value = value.strip() or DEFAULT_TIME
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValueError(f"Ongeldig tijdstip: {value!r} (verwacht HH:MM)")
    brain.run(
        "MERGE (c:Config {id:'runtime'}) SET c.briefing_time = $t", t=value
    )
    return value


def generate_daily(
    brain: BrainDB,
    llm: LLMClient,
    o365: Any = None,
    asana: Any = None,
    light_model: str | None = None,
) -> dict[str, Any]:
    """Briefing + gesproken samenvatting; faalt zacht naar kale briefing."""
    data = build_briefing(brain, o365, asana)
    if o365 is not None:
        try:
            data["followups"] = o365.unanswered_sent()
        except Exception:
            data["followups"] = []
    data["weather"] = _weather()
    spoken = ""
    try:
        message = llm.chat(
            [
                {"role": "system", "content": SPOKEN_PROMPT},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)},
            ],
            model=light_model,
            temperature=0.5,
            max_tokens=600,
        )
        spoken = (message.content or "").strip()
    except Exception:
        spoken = data.get("greeting", "Goedemorgen.")
    return {"date": date.today().isoformat(), "spoken": spoken, "briefing": data}


def _weather(lat: float = 52.156, lon: float = 5.387) -> dict[str, Any]:
    """Weer voor Amersfoort e.o. via open-meteo (gratis, geen key). Zacht falend."""
    try:
        import requests
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "current": "temperature_2m",
                "timezone": "Europe/Amsterdam", "forecast_days": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "nu": d.get("current", {}).get("temperature_2m"),
            "max": (d.get("daily", {}).get("temperature_2m_max") or [None])[0],
            "min": (d.get("daily", {}).get("temperature_2m_min") or [None])[0],
            "neerslagkans_pct": (d.get("daily", {}).get("precipitation_probability_max") or [None])[0],
        }
    except Exception:
        return {}


WEEKREVIEW_PROMPT = """Je bent Span. Hieronder de sessie-samenvattingen en fragmenten van
deze week (JSON). Schrijf een weekreview in het Nederlands: 4-6 zinnen — wat is
bereikt, wat schoof door, welk patroon valt op. Eerlijk, geen opsmuk.
Antwoord met uitsluitend de tekst."""


def generate_weekreview(brain, llm, light_model=None) -> str:
    """Vrijdagmiddag: terugblik op de week, opgeslagen als Insight."""
    data = {
        "sessies": brain.run(
            "MATCH (s:Session) WHERE s.started > datetime() - duration('P7D') "
            "AND s.summary IS NOT NULL RETURN s.summary AS summary ORDER BY s.started"
        ),
        "fragmenten": brain.run(
            "MATCH (mf:MemoryFragment) WHERE mf.created > datetime() - duration('P7D') "
            "RETURN mf.type AS type, mf.content AS content ORDER BY mf.created DESC LIMIT 40"
        ),
    }
    message = llm.chat(
        [
            {"role": "system", "content": WEEKREVIEW_PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)},
        ],
        model=light_model, temperature=0.4, max_tokens=500,
    )
    review = (message.content or "").strip()
    if review:
        brain.run(
            "CREATE (:Insight {title: $title, body: $body, source: 'weekreview', "
            "created: datetime()})",
            title=f"Weekreview {date.today().isoformat()}", body=review,
        )
    return review


def generate_evening(brain, llm, o365=None, asana=None, light_model=None) -> dict[str, Any]:
    """Dagafsluiting: wat bleef liggen + eerste afspraak morgen."""
    data = build_briefing(brain, o365, asana)
    if o365 is not None:
        try:
            data["followups"] = o365.unanswered_sent()
            data["tomorrow"] = o365.calendar(days=2)[:4]
        except Exception:
            pass
    try:
        message = llm.chat(
            [
                {"role": "system", "content": EVENING_PROMPT},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)},
            ],
            model=light_model, temperature=0.5, max_tokens=400,
        )
        spoken = (message.content or "").strip()
    except Exception:
        spoken = "De dag zit erop."
    return {"date": date.today().isoformat(), "spoken": spoken, "briefing": data}


def consolidate_memory(brain, llm, light_model=None) -> dict[str, int]:
    """Slaap-cyclus: duplicaten markeren, patronen promoveren naar Insights."""
    fragments = brain.run(
        "MATCH (mf:MemoryFragment) WHERE mf.superseded IS NULL "
        "RETURN mf.id AS id, mf.type AS type, mf.content AS content "
        "ORDER BY mf.created DESC LIMIT 60"
    )
    if len(fragments) < 10:
        return {"duplicates": 0, "insights": 0, "contradictions": []}
    parsed = llm.chat_json(
        [
            {"role": "system", "content": CONSOLIDATE_PROMPT},
            {"role": "user", "content": json.dumps(fragments, ensure_ascii=False)},
        ],
        model=light_model,
    )
    known_ids = {f["id"] for f in fragments}
    dup_count = 0
    for group in (parsed.get("duplicates") or [])[:10]:
        to_mark = [g for g in group[1:] if g in known_ids]  # eerste blijft
        if to_mark:
            brain.run(
                "UNWIND $ids AS mf_id MATCH (mf:MemoryFragment {id: mf_id}) "
                "SET mf.superseded = true",
                ids=to_mark,
            )
            dup_count += len(to_mark)
    insight_count = 0
    for ins in (parsed.get("insights") or [])[:3]:
        title = (ins.get("title") or "").strip()
        body = (ins.get("body") or "").strip()
        if title and body:
            brain.run(
                "CREATE (:Insight {title: $title, body: $body, "
                "source: 'consolidatie', created: datetime()})",
                title=title, body=body,
            )
            insight_count += 1
    # tegenspraken: alleen melden als ze niet eerder gemeld zijn, daarna vlaggen
    contradictions = []
    for c in (parsed.get("contradictions") or [])[:3]:
        ids = [i for i in (c.get("ids") or []) if i in known_ids]
        if not c.get("issue") or not ids:
            continue
        already = brain.run(
            "UNWIND $ids AS i MATCH (mf:MemoryFragment {id: i}) "
            "WHERE mf.contradiction_flagged = true RETURN count(mf) AS n",
            ids=ids,
        )
        if already and already[0]["n"] > 0:
            continue  # eerder gemeld — niet opnieuw lastigvallen
        brain.run(
            "UNWIND $ids AS i MATCH (mf:MemoryFragment {id: i}) "
            "SET mf.contradiction_flagged = true",
            ids=ids,
        )
        contradictions.append({"ids": ids, "issue": c["issue"]})
    return {"duplicates": dup_count, "insights": insight_count,
            "contradictions": contradictions}


def _last_run(brain, key: str) -> str:
    rows = brain.run(
        f"MATCH (c:Config {{id:'runtime'}}) RETURN c.last_{key} AS d"
    )
    return (rows[0]["d"] if rows else None) or ""


def _mark_run(brain, key: str) -> None:
    brain.run(
        f"MERGE (c:Config {{id:'runtime'}}) SET c.last_{key} = $d",
        d=date.today().isoformat(),
    )


def reflect_orphan_sessions(state: dict[str, Any], max_sessions: int = 2) -> int:
    """Sessies die nooit netjes met /end zijn afgesloten (tab dicht, verbinding
    weg) alsnog evalueren zodra ze > 3 uur oud zijn — de cirkel blijft rond."""
    brain = state["brain"]
    orphans = brain.run(
        """
        MATCH (s:Session)
        WHERE s.ended IS NULL
          AND s.started < datetime() - duration('PT3H')
          AND EXISTS { MATCH (:MemoryFragment)-[:FROM_SESSION]->(s) }
        RETURN s.id AS id ORDER BY s.started LIMIT $n
        """,
        n=max_sessions,
    )
    if not orphans:
        return 0
    from span.evaluation.reflect import reflect_session
    from span.memory.fragments import FragmentStore
    fragments = FragmentStore(brain, state["llm"])
    done = 0
    for row in orphans:
        try:
            result = reflect_session(state["settings"], brain, state["llm"],
                                     fragments, row["id"])
            done += 1
            inbox = state.get("inbox")
            if inbox is not None and result.get("written"):
                inbox.add(
                    kind="notify", title="Achtergelaten sessie geëvalueerd",
                    detail=f"{row['id']}: {result['summary'][:160]}",
                )
        except Exception:
            continue
    return done


EVENING_TIME = "17:15"
CONSOLIDATE_TIME = "03:30"


async def daily_scheduler(state: dict[str, Any]) -> None:
    """Achtergrondtaak: dagstart, dagafsluiting en nachtelijke consolidatie.
    Laatste-run-datums staan in de Config-node, zodat een herstart niets
    dubbel uitvoert."""

    def due(target: str, key: str, now: datetime) -> bool:
        today = date.today().isoformat()
        return (now.strftime("%H:%M") >= target
                and _last_run(state["brain"], key) != today)

    def mark(key: str) -> None:
        _mark_run(state["brain"], key)

    while True:
        try:
            now = datetime.now()
            today = date.today().isoformat()

            target = briefing_time(state["brain"])
            cached = state.get("daily")
            if now.strftime("%H:%M") >= target and (
                not cached or cached.get("date") != today
            ):
                state["daily"] = await asyncio.to_thread(
                    generate_daily, state["brain"], state["llm"],
                    state.get("o365"), state.get("asana"),
                    state["settings"].model_light,
                )

            if due(EVENING_TIME, "evening", now):
                mark("evening")
                evening = await asyncio.to_thread(
                    generate_evening, state["brain"], state["llm"],
                    state.get("o365"), state.get("asana"),
                    state["settings"].model_light,
                )
                state["evening"] = evening
                inbox = state.get("inbox")
                if inbox is not None:
                    inbox.add(kind="notify", title="Dagafsluiting",
                              detail=evening["spoken"][:240])
                tg = state.get("telegram")
                if tg is not None and tg.linked:
                    await asyncio.to_thread(tg.send, "🌇 DAGAFSLUITING\n\n" + evening["spoken"])

            if due(CONSOLIDATE_TIME, "consolidate", now):
                mark("consolidate")
                result = await asyncio.to_thread(
                    consolidate_memory, state["brain"], state["llm"],
                    state["settings"].model_light,
                )
                inbox = state.get("inbox")
                if inbox is not None:
                    if result["duplicates"] or result["insights"]:
                        inbox.add(
                            kind="notify", title="Nachtelijke consolidatie",
                            detail=f"{result['duplicates']} duplicaten samengevoegd, "
                                   f"{result['insights']} nieuwe inzichten.",
                        )
                    for c in result.get("contradictions", []):
                        inbox.add(
                            kind="notify", title="Tegenspraak in het geheugen",
                            detail=f"{c['issue']} ({', '.join(c['ids'])}) — welke klopt?",
                            urgency="high",
                        )

            # door Span zelf geplande taken/herinneringen
            from span.jarvis.crons import run_due_crons
            await asyncio.to_thread(run_due_crons, state)

            # verweesde sessies alsnog door de evaluatiecirkel (elk half uur)
            if now.minute % 30 == 10:
                await asyncio.to_thread(reflect_orphan_sessions, state)

            # Fireflies-meetings binnenhalen (idempotent, elke ~30 min)
            if state.get("fireflies") is not None and now.minute % 30 == 0:
                from span.jarvis.meetings import sync_meetings
                result = await asyncio.to_thread(sync_meetings, state)
                if result["new"]:
                    inbox = state.get("inbox")
                    if inbox is not None:
                        inbox.add(
                            kind="notify", title="Meetings vastgelegd",
                            detail=f"{result['new']} nieuwe meeting(s) in het geheugen; "
                                   f"{result['tasks']} actiepunt(en) klaar voor Asana "
                                   "in de Agent Inbox.",
                        )

            if now.weekday() == 4 and due("16:30", "weekreview", now):
                mark("weekreview")
                review = await asyncio.to_thread(
                    generate_weekreview, state["brain"], state["llm"],
                    state["settings"].model_light,
                )
                if review:
                    inbox = state.get("inbox")
                    if inbox is not None:
                        inbox.add(kind="notify", title="Weekreview",
                                  detail=review[:240])
                    tg = state.get("telegram")
                    if tg is not None and tg.linked:
                        await asyncio.to_thread(tg.send, "📊 WEEKREVIEW\n\n" + review)
        except Exception:
            pass  # scheduler mag nooit sterven
        await asyncio.sleep(60)
