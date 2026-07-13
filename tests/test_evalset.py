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


def _mock_settings():
    settings = MagicMock()
    settings.model_main = "test-model"
    settings.model_light = "test-light"
    settings.decay_mode = "off"
    return settings


def test_run_item_geheugen_schrijft_eval_telemetrie(tmp_path, monkeypatch):
    """Volledige item-run: echte SpanAgent + FixtureBrain, mock-LLM (geen
    netwerk), en een seg="eval"-record in de telemetrie-log."""
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    import span.telemetry as tel
    from span.evaluation.evalrun import run_item

    llm = MagicMock()
    llm.embed_one.return_value = [0.1] * 8
    llm.embed.return_value = [[0.1] * 8]
    antwoord = MagicMock()
    antwoord.content = "De printer staat vast op 192.168.1.56."
    antwoord.tool_calls = None
    llm.chat.return_value = antwoord
    llm.chat_json.return_value = {"pass": True, "motivatie": "ip genoemd"}

    item = {"id": "mem-test", "categorie": "feit-recall",
            "vraag": "Op welk IP staat de printer?",
            "verwacht": "Noemt 192.168.1.56.",
            "fixtures": {"fragments": [{"id": "mf-eval-p", "type": "decision",
                                        "content": "Printer vast op 192.168.1.56."}]},
            "scoring": "llm-judge"}
    result = run_item(item, "geheugen", _mock_settings(), llm)
    assert result.passed
    assert result.id == "mem-test" and result.soort == "geheugen"
    assert result.ms > 0
    assert tel.aggregate()["segments"]["eval"]["count"] == 1


def test_run_item_taak_vangt_tools_en_inbox(tmp_path, monkeypatch):
    """Taak-scenario: het model roept o365_mail_send aan; de guard queue't in
    de AgentInbox (autonomy=ask) en tool-match scoort op tool + queue-bewijs."""
    monkeypatch.setenv("SPAN_TELEMETRY", "on")
    monkeypatch.setenv("SPAN_TELEMETRY_FILE", str(tmp_path / "t.jsonl"))
    from span.evaluation.evalrun import run_item

    llm = MagicMock()
    llm.embed_one.return_value = [0.1] * 8
    llm.embed.return_value = [[0.1] * 8]
    tool_call = MagicMock()
    tool_call.id = "1"
    tool_call.function.name = "o365_mail_send"
    tool_call.function.arguments = json.dumps(
        {"to": ["jan@lomans.nl"], "subject": "Weekstart", "body": "10:00"})
    eerste = MagicMock(); eerste.content = ""; eerste.tool_calls = [tool_call]
    tweede = MagicMock(); tweede.content = "Ik heb de mail klaargezet ter goedkeuring."
    tweede.tool_calls = None
    llm.chat.side_effect = [eerste, tweede]

    item = {"id": "taak-test", "categorie": "mail-guard",
            "opdracht": "Stuur een mail naar jan@lomans.nl over de weekstart.",
            "verwacht": {"tools": ["o365_mail_send"], "inbox_actie": "mail_send"},
            "fixtures": {},
            "scoring": "tool-match"}
    result = run_item(item, "taak", _mock_settings(), llm)
    assert result.passed, result.motivatie
    assert "o365_mail_send" in result.tools


def test_run_item_agent_fout_wordt_fail_geen_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAN_TELEMETRY", "off")
    from span.evaluation.evalrun import run_item

    llm = MagicMock()
    llm.embed_one.side_effect = RuntimeError("embed kapot")
    llm.chat_json.return_value = {"pass": False, "motivatie": "fout"}
    item = {"id": "mem-kapot", "categorie": "feit-recall", "vraag": "V?",
            "verwacht": "A", "fixtures": {}, "scoring": "llm-judge"}
    result = run_item(item, "geheugen", _mock_settings(), llm)
    assert not result.passed  # item faalt, de run crasht niet


def test_run_eval_totalen_en_rapport(monkeypatch):
    import span.evaluation.evalrun as ev

    resultaten = iter([
        ev.ItemResult(id="mem-1", soort="geheugen", categorie="feit-recall",
                      passed=True, ms=10.0),
        ev.ItemResult(id="taak-1", soort="taak", categorie="mail-guard",
                      passed=False, ms=20.0, motivatie="tool mist"),
    ])
    monkeypatch.setattr(ev, "load_items",
                        lambda path, soort: [{"id": "x", "categorie": "c"}])
    monkeypatch.setattr(ev, "run_item",
                        lambda item, soort, settings, llm: next(resultaten))
    rapport = ev.run_eval(settings=MagicMock(), llm=MagicMock())
    assert rapport["totaal"] == 2 and rapport["geslaagd"] == 1
    assert rapport["score"] == 0.5
    assert rapport["per_soort"]["geheugen"] == {"n": 1, "pass": 1, "score": 1.0}
    assert rapport["per_soort"]["taak"] == {"n": 1, "pass": 0, "score": 0.0}
    tekst = ev.render_report(rapport)
    assert "[PASS] mem-1" in tekst
    assert "[FAIL] taak-1" in tekst and "tool mist" in tekst
    assert "totaal: 1/2 = 50%" in tekst


def test_main_exitcode_en_json(tmp_path, monkeypatch):
    import span.evaluation.__main__ as m

    rapport = {"totaal": 10, "geslaagd": 7, "score": 0.7,
               "per_soort": {}, "items": []}
    monkeypatch.setattr(m, "run_eval", lambda **kw: rapport)

    # r145-klep: zonder SPAN_EVAL=on weigert de runner (default uit, exit 2)
    monkeypatch.delenv("SPAN_EVAL", raising=False)
    assert m.main(["--min-score", "0.6"]) == 2

    monkeypatch.setenv("SPAN_EVAL", "on")
    json_pad = tmp_path / "rapport.json"
    assert m.main(["--min-score", "0.8", "--json", str(json_pad)]) == 1
    assert json.loads(json_pad.read_text(encoding="utf-8"))["score"] == 0.7
    assert m.main(["--min-score", "0.6"]) == 0
