from __future__ import annotations

from pathlib import Path

import pytest

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _packet() -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": "task_0002",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
        "role": "review",
        "phase": "CALIBRATION_REVIEWED",
        "objective": "Review the calibration submission for validity and basic quality.",
        "inputs": ["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        "allowed_paths": [],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 0,
            "max_failed_commands": 0,
            "max_input_chars": 10000,
            "max_output_chars": 5000,
        },
        "required_outputs": ["review.json"],
        "success_criteria": ["valid review.json"],
    }


def test_invoke_returns_schema_valid_result(tmp_path: Path) -> None:
    provider = StubClaudeProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(_packet())
    validate("provider_result", result.to_dict())
    assert result.status == "success"


def test_invoke_writes_real_empty_trace_files(tmp_path: Path) -> None:
    provider = StubClaudeProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(_packet())
    stdout = Path(result.stdout_path)
    stderr = Path(result.stderr_path)
    assert stdout.exists() and stdout.read_text() == ""
    assert stderr.exists() and stderr.read_text() == ""


def test_invoke_rejects_missing_experiment_id(tmp_path: Path) -> None:
    """invoke() requires experiment_id to be set, even though the schema
    permits null. Hand-built packets that skip exp_id must fail loudly.
    Mirrors the equivalent test on StubCodexProvider."""
    packet = _packet()
    packet["experiment_id"] = None  # schema-valid but invalid for this provider

    provider = StubClaudeProvider(workspace_root=tmp_path / "worktrees")
    with pytest.raises(ValueError, match="experiment_id"):
        provider.invoke(packet)
