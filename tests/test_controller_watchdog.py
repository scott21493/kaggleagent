from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.budget.governor import BudgetExceeded, BudgetGovernor, RunAccumulators
from arena.budget.kill_switch import KILL_SWITCH_ENV, Breaker, KillSwitch
from arena.budget.policy import Phase0HardCeilings
from arena.controller.watchdog import KillSwitchActive, Watchdog
from arena.providers.base import ProviderAdapter, ProviderResult


def _packet() -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission for the fixture.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid"],
    }


class _MockProvider(ProviderAdapter):
    """Test-only provider that emits a configurable usage_proxy."""

    def __init__(self, *, shell_commands: int = 0, wall_seconds: float = 0.0) -> None:
        self._shell_commands = shell_commands
        self._wall_seconds = wall_seconds

    @property
    def name(self) -> str:
        return "stub_codex"

    @property
    def version(self) -> str:
        return "stub_codex.v1"

    def invoke(self, task_packet: dict) -> ProviderResult:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        return ProviderResult(
            task_id=task_packet["task_id"],
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path="<test>",
            stderr_path="<test>",
            artifacts=[],
            usage_proxy={
                "input_chars": 0,
                "output_chars": 0,
                "wall_seconds": self._wall_seconds,
                "shell_commands": self._shell_commands,
                "failed_commands": 0,
                "waste_events": 0,
            },
            started_at=now,
            finished_at=now,
        )


def test_check_can_invoke_passes_when_no_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    watchdog.check_can_invoke("stub_codex")  # must not raise


def test_check_can_invoke_raises_when_kill_switch_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    KillSwitch.activate()
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    with pytest.raises(KillSwitchActive):
        watchdog.check_can_invoke("stub_codex")
    assert governor.accumulators.provider_calls == 0


def test_check_can_invoke_raises_when_kill_switch_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    with pytest.raises(KillSwitchActive):
        watchdog.check_can_invoke("stub_codex")


def test_check_can_invoke_blocks_thirteenth_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(
        Phase0HardCeilings(),
        accumulators=RunAccumulators(provider_calls=12),
    )
    watchdog = Watchdog(governor=governor)
    with pytest.raises(BudgetExceeded) as exc:
        watchdog.check_can_invoke("stub_codex")
    assert exc.value.breaker is Breaker.PROVIDER_CALL


def test_wrap_invoke_returns_result_under_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    # Caller is responsible for calling check_can_invoke first; test just
    # exercises wrap_invoke directly.
    result = watchdog.wrap_invoke(_MockProvider(shell_commands=2), _packet())
    assert result.status == "success"
    assert governor.accumulators.provider_calls == 1


def test_wrap_invoke_raises_shell_command_breaker_on_misbehaving_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion: a misbehaving stub provider that emits 100 shell
    command events trips ShellCommandBreaker (post-invoke check)."""
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    with pytest.raises(BudgetExceeded) as exc:
        watchdog.wrap_invoke(_MockProvider(shell_commands=100), _packet())
    assert exc.value.breaker is Breaker.SHELL_COMMAND
