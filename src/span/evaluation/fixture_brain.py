"""A7 — eval-set: in-process fixture-brain + integratie-fakes.

Geen Neo4j nodig: Community kent maar één database, dus een wegwerp-test-db op
prod kan niet — daarom een dubbelganger die precies de queries beantwoordt die
SpanAgent.begin()/turn() nodig hebben, plus per eval-item "gearmde" geheugen-
fixtures via vector_search. Herbruikbaar in pytest én in de handmatige run.
Prod-integraties (Graph/Asana) worden hier ALTIJD gemockt: lezen levert
fixture-data, versturen faalt hard (de AgentInbox-poort hoort dat te vangen).
"""
from __future__ import annotations

from typing import Any

IDENTITY = {
    "name": "LO",
    "philosophy": "Eerlijk, nuchter, behulpzaam.",
    "origin": "eval-fixture",
    "owner": "Bas",
    "voice": "",
}


class FixtureBrain:
    """Neo4j-dubbelganger: run() herkent de Identity-bootstrap-query op
    substring; alle andere queries (protocollen, quests, skills, config,
    feedback en élke write) geven een lege lijst terug — precies genoeg om
    SpanAgent zonder database te draaien (load_security valt op DEFAULTS
    terug bij lege rows). vector_search serveert de gearmde fixtures."""

    def __init__(self) -> None:
        self.database = "eval-fixture"  # ToolBox leest dit als owner-tag
        self._fragments: list[dict[str, Any]] = []

    def arm(self, fragments: list[dict[str, Any]]) -> None:
        """Zet de geheugen-fixtures voor het huidige eval-item klaar."""
        self._fragments = [dict(f) for f in (fragments or [])]

    # -- BrainDB-interface ------------------------------------------------

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "(i:Identity)" in q:
            return [dict(IDENTITY)]
        return []

    def run_read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        return self.run(query, **params)

    def vector_search(self, index: str, embedding: list[float],
                      k: int = 5) -> list[dict[str, Any]]:
        if index != "mf_embedding":
            return []  # v1 heeft geen formele-kennis-fixtures
        hits: list[dict[str, Any]] = []
        for i, frag in enumerate(self._fragments[:k]):
            node = {
                "id": frag.get("id", f"mf-eval-{i}"),
                "type": frag.get("type", "observation"),
                "content": frag.get("content", ""),
                "context": frag.get("context", ""),
                "created": frag.get("created", "2026-07-12T00:00:00"),
                "event_date": frag.get("event_date", ""),
                "source": frag.get("source", "span"),
                "trust": frag.get("trust", "trusted"),
            }
            # 0.9 aflopend: ruim boven de 0.55-memo-drempel, stabiel geordend
            hits.append({"node": node, "score": 0.9 - i * 0.01})
        return hits

    def close(self) -> None:
        pass


class FakeO365:
    """O365-dubbelganger voor taak-scenario's. Signaturen volgen de aanroepen
    in ToolBox (_tool_o365_calendar -> calendar(days=), _tool_o365_mail_search
    -> search_mail(query=, top=), enz.). Hoog-risico acties horen de fake
    nooit te bereiken: de guard queue't ze in de AgentInbox."""

    def __init__(self, fixtures: dict[str, Any] | None = None) -> None:
        self._fx = fixtures or {}

    def calendar(self, days: int = 1) -> list[dict[str, Any]]:
        return self._fx.get("calendar", [])

    def calendar_search(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        return self._fx.get("calendar", [])

    def search_mail(self, query: str, top: int = 15) -> list[dict[str, Any]]:
        return self._fx.get("mail", [])

    def send_mail(self, **kwargs: Any) -> None:
        raise AssertionError(
            "send_mail aangeroepen in een eval-run — de AgentInbox-poort had "
            "dit moeten onderscheppen (guard kapot: eval MOET rood zijn)")


class FakeAsana:
    """Asana-dubbelganger. create_task/search_tasks/my_tasks volgen de echte
    AsanaClient-signaturen die ToolBox aanroept."""

    def __init__(self, fixtures: dict[str, Any] | None = None) -> None:
        self._fx = fixtures or {}
        self.created: list[dict[str, Any]] = []

    def my_tasks(self, top: int = 20) -> list[dict[str, Any]]:
        return self._fx.get("asana_tasks", [])

    def search_tasks(self, text: str) -> list[dict[str, Any]]:
        return self._fx.get("asana_tasks", [])

    def create_task(self, name: str, notes: str = "", due_on: str = "",
                    project_gid: str = "") -> dict[str, Any]:
        task = {"gid": f"eval-{len(self.created) + 1}", "name": name,
                "notes": notes, "due_on": due_on, "project_gid": project_gid}
        self.created.append(task)
        return task
