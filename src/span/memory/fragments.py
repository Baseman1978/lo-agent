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


# Verval-parameters (alleen actief als decay_mode != "off"). Bewust ZACHT:
# de totale multiplier blijft binnen ~0.83..1.18, zodat cosine-relevantie
# dominant blijft en verval alleen tussen bijna-gelijke scores de tiebreak
# doet. (Eerste, te agressieve parametrisatie gaf op het echte brein 1/5
# overlap — relevante kennis werd weggeduwd; daarom flink ingeperkt.)
_DECAY_HALF_LIFE_DAYS = 120.0         # na 120 dagen ongebruik telt recency nog half
_TYPE_WEIGHT = {
    "soul": 1.08, "decision": 1.06, "anti-pattern": 1.06,
    "reflection": 1.03, "observation": 1.00, "interaction-log": 0.96,
}


class FragmentStore:
    def __init__(self, brain: BrainDB, llm: LLMClient, decay_mode: str = "off"):
        self._brain = brain
        self._llm = llm
        self._decay_mode = decay_mode if decay_mode in {"off", "soft", "log"} else "off"

    def embed(self, text: str) -> list[float]:
        """Eén embedding voor hergebruik over meerdere zoekopdrachten."""
        return self._llm.embed_one(text)

    @staticmethod
    def _age_days(node: dict[str, Any]) -> float:
        """Dagen sinds het fragment voor het laatst 'warm' was (last_accessed,
        anders created). Robuust tegen neo4j DateTime én strings; bij twijfel 0
        (= vers, geen straf)."""
        from datetime import datetime, timezone
        raw = node.get("last_accessed") or node.get("created")
        if raw is None:
            return 0.0
        try:
            dt = raw.to_native() if hasattr(raw, "to_native") else datetime.fromisoformat(str(raw))
        except (ValueError, AttributeError):
            return 0.0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400.0)

    @classmethod
    def _decay_factor(cls, node: dict[str, Any]) -> float:
        """Zachte multiplier op de cosine-score. Spread is klein gehouden
        (~0.83..1.18) zodat cosine-relevantie dominant blijft: verval stuurt
        alleen bij tussen bijna-gelijke scores, het filtert nooit."""
        recency = 0.5 ** (cls._age_days(node) / _DECAY_HALF_LIFE_DAYS)  # 1.0 vers .. 0 oud
        recency_f = 0.92 + 0.08 * recency          # 0.92 (oud) .. 1.00 (vers)
        freq_boost = 1.0 + 0.008 * min(int(node.get("access_count") or 0), 10)  # max +8%
        type_weight = _TYPE_WEIGHT.get(node.get("type"), 1.0)  # 0.96 .. 1.08
        return recency_f * freq_boost * type_weight

    def write(
        self,
        *,
        mf_type: str,
        content: str,
        session_id: str,
        context: str = "",
        source: str = "span",
        event_date: str = "",
        scope: str = "algemeen",
    ) -> str:
        if mf_type not in MF_TYPES:
            raise ValueError(f"Onbekend MF-type '{mf_type}'. Kies uit: {sorted(MF_TYPES)}")
        content = content.strip()
        if not content:
            raise ValueError("Leeg MemoryFragment wordt niet opgeslagen.")
        # F3.4 scope-tag: scheidt privé van Lomans-werk in het brein
        scope = scope if scope in {"algemeen", "werk", "prive"} else "algemeen"

        mf_id = new_mf_id(mf_type)
        embedding = self._llm.embed_one(f"{mf_type}: {content}\n{context}".strip())
        self._brain.run(
            """
            MATCH (s:Session {id: $session_id})
            CREATE (mf:MemoryFragment {
              id: $id, type: $type, content: $content, context: $context,
              source: $source, created: datetime(), embedding: $embedding,
              event_date: $event_date, scope: $scope
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
            scope=scope,
        )
        return mf_id

    def search(self, query: str, k: int = 5,
               embedding: list[float] | None = None) -> list[dict[str, Any]]:
        """Vector search over alle fragmenten; geeft id, type, content, score.

        decay_mode 'off' = pure cosine (default). 'soft'/'log' = zacht
        herordenen op recency/frequentie/type uit een ruimere kandidatenpool;
        'log' print bovendien welke fragmenten t.o.v. pure cosine verschuiven."""
        embedding = embedding or self._llm.embed_one(query)

        def _entry(node: dict[str, Any], score: float) -> dict[str, Any]:
            return {
                "id": node.get("id"), "type": node.get("type"),
                "content": node.get("content"), "context": node.get("context", ""),
                "created": str(node.get("created", "")),
                "event_date": node.get("event_date") or "",
                "score": round(score, 4),
            }

        if self._decay_mode == "off":
            rows = self._brain.vector_search("mf_embedding", embedding, k=k + 4)
            results = []
            for row in rows:
                node = row["node"]
                if node.get("superseded"):
                    continue  # consolidatie heeft dit fragment afgeschreven
                if len(results) >= k:
                    break
                results.append(_entry(node, row["score"]))
        else:
            # ruimere pool zodat herordenen daadwerkelijk iets kan verschuiven
            pool = max(k * 3, k + 10)
            rows = self._brain.vector_search("mf_embedding", embedding, k=pool)
            cands = [r for r in rows if not r["node"].get("superseded")]
            ranked = sorted(
                cands,
                key=lambda r: r["score"] * self._decay_factor(r["node"]),
                reverse=True,
            )
            if self._decay_mode == "log":
                cosine_order = [r["node"].get("id") for r in cands[:k]]
                decay_order = [r["node"].get("id") for r in ranked[:k]]
                if cosine_order != decay_order:
                    print(f"[decay] top-{k} verschoven\n  cosine: {cosine_order}"
                          f"\n  decay : {decay_order}", flush=True)
            results = [_entry(r["node"], r["score"]) for r in ranked[:k]]

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
