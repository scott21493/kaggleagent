from __future__ import annotations

from arena.controller.planner import create_calibration_task_packet
from arena.schemas.validate import validate


def test_calibration_task_packet_is_schema_valid() -> None:
    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    validate("task_packet", packet)


def test_calibration_task_packet_has_role_and_phase() -> None:
    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    assert packet["role"] == "implementation"
    assert packet["phase"] == "CALIBRATION_TASK_CREATED"
    assert "submission.csv" in packet["required_outputs"]


def test_calibration_task_packet_is_deterministic() -> None:
    a = create_calibration_task_packet("tabular_binary_v1", "task_0001", "exp_0001", "stub_codex")
    b = create_calibration_task_packet("tabular_binary_v1", "task_0001", "exp_0001", "stub_codex")
    assert a == b
