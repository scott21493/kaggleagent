from __future__ import annotations

from dataclasses import dataclass

from arena.budget.kill_switch import Breaker
from arena.budget.policy import Phase0HardCeilings


@dataclass
class TaskWasteCounters:
    """Per-task accumulators tracked across provider events.

    PR2 ships the data shape and the observer/check methods; the live
    event observer that calls observe_failed_command lands in PR4 with
    the trace store. Unit tests exercise the methods in isolation.
    """

    failed_commands: int = 0
    repeated_same_failure: int = 0
    waste_events: int = 0
    last_failed_command: str | None = None


class WasteDetector:
    """Tracks repeated-same-failure per task and enforces per-task waste caps."""

    def __init__(self, ceilings: Phase0HardCeilings) -> None:
        self._ceilings = ceilings

    def observe_failed_command(self, state: TaskWasteCounters, command: str) -> None:
        """Record a failed shell command. If it matches the previous failure,
        bump repeated_same_failure; otherwise reset that counter."""
        state.failed_commands += 1
        if state.last_failed_command == command:
            state.repeated_same_failure += 1
        else:
            state.repeated_same_failure = 0
            state.last_failed_command = command

    def check_task_caps(self, state: TaskWasteCounters, *, task_id: str) -> None:
        """Raise BudgetExceeded if any per-task waste cap is exceeded."""
        # Local import avoids circular dep between waste.py and governor.py.
        from arena.budget.governor import BudgetExceeded

        if state.repeated_same_failure > self._ceilings.repeated_same_failure_per_task:
            raise BudgetExceeded(
                Breaker.REPEATED_FAILURE,
                f"task {task_id} repeated the same failed command "
                f"{state.repeated_same_failure} times "
                f"(ceiling {self._ceilings.repeated_same_failure_per_task})",
            )
        if state.failed_commands > self._ceilings.failed_commands_per_task:
            raise BudgetExceeded(
                Breaker.REPEATED_FAILURE,
                f"task {task_id} accumulated {state.failed_commands} failed "
                f"commands (ceiling {self._ceilings.failed_commands_per_task})",
            )
        if state.waste_events > self._ceilings.waste_events_per_task:
            raise BudgetExceeded(
                Breaker.WASTE_EVENT,
                f"task {task_id} emitted {state.waste_events} waste events "
                f"(ceiling {self._ceilings.waste_events_per_task})",
            )
