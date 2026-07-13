# tests/test_bootstrap_limits.py
"""A4 — quest-limiet: quests waren de enige hard groeiende onbegrensde categorie."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.memory.bootstrap as bootstrap


def test_quests_query_heeft_limit_en_recentste_eerst(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def run(query, **params):
        if "MATCH (i:Identity)" in query:
            return [{"name": "LO", "philosophy": "p", "origin": "o",
                     "owner": "Bas Spaan", "voice": None}]
        captured.append((query, params))
        return []

    brain = MagicMock()
    brain.run.side_effect = run
    brain.vector_search.return_value = []
    frag = MagicMock()
    frag.recent.return_value = []
    frag.search.return_value = []
    # feedback_summary wordt lazy geïmporteerd -> patchen op de bronmodule
    monkeypatch.setattr("span.jarvis.feedback.feedback_summary", lambda b: [])

    ctx = bootstrap.load_bootstrap(brain, frag, first_message=None)

    assert ctx.quests == []
    quest_queries = [(q, p) for q, p in captured if "MATCH (q:Quest)" in q]
    assert len(quest_queries) == 1
    query, params = quest_queries[0]
    assert "LIMIT $quest_limit" in query
    assert params["quest_limit"] == bootstrap.QUEST_LIMIT
    assert "ORDER BY coalesce(q.updated, q.created) DESC" in query


def test_render_bootstrap_capt_quest_steps():
    ident = {"name": "LO", "owner": "Bas Spaan", "philosophy": "p",
             "origin": "o", "voice": None}
    steps = [{"order": i, "body": f"stap {i}", "status": "open"}
             for i in range(1, 15)]  # 14 stappen
    ctx = bootstrap.BootstrapContext(
        identity=ident, protocols=[],
        quests=[{"id": "quest-1", "title": "Grote quest", "status": "open",
                 "steps": steps}],
        decisions=[], anti_patterns=[], soul=[], skills=[],
    )
    out = bootstrap.render_bootstrap(ctx)
    getoond = [regel for regel in out.splitlines()
               if regel.strip().startswith("- step-")]
    assert len(getoond) == bootstrap.QUEST_STEPS_LIMIT
    verborgen = 14 - bootstrap.QUEST_STEPS_LIMIT
    assert f"+{verborgen} stappen verborgen" in out
