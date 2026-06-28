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


# --- WP-3 polish: Span stelt delen voor via de Agent Inbox ---

from span.jarvis.ambient import AgentInbox, execute_approval
from span.orchestrator.tools import ToolBox


class _ShareBrain:
    """Beantwoordt de labels/preview-query van propose_share."""

    def __init__(self, labels, preview="hallo team"):
        self._labels = labels
        self._preview = preview

    def run(self, query, **params):
        if "RETURN labels(n)" in query:
            return [{"labels": self._labels, "preview": self._preview}]
        return []


def _toolbox(brain, inbox, shared):
    return ToolBox(brain, None, "sess", inbox=inbox, shared=shared)


def test_propose_share_queues_agent_item():
    inbox = AgentInbox()
    res = _toolbox(_ShareBrain(["Insight"]), inbox, shared=_RunBrain())._tool_propose_share(
        "i1", reason="nuttig voor het team")
    assert res["label"] == "Insight" and "proposed" in res
    item = inbox.snapshot()[0]
    assert item["action"] == "share_memory"
    assert item["origin"] == "agent"          # Bas keurt goed, Span niet zelf
    assert item["payload"]["node_id"] == "i1"


def test_propose_share_rejects_non_shareable():
    inbox = AgentInbox()
    res = _toolbox(_ShareBrain(["Session"]), inbox, shared=_RunBrain())._tool_propose_share("s1")
    assert "error" in res and inbox.open_count() == 0


def test_propose_share_needs_shared_brain():
    inbox = AgentInbox()
    res = _toolbox(_ShareBrain(["Insight"]), inbox, shared=None)._tool_propose_share("i1")
    assert "error" in res and inbox.open_count() == 0


def test_propose_share_hidden_in_single_user():
    single = {s["function"]["name"]
              for s in ToolBox(_ShareBrain([]), None, "s", inbox=AgentInbox(), shared=None).specs()}
    multi = {s["function"]["name"]
             for s in ToolBox(_ShareBrain([]), None, "s", inbox=AgentInbox(), shared=_RunBrain()).specs()}
    assert "propose_share" not in single
    assert "propose_share" in multi


def test_execute_approval_share_memory_copies():
    priv = _RunBrain(match_rows=[{"labels": ["Skill"], "props": {"id": "k1", "content": "c"}}])
    shared = _RunBrain()
    item = {"action": "share_memory", "kind": "action", "payload": {"node_id": "k1"}}
    res = execute_approval(item, None, brain=priv, shared=shared, shared_by="bas@lomans.nl")
    assert res["label"] == "Skill"
    assert any("MERGE" in q for q, _ in shared.calls)


def test_execute_approval_share_memory_requires_shared():
    item = {"action": "share_memory", "kind": "action", "payload": {"node_id": "k1"}}
    res = execute_approval(item, None, brain=_RunBrain(), shared=None)
    assert "error" in res
