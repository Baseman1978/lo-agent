"""Tool-retrieval: per beurt alleen de relevante tools aan het model aanbieden.

Conservatief thema met vangnetten: bij retrieval-uit, lege query, kleine pool
of een embed/rank-fout valt alles terug op de VOLLEDIGE (reeds gefilterde)
lijst — nooit een regressie, en nooit een geblokkeerde tool terug.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from span.orchestrator.tools import ToolBox, _CORE_TOOLS

# Kleine, deterministische "embeddings": een tekst krijgt op een handvol
# semantische assen een 1.0 als het bijbehorende sleutelwoord erin staat,
# anders een kleine basiswaarde. Zo ligt een agenda-vraag dicht bij de
# calendar-tools en ver van bv. weer — zonder een echt embedding-model.
_AXES = ["calendar", "mail", "weather", "asana", "brain", "skill", "web", "inbox"]


def _vec(text: str) -> list[float]:
    t = (text or "").lower()
    return [1.0 if ax in t else 0.01 for ax in _AXES]


class _MockLLM:
    def __init__(self, fail: bool = False):
        self._fail = fail

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError("embed kapot (test)")
        return [_vec(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        if self._fail:
            raise RuntimeError("embed kapot (test)")
        return _vec(text)


def _tb(llm=None, perms=None, retrieval=True, k=24, o365=True, asana=True):
    return ToolBox(
        brain=MagicMock(), fragments=MagicMock(), session_id="s",
        llm=llm, perms=perms,
        o365=MagicMock() if o365 else None,
        asana=MagicMock() if asana else None,
        tool_retrieval=retrieval, tool_retrieval_k=k,
    )


def _names(specs):
    return {s["function"]["name"] for s in specs}


# -- de kern: relevante subset, kern erbij, irrelevante eruit -----------------

def test_agenda_vraag_kiest_calendar_en_dropt_weer():
    tb = _tb(llm=_MockLLM())
    pool = _names(tb.specs())
    assert len(pool) > 40                      # grote pool -> retrieval actief
    subset = _names(tb.specs_for("calendar agenda afspraak"))
    assert "o365_calendar" in subset           # relevante tool zit erin
    assert "weather" not in subset             # niet-relevante valt weg
    assert len(subset) < len(pool)             # het is echt een subset
    # de volledige lijst (macro-validatiepad) blijft ongewijzigd
    assert _names(tb.specs()) == pool


def test_kern_tools_altijd_erbij_ook_bij_kleine_k():
    tb = _tb(llm=_MockLLM(), k=2)
    subset = _names(tb.specs_for("calendar agenda"))
    pool = _names(tb.specs())
    # elke kern-tool die überhaupt in de pool zit, moet aangeboden worden
    for name in _CORE_TOOLS:
        if name in pool:
            assert name in subset, name
    # web_search is kern maar niet calendar-relevant -> bewijst de kern-insluiting
    assert "web_search" in subset


def test_geblokkeerde_tool_komt_nooit_terug():
    tb = _tb(llm=_MockLLM(), perms={"O365 Mail": {"read": False, "write": False}})
    subset = _names(tb.specs_for("stuur een mail naar jan"))
    assert "o365_mail_send" not in subset      # permissie dicht -> nooit aangeboden
    assert "o365_mail_inbox" not in subset      # ook al is 't een kern-tool


def test_gebruikte_tool_blijft_in_subset():
    tb = _tb(llm=_MockLLM(), k=2)
    # controle: zonder gebruik zit asana_my_tasks niet in een calendar-subset
    assert "asana_my_tasks" not in _names(tb.specs_for("calendar agenda"))
    # nu deze sessie aangeroepen -> dispatch vult _used_tools
    tb.dispatch("asana_my_tasks", {})
    assert "asana_my_tasks" in tb._used_tools
    # vervolgvraag (onverwant) biedt de net-gebruikte tool alsnog aan
    assert "asana_my_tasks" in _names(tb.specs_for("calendar agenda"))


# -- gedrag identiek aan specs() bij uit/klein/leeg/fout ----------------------

def test_retrieval_uit_geeft_volledige_lijst():
    tb = _tb(llm=_MockLLM(), retrieval=False)
    assert _names(tb.specs_for("calendar agenda")) == _names(tb.specs())


def test_lege_query_geeft_volledige_lijst():
    tb = _tb(llm=_MockLLM())
    assert _names(tb.specs_for("")) == _names(tb.specs())
    assert _names(tb.specs_for("   ")) == _names(tb.specs())
    assert _names(tb.specs_for(None)) == _names(tb.specs())


def test_kleine_pool_geeft_volledige_lijst():
    # zonder o365/asana is de pool klein (<= drempel) -> geen retrieval
    tb = _tb(llm=_MockLLM(), o365=False, asana=False)
    assert len(tb.specs()) <= 40
    assert _names(tb.specs_for("calendar agenda")) == _names(tb.specs())


def test_embed_fout_valt_terug_op_volledige_lijst():
    tb = _tb(llm=_MockLLM(fail=True))
    # embed gooit -> vangnet -> volledige lijst, geen crash
    assert _names(tb.specs_for("calendar agenda")) == _names(tb.specs())


def test_geen_llm_valt_terug_op_volledige_lijst():
    tb = _tb(llm=None)
    assert _names(tb.specs_for("calendar agenda")) == _names(tb.specs())
