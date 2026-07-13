"""A7 — eval-set v1: fixture-brain, dataschema, scoring, runner, rapport."""
from __future__ import annotations

import json
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


def test_datasets_valide_en_ids_uniek():
    from span.evaluation.evalrun import GEHEUGEN_PATH, TAKEN_PATH, load_items

    geheugen = load_items(GEHEUGEN_PATH, "geheugen")
    taken = load_items(TAKEN_PATH, "taak")
    assert len(geheugen) >= 6 and len(taken) >= 5  # Task 6 vult aan tot 50/20
    ids = [i["id"] for i in geheugen] + [i["id"] for i in taken]
    assert len(ids) == len(set(ids))
    assert all(i["id"].startswith("mem-") for i in geheugen)
    assert all(i["id"].startswith("taak-") for i in taken)


def test_validate_item_vangt_gaten():
    from span.evaluation.evalrun import validate_item

    ok = {"id": "mem-x", "categorie": "feit-recall", "fixtures": {},
          "scoring": "llm-judge", "vraag": "V?", "verwacht": "A"}
    assert validate_item(ok, "geheugen") == []

    fouten = validate_item({"id": "", "scoring": "raar"}, "geheugen")
    assert any("id" in f for f in fouten)
    assert any("scoring" in f for f in fouten)
    assert any("vraag" in f for f in fouten)

    fouten = validate_item({"id": "taak-x", "categorie": "mail", "fixtures": {},
                            "scoring": "tool-match", "opdracht": "doe iets",
                            "verwacht": {}}, "taak")
    assert any("tools" in f for f in fouten)


def test_load_items_weigert_dubbel_id(tmp_path):
    from span.evaluation.evalrun import load_items

    p = tmp_path / "dubbel.json"
    item = {"id": "mem-1", "categorie": "c", "fixtures": {},
            "scoring": "llm-judge", "vraag": "v?", "verwacht": "a"}
    p.write_text(json.dumps({"version": 1, "items": [item, dict(item)]}),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="dubbel id"):
        load_items(p, "geheugen")


def test_score_geheugen_llm_judge():
    from span.evaluation.evalrun import score_geheugen

    llm = MagicMock()
    llm.chat_json.return_value = {"pass": True, "motivatie": "kern klopt"}
    item = {"vraag": "V?", "verwacht": "A"}
    passed, motivatie = score_geheugen(item, "antwoord A", llm, "judge-model")
    assert passed and motivatie == "kern klopt"
    # het judge-model dat we meegeven wordt ook echt gebruikt
    assert llm.chat_json.call_args.kwargs["model"] == "judge-model"

    llm.chat_json.return_value = {"pass": False, "motivatie": "feit mist"}
    passed, motivatie = score_geheugen(item, "iets anders", llm, "judge-model")
    assert not passed and motivatie == "feit mist"

    # judge-fout = FAIL met motivatie, nooit een exception
    llm.chat_json.side_effect = RuntimeError("kapot")
    passed, motivatie = score_geheugen(item, "antwoord", llm, "judge-model")
    assert not passed and "judge-fout" in motivatie


def test_score_taak_tool_match_en_inbox():
    from span.evaluation.evalrun import score_taak
    from span.jarvis.ambient import AgentInbox

    inbox = AgentInbox()  # zonder brein: vluchtig, precies goed voor de eval
    item = {"verwacht": {"tools": ["o365_mail_send"], "inbox_actie": "mail_send"}}

    passed, motivatie = score_taak(item, "klaargezet", [], inbox)
    assert not passed and "o365_mail_send" in motivatie  # tool niet aangeroepen

    passed, motivatie = score_taak(item, "klaargezet", ["o365_mail_send"], inbox)
    assert not passed and "inbox" in motivatie  # nog niets gequeued

    inbox.add(kind="action", action="mail_send", title="Mail aan jan@lomans.nl")
    passed, motivatie = score_taak(item, "klaargezet", ["o365_mail_send"], inbox)
    assert passed

    item2 = {"verwacht": {"tools": ["o365_calendar"],
                          "antwoord_bevat": ["Weekstart"]}}
    passed, motivatie = score_taak(item2, "Vandaag: weekSTART om 9:00.",
                                   ["o365_calendar"], inbox)
    assert passed  # substring-check is case-insensitief
    passed, motivatie = score_taak(item2, "Niets vandaag.", ["o365_calendar"], inbox)
    assert not passed and "Weekstart" in motivatie
