"""MemoryFragment-gedrag zonder live database (mocks)."""

from unittest.mock import MagicMock

import pytest

from span.llm.client import _parse_json_block
from span.memory.fragments import FragmentStore, new_mf_id


def make_store():
    brain = MagicMock()
    llm = MagicMock()
    llm.embed_one.return_value = [0.1] * 8
    return FragmentStore(brain, llm), brain, llm


def test_write_rejects_unknown_type():
    store, _, _ = make_store()
    with pytest.raises(ValueError, match="Onbekend MF-type"):
        store.write(mf_type="gossip", content="x", session_id="s1")


def test_write_rejects_empty_content():
    store, _, _ = make_store()
    with pytest.raises(ValueError, match="Leeg"):
        store.write(mf_type="decision", content="   ", session_id="s1")


def test_write_stores_with_embedding():
    store, brain, llm = make_store()
    mf_id = store.write(
        mf_type="anti-pattern",
        content="Edit-tool truncate bij lange regels; gebruik heredoc + cp.",
        session_id="s1",
    )
    assert mf_id.startswith("mf-")
    assert "-ap" in mf_id  # typecode van anti-pattern
    llm.embed_one.assert_called_once()
    kwargs = brain.run.call_args.kwargs
    assert kwargs["embedding"] == [0.1] * 8
    assert kwargs["session_id"] == "s1"


def test_mf_id_typecodes():
    assert "-il" in new_mf_id("interaction-log")
    assert "-s" in new_mf_id("soul")


def test_parse_json_with_fences():
    assert _parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_surrounding_text():
    assert _parse_json_block('Hier: {"fragments": []} klaar.') == {"fragments": []}


def test_parse_json_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_json_block("geen json hier")
