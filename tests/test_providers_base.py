from __future__ import annotations

import pytest

from arena.providers.base import ProviderAdapter, ProviderResult


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ProviderAdapter()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated() -> None:
    class _Echo(ProviderAdapter):
        @property
        def name(self) -> str:
            return "echo"

        @property
        def version(self) -> str:
            return "echo.v1"

        def invoke(self, task_packet: dict) -> ProviderResult:
            raise NotImplementedError

    _Echo()


def test_provider_result_dataclass_has_required_fields() -> None:
    result = ProviderResult(
        task_id="task_0001",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="success",
        stdout_path="traces/run_x/task_0001/stdout.scrubbed",
        stderr_path="traces/run_x/task_0001/stderr.scrubbed",
        artifacts=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        usage_proxy={
            "input_chars": 0,
            "output_chars": 0,
            "wall_seconds": 0.0,
            "shell_commands": 0,
            "failed_commands": 0,
            "waste_events": 0,
        },
        started_at="2026-04-30T10:00:00Z",
        finished_at="2026-04-30T10:00:01Z",
    )
    assert result.status == "success"
