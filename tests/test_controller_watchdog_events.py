# tests/test_controller_watchdog_events.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.budget.governor import BudgetGovernor
from arena.budget.policy import Phase0HardCeilings
from arena.controller.watchdog import Watchdog
from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.runner import (
    SandboxAttempt,
    SandboxAttemptKind,
    SandboxRunner,
    SandboxViolation,
    assert_sandbox_allowed,
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
        "objective": "obj",
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


class _SuccessProvider(ProviderAdapter):
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
                "wall_seconds": 0.0,
                "shell_commands": 0,
                "failed_commands": 0,
                "waste_events": 0,
            },
            started_at=now,
            finished_at=now,
        )


class _SecretReadProvider(_SuccessProvider):
    def invoke(self, task_packet: dict) -> ProviderResult:
        assert_sandbox_allowed(
            SandboxAttempt(
                kind=SandboxAttemptKind.SECRET_READ,
                target=str(Path("~/.kaggle/kaggle.json").expanduser()),
            )
        )
        return super().invoke(task_packet)


def test_wrap_invoke_emits_provider_invoked_and_task_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    store = TraceStore(run_id="run_x", root=tmp_path / "traces")

    result = watchdog.wrap_invoke(_SuccessProvider(), _packet(), event_emitter=store)
    assert result.status == "success"

    log = tmp_path / "traces" / "run_x" / "task_0001" / "events.jsonl"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    types = [e["event_type"] for e in events]
    assert types == ["provider_invoked", "task_finished"]


def test_wrap_invoke_emits_breaker_triggered_on_sandbox_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    sandbox = SandboxRunner(
        SandboxPolicy.from_packet(
            {"allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"], "blocked_paths": []},
            workspace_root=tmp_path,
        )
    )
    store = TraceStore(run_id="run_x", root=tmp_path / "traces")

    with pytest.raises(SandboxViolation):
        watchdog.wrap_invoke(_SecretReadProvider(), _packet(), sandbox=sandbox, event_emitter=store)

    log = tmp_path / "traces" / "run_x" / "task_0001" / "events.jsonl"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    types = [e["event_type"] for e in events]
    assert "breaker_triggered" in types
    breaker_evt = next(e for e in events if e["event_type"] == "breaker_triggered")
    assert breaker_evt["payload"]["breaker"] == "SecretAccessBreaker"
    assert breaker_evt["payload"]["evidence"]  # non-empty


def test_wrap_invoke_emits_no_events_when_event_emitter_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: PR2 callers without an event_emitter must work and
    must NOT create any traces/ directory."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.chdir(tmp_path)
    governor = BudgetGovernor(Phase0HardCeilings())
    watchdog = Watchdog(governor=governor)
    result = watchdog.wrap_invoke(_SuccessProvider(), _packet())
    assert result.status == "success"
    assert not (tmp_path / "traces").exists()
