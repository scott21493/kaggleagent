from __future__ import annotations

import pytest

from arena.budget.governor import BudgetExceeded, BudgetGovernor, RunAccumulators
from arena.budget.kill_switch import Breaker
from arena.budget.policy import Phase0HardCeilings


def _usage(**overrides: object) -> dict:
    base = {
        "input_chars": 0,
        "output_chars": 0,
        "wall_seconds": 0.0,
        "shell_commands": 0,
        "failed_commands": 0,
        "waste_events": 0,
    }
    base.update(overrides)
    return base


def test_budget_exceeded_carries_breaker_and_message() -> None:
    err = BudgetExceeded(Breaker.PROVIDER_CALL, "too many calls")
    assert err.breaker is Breaker.PROVIDER_CALL
    assert "too many calls" in str(err)


def test_pre_invoke_passes_under_total_cap() -> None:
    g = BudgetGovernor(Phase0HardCeilings())
    g.check_pre_invoke("stub_codex")  # first call, must not raise


def test_pre_invoke_raises_on_thirteenth_call() -> None:
    g = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(provider_calls=12),
    )
    with pytest.raises(BudgetExceeded) as exc:
        g.check_pre_invoke("stub_codex")
    assert exc.value.breaker is Breaker.PROVIDER_CALL


def test_pre_invoke_raises_on_seventh_codex_call() -> None:
    g = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(provider_calls=6, codex_calls=6),
    )
    with pytest.raises(BudgetExceeded) as exc:
        g.check_pre_invoke("stub_codex")
    assert exc.value.breaker is Breaker.PROVIDER_CALL


def test_pre_invoke_raises_on_seventh_claude_call() -> None:
    g = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(provider_calls=6, claude_calls=6),
    )
    with pytest.raises(BudgetExceeded) as exc:
        g.check_pre_invoke("stub_claude")
    assert exc.value.breaker is Breaker.PROVIDER_CALL


def test_record_post_invoke_updates_accumulators_under_caps() -> None:
    g = BudgetGovernor(Phase0HardCeilings())
    g.check_pre_invoke("stub_codex")
    g.record_post_invoke(
        "stub_codex",
        _usage(input_chars=100, output_chars=50, wall_seconds=0.5, shell_commands=2),
        task_id="task_0001",
    )
    assert g.accumulators.provider_calls == 1
    assert g.accumulators.codex_calls == 1
    assert g.accumulators.input_chars == 100
    assert g.accumulators.output_chars == 50
    assert g.accumulators.wall_seconds == pytest.approx(0.5)


def test_record_post_invoke_raises_on_shell_command_breaker() -> None:
    g = BudgetGovernor(Phase0HardCeilings())  # shell_commands_per_task=35
    g.check_pre_invoke("stub_codex")
    with pytest.raises(BudgetExceeded) as exc:
        g.record_post_invoke(
            "stub_codex",
            _usage(shell_commands=100),
            task_id="task_0001",
        )
    assert exc.value.breaker is Breaker.SHELL_COMMAND
    assert "task_0001" in str(exc.value)


def test_record_post_invoke_raises_on_wall_clock_breaker_per_call() -> None:
    g = BudgetGovernor(Phase0HardCeilings())  # per_call=20 minutes = 1200s
    g.check_pre_invoke("stub_codex")
    with pytest.raises(BudgetExceeded) as exc:
        g.record_post_invoke(
            "stub_codex",
            _usage(wall_seconds=1500.0),  # 25 minutes > 20-minute per-call cap
            task_id="task_0001",
        )
    assert exc.value.breaker is Breaker.WALL_CLOCK


def test_record_post_invoke_raises_on_input_chars_total() -> None:
    g = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(input_chars=850_000),
    )
    g.check_pre_invoke("stub_codex")
    with pytest.raises(BudgetExceeded) as exc:
        g.record_post_invoke(
            "stub_codex",
            _usage(input_chars=100_000),  # 850k + 100k > 900k total
            task_id="task_0001",
        )
    # PR2 maps total-chars overflow to PROVIDER_CALL since there is no
    # dedicated input-chars breaker among the 5 PR2 owns. PR4 may
    # introduce a dedicated breaker.
    assert exc.value.breaker is Breaker.PROVIDER_CALL


def test_waste_detector_check_task_caps_raises_on_repeated_failure_breaker() -> None:
    """Cap-raising test for WasteDetector — moved here from Task 3 so
    Task 3 can commit independently. WasteDetector.check_task_caps raises
    BudgetExceeded(REPEATED_FAILURE) when the per-task counter exceeds
    repeated_same_failure_per_task (default 2)."""
    from arena.budget.waste import TaskWasteCounters, WasteDetector

    detector = WasteDetector(Phase0HardCeilings())
    state = TaskWasteCounters(failed_commands=5, repeated_same_failure=3)
    with pytest.raises(BudgetExceeded) as exc:
        detector.check_task_caps(state, task_id="task_0001")
    assert exc.value.breaker is Breaker.REPEATED_FAILURE
    assert "task_0001" in str(exc.value)


def test_status_snapshot_reports_accumulators_and_ceilings() -> None:
    g = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(
            provider_calls=3, codex_calls=2, claude_calls=1, input_chars=1000
        ),
    )
    snap = g.status()
    assert snap["provider_calls"] == {"used": 3, "ceiling": 12}
    assert snap["codex_calls"] == {"used": 2, "ceiling": 6}
    assert snap["claude_calls"] == {"used": 1, "ceiling": 6}
    assert snap["input_chars"] == {"used": 1000, "ceiling": 900_000}
