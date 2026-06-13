"""RunBudget + circuit-breaker (F1.6).

Begrenst een autonome run op tool-iteraties en wandklok, zodat een
doorgeslagen loop (of een gekaapte agent) zichzelf niet eindeloos voedt.
Bewust geen tokenteller hier — de LLM-laag heeft al max_tokens; dit gaat over
het aantal acties en de duur.
"""

from __future__ import annotations

import time


class BudgetExceeded(RuntimeError):
    """De run overschreed zijn iteratie- of tijdslimiet."""


class RunBudget:
    def __init__(self, max_iterations: int = 12, max_seconds: float = 180.0):
        self.max_iterations = max_iterations
        self.max_seconds = max_seconds
        self._iterations = 0
        self._start = None  # gezet bij eerste tick (Date.now/monotonic mag hier wel)

    def tick(self) -> None:
        """Eén iteratie verbruiken; gooit BudgetExceeded bij overschrijding."""
        if self._start is None:
            self._start = time.monotonic()
        self._iterations += 1
        if self._iterations > self.max_iterations:
            raise BudgetExceeded(
                f"iteratie-limiet bereikt ({self.max_iterations})")
        if time.monotonic() - self._start > self.max_seconds:
            raise BudgetExceeded(
                f"tijdslimiet bereikt ({self.max_seconds:.0f}s)")

    @property
    def iterations(self) -> int:
        return self._iterations
