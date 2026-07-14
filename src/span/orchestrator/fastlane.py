"""B1 — fast-lane-routering.

Escalatie-model: een beurt start op het lichte model (snel) en schakelt naar
het hoofdmodel zodra er een tool wordt aangeroepen (de synthese ná tool-
resultaten verdient het sterke model). Achter de flag SPAN_FAST_LANE, default
UIT — poort pas open nadat de telemetrie de winst bevestigt (zie A2-precedent).
"""
from __future__ import annotations

import os
from typing import Any

# lane-labels voor de telemetrie (segment "llm", veld "lane")
LANE_MAIN = "main"            # startte én bleef op het hoofdmodel
LANE_FAST = "fast"            # bleef op het lichte model (puur gesprek, geen tool)
LANE_ESCALATED = "escalated"  # startte licht, schakelde naar hoofdmodel bij een tool


def enabled() -> bool:
    """Feature-flag SPAN_FAST_LANE. Default UIT (leeg/0/off/false/no = uit)."""
    return os.environ.get("SPAN_FAST_LANE", "").strip().lower() in (
        "1", "true", "yes", "on")


def initial_model(settings: Any) -> str:
    """Startmodel voor een beurt: het lichte model als de flag aan staat,
    anders het hoofdmodel (dan is de hele routering een no-op)."""
    return settings.model_light if enabled() else settings.model_main
