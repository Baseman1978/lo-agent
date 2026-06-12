"""Productiedata: strikt alleen-lezen Neo4j client.

Security by design (zie functioneel ontwerp): de agent leest beide databases,
schrijft alleen in het eigen brein. Geen write op productie — ooit.
Dubbele borging: cypher-guard hier + READ access mode op de sessie.
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import GraphDatabase, Driver, READ_ACCESS

from span.config import WorkDbConfig

# Cypher-clauses die de graph muteren. Bewust breed: liever een valse
# weigering dan een schrijfactie op productiedata.
_WRITE_PATTERN = re.compile(
    r"(?i)\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|"
    r"CALL\s+\{.*\}\s+IN\s+TRANSACTIONS|apoc\.(create|merge|refactor|periodic))\b"
)


class ReadOnlyViolation(Exception):
    """Query probeerde productiedata te muteren."""


def assert_read_only(query: str) -> None:
    match = _WRITE_PATTERN.search(query)
    if match:
        raise ReadOnlyViolation(
            f"Geweigerd: '{match.group(0)}' is een schrijfoperatie. "
            "Productiedata is alleen-lezen; schrijf alleen in het brein."
        )


class WorkDB:
    """Alleen-lezen toegang tot een productie-graph."""

    def __init__(self, config: WorkDbConfig):
        self._config = config
        self._driver: Driver = GraphDatabase.driver(
            config.uri,
            auth=(config.user, config.password),
            notifications_min_severity="OFF",
        )
        self.database = config.database

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        assert_read_only(query)
        with self._driver.session(
            database=self.database, default_access_mode=READ_ACCESS
        ) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]
