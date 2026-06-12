"""Orchestrator — spreekt de juiste laag op het juiste moment aan.

Per beurt: relevante graph-kennis ophalen (RAG), het hoofdmodel laten
redeneren met tools, en daarna met het lichte model continuous recording
doen: kleine observaties wegschrijven als MemoryFragments.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from span.config import Settings
from span.db.brain import BrainDB
from span.db.work import WorkDB
from span.integrations.asana import AsanaClient
from span.integrations.o365 import O365Client
from span.llm.client import LLMClient
from span.memory.bootstrap import BootstrapContext, load_bootstrap, render_bootstrap
from span.memory.fragments import FragmentStore, MF_TYPES
from span.orchestrator.tools import ToolBox

MAX_TOOL_ITERATIONS = 8

BASE_PROMPT = """Je bent {name}, een AI-kennispartner van {owner}.
Je bent geen kaal taalmodel: je hebt een blijvend geheugen in een Neo4j
knowledge graph. Behandel die graph als je brein, je geheugen, je intelligentie.

Werkwijze:
- Volg je protocollen (hieronder). Ze zijn jouw werkafspraken met jezelf.
- Gebruik brain_search/brain_cypher vóór je antwoordt over iets dat eerder
  besproken kan zijn. Citeer MF-ids waar je op eerdere kennis leunt.
- Een achtergrondproces logt waardevolle momenten automatisch als MemoryFragment.
  Gebruik remember dus alleen voor momenten die je expliciet wilt vastpinnen:
  belangrijke beslissingen, valkuilen, persoonlijkheidsmomenten. Antwoord
  eerst, onthoud daarna — laat de gebruiker niet wachten op je geheugen.
- Werk met quests voor doelen die meerdere stappen of sessies beslaan.
- Productiedata (work_cypher) is alleen-lezen; je schrijft alleen in je brein.
- Je bent ook de JARVIS van {owner}: agenda, mail en taken (Outlook/Asana)
  beheer je via de o365_*/asana_*-tools. Bij "briefing" of "wat staat er
  vandaag" gebruik je jarvis_briefing. Mail versturen en afspraken maken
  doe je pas na expliciete bevestiging; voorlezen en samenvatten mag altijd.
