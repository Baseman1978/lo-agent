# tests/test_fastlane.py
"""B1 — fast-lane-routering: flag + startmodel-keuze + escalatie."""
from __future__ import annotations

from unittest.mock import MagicMock

import span.orchestrator.fastlane as fl


class TestFlag:
    def test_default_uit(self, monkeypatch):
        monkeypatch.delenv("SPAN_FAST_LANE", raising=False)
        assert fl.enabled() is False

    def test_lege_waarde_uit(self, monkeypatch):
        monkeypatch.setenv("SPAN_FAST_LANE", "")
        assert fl.enabled() is False

    def test_aan_waarden(self, monkeypatch):
        for val in ("1", "true", "yes", "on", "ON", " True "):
            monkeypatch.setenv("SPAN_FAST_LANE", val)
            assert fl.enabled() is True, val

    def test_uit_waarden(self, monkeypatch):
        for val in ("0", "off", "false", "no", "nope"):
            monkeypatch.setenv("SPAN_FAST_LANE", val)
            assert fl.enabled() is False, val


class TestInitialModel:
    def _settings(self):
        s = MagicMock()
        s.model_main = "sonnet"
        s.model_light = "haiku"
        return s

    def test_flag_uit_kiest_main(self, monkeypatch):
        monkeypatch.delenv("SPAN_FAST_LANE", raising=False)
        assert fl.initial_model(self._settings()) == "sonnet"

    def test_flag_aan_kiest_light(self, monkeypatch):
        monkeypatch.setenv("SPAN_FAST_LANE", "on")
        assert fl.initial_model(self._settings()) == "haiku"
