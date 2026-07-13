"""A7 — eval-set v1: fixture-brain, dataschema, scoring, runner, rapport."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_fixture_brain_bootstrap_zonder_neo4j():
    """load_bootstrap draait volledig op de FixtureBrain — geen database nodig."""
    from span.evaluation.fixture_brain import FixtureBrain
    from span.memory.bootstrap import load_bootstrap

    brain = FixtureBrain()
    frag = MagicMock()
    frag.recent.return_value = []
    ctx = load_bootstrap(brain, frag)
    assert ctx.identity["name"] == "LO"
    assert ctx.identity["owner"] == "Bas"
    assert ctx.protocols == []
    assert ctx.quests == []


def test_fixture_brain_vector_search_serveert_gearmde_fragmenten():
    from span.evaluation.fixture_brain import FixtureBrain

    brain = FixtureBrain()
    assert brain.vector_search("mf_embedding", [0.0], k=5) == []
    brain.arm([{"id": "mf-eval-1", "type": "decision", "content": "fq_codel aan"}])
    hits = brain.vector_search("mf_embedding", [0.0], k=5)
    assert hits[0]["node"]["id"] == "mf-eval-1"
    assert hits[0]["node"]["content"] == "fq_codel aan"
    assert hits[0]["score"] > 0.55  # boven de RAG-memo-drempel van turn()
    # formele indexen blijven leeg in v1 (geen Insight/Mistake-fixtures)
    assert brain.vector_search("insight_embedding", [0.0], k=5) == []


def test_fake_o365_weigert_echt_versturen():
    """De guard hoort mail_send te queuen; bereikt een send tóch de fake, dan
    moet de eval hard rood worden — dat betekent dat de poort kapot is."""
    from span.evaluation.fixture_brain import FakeO365

    fake = FakeO365({"calendar": [{"subject": "Weekstart"}]})
    assert fake.calendar(days=1) == [{"subject": "Weekstart"}]
    with pytest.raises(AssertionError):
        fake.send_mail(to=["x@extern.nl"], subject="s", body="b")