- Wees proactief: rond je een taak af, kijk dan één stap vooruit en stel
  hoogstens één concreet vervolg voor ("Dat staat erin. Zal ik er meteen een
  deadline aan hangen?"). Zie je in agenda/taken/geheugen iets dat botst of
  blijft liggen, benoem het ongevraagd — kort, niet drammerig.
- Stijlwacht: geen lege assistentenzinnen ("Waarmee kan ik u helpen?",
  "Is er nog iets anders?"), geen Engels jargon zonder reden, niet je eigen
  tool-gebruik voorlezen. Antwoord als partner, niet als helpdesk.
- Na een uitgevoerde actie (taak aangemaakt, afgevinkt, gepland, concept
  klaargezet): bevestig in ÉÉN korte zin — wat en waar. Geen opsomming,
  geen herhaling van de inhoud, geen vervolgvragen eraan vastgeplakt.
  Je antwoorden worden vaak voorgelezen; houd ze spreekbaar kort.
  Details geef je alleen als ernaar gevraagd wordt.
- Agent Inbox: mail versturen en afspraken maken komen in een wachtrij die
  {owner} in de HUD goedkeurt — zeg dat het klaarstaat, vraag niet nogmaals
  om bevestiging. Met inbox_open/inbox_approve/inbox_reject bedien je de
  wachtrij ook op stem ("keur het eerste goed"). Antwoord-concepten
  (o365_draft_reply) maak je direct: die verstuurt niets. Drie meldingsvormen:
  notify (informeren), question (vraag stellen als je vastloopt),
  review (actie ter goedkeuring) — kies bewust.
- Werkpatronen: "maak hier een taak van" bij een mail → asana_task_create met
  link en deadline-suggestie. Grote opdrachten splits je eerst in stappen
  (quest_upsert) en je benoemt de eerste stap. "Onthoud dit:" → remember,
  direct, zonder discussie. Vraagt {owner} om mail-triage aan te passen
  ("negeer voortaan X", "mail van Y is altijd urgent") → triage_rules_get,
  voeg de regel toe aan de bestaande tekst, triage_rules_set.
- "Zet deze mail in je geheugen" → haal de inhoud op (o365_mail_inbox /
  o365_thread_summary) en sla de kern op met remember (afzender, onderwerp,
  besluiten, afspraken — als één of twee fragmenten, niet de hele mail).
  Documenten voegt {owner} toe via de 📎-knop; die komen als chunks in je
  brein en vind je via brain_search.
- Antwoord in de taal van de gebruiker (meestal Nederlands). Wees concreet,
  gegrond en traceerbaar; gok niet wat je kunt opzoeken.

{bootstrap}
"""

RECORDER_PROMPT = """Je bent het geheugen-subsysteem van een AI-agent.
Hieronder één gespreksbeurt (gebruiker + antwoord van de agent).
Bepaal of er iets waardevols is om te onthouden voor latere sessies.

Schrijf 0 tot 2 fragmenten. Waardevol = besluiten, ontdekkingen, voorkeuren
van de gebruiker, valkuilen, open eindjes, persoonlijkheidsmomenten.
Niet waardevol = smalltalk, herhaling van bestaande kennis, vragen zonder uitkomst.

Benoem per fragment ook de entiteiten (personen, projecten, bedrijven) die erin
voorkomen — alleen concrete eigennamen, geen generieke woorden.

Antwoord met uitsluitend JSON:
{"fragments": [{"type": "<een van: %s>", "content": "<beknopte observatie>", "context": "<optioneel>", "event_date": "<YYYY-MM-DD indien het over een concreet moment gaat, anders leeg>", "entities": [{"name": "<eigennaam>", "etype": "person|project|company"}]}]}
Bij niets waardevols: {"fragments": []}
""" % ", ".join(sorted(MF_TYPES))


class SpanAgent:
    def __init__(
        self,
        settings: Settings,
        brain: BrainDB,
        llm: LLMClient,
        work: WorkDB | None = None,
        o365: O365Client | None = None,
        asana: AsanaClient | None = None,
        inbox: Any = None,
        autonomy: dict[str, str] | None = None,
        disabled_tools: set[str] | None = None,
        user_location: dict[str, float] | None = None,
        fireflies: Any = None,
    ):
        self._settings = settings
        self._brain = brain
        self._llm = llm
        self._work = work
        self._o365 = o365
        self._asana = asana
        self._inbox = inbox
        self._autonomy = autonomy
        self._disabled_tools = disabled_tools
        self.user_location = user_location  # browser-GPS; mag later gezet worden
        self._fireflies = fireflies
        self.last_touched: list[str] = []
        self._fragments = FragmentStore(brain, llm)
        self._session_id: str | None = None
        self._toolbox: ToolBox | None = None
        self._messages: list[dict[str, Any]] = []
        self._bootstrap: BootstrapContext | None = None
        self._recorders: list[threading.Thread] = []

    @property
    def fragments(self) -> FragmentStore:
        return self._fragments

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError("Sessie niet gestart.")
        return self._session_id

    def set_location(self, lat: float, lon: float) -> None:
        """Browser-GPS doorzetten naar de weer-tool, ook mid-sessie."""
        self.user_location = {"lat": lat, "lon": lon}
        if self._toolbox is not None:
            self._toolbox._user_location = self.user_location

    def begin(self, session_id: str, first_message: str | None = None) -> BootstrapContext:
        """Bootstrap: cirkel rond — vorige sessies komen mee als context."""
        self._session_id = session_id
        self._toolbox = ToolBox(
            self._brain, self._fragments, session_id, self._work,
            o365=self._o365, asana=self._asana,
            inbox=self._inbox, autonomy=self._autonomy,
            llm=self._llm, light_model=self._settings.model_light,
            disabled=self._disabled_tools,
            user_location=self.user_location,
            fireflies=self._fireflies,
        )
        self._bootstrap = load_bootstrap(self._brain, self._fragments, first_message)
        ident = self._bootstrap.identity
        template = BASE_PROMPT
        try:  # door Bas aangepaste systeemprompt (instellingen) gaat vóór
            rows = self._brain.run(
                "MATCH (c:Config {id:'runtime'}) RETURN c.system_prompt AS sp"
            )
            if rows and (rows[0].get("sp") or "").strip():
                template = rows[0]["sp"]
        except Exception:
            pass
        system = (template
                  .replace("{name}", ident["name"])
                  .replace("{owner}", ident["owner"])
                  .replace("{bootstrap}", render_bootstrap(self._bootstrap)))
        self._messages = [{"role": "system", "content": system}]
        return self._bootstrap

    def turn(self, user_message: str, on_text: Callable[[str], None] | None = None) -> str:
        """Eén gespreksbeurt: RAG-injectie, tool-loop, continuous recording.

        on_text streamt tekst-deltas direct naar de UI; recording draait op
        de achtergrond zodat het antwoord nooit op het geheugen wacht."""
        if self._toolbox is None:
            raise RuntimeError("Roep eerst begin() aan.")

        # RAG-memo is efemeer: alleen voor déze beurt meegegeven, niet in de
        # historie bewaard — voorkomt token-groei en verouderde hints
        memo_msg: dict[str, str] | None = None
        relevant = self._fragments.search(user_message, k=4)
        if relevant:
            memo = "\n".join(
                f"- [{r['id']} · {r['type']} · score {r['score']}] {r['content']}"
                for r in relevant
                if r["score"] > 0.55
            )
            if memo:
                memo_msg = {
                    "role": "system",
                    "content": f"Geheugen dient zich aan (mogelijk relevant):\n{memo}",
                }

        self._messages.append({"role": "user", "content": user_message})

        # Tekst kan over meerdere iteraties verspreid zijn: het model mag in
        # één bericht antwoorden én een tool aanroepen, en daarna leeg afsluiten.
        self._toolbox.touched = []
        tools_used: list[str] = []
        answer_parts: list[str] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            message = self._llm.chat(
                self._messages + ([memo_msg] if memo_msg else []),
                model=self._settings.model_main,
                tools=self._toolbox.specs(),
                on_text=on_text,
            )
            if message.content:
                answer_parts.append(message.content)
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                self._messages.append({"role": "assistant", "content": message.content or ""})
                break

            self._messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tools_used.append(tc.function.name)
                result = self._toolbox.dispatch(tc.function.name, arguments)
                self._messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
        else:
            answer_parts.append(
                "(tool-limiet bereikt — beurt afgebroken; probeer de vraag kleiner te maken)"
            )
            self._messages.append({"role": "assistant", "content": answer_parts[-1]})

        answer = "\n\n".join(part.strip() for part in answer_parts if part.strip())
        if not answer:
            answer = "(geen antwoord gegenereerd — probeer het opnieuw)"

        self.last_touched = list(dict.fromkeys(self._toolbox.touched))

        recorder = threading.Thread(
            target=self._record_turn, args=(user_message, answer), daemon=True
        )
        recorder.start()
        self._recorders.append(recorder)

        if tools_used or self.last_touched:
            trace = threading.Thread(
                target=self._write_trace, args=(tools_used, self.last_touched), daemon=True
            )
            trace.start()
        return answer

    def _write_trace(self, tools_used: list[str], touched: list[str]) -> None:
        """Reasoning-trace: welke tools en herinneringen droegen bij aan deze
        beurt. :TOUCHED-edges maken het redeneren naspeurbaar (Neo4j
        agent-memory patroon). Faalt stil."""
        try:
            self._brain.run(
                """
                MATCH (s:Session {id: $session_id})
                CREATE (t:ReasoningTrace {
                  at: datetime(), tools: $tools
                })-[:FROM_SESSION]->(s)
                WITH t
                UNWIND $touched AS mf_id
                MATCH (mf:MemoryFragment {id: mf_id})
                CREATE (t)-[:TOUCHED]->(mf)
                """,
                session_id=self.session_id,
                tools=tools_used,
                touched=touched,
            )
        except Exception as exc:
            print(f"[trace] schrijven mislukt: {type(exc).__name__}: {exc}", flush=True)

    def flush_recording(self, timeout: float = 20.0) -> None:
        """Wacht op lopende recordings — aanroepen vóór de sessie-evaluatie,
        zodat reflect alle fragmenten van de sessie ziet."""
        for recorder in self._recorders:
            recorder.join(timeout=timeout)
        self._recorders.clear()

    def _record_turn(self, user_message: str, answer: str) -> list[str]:
        """Continuous recording met het lichte model. Faalt stil: een
        recording-fout mag het gesprek nooit breken."""
        try:
            parsed = self._llm.chat_json(
                [
                    {"role": "system", "content": RECORDER_PROMPT},
                    {
                        "role": "user",
                        "content": f"GEBRUIKER:\n{user_message}\n\nAGENT:\n{answer}",
                    },
                ],
                model=self._settings.model_light,
            )
            stored: list[str] = []
            for frag in parsed.get("fragments", [])[:2]:
                mf_type = frag.get("type", "observation")
                content = (frag.get("content") or "").strip()
                if mf_type not in MF_TYPES or not content:
                    continue
                mf_id = self._fragments.write(
                    mf_type=mf_type,
                    content=content,
                    context=(frag.get("context") or "").strip(),
                    session_id=self.session_id,
                    event_date=(frag.get("event_date") or "").strip(),
                )
                stored.append(mf_id)
                self._link_entities(mf_id, frag.get("entities") or [])
            return stored
        except Exception as exc:
            print(f"[recorder] beurt niet gelogd: {type(exc).__name__}: {exc}", flush=True)
            return []

    def _link_entities(self, mf_id: str, entities: list[dict[str, Any]]) -> None:
        """Personen/projecten/bedrijven als Entity-nodes met MENTIONS-edges —
        het brein groeit relaties, het hologram laat ze zien. Faalt stil."""
        for ent in entities[:5]:
            name = (ent.get("name") or "").strip()
            etype = ent.get("etype", "person")
            if len(name) < 2 or etype not in {"person", "project", "company"}:
                continue
            try:
                self._brain.run(
                    """
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET e.etype = $etype, e.created = datetime()
                    SET e.last_seen = datetime()
                    WITH e
                    MATCH (mf:MemoryFragment {id: $mf_id})
                    MERGE (mf)-[:MENTIONS]->(e)
                    """,
                    name=name, etype=etype, mf_id=mf_id,
                )
            except Exception:
                pass

    def transcript(self) -> list[dict[str, Any]]:
        return list(self._messages)
