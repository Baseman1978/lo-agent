"""Eén klok voor heel Span — planners denken in Nederlandse tijd,
ook wanneer de container in UTC draait."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")


def now_local() -> datetime:
    return datetime.now(TZ)


def today_local() -> str:
    return now_local().date().isoformat()
