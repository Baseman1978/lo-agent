"""MemoryFragments — het informele geheugen.

Niet elk waardevol moment past in een quest, protocol of beslissing.
Daarom: continu kleine observaties wegschrijven, type per moment gekozen,
vorm licht. Formeel en informeel samen vormen het langetermijngeheugen.
"""

from __future__ import annotations

import time
from typing import Any

from span.db.brain import BrainDB
from span.llm.client import LLMClient

MF_TYPES = {
    "interaction-log",  # korte momentnotitie
    "decision",         # genomen beslissing, met reden
    "anti-pattern",     # bekende valkuil + workaround
    "reflection",       # terugblik / les
    "observation",      # losse waarneming
    "soul",             # persoonlijkheidsmoment (tone, humor, waarden)
}


def new_mf_id(mf_type: str) -> str:
    """Id-vorm volgt het origineel, plus random suffix: twee recorder-threads
    in dezelfde milliseconde mogen nooit op de unique constraint klappen."""
    from uuid import uuid4
    code = "".join(part[0] for part in mf_type.split("-"))
    return f"mf-{int(time.time() * 1000)}-{code}-{uuid4().hex[:6]}"


class FragmentStore:
    def __init__(self, brain: BrainDB, llm: LLMClient):
        self._brain = brain
        self._llm = llm

    def embed(self, text: str) -> list[float]:
        """Eén embedding voor hergebruik over meerdere zoekopdrachten."""
        return self._llm.embed_one(text)

    def write(
        self,
        *,
        mf_type: str,
        content: str,
        session_id: str,
        context: str = "",
        source: str = "span",
        event_date: str = "",
    ) -> str:
        if mf_type not in MF_TYPES:
            raise ValueError(f"Onbekend MF-type '{mf_type}'. Kies uit: {sorted(MF_TYPES)}")
        content = content.strip()
        if not content:
            raise ValueError("Leeg MemoryFragment wordt niet opgeslagen.")

        mf_id = new_mf_id(mf_type)
        embedding = self._llm.embed_one(f"{mf_type}: {content}\n{context}".strip())
        self._brain.run(
            """
            MATCH (s:Session {id: $session_id})
            CREATE (mf:MemoryFragment {
              id: $id, type: $type, content: $content, context: $context,
              source: $source, created: datetime(), embedding: $embedding,
              event_date: $event_date
            })
            CREATE (mf)-[:FROM_SESSION]->(s)
            """,
            session_id=session_id,
            id=mf_id,
            type=mf_type,
            content=content,
            context=context,
            source=source,
            embedding=embedding,
            event_date=event_date or None,  # bi-temporeel: wanneer gebeurde het
        )
        return mf_id

    def search(self, query: str, k: int = 5,
               embedding: list[float] | None = None) -> list[dict[str, Any]]:
        """Vector search over alle fragmenten; geeft id, type, content, score."""
        embedding = embedding or self._llm.embed_one(query)
        rows = self._brain.vector_search("mf_embedding", embedding, k=k + 4)
        results = []
        for row in rows:
            node = row["node"]
            if node.get("superseded"):
                continue  # consolidatie heeft dit fragment afgeschreven
            if len(results) >= k:
                break
            results.append(
                {
                    "id": node.get("id"),
                    "type": node.get("type"),
                    "content": node.get("content"),
                    "context": node.get("context", ""),
                    "created": str(node.get("created", "")),
                    "event_date": node.get("event_date") or "",
                    "score": round(row["score"], 4),
                }
            )
        if results:  # decay-administratie: gebruik houdt herinneringen warm
            try:
                self._brain.run(
                    "UNWIND $ids AS mf_id MATCH (mf:MemoryFragment {id: mf_id}) "
                    "SET mf.last_accessed = datetime(), "
                    "    mf.access_count = coalesce(mf.access_count, 0) + 1",
                    ids=[r["id"] for r in results],
                )
            except Exception:
                pass
        return results

    FORMAL_INDEXES = [
        ("insight_embedding", "Insight"),
        ("mistake_embedding", "Mistake"),
        ("idea_embedding", "Idea"),
    ]

    def search_formal(self, query: str, k: int = 3,
                      embedding: list[float] | None = None) -> list[dict[str, Any]]:
        """Vector search over formele kennis (Insight/Mistake/Idea) — de
        leeskant van de evaluatiecirkel. Oude nodes zonder embedding worden
        simpelweg niet gevonden; nieuwe wel."""
        embedding = embedding or self._llm.embed_one(query)
        results: list[dict[str, Any]] = []
        for index_name, label in self.FORMAL_INDEXES:
            try:
                rows = self._brain.vector_search(index_name, embedding, k=k)
            except Exception:
                continue  # index bestaat (nog) niet — geen reden om te breken
            for row in rows:
                node = row["node"]
                entry = {
                    "id": node.get("id"),
                    "label": label,
                    "content": node.get("content"),
                    "created": str(node.get("created", "")),
                    "score": round(row["score"], 4),
                }
                if node.get("lesson"):
                    entry["lesson"] = node["lesson"]
                results.append(entry)
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]

    def recent(self, k: int = 10, mf_type: str | None = None) -> list[dict[str, Any]]:
        type_filter = "AND mf.type = $type" if mf_type else ""
        return self._brain.run(
            f"""
            MATCH (mf:MemoryFragment)
            WHERE mf.superseded IS NULL {type_filter}
            RETURN mf.id AS id, mf.type AS type, mf.content AS content,
                   toString(mf.created) AS created
            ORDER BY mf.created DESC LIMIT $k
            """,
            k=k,
            type=mf_type,
        )

    def session_fragments(self, session_id: str) -> list[dict[str, Any]]:
        return self._brain.run(
            """
            MATCH (mf:MemoryFragment)-[:FROM_SESSION]->(:Session {id: $session_id})
            RETURN mf.id AS id, mf.type AS type, mf.content AS content,
                   mf.context AS context, toString(mf.created) AS created
            ORDER BY mf.created
            """,
            session_id=session_id,
        )

    def count(self) -> dict[str, int]:
        rows = self._brain.run(
            """
            MATCH (mf:MemoryFragment)
            RETURN mf.type AS type, count(*) AS n ORDER BY n DESC
            """
        )
        return {row["type"]: row["n"] for row in rows}
