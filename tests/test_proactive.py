"""PROACTIEF SPREKEN — aankondigingen-wachtrij, endpoints en agenda-nuance.

De queue is de kern (enqueue -> zichtbaar -> uitgesproken -> weg); daarnaast de
REST-endpoints (met de quiet-hours-filter) en de meeting_now-nuance: alleen een
afspraak MET andere genodigden blokkeert.
"""

from __future__ import annotations

import asyncio

from unittest.mock import MagicMock

import span.server.routes as routes
import span.server.state as st
from span.integrations.o365 import O365Client
from span.jarvis.announce import AnnouncementQueue, enqueue


class _Req:
    def __init__(self) -> None:
        self.cookies: dict = {}
        self.headers: dict = {}
        self.client = None


# -- de queue ---------------------------------------------------------------
class TestAnnouncementQueue:
    def test_enqueue_zichtbaar_uitgesproken_weg(self):
        q = AnnouncementQueue()
        i = q.add("evening", "De dag zit erop.")
        assert isinstance(i, int)
        items = q.open_items()
        assert len(items) == 1
        assert items[0]["type"] == "evening"
        assert items[0]["text"] == "De dag zit erop."
        assert set(items[0]) == {"id", "type", "text"}   # geen created-lek
        assert q.mark_spoken(i) is True
        assert q.open_items() == []
        assert q.mark_spoken(i) is False                 # tweede keer: niets meer

    def test_onbekend_type_en_lege_tekst_geweigerd(self):
        q = AnnouncementQueue()
        assert q.add("gossip", "iets") is None
        assert q.add("urgent", "   ") is None
        assert q.open_items() == []

    def test_exacte_duplicaat_niet_dubbel(self):
        q = AnnouncementQueue()
        q.add("urgent", "Zelfde tekst")
        assert q.add("urgent", "Zelfde tekst") is None
        assert len(q.open_items()) == 1

    def test_cap_houdt_laatste_50(self):
        q = AnnouncementQueue()
        for n in range(60):
            q.add("urgent", f"melding {n}")
        items = q.open_items()
        assert len(items) == 50
        assert items[-1]["text"] == "melding 59"

    def test_enqueue_helper_zacht_falend(self):
        q = AnnouncementQueue()
        enqueue({"announcements": q}, "weekreview", "Terugblik.")
        assert len(q.open_items()) == 1
        enqueue({}, "weekreview", "geen queue -> stil")  # geen exception


# -- endpoints --------------------------------------------------------------
class TestAnnouncementEndpoints:
    def _auth(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_rest_auth", lambda r: None)

    def test_lijst_toont_open_items(self, monkeypatch):
        self._auth(monkeypatch)
        monkeypatch.setattr(routes, "quiet_hours_active", lambda brain: False)
        q = AnnouncementQueue()
        q.add("evening", "Klaar voor vandaag.")
        st._state["announcements"] = q
        st._state["brain"] = MagicMock()
        try:
            out = asyncio.run(routes.announcements_list(_Req()))
            assert len(out["items"]) == 1
            assert out["items"][0]["text"] == "Klaar voor vandaag."
        finally:
            st._state.pop("announcements", None)
            st._state.pop("brain", None)

    def test_lijst_leeg_tijdens_stille_uren(self, monkeypatch):
        self._auth(monkeypatch)
        monkeypatch.setattr(routes, "quiet_hours_active", lambda brain: True)
        q = AnnouncementQueue()
        q.add("evening", "Wacht tot na de stille uren.")
        st._state["announcements"] = q
        st._state["brain"] = MagicMock()
        try:
            out = asyncio.run(routes.announcements_list(_Req()))
            assert out["items"] == []          # item blijft staan, alleen verborgen
            assert len(q.open_items()) == 1
        finally:
            st._state.pop("announcements", None)
            st._state.pop("brain", None)

    def test_spoken_verwijdert_item(self, monkeypatch):
        self._auth(monkeypatch)
        q = AnnouncementQueue()
        i = q.add("urgent", "Urgente mail.")
        st._state["announcements"] = q
        try:
            out = asyncio.run(routes.announcements_spoken(_Req(), i))
            assert out["spoken"] is True
            assert q.open_items() == []
        finally:
            st._state.pop("announcements", None)


# -- agenda-nuance: alleen blokkeren bij ANDERE genodigden -------------------
class TestMeetingNow:
    def _client(self, events, me="bas@lomans.nl"):
        c = O365Client.__new__(O365Client)   # __init__ overslaan: alleen de logica
        c._get = MagicMock(return_value={"value": events})
        c.account_name = MagicMock(return_value=me)
        return c

    def test_meeting_met_andere_genodigde_blokkeert(self):
        c = self._client([{
            "subject": "Sprint review", "isAllDay": False,
            "attendees": [
                {"emailAddress": {"address": "bas@lomans.nl"}},
                {"emailAddress": {"address": "collega@lomans.nl"}},
            ],
        }])
        assert c.meeting_now() is True

    def test_solo_blok_blokkeert_niet(self):
        c = self._client([{
            "subject": "Bas Werkt", "isAllDay": False,
            "attendees": [{"emailAddress": {"address": "bas@lomans.nl"}}],
        }])
        assert c.meeting_now() is False

    def test_geen_event_blokkeert_niet(self):
        assert self._client([]).meeting_now() is False

    def test_hele_dag_item_telt_niet(self):
        c = self._client([{
            "subject": "Vakantie", "isAllDay": True,
            "attendees": [{"emailAddress": {"address": "collega@lomans.nl"}}],
        }])
        assert c.meeting_now() is False

    def test_endpoint_zonder_o365_faalt_zacht(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_rest_auth", lambda r: None)
        monkeypatch.setattr(routes, "_request_context",
                            lambda r: type("C", (), {"o365": None})())
        out = asyncio.run(routes.presence_meeting_now(_Req()))
        assert out == {"blocking": False}

    def test_endpoint_met_o365_geeft_blocking(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_rest_auth", lambda r: None)
        o365 = MagicMock()
        o365.meeting_now.return_value = True
        monkeypatch.setattr(routes, "_request_context",
                            lambda r: type("C", (), {"o365": o365})())
        out = asyncio.run(routes.presence_meeting_now(_Req()))
        assert out == {"blocking": True}

    def test_endpoint_o365_fout_faalt_zacht(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_rest_auth", lambda r: None)
        o365 = MagicMock()
        o365.meeting_now.side_effect = RuntimeError("graph down")
        monkeypatch.setattr(routes, "_request_context",
                            lambda r: type("C", (), {"o365": o365})())
        out = asyncio.run(routes.presence_meeting_now(_Req()))
        assert out == {"blocking": False}
