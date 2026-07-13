"""Autonome dagstart — elke ochtend staat de briefing klaar, ongevraagd.

Een achtergrondtaak genereert op het ingestelde tijdstip (Config-node,
default 07:00) de briefing plus een spreekbare samenvatting (licht model).
De HUD haalt hem op via /api/jarvis/daily en leest hem één keer voor.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Any

from span import AGENT_NAME
# re-export: ambient.py en crons.py importeren de klok van hieruit
from span.clock import TZ, now_local, today_local  # noqa: F401
from span.db.brain import BrainDB
from span.jarvis.announce import enqueue
from span.jarvis.briefing import build_briefing
from span.llm.client import LLMClient

DEFAULT_TIME = "07:00"

SPOKEN_PROMPT = "Je bent " + AGENT_NAME + """, de JARVIS van Bas. Hieronder de ruwe dagstart-data (JSON).
Schrijf een gesproken ochtendbriefing in het Nederlands: 3 tot 6 zinnen,
kordaat en met overzicht, als een stafchef die zijn CEO brieft — eerst de kern,
dan details. Noem alleen wat er echt is: afspraken met
tijden, agenda-conflicten, urgente mail, onbeantwoorde vragen (followups),
taken met deadlines, stilgevallen quests (stale). Niets verzinnen;
lege onderdelen oversla je. Sluit af met één concrete focus-suggestie.
Antwoord met uitsluitend de gesproken tekst."""

EVENING_PROMPT = "Je bent " + AGENT_NAME + """, de JARVIS van Bas. Hieronder de stand van zaken (JSON).
Schrijf een korte gesproken dagafsluiting in het Nederlands: 2 tot 4 zinnen.
Wat bleef vandaag liggen (onbeantwoorde vragen, open taken), en wat is morgen
de eerste afspraak. Kordaat en met overzicht, als een stafchef die zijn CEO
brieft — eerst de kern, dan details. Antwoord met uitsluitend de gesproken tekst."""

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


# -- Stille uren (thema PROACTIEVER) -----------------------------------------
# Niet-urgente Telegram-pushes wachten tijdens dit venster; alleen echt urgente
# meldingen breken door. De Agent Inbox/HUD houdt de items sowieso vast, dus er
# gaat niets verloren — alleen de ping wacht.
QUIET_START_DEFAULT = "22:00"
QUIET_END_DEFAULT = "07:00"


def quiet_window(brain: BrainDB) -> tuple[str, str]:
    """(start, eind) van het stiltevenster uit de runtime-Config (zelfde node
    als briefing_time), met defaults 22:00–07:00. Zacht falend."""
    try:
        rows = brain.run(
            "MATCH (c:Config {id:'runtime'}) "
            "RETURN c.quiet_start AS s, c.quiet_end AS e"
        )
    except Exception:
        return QUIET_START_DEFAULT, QUIET_END_DEFAULT
    row = rows[0] if rows else {}
    return (row.get("s") or QUIET_START_DEFAULT,
            row.get("e") or QUIET_END_DEFAULT)


def _set_quiet(brain: BrainDB, prop: str, value: str, default: str) -> str:
    value = value.strip() or default
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValueError(f"Ongeldig tijdstip: {value!r} (verwacht HH:MM)")
    brain.run(
        f"MERGE (c:Config {{id:'runtime'}}) SET c.{prop} = $t", t=value
    )
    return value


# -- Meeting-prep-voorsprong (thema PROACTIEF SPREKEN) -----------------------
# Hoeveel minuten vóór een afspraak de ambient watcher een prep-kaart klaarzet
# (en die ook als gesproken aankondiging inrijgt). Config-node, default 20.
MEETING_PREP_LEAD_DEFAULT = 20


def meeting_prep_lead(brain: BrainDB) -> int:
    """Voorsprong in minuten voor meeting-prep (1..120). Zacht falend."""
    try:
        rows = brain.run(
            "MATCH (c:Config {id:'runtime'}) RETURN c.meeting_prep_lead AS m"
        )
    except Exception:
        return MEETING_PREP_LEAD_DEFAULT
    raw = rows[0]["m"] if rows else None
    try:
        return max(1, min(120, int(raw)))
    except (TypeError, ValueError):
        return MEETING_PREP_LEAD_DEFAULT


def set_meeting_prep_lead(brain: BrainDB, value: Any) -> int:
    try:
        v = max(1, min(120, int(value)))
    except (TypeError, ValueError):
        raise ValueError(f"Ongeldige voorsprong: {value!r} (verwacht minuten 1–120).")
    brain.run(
        "MERGE (c:Config {id:'runtime'}) SET c.meeting_prep_lead = $m", m=v
    )
    return v


def set_quiet_start(brain: BrainDB, value: str) -> str:
    return _set_quiet(brain, "quiet_start", value, QUIET_START_DEFAULT)


def set_quiet_end(brain: BrainDB, value: str) -> str:
    return _set_quiet(brain, "quiet_end", value, QUIET_END_DEFAULT)


def quiet_hours_active(brain: BrainDB, now: datetime | None = None) -> bool:
    """Zit 'now' (default now_local) binnen het stiltevenster? Ondersteunt een
    venster dat over middernacht loopt (22:00–07:00). Faalt zacht naar False
    (bij twijfel niet onderdrukken — huidige 24/7-gedrag als vangnet)."""
    try:
        start_s, end_s = quiet_window(brain)
        start = datetime.strptime(start_s, "%H:%M").time()
        end = datetime.strptime(end_s, "%H:%M").time()
    except Exception:
        return False
    cur = (now or now_local()).time()
    if start == end:
        return False  # leeg venster = altijd stil zou pushes doden -> uit
    if start < end:
        return start <= cur < end
    # over middernacht: actief vanaf start tot eind de volgende ochtend
    return cur >= start or cur < end


def send_respecting_quiet(tg: Any, text: str, brain: BrainDB,
                          urgent: bool = False) -> bool:
    """Stuur een Telegram-push, maar respecteer de stille uren: een niet-urgente
    push tijdens het stiltevenster wordt overgeslagen (en gelogd). Urgente
    pushes breken altijd door. Geeft True terug als er echt verstuurd is."""
    if not urgent and quiet_hours_active(brain):
        title = (text or "").strip().splitlines()[0][:60] if text else ""
        print(f"[quiet] push onderdrukt (stille uren): {title}", flush=True)
        return False
    tg.send(text)
    return True


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
    return {"date": today_local(), "spoken": spoken, "briefing": data}


def _weather() -> dict[str, Any]:
    """Weer voor de dagstart via de gedeelde weer-integratie. Zacht falend."""
    try:
        from span.integrations import weather as wx
        fc = wx.forecast(wx.DEFAULT_LAT, wx.DEFAULT_LON, days=1, place=wx.DEFAULT_PLACE)
        day = (fc.get("dagen") or [{}])[0]
        return {
            "nu": fc.get("nu", {}).get("temperatuur"),
            "weer": fc.get("nu", {}).get("weer"),
            "max": day.get("max"),
            "min": day.get("min"),
            "neerslagkans_pct": day.get("neerslagkans_pct"),
        }
    except Exception:
        return {}


def _store_insight(brain, llm, content: str, source: str) -> str:
    """Insight in hetzelfde schema als reflect.py: id + content + embedding,
    zodat bootstrap en brain_search hem net zo vinden als sessie-inzichten."""
    from uuid import uuid4
    node_id = f"insight-{uuid4().hex[:12]}"
    embedding = None
    try:
        embedding = llm.embed_one(f"Insight: {content}")
    except Exception as exc:
        print(f"[insight] embedding mislukt: {exc}", flush=True)
    brain.run(
        "CREATE (n:Insight {id: $id, content: $content, source: $source, "
        "created: datetime()}) SET n.embedding = $embedding",
        id=node_id, content=content, source=source, embedding=embedding,
    )
    return node_id


WEEKREVIEW_PROMPT = "Je bent " + AGENT_NAME + """. Hieronder de sessie-samenvattingen en fragmenten van
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
        _store_insight(brain, llm, f"Weekreview {today_local()}: {review}", "weekreview")
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
    return {"date": today_local(), "spoken": spoken, "briefing": data}


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
            _store_insight(brain, llm, f"{title}: {body}", "consolidatie")
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
        # de fragmentteksten meegeven zodat de melding een echte keuze kan
        # voorleggen ("welke klopt?") i.p.v. alleen ids die niemand kan duiden
        by_id = {f["id"]: f for f in fragments}
        contradictions.append({
            "ids": ids, "issue": c["issue"],
            "options": [{"id": i, "content": by_id[i]["content"]} for i in ids],
        })
    merged_entities = dedup_entities(brain)
    return {"duplicates": dup_count, "insights": insight_count,
            "contradictions": contradictions, "entities_merged": merged_entities}


