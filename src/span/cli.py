"""Span CLI — init, chat, status, memory, reflect."""

from __future__ import annotations

import sys
import time

if sys.platform == "win32":
    import msvcrt

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from span import AGENT_NAME, __version__
from span.config import Settings, load_settings
from span.db.brain import BrainDB
from span.db.schema import init_schema
from span.db.work import WorkDB
from span.evaluation.reflect import reflect_session
from span.integrations import build_integrations
from span.llm.client import LLMClient
from span.memory.bootstrap import start_session
from span.memory.fragments import FragmentStore
from span.orchestrator.agent import SpanAgent

# Windows-consoles staan vaak op cp1252; emoji's in antwoorden crashen dan.
for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help=f"{AGENT_NAME} v{__version__} — een AI die zichzelf onthoudt.")
console = Console()


def _read_message() -> str:
    """Lees één bericht. Meerregelige plak wordt gebundeld: regels die direct
    na elkaar binnenkomen (paste-buffer) horen bij hetzelfde bericht.
    /paste start expliciete meerregel-modus tot /done."""
    first = console.input("[bold cyan]jij[/bold cyan] > ").rstrip()

    if first.strip() == "/paste":
        console.print("[dim]Plak-modus: sluit af met een regel met alleen /done[/dim]")
        lines: list[str] = []
        while True:
            line = input()
            if line.strip() == "/done":
                break
            lines.append(line)
        return "\n".join(lines).strip()

    lines = [first]
    if sys.platform == "win32" and sys.stdin.isatty():
        time.sleep(0.15)  # geef de paste-buffer tijd om aan te komen
        while msvcrt.kbhit():
            try:
                lines.append(input())
            except EOFError:
                break
            time.sleep(0.02)
    return "\n".join(lines).strip()


def _connect() -> tuple[Settings, BrainDB, LLMClient, WorkDB | None]:
    try:
        settings = load_settings()
    except RuntimeError as exc:
        console.print(f"[red]Config-fout:[/red] {exc}")
        raise typer.Exit(1)
    brain = BrainDB(settings)
    try:
        brain.verify()
    except Exception as exc:
        console.print(
            f"[red]Geen verbinding met Neo4j op {settings.neo4j_uri}:[/red] {exc}\n"
            "Start de DBMS in Neo4j Desktop en controleer NEO4J_PASSWORD in .env."
        )
        raise typer.Exit(1)
    work = None
    if settings.work:
        work = WorkDB(settings.work)
        try:
            work.verify()
        except Exception as exc:
            console.print(f"[yellow]Productiedata niet bereikbaar, ga door zonder:[/yellow] {exc}")
            work = None
    return settings, brain, LLMClient(settings), work


@app.command()
def init() -> None:
    """Maak database, schema, identity en kernprotocollen aan (idempotent)."""
    settings, brain, _, _ = _connect()
    try:
        for line in init_schema(brain, settings):
            console.print(f"[green]+[/green] {line}")
        console.print(f"\n[bold]{AGENT_NAME} is wakker.[/bold] Start een gesprek met: span chat")
    finally:
        brain.close()


@app.command()
def chat() -> None:
    """Interactieve sessie. /end = evalueren + afsluiten, /mem <vraag> = geheugen zoeken."""
    settings, brain, llm, work = _connect()
    o365, asana = build_integrations(settings)
    # zelfde vangrail als de web-UI: gevoelige acties (mail, agenda, Asana)
    # gaan via de Agent Inbox en wachten op expliciet akkoord in de terminal
    from span.jarvis.ambient import AgentInbox
    inbox = AgentInbox()
    agent = SpanAgent(settings, brain, llm, work, o365=o365, asana=asana, inbox=inbox)
    try:
        console.print(
            Panel.fit(
                f"[bold]{AGENT_NAME}[/bold] — sessie start. "
                "Commando's: /end (evalueren + stoppen), /mem <vraag>, "
                "/paste (lang document plakken, afsluiten met /done), "
                "/quit (stoppen zonder evaluatie)",
                border_style="cyan",
            )
        )
        first = _read_message()
        if not first or first in {"/quit", "/end"}:
            console.print("Niets te doen.")
            raise typer.Exit(0)

        session_id = start_session(brain)
        ctx = agent.begin(session_id, first_message=first)
        console.print(
            f"[dim]bootstrap: {len(ctx.protocols)} protocollen, {len(ctx.quests)} quests, "
            f"{len(ctx.relevant)} relevante herinneringen · {session_id}[/dim]\n"
        )

        message: str | None = first
        while True:
            if message is None:
                message = _read_message()
            if not message:
                message = None
                continue
            if message == "/quit":
                console.print("[dim]Gestopt zonder evaluatie — fragmenten blijven bewaard.[/dim]")
                break
            if message == "/end":
                agent.flush_recording()
                _run_reflect(settings, brain, llm, agent.fragments, session_id)
                break
            if message.startswith("/mem "):
                _print_memories(agent.fragments, message[5:].strip())
                message = None
                continue

            console.print(f"\n[bold magenta]{AGENT_NAME.lower()}[/bold magenta] >")

            def stream_chunk(text: str) -> None:
                sys.stdout.write(text)
                sys.stdout.flush()

            agent.turn(message, on_text=stream_chunk)
            console.print("\n")
            _handle_inbox(inbox, o365, asana, llm, settings)
            message = None
    finally:
        brain.close()
        if work:
            work.close()


