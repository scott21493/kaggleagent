from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import ValidationError

from arena.controller.task_queue import TaskQueue


def _packet(task_id: str = "task_0001") -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": "tabular_binary_v1",
        "experiment_id": None,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": ["~/.kaggle/"],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid submission CSV"],
    }


def test_enqueue_then_dequeue(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue")
    queue.enqueue(_packet("task_0001"))
    queue.enqueue(_packet("task_0002"))
    assert queue.size() == 2
    first = queue.dequeue()
    second = queue.dequeue()
    assert first is not None and first["task_id"] == "task_0001"
    assert second is not None and second["task_id"] == "task_0002"
    assert queue.size() == 0


def test_enqueue_validates_packet(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue")
    bad = _packet()
    del bad["objective"]
    with pytest.raises(ValidationError):
        queue.enqueue(bad)


def test_queue_persists_across_instances(tmp_path: Path) -> None:
    qdir = tmp_path / "queue"
    TaskQueue(qdir).enqueue(_packet("task_0001"))
    assert TaskQueue(qdir).size() == 1
