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