@app.command()
def status() -> None:
    """Toon de staat van het brein: aantallen per knoop-type."""
    settings, brain, llm, work = _connect()
    try:
        table = Table(title=f"{AGENT_NAME} — brein ({settings.brain_db})")
        table.add_column("Type")
        table.add_column("Aantal", justify="right")
        for label in ["MemoryFragment", "Insight", "Mistake", "Idea", "Quest", "Skill", "Protocol", "Session"]:
            rows = brain.run(f"MATCH (n:{label}) RETURN count(n) AS n")
            table.add_row(label, str(rows[0]["n"]))
        console.print(table)

        fragments = FragmentStore(brain, llm)
        per_type = fragments.count()
        if per_type:
            console.print("MF per type: " + ", ".join(f"{k}={v}" for k, v in per_type.items()))
        console.print(
            f"Productiedata: {'gekoppeld (alleen-lezen)' if work else 'niet gekoppeld'}"
        )
    finally:
        brain.close()
        if work:
            work.close()


@app.command()
def memory(query: str, k: int = typer.Option(8, help="Aantal resultaten")) -> None:
    """Zoek semantisch in het geheugen."""
    _, brain, llm, _ = _connect()
    try:
        _print_memories(FragmentStore(brain, llm), query, k)
    finally:
        brain.close()


@app.command()
def reflect(session_id: str) -> None:
    """Draai de evaluatie handmatig voor een sessie (bv. na /quit)."""
    settings, brain, llm, _ = _connect()
    try:
        fragments = FragmentStore(brain, llm)
        _run_reflect(settings, brain, llm, fragments, session_id)
    finally:
        brain.close()


def _handle_inbox(inbox, o365, asana, llm, settings) -> None:
    """Open actie-items uit de Agent Inbox interactief afhandelen — de
    CLI-tegenhanger van de goedkeuringsknoppen in de HUD."""
    from span.jarvis.ambient import execute_approval
    for item in inbox.snapshot():
        if item["status"] != "open" or item["kind"] != "action":
            continue
        console.print(Panel(
            f"{item['title']}\n[dim]{item['detail']}[/dim]",
            title=f"Wacht op akkoord · {item['action']}", border_style="yellow",
        ))
        answer = console.input("[bold yellow]uitvoeren? (j/n)[/bold yellow] > ").strip().lower()
        if answer in {"j", "ja", "y", "yes"}:
            try:
                execute_approval(item, o365, llm=llm,
                                 light_model=settings.model_light, asana=asana)
                inbox.resolve(item["id"], "approved")
                console.print("[green]Uitgevoerd.[/green]")
            except Exception as exc:
                console.print(f"[red]Mislukt:[/red] {exc}")
        else:
            inbox.resolve(item["id"], "rejected")
            console.print("[dim]Afgewezen — niets verstuurd.[/dim]")


def _run_reflect(settings, brain, llm, fragments, session_id: str) -> None:
    with console.status("[dim]evalueert sessie — de cirkel rond…[/dim]"):
        result = reflect_session(settings, brain, llm, fragments, session_id)
    console.print(Panel(result["summary"], title="Sessie-samenvatting", border_style="green"))
    for kind, items in result["written"].items():
        console.print(f"[green]+[/green] {kind}: {', '.join(items)}")


def _print_memories(fragments: FragmentStore, query: str, k: int = 8) -> None:
    if not query:
        console.print("[yellow]Lege zoekvraag.[/yellow]")
        return
    results = fragments.search(query, k=k)
    if not results:
        console.print("[dim]Geen herinneringen gevonden.[/dim]")
        return
    for r in results:
        console.print(
            f"[dim]{r['score']:.2f}[/dim] [cyan]{r['id']}[/cyan] ({r['type']}) {r['content']}"
        )


@app.command(name="o365-login")
def o365_login() -> None:
    """Log eenmalig in bij Microsoft 365 (device code flow)."""
    try:
        settings = load_settings()
    except RuntimeError as exc:
        console.print(f"[red]Config-fout:[/red] {exc}")
        raise typer.Exit(1)
    o365, _ = build_integrations(settings)
    if o365 is None:
        console.print("[red]MS_CLIENT_ID ontbreekt in .env — registreer een app in Entra ID.[/red]")
        raise typer.Exit(1)
    if o365.is_authenticated():
        console.print(f"[green]Al ingelogd als {o365.account_name()}.[/green]")
        return
    flow = o365.start_device_flow()
    console.print(Panel.fit(flow.get("message", ""), border_style="cyan", title="Microsoft 365 login"))
    account = o365.complete_device_flow(flow)
    console.print(f"[green]Ingelogd als {account}.[/green]")


@app.command(name="o365-logout")
def o365_logout() -> None:
    """Ontkoppel het gekoppelde Microsoft 365-account."""
    settings = load_settings()
    o365, _ = build_integrations(settings)
    if o365 is None or not o365.is_authenticated():
        console.print("[dim]Geen O365-account gekoppeld.[/dim]")
        return
    name = o365.logout()
    console.print(f"[green]Ontkoppeld: {name}.[/green] Opnieuw koppelen: span o365-login")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 = bereikbaar van buiten (zet dan SPAN_AUTH_TOKEN)"),
    port: int = typer.Option(8472, help="Poort voor de web-UI"),
) -> None:
    """Start de JARVIS web-UI (FastAPI + WebSocket streaming)."""
    import uvicorn

    console.print(f"[bold]{AGENT_NAME}[/bold] web-UI op http://{host}:{port}")
    uvicorn.run("span.server.app:app", host=host, port=port, log_level="warning")


def main() -> None:
    sys.exit(app())


if __name__ == "__main__":
    main()
