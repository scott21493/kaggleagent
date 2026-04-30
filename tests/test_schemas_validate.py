from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.schemas.validate import validate


def _valid_task_packet() -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": None,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission for the tabular binary fixture.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": ["~/.kaggle/", "~/.codex/"],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid submission CSV with id and target columns"],
    }


def test_validate_passes_on_valid_packet() -> None:
    validate("task_packet", _valid_task_packet())


def test_validate_fails_on_missing_required() -> None:
    bad = _valid_task_packet()
    del bad["objective"]
    with pytest.raises(ValidationError):
        validate("task_packet", bad)


def test_validate_fails_on_unknown_field() -> None:
    bad = _valid_task_packet()
    bad["bonus_field"] = "nope"
    with pytest.raises(ValidationError):
        validate("task_packet", bad)
