"""B1 — fast-lane-routering (v2).

Routering vóór de beurt op basis van de retrieval-tool-subset:
- Bevat de beurt een actie/integratie-tool (o365_*/asana_*)? -> hoofdmodel
  (Sonnet). Die tools worden op het lichte model niet betrouwbaar aangeroepen
  (gemeten: mail-guard/agenda-scenario's faalden op Haiku).
- Anders (geheugen/gesprek — scoorde 100% op Haiku) -> licht model (snel).

Escalatie blijft als vangnet: draait een fast-lane-beurt tóch een actie-tool
(retrieval miste 'm), dan schakelt de synthese alsnog naar het hoofdmodel.

Achter de flag SPAN_FAST_LANE, default UIT (poort na bewezen meting; A2-idioom).
"""
from __future__ import annotations

import os
from typing import Any

from span.orchestrator.tool_specs import ASANA_TOOLS, O365_TOOLS

# lane-labels voor de telemetrie (segment "llm", veld "lane")
LANE_MAIN = "main"            # (start op) hoofdmodel — flag uit of actie-tool in de beurt
LANE_FAST = "fast"            # lichte model, geen actie-tool
LANE_ESCALATED = "escalated"  # startte licht, actie-tool dook op -> hoofdmodel

# actie/integratie-tools die op het lichte model onbetrouwbaar zijn
ACTION_TOOLS = O365_TOOLS | ASANA_TOOLS


def enabled() -> bool:
    """Feature-flag SPAN_FAST_LANE. Default UIT (leeg/0/off/false/no = uit)."""
    return os.environ.get("SPAN_FAST_LANE", "").strip().lower() in (
        "1", "true", "yes", "on")


def is_action_tool(name: str) -> bool:
    """Is dit een actie/integratie-tool die op het lichte model faalt?"""
    return name in ACTION_TOOLS


def _tool_names(turn_tools: Any) -> list[str]:
    """Namen uit een lijst OpenAI-tool-specs ({'function': {'name': ...}})."""
    names: list[str] = []
    for spec in turn_tools or []:
        if isinstance(spec, dict):
            fn = spec.get("function") or {}
            name = fn.get("name")
            if name:
                names.append(name)
    return names


def turn_model(settings: Any, turn_tools: Any) -> tuple[str, str]:
    """Startmodel + lane vóór de beurt.

    Flag uit -> altijd het hoofdmodel (pure no-op). Flag aan -> het lichte
    model, TENZIJ de beurt-tools een actie/integratie-tool bevatten (dan het
    hoofdmodel, want die worden op Haiku niet betrouwbaar aangeroepen)."""
    if not enabled():
        return settings.model_main, LANE_MAIN
    if any(is_action_tool(n) for n in _tool_names(turn_tools)):
        return settings.model_main, LANE_MAIN
    return settings.model_light, LANE_FAST
