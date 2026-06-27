"""Gedeeld geheugen (WP-3): FragmentStore leest privé ∪ shared."""

from span.memory.fragments import FragmentStore


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
