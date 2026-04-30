from __future__ import annotations

from arena.budget.policy import Phase0HardCeilings
from arena.budget.waste import TaskWasteCounters, WasteDetector


def test_observe_failed_command_increments_count() -> None:
    detector = WasteDetector(Phase0HardCeilings())
    state = TaskWasteCounters()
    detector.observe_failed_command(state, "git status")
    assert state.failed_commands == 1
    assert state.last_failed_command == "git status"


def test_observe_repeated_same_failure() -> None:
    detector = WasteDetector(Phase0HardCeilings())
    state = TaskWasteCounters()
    detector.observe_failed_command(state, "git status")
    detector.observe_failed_command(state, "git status")
    detector.observe_failed_command(state, "git status")
    # First call sets last_failed_command; subsequent identical calls increment.
    assert state.failed_commands == 3
    assert state.repeated_same_failure == 2


def test_different_failed_commands_reset_repeat_counter() -> None:
    detector = WasteDetector(Phase0HardCeilings())
    state = TaskWasteCounters()
    detector.observe_failed_command(state, "git status")
    detector.observe_failed_command(state, "git status")
    detector.observe_failed_command(state, "git diff")  # different — repeat counter resets
    assert state.repeated_same_failure == 0
    assert state.last_failed_command == "git diff"


def test_check_task_caps_passes_under_ceiling() -> None:
    detector = WasteDetector(Phase0HardCeilings())  # repeated_same_failure_per_task=2
    state = TaskWasteCounters(failed_commands=1, repeated_same_failure=1)
    detector.check_task_caps(state, task_id="task_0001")  # 1 <= 2, no raise