def dedup_entities(brain) -> int:
    """Geheugen-hygiene (F2.8/F3): voeg Entity-nodes met een genormaliseerd-
    gelijke naam samen (case/spaties), zodat 'Ron Meijer' en 'ron meijer' één
    knoop met al hun MENTIONS-edges worden. Idempotent.

    Houdt de oudste node als kanoniek, hangt alle MENTIONS van duplicaten
    daaraan en verwijdert de duplicaten."""
    groups = brain.run(
        """
        MATCH (e:Entity)
        WITH toLower(trim(e.name)) AS norm, collect(e) AS nodes
        WHERE size(nodes) > 1
        RETURN norm, [n IN nodes | elementId(n)] AS ids
        """
    )
    merged = 0
    for g in groups:
        ids = g.get("ids") or []
        if len(ids) < 2:
            continue
        # kanoniek = oudste; herhang MENTIONS van de rest en verwijder ze
        brain.run(
            """
            MATCH (e:Entity) WHERE elementId(e) IN $ids
            WITH e ORDER BY e.created LIMIT 1
            WITH e AS keep, $ids AS ids
            MATCH (dup:Entity) WHERE elementId(dup) IN ids AND dup <> keep
            OPTIONAL MATCH (mf)-[r:MENTIONS]->(dup)
            FOREACH (_ IN CASE WHEN mf IS NULL THEN [] ELSE [1] END |
                MERGE (mf)-[:MENTIONS]->(keep))
            DELETE r
            WITH keep, dup
            DETACH DELETE dup
            """,
            ids=ids,
        )
        merged += len(ids) - 1
    return merged


