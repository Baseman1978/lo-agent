"""Runners voor achtergrondtaken — gescheiden van de server-bootstrap (app.py).

Een sub-agent voert een doel uit terwijl de chat vrij blijft, met hetzelfde
brein + dezelfde veiligheidspoort. Sub-agents krijgen GEEN TaskManager mee
(tasks niet gezet) -> ze kunnen zelf geen taken/teams spawnen (geen recursie).

make_runners(state) levert (task_runner, team_runner) voor de TaskManager;
beide closen over de gedeelde server-state (settings, llm, integraties, inbox).
"""

from __future__ import annotations

from typing import Any, Callable

# voortgangslabels per tool (mensvriendelijk in het Taken-paneel)
TASK_LABELS = {
    "o365_archive_folder": "📥 mailmap archiveren…", "o365_mail_search": "🔎 mail zoeken…",
    "brain_search": "🧠 geheugen doorzoeken…", "web_search": "🌐 web zoeken…",
    "web_read": "🌐 pagina lezen…", "o365_doc_generate": "📄 document maken…",
    "o365_file_read": "📄 bestand lezen…", "remember": "🧠 onthouden…",
    "o365_enrich_archive": "🧠 archief verrijken…",
    "o365_unanswered_sent": "📨 open antwoorden zoeken…",
}


def make_runners(state: dict[str, Any]) -> tuple[Callable[..., str], Callable[..., str]]:
    from span.orchestrator.agent import SpanAgent
    from span.memory.bootstrap import start_session

    def _agent(ctx, **extra):
        return SpanAgent(
            state["settings"], ctx.get("brain") or state["brain"], state["llm"],
            state.get("work"), o365=ctx.get("o365", state.get("o365")),
            asana=state.get("asana"), inbox=state["inbox"], autonomy=state["autonomy"],
            disabled_tools=state.get("disabled_tools"), fireflies=state.get("fireflies"),
            telegram=state.get("telegram"),
            tool_retrieval=state.get("tool_retrieval", True),
            tool_retrieval_k=state.get("tool_retrieval_k", 24),
            mcp=state.get("mcp"), shared_brain=ctx.get("shared"), **extra)

    def task_runner(task, set_progress, should_cancel, ctx):
        tbrain = ctx.get("brain") or state["brain"]
        # zodra de sub-agent zelf een % meldt (accuraat bij batch), neemt die het
        # over van de stapgebaseerde basisschatting
        agent_reported = [False]

        def _agent_progress(percent, label=""):
            agent_reported[0] = True
            set_progress(label, percent)

        agent = _agent(ctx, progress_cb=_agent_progress)
        agent.begin(start_session(tbrain), task["goal"])

        step = [0]

        def on_tool(name, phase):
            if phase != "start":
                return
            step[0] += 1
            label = TASK_LABELS.get(name, "⚙ " + name + "…")
            if agent_reported[0]:
                set_progress(label)  # alleen het label; de agent stuurt het %
            else:  # vloeiende basisschatting, nooit 100 vóór klaar
                set_progress(label, min(95, round(100 * (1 - 0.8 ** step[0]))))

        goal = (task["goal"] + "\n\n[Achtergrondtaak: werk je voortgang bij met "
                "report_progress(percent, label) terwijl je werkt, vooral bij batch-stappen.]")
        result = agent.turn(goal, on_tool=on_tool, should_cancel=should_cancel, max_steps=30)
        try:
            agent.flush_recording()
        except Exception:
            pass
        return result

    def team_runner(task, set_progress, should_cancel, ctx):
        """Coördinator: splitst het doel in 2-4 deeltaken, draait die PARALLEL als
        sub-agents en voegt de resultaten samen (fan-out -> parallel -> reduce)."""
        import concurrent.futures
        import json as _json
        import re as _re
        import threading as _threading

        tbrain = ctx.get("brain") or state["brain"]
        llm = state["llm"]
        model = state["settings"].model_main

        def _json_obj(text):
            t = (text or "").strip()
            t = _re.sub(r"^```(?:json)?|```$", "", t, flags=_re.MULTILINE).strip()
            try:
                return _json.loads(t)
            except Exception:
                m = _re.search(r'\{[\s\S]*"subtasks"[\s\S]*\}', t)
                if m:
                    try:
                        return _json.loads(m.group(0))
                    except Exception:
                        return {}
            return {}

        # 1) DECOMPOSE
        set_progress("plan maken…", 6)
        plan_sys = (
            "Je bent een coördinator. Splits het doel op in 2 tot 4 ONAFHANKELIJKE "
            "deeltaken die parallel kunnen draaien, elk met een rol en een concreet, "
            "zelfstandig uitvoerbaar doel (geen deeltaak die op een andere wacht). "
            "Antwoord met UITSLUITEND geldige JSON, zonder uitleg eromheen, exact in de vorm: "
            '{"subtasks":[{"role":"korte rol","goal":"concreet doel"}]}')
        try:
            msg = llm.chat([{"role": "system", "content": plan_sys},
                            {"role": "user", "content": "Doel:\n" + task["goal"]}], model=model)
            subtasks = (_json_obj(msg.content or "").get("subtasks") or [])
        except Exception:
            subtasks = []
        subtasks = [s for s in subtasks if s.get("goal")][:4]
        if not subtasks:  # fallback: één agent doet alles
            subtasks = [{"role": "uitvoerder", "goal": task["goal"]}]
        n = len(subtasks)
        set_progress(f"{n} deeltaken parallel…", 12)

        # 2) PARALLELLE SUB-AGENTS
        results: list = [None] * n
        done = [0]
        lock = _threading.Lock()

        def run_sub(i, st):
            if should_cancel():
                return
            agent = _agent(ctx)
            agent.begin(start_session(tbrain), st["goal"])
            ans = agent.turn(f"[Deeltaak — rol: {st.get('role', 'uitvoerder')}] {st['goal']}",
                             should_cancel=should_cancel, max_steps=20)
            try:
                agent.flush_recording()
            except Exception:
                pass
            results[i] = {"role": st.get("role", ""), "result": ans}
            with lock:
                done[0] += 1
                set_progress(f"deeltaak {done[0]}/{n} klaar", 12 + round(68 * done[0] / n))

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, n)) as ex:
            list(ex.map(lambda p: run_sub(*p), list(enumerate(subtasks))))
        if should_cancel():
            return "(geannuleerd)"

        # 3) SYNTHESE
        set_progress("samenvoegen…", 88)
        parts = "\n\n".join(f"## {r['role']}\n{r['result']}" for r in results if r)
        syn_sys = ("Je bent de coördinator. Voeg de deelresultaten samen tot één helder, "
                   "samenhangend eindantwoord op het oorspronkelijke doel.")
        try:
            msg = llm.chat([{"role": "system", "content": syn_sys},
                            {"role": "user", "content": "Doel: " + task["goal"]
                             + "\n\nDeelresultaten:\n" + parts}], model=model)
            final = (msg.content or "").strip()
        except Exception as exc:
            final = parts + f"\n\n(samenvoegen mislukt: {exc})"
        return final or parts

    return task_runner, team_runner
