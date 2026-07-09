"""Aankondigingen-wachtrij voor PROACTIEF SPREKEN.

LO leest een klein aantal spreekwaardige items HARDOP voor — dagafsluiting,
weekreview, meeting-prep en urgente mail/acties — maar ALLEEN als het moment
veilig is (zie static/proactive.js). Deze queue houdt de items vast tot dat kan;
de HUD polt /api/announcements, spreekt één item en markeert het als
uitgesproken zodat het nooit twee keer klinkt.

Bewust in-memory en los van de AgentInbox: dit zijn vluchtige spraakmeldingen,
geen taken of goedkeuringen, en ze tellen niet mee in het inbox-badge. Een
herstart begint schoon — de onderliggende meldingen staan al in de Agent Inbox.
"""

from __future__ import annotations

import itertools
import threading
from typing import Any

# de klok komt uit span.clock (niet uit daily.py) zodat daily.py deze module
# aan de top kan importeren zonder circulaire import
from span.clock import now_local

# Alleen deze soorten worden ooit hardop gezegd (bewaakt in .add()).
ANNOUNCE_TYPES = ("evening", "weekreview", "meeting_prep", "urgent")
_MAX_ITEMS = 50


class AnnouncementQueue:
    """Thread-safe, in-memory wachtrij van uit-te-spreken items.

    Elk item: id, type (evening|weekreview|meeting_prep|urgent), text (de
    al-gegenereerde gesproken tekst), created."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def add(self, kind: str, text: str) -> int | None:
        """Rij een item in. Negeert onbekende soorten, lege tekst en exacte
        duplicaten (dezelfde tekst mag niet twee keer in de rij staan)."""
        text = (text or "").strip()
        if kind not in ANNOUNCE_TYPES or not text:
            return None
        item = {
            "id": next(self._ids),
            "type": kind,
            "text": text[:1200],
            "created": now_local().isoformat(timespec="seconds"),
        }
        with self._lock:
            if any(i["text"] == item["text"] for i in self._items):
                return None  # al ingerijd, niet dubbel voorlezen
            self._items.append(item)
            del self._items[:-_MAX_ITEMS]  # houd het compact
        return item["id"]

    def open_items(self) -> list[dict[str, Any]]:
        """De openstaande items (id, type, text) in inreivolgorde."""
        with self._lock:
            return [{"id": i["id"], "type": i["type"], "text": i["text"]}
                    for i in self._items]

    def mark_spoken(self, item_id: int) -> bool:
        """Verwijder een uitgesproken item. True als er echt iets wegviel."""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["id"] != item_id]
            return len(self._items) < before


def enqueue(state: dict[str, Any], kind: str, text: str) -> None:
    """Zacht-falende helper voor de generatie-kant (daily.py / ambient.py):
    rij een spraakaankondiging in als de queue bestaat, en zwijg bij een fout —
    het spreken mag nooit een scheduler- of watcher-tick laten omvallen."""
    q = state.get("announcements")
    if q is None:
        return
    try:
        q.add(kind, text)
    except Exception as exc:
        print(f"[announce] enqueue mislukt: {exc}", flush=True)
