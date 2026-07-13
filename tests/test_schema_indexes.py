# tests/test_schema_indexes.py
"""A4 — geheugen-onderhoud: range-indexen + Entity-constraint in init_schema."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.db.schema as schema

EXPECTED_RANGE_NAMES = {
    "mf_created", "mf_type", "session_started", "quest_status",
    "quest_created", "insight_created", "mistake_created", "inboxitem_item_id",
}


def _settings():
    s = MagicMock()
    s.embed_dims = 8
    s.embed_model = "test-embed"
    return s


def test_range_indexes_zijn_compleet_en_idempotent():
    names = {name for name, _ in schema.RANGE_INDEXES}
    assert names == EXPECTED_RANGE_NAMES
    for name, cypher in schema.RANGE_INDEXES:
        assert "IF NOT EXISTS" in cypher  # draait bij ELKE serverstart -> idempotent
        assert f"CREATE INDEX {name} " in cypher


def test_init_schema_maakt_range_indexen_aan():
    brain = MagicMock()
    brain.run.return_value = []  # ook de drift-guard ziet dan 'geen config' -> geen raise

    log = schema.init_schema(brain, _settings())

    executed = [c.args[0] for c in brain.run.call_args_list]
    for _name, cypher in schema.RANGE_INDEXES:
        assert cypher in executed
    assert any("range-indexen" in regel for regel in log)


def test_init_schema_zet_entity_constraint_na_dedup(monkeypatch):
    dedup_calls: list = []

    def nep_dedup(brain):
        dedup_calls.append(brain)
        return 0

    # init_schema importeert dedup_entities lazy -> patchen op de bronmodule werkt
    monkeypatch.setattr("span.jarvis.daily.dedup_entities", nep_dedup)
    brain = MagicMock()
    brain.run.return_value = []  # SHOW CONSTRAINTS: entity_name bestaat nog niet

    log = schema.init_schema(brain, _settings())

    executed = [c.args[0] for c in brain.run.call_args_list]
    assert schema.ENTITY_NAME_CONSTRAINT in executed
    assert len(dedup_calls) == 1  # dedup draait VOOR de constraint
    assert any("entity_name" in regel for regel in log)


def test_entity_constraint_faalt_zacht(monkeypatch):
    def kapotte_dedup(brain):
        raise RuntimeError("dubbele Entity-namen")

    monkeypatch.setattr("span.jarvis.daily.dedup_entities", kapotte_dedup)
    brain = MagicMock()
    brain.run.return_value = []

    log = schema.init_schema(brain, _settings())  # geen exception = fail-soft werkt

    executed = [c.args[0] for c in brain.run.call_args_list]
    assert schema.ENTITY_NAME_CONSTRAINT not in executed
    assert any("overgeslagen" in regel for regel in log)
