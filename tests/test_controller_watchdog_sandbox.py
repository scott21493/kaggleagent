from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.budget.governor import BudgetGovernor
from arena.budget.kill_switch import Breaker
from arena.budget.policy import Phase0HardCeilings
from arena.controller.watchdog import Watchdog
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.runner import (
    SandboxAttempt,
    SandboxAttemptKind,
    SandboxRunner,
    SandboxViolation,
    assert_sandbox_allowed,
    get_active_sandbox,
)


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


class _ObservingProvider(ProviderAdapter):
    """Provider that records whether a sandbox was active during invoke."""

    def __init__(self) -> None:
        self.observed_sandbox: SandboxRunner | None = None

    @property
    def name(self) -> str:
        return "stub_codex"

    @property
    def version(self) -> str:
        return "stub_codex.v1"

    def invoke(self, task_packet: dict) -> ProviderResult:
        self.observed_sandbox = get_active_sandbox()
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
                "wall_seconds": 0.0,
                "shell_commands": 0,
                "failed_commands": 0,
                "waste_events": 0,
            },
            started_at=now,
            finished_at=now,
        )


class _MisbehavingSecretReadProvider(_ObservingProvider):
    """Provider that calls assert_sandbox_allowed with a forbidden secret path
    inside its invoke. Used to verify that SandboxViolation propagates through
    Watchdog.wrap_invoke and that the active sandbox is deactivated even when
    invoke raises."""

    def invoke(self, task_packet: dict) -> ProviderResult:
        # Provider attempts a secret read.
        assert_sandbox_allowed(
            SandboxAttempt(
                kind=SandboxAttemptKind.SECRET_READ,
                target=str(Path("~/.kaggle/kaggle.json").expanduser()),
            )
        )
        # assert_sandbox_allowed raises SandboxViolation when a sandbox is
        # active — intentionally unreachable in tests that activate one.
        return super().invoke(task_packet)


def _policy(tmp_path: Path) -> SandboxPolicy:
    """Packet-scoped policy aligned with `_packet()` allowed_paths."""
    return SandboxPolicy.from_packet(
        {"allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"]},
        workspace_root=tmp_path,
    )


def test_wrap_invoke_activates_sandbox_during_invoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    sandbox = SandboxRunner(_policy(tmp_path))
    watchdog = Watchdog(governor=governor)
    provider = _ObservingProvider()
    result = watchdog.wrap_invoke(provider, _packet(), sandbox=sandbox)
    assert result.status == "success"
    # Sandbox was active during invoke...
    assert provider.observed_sandbox is sandbox
    # ...and is deactivated afterwards.
    assert get_active_sandbox() is None


def test_wrap_invoke_deactivates_sandbox_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    sandbox = SandboxRunner(_policy(tmp_path))
    watchdog = Watchdog(governor=governor)
    provider = _MisbehavingSecretReadProvider()
    with pytest.raises(SandboxViolation) as exc:
        watchdog.wrap_invoke(provider, _packet(), sandbox=sandbox)
    assert exc.value.breaker is Breaker.SECRET_ACCESS
    # Sandbox is deactivated even though invoke raised.
    assert get_active_sandbox() is None


def test_wrap_invoke_with_no_sandbox_works_for_backward_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR2 callers that don't pass a sandbox must continue to work."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)  # no sandbox kwarg either
    provider = _ObservingProvider()
    result = watchdog.wrap_invoke(provider, _packet())  # no sandbox kwarg
    assert result.status == "success"
    # No sandbox was active — provider observed None.
    assert provider.observed_sandbox is None
