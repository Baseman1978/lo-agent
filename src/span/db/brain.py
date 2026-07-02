"""Brein-database: Neo4j read/write client voor de span-brain graph."""

from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase, Driver, READ_ACCESS

from span.config import Settings


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

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self.database) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def run_read(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Strikt lezen: de database zelf weigert schrijfacties (READ_ACCESS),
        onafhankelijk van wat een regex-check ervan vindt."""
        with self._driver.session(
            database=self.database, default_access_mode=READ_ACCESS
        ) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

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
        return self.run(
            """
            CALL db.index.vector.queryNodes($index, $k, $embedding)
            YIELD node, score
            RETURN node, score
            """,
            index=index,
            k=k,
            embedding=embedding,
        )
