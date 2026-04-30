from __future__ import annotations

from arena.providers.parser import build_result
from arena.schemas.validate import validate


def test_build_result_is_schema_valid() -> None:
    result = build_result(
        task_id="task_0001",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="success",
        stdout_path="traces/run_x/task_0001/stdout.scrubbed",
        stderr_path="traces/run_x/task_0001/stderr.scrubbed",
        artifacts=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        input_chars=120,
        output_chars=80,
        wall_seconds=0.05,
        shell_commands=0,
        failed_commands=0,
        waste_events=0,
        started_at="2026-04-30T10:00:00Z",
        finished_at="2026-04-30T10:00:01Z",
    )
    validate("provider_result", result.to_dict())