def _last_run(brain, key: str) -> str:
    rows = brain.run(
        f"MATCH (c:Config {{id:'runtime'}}) RETURN c.last_{key} AS d"
    )
    return (rows[0]["d"] if rows else None) or ""


def _mark_run(brain, key: str) -> None:
    brain.run(
        f"MERGE (c:Config {{id:'runtime'}}) SET c.last_{key} = $d",
        d=today_local(),
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
            # puur interne huishouding: log-only, GEEN inbox-item (dit vraagt
            # geen actie van Bas en zou de inbox alleen vervuilen).
            if result.get("written"):
                print(f"[orphan-reflectie] {row['id']} geëvalueerd: "
                      f"{result['summary'][:160]}", flush=True)
        except Exception as exc:
            print(f"[orphan-reflectie] {row['id']}: {type(exc).__name__}: {exc}", flush=True)
            continue
    return done


EVENING_TIME = "17:15"
CONSOLIDATE_TIME = "03:30"
BRAINHEALTH_TIME = "03:45"  # ná de consolidatie van 03:30
# A5: ná de nachtdump (cron 03:00), de consolidatie (03:30) én de brainhealth
# (03:45) — twee zware nachttaken op exact hetzelfde tijdstip is onwenselijk
CHAINCHECK_TIME = "03:50"


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


MAX_ATTEMPTS = 3  # daarna geven we de dagtaak op (met melding) i.p.v. te spammen


async def daily_scheduler(state: dict[str, Any]) -> None:
    """Achtergrondtaak in NL-tijd: dagstart, dagafsluiting, consolidatie,
    weekreview, crons, orphan-reflectie en Fireflies-sync.

    Faal-gedrag: een taak wordt pas als 'gedaan' gemarkeerd NA succes; bij een
    fout volgt een log-regel en een retry op de volgende tick, met een cap van
    MAX_ATTEMPTS per dag (daarna één melding in de Agent Inbox)."""
    attempts: dict[str, int] = {}

    def log(msg: str) -> None:
        print(f"[scheduler {now_local():%H:%M}] {msg}", flush=True)

    async def run_task(key: str, coro_fn) -> None:
        """Voer een dagtaak uit met mark-after-success en attempt-cap."""
        attempt_key = f"{key}:{today_local()}"
        if attempts.get(attempt_key, 0) >= MAX_ATTEMPTS:
            return
        try:
            await coro_fn()
            _mark_run(state["brain"], key)
            attempts.pop(attempt_key, None)
            log(f"{key}: gelukt")
        except Exception as exc:
            attempts[attempt_key] = attempts.get(attempt_key, 0) + 1
            log(f"{key}: poging {attempts[attempt_key]} mislukt — {type(exc).__name__}: {exc}")
            if attempts[attempt_key] >= MAX_ATTEMPTS:
                _mark_run(state["brain"], key)  # opgeven voor vandaag
                inbox = state.get("inbox")
                if inbox is not None:
                    inbox.add(kind="notify", title=f"Taak '{key}' vandaag mislukt",
                              detail=f"{MAX_ATTEMPTS}x geprobeerd; laatste fout: {exc}",
                              urgency="high")

    def due(target: str, key: str, now: datetime) -> bool:
        return (now.strftime("%H:%M") >= target
                and _last_run(state["brain"], key) != today_local())

    async def do_evening() -> None:
        evening = await asyncio.to_thread(
            generate_evening, state["brain"], state["llm"],
            state.get("o365"), state.get("asana"), state["settings"].model_light,
        )
        state["evening"] = evening
        inbox = state.get("inbox")
        if inbox is not None:
            inbox.add(kind="notify", title="Dagafsluiting", detail=evening["spoken"][:240])
        # PROACTIEF SPREKEN: dagafsluiting ook hardop, zodra het moment veilig is
        enqueue(state, "evening", evening["spoken"])
        tg = state.get("telegram")
        if tg is not None and tg.linked:
            # dagafsluiting mag wachten tot na de stille uren
            await asyncio.to_thread(send_respecting_quiet, tg,
                                    "🌇 DAGAFSLUITING\n\n" + evening["spoken"],
                                    state["brain"])

    async def do_consolidate() -> None:
        result = await asyncio.to_thread(
            consolidate_memory, state["brain"], state["llm"],
            state["settings"].model_light,
        )
        # De samenvatting (X duplicaten, Y inzichten) is puur intern gepruttel:
        # log-only, GEEN inbox-item meer. De tegenspraak-keuzes hieronder BLIJVEN
        # wél — dat is een echte keuze die Bas moet maken.
        if result["duplicates"] or result["insights"]:
            log(f"consolidatie: {result['duplicates']} duplicaten samengevoegd, "
                f"{result['insights']} nieuwe inzichten")
        inbox = state.get("inbox")
        if inbox is not None:
            for c in result.get("contradictions", []):
                # keuze-item: de HUD toont beide versies met een knop per
                # versie; kiezen archiveert de andere (superseded)
                inbox.add(kind="choice", title="Tegenspraak in het geheugen",
                          detail=f"{c['issue']} — welke versie klopt?",
                          action="memory_pick",
                          payload={"issue": c["issue"],
                                   "options": c.get("options") or []},
                          urgency="high")

    async def do_weekreview() -> None:
        review = await asyncio.to_thread(
            generate_weekreview, state["brain"], state["llm"],
            state["settings"].model_light,
        )
        if review:
            inbox = state.get("inbox")
            if inbox is not None:
                inbox.add(kind="notify", title="Weekreview", detail=review[:240])
            # PROACTIEF SPREKEN: weekreview ook hardop, zodra het moment veilig is
            enqueue(state, "weekreview", review)
            tg = state.get("telegram")
            if tg is not None and tg.linked:
                # weekreview mag wachten tot na de stille uren
                await asyncio.to_thread(send_respecting_quiet, tg,
                                        "📊 WEEKREVIEW\n\n" + review,
                                        state["brain"])

    async def do_brainhealth() -> None:
        from span.db.health import check_brain_health
        report = await asyncio.to_thread(
            check_brain_health, state["brain"], state.get("inbox"))
        log(f"brainhealth: ok={report['ok']} latency={report['latency_ms']}ms")

    # interval-administratie: 'elke ~30 min' mag nooit afhangen van het
    # toevallig raken van een specifieke minuut (een trage tick mist die)
    HALF_HOUR = timedelta(minutes=30)
    orphan_last = now_local() - HALF_HOUR
    ff_last = now_local() - HALF_HOUR
    wd_last = now_local() - HALF_HOUR

    while True:
        try:
            now = now_local()
            today = today_local()

            target = briefing_time(state["brain"])
            cached = state.get("daily")
            daily_key = f"daily:{today}"
            if (now.strftime("%H:%M") >= target
                    and (not cached or cached.get("date") != today)
                    and attempts.get(daily_key, 0) < MAX_ATTEMPTS):
                try:
                    state["daily"] = await asyncio.to_thread(
                        generate_daily, state["brain"], state["llm"],
                        state.get("o365"), state.get("asana"),
                        state["settings"].model_light,
                    )
                    state["daily"]["date"] = today  # NL-dag, niet UTC-dag
                    attempts.pop(daily_key, None)
                    log("dagstart: gegenereerd")
                except Exception as exc:
                    attempts[daily_key] = attempts.get(daily_key, 0) + 1
                    log(f"dagstart: poging {attempts[daily_key]} mislukt — {exc}")
                    if attempts[daily_key] >= MAX_ATTEMPTS:
                        inbox = state.get("inbox")
                        if inbox is not None:
                            inbox.add(kind="notify", title="Dagstart vandaag mislukt",
                                      detail=f"{MAX_ATTEMPTS}x geprobeerd; laatste fout: {exc}",
                                      urgency="high")

            if due(EVENING_TIME, "evening", now):
                await run_task("evening", do_evening)
            if due(CONSOLIDATE_TIME, "consolidate", now):
                await run_task("consolidate", do_consolidate)
            if due(BRAINHEALTH_TIME, "brainhealth", now):
                await run_task("brainhealth", do_brainhealth)
            if now.weekday() == 4 and due("16:30", "weekreview", now):
                await run_task("weekreview", do_weekreview)

            # door Span zelf geplande taken/herinneringen
            from span.jarvis.crons import run_due_crons
            try:
                ran = await asyncio.to_thread(run_due_crons, state)
                if ran:
                    log(f"crons: {ran} uitgevoerd")
            except Exception as exc:
                log(f"crons: mislukt — {exc}")

            # verweesde sessies alsnog door de evaluatiecirkel (elk half uur)
            if now - orphan_last >= HALF_HOUR:
                orphan_last = now
                try:
                    done = await asyncio.to_thread(reflect_orphan_sessions, state)
                    if done:
                        log(f"orphan-reflectie: {done} sessies geëvalueerd")
                except Exception as exc:
                    log(f"orphan-reflectie: mislukt — {exc}")

            # A3 cron-toets: liepen de dagtaken van gisteren echt? (elk half uur;
            # de eerste tick na een herstart toetst meteen — juist dán is een
            # gat waarschijnlijk)
            if now - wd_last >= HALF_HOUR:
                wd_last = now
                from span.jarvis.watchdog import watchdog_tick
                try:
                    n = await asyncio.to_thread(watchdog_tick, state)
                    if n:
                        log(f"watchdog: {n} gemiste geplande taken gemeld")
                except Exception as exc:
                    log(f"watchdog: mislukt — {exc}")

            # Fireflies-meetings binnenhalen (idempotent, elke ~30 min)
            if state.get("fireflies") is not None and now - ff_last >= HALF_HOUR:
                ff_last = now
                from span.jarvis.meetings import sync_meetings
                try:
                    result = await asyncio.to_thread(sync_meetings, state)
                    if result["new"]:
                        # log-only: het vastleggen zelf vraagt geen actie. De
                        # actiepunten die sync_meetings genereert komen los als
                        # eigen inbox-items binnen — dáár zit de eventuele to-do.
                        log(f"fireflies: {result['new']} nieuw, {result['tasks']} taken")
                except Exception as exc:
                    log(f"fireflies: mislukt — {exc}")
        except Exception as exc:
            print(f"[scheduler] tick-fout: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(60)
