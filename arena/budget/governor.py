from __future__ import annotations

from arena.budget.kill_switch import Breaker


class BudgetExceeded(Exception):
    """Raised when a budget cap is exceeded."""

    def __init__(self, breaker: Breaker, message: str) -> None:
        self.breaker = breaker
        super().__init__(message)
