"""Brein-database: Neo4j read/write client voor de span-brain graph."""

from __future__ import annotations

import os
import time
from typing import Any

from neo4j import GraphDatabase, Driver, READ_ACCESS

from span import telemetry
from span.config import Settings


def _tel_enabled() -> bool:
    """SPAN_BRAIN_TELEMETRY (default aan): klep op het A4-meetpunt brain-latency.
    Apart van SPAN_TELEMETRY omdat brain-records volumineus zijn (elke query
    is één JSONL-regel); 'off/0/false/no' zet alleen dít segment uit."""
    val = os.environ.get("SPAN_BRAIN_TELEMETRY", "on").strip().lower()
    return val not in {"off", "0", "false", "no", ""}


class BrainDB:
    """Dunne wrapper rond de Neo4j driver, gericht op het brein."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            notifications_min_severity="OFF",  # geen warnings over lege property keys
        )
        self.database = settings.brain_db

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def _run_raw(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self.database) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def _read_raw(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(
            database=self.database, default_access_mode=READ_ACCESS
        ) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def _timed(self, op: str, fn: Any, query: str,
               **params: Any) -> list[dict[str, Any]]:
        """A4-meetpunt: brain-latency per operatie naar de telemetrie-JSONL.
        telemetry.record is zelf al best-effort; dit pad kan een query dus
        nooit breken — bij een query-fout meten we de duur mét outcome=error
        en gooien we de fout gewoon door."""
        if not _tel_enabled():
            return fn(query, **params)
        t0 = time.perf_counter()
        try:
            out = fn(query, **params)
        except Exception:
            telemetry.record("brain", (time.perf_counter() - t0) * 1000.0,
                             {"op": op, "outcome": "error"})
            raise
        telemetry.record("brain", (time.perf_counter() - t0) * 1000.0, {"op": op})
        return out

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        return self._timed("run", self._run_raw, query, **params)

    def run_read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Strikt lezen: de database zelf weigert schrijfacties (READ_ACCESS),
        onafhankelijk van wat een regex-check ervan vindt."""
        return self._timed("read", self._read_raw, query, **params)

    def run_system(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Voor CREATE DATABASE e.d. — draait tegen de system database."""
        with self._driver.session(database="system") as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def ensure_database(self) -> None:
        """Maak de brein-database aan als die nog niet bestaat.

        Community-bestendig: bestaat de database al (bv. als hernoemde default
        via initial.dbms.default_database), dan is er niets te doen — CREATE
        DATABASE zou daar falen omdat Community dat commando niet kent.
        Alleen als hij ontbreekt proberen we CREATE (enterprise/dev)."""
        if self.database == "neo4j":
            return
        try:
            rows = self.run_system(
                "SHOW DATABASES YIELD name WHERE name = $db RETURN name",
                db=self.database)
            if rows:
                return  # bestaat al — ook op community prima
        except Exception:
            pass  # SHOW niet beschikbaar? -> val terug op CREATE-poging
        try:
            self.run_system(f"CREATE DATABASE `{self.database}` IF NOT EXISTS WAIT")
        except Exception as exc:  # community zonder bestaande db, of geen rechten
            raise RuntimeError(
                f"Kan database '{self.database}' niet aanmaken ({exc}). "
                "Op community edition: laad de database als hernoemde default "
                "(initial.dbms.default_database) of zet BRAIN_DB=neo4j."
            ) from exc

    def vector_search(
        self, index: str, embedding: list[float], k: int = 5
    ) -> list[dict[str, Any]]:
        return self._timed(
            "vector", self._run_raw,
            """
            CALL db.index.vector.queryNodes($index, $k, $embedding)
            YIELD node, score
            RETURN node, score
            """,
            index=index,
            k=k,
            embedding=embedding,
        )
