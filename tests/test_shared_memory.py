"""Gedeeld geheugen (WP-3): FragmentStore leest privé ∪ shared + delen."""

import pytest

from span.memory.fragments import FragmentStore
from span.memory.sharing import share_node, unshare_node


class _LLM:
    def embed_one(self, text):
        return [0.1] * 8


class _Brain:
    def __init__(self, rows):
        self._rows = rows

    def vector_search(self, index, embedding, k):
        return self._rows if index == "mf_embedding" else []

    def run(self, *a, **k):
        return []


def _mf(node_id, score, content="x"):
    return {"node": {"id": node_id, "type": "fact", "content": content}, "score": score}


def test_search_without_shared_tags_private():
    fs = FragmentStore(_Brain([_mf("p1", 0.9)]), _LLM())
    res = fs.search("q", k=3)
    assert [r["id"] for r in res] == ["p1"]
    assert res[0]["shared"] is False


def test_search_union_dedup_sort_tag():
    priv = _Brain([_mf("p1", 0.9), _mf("dup", 0.5)])
    shared = _Brain([_mf("s1", 0.7), _mf("dup", 0.95)])  # 'dup' bestaat in beide
    fs = FragmentStore(priv, _LLM(), extra_brains=[shared])
    res = fs.search("q", k=5)
    by = {r["id"]: r for r in res}
    # gesorteerd op score, privé wint bij dubbele id
    assert [r["id"] for r in res] == ["p1", "s1", "dup"]
    assert by["p1"]["shared"] is False
    assert by["s1"]["shared"] is True          # uit het gedeelde brein -> getagd
    assert by["dup"]["shared"] is False        # privé eerst gezien
    assert by["s1"]["source"] == "shared"


class _RunBrain:
    def __init__(self, match_rows=None):
        self.calls = []
        self._match = match_rows

    def run(self, query, **params):
        self.calls.append((query, params))
        if query.lstrip().startswith("MATCH (n {id:$id}) RETURN labels"):
            return self._match or []
        return []


def test_share_node_copies_with_provenance():
    priv = _RunBrain(match_rows=[{"labels": ["Insight"],
                                  "props": {"id": "i1", "content": "c", "embedding": [0.1]}}])
    shared = _RunBrain()
    res = share_node(priv, shared, "i1", "bas@lomans.nl")
    assert res["label"] == "Insight"
    merge = [(q, p) for q, p in shared.calls if "MERGE" in q][0]
    assert "MERGE (n:`Insight`" in merge[0]
    assert merge[1]["props"]["shared_by"] == "bas@lomans.nl"
    assert merge[1]["props"]["origin_id"] == "i1"


def test_share_node_rejects_non_shareable():
    priv = _RunBrain(match_rows=[{"labels": ["Session"], "props": {"id": "s1"}}])
    with pytest.raises(ValueError):
        share_node(priv, _RunBrain(), "s1")


def test_share_node_not_found():
    with pytest.raises(ValueError):
        share_node(_RunBrain(match_rows=[]), _RunBrain(), "nope")


def test_unshare_deletes():
    shared = _RunBrain()
    unshare_node(shared, "i1")
    assert any("DETACH DELETE" in q for q, _ in shared.calls)
