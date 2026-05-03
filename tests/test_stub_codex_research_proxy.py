# tests/test_stub_codex_research_proxy.py
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from arena.providers.stub_codex import StubCodexProvider


def _proxy_packet(
    *,
    workspace_root: Path,
    fusion_id: str = "fusion_0001",
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0001",
    task_id: str = "task_0001",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "FUSION_PROXY_IMPLEMENTED",
        "objective": (
            f"Implement the smallest proxy test for fusion {fusion_id}. "
            "The packet's inputs[0] is the fusion_proposal.json path."
        ),
        "inputs": [
            f"worktrees/{competition_slug}/{experiment_id}/fusion_proposal.json",
            f"fixtures/{competition_slug}/test.csv",
        ],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
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


@pytest.fixture
def fixture_workspace_with_fusion(fixture_workspace: Path) -> Path:
    """Bootstrap a fixture workspace AND drop a fusion_proposal.json into
    the experiment worktree so stub_codex can read it."""
    workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0001"
    workspace.mkdir(parents=True, exist_ok=True)
    fusion_payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Test fusion",
        "hypothesis": "A 20+ char hypothesis string for the schema.",
        "mechanisms_combined": [
            {"mechanism_name": "a", "source_ref": "r1", "role_in_fusion": "primary."},
            {"mechanism_name": "b", "source_ref": "r2", "role_in_fusion": "secondary."},
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": [],
            "expected_outputs": ["submission.csv"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [{"name": "a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 5,
        },
        "risks": [],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["r1"],
    }
    (workspace / "fusion_proposal.json").write_text(json.dumps(fusion_payload), encoding="utf-8")
    return fixture_workspace


def test_stub_codex_emits_submission_with_fusion_id_artifact(
    fixture_workspace_with_fusion: Path,
) -> None:
    """Phase=FUSION_PROXY_IMPLEMENTED → submission.csv + <fusion_id:fusion_NNNN> token."""
    provider = StubCodexProvider(workspace_root=fixture_workspace_with_fusion / "worktrees")
    packet = _proxy_packet(workspace_root=fixture_workspace_with_fusion)
    result = provider.invoke(packet)
    assert result.status == "success"
    # submission.csv exists and has the calibration shape (id, target).
    submission_path = next(p for p in result.artifacts if p.endswith("submission.csv"))
    df = pd.read_csv(submission_path)
    assert list(df.columns) == ["id", "target"]
    # fusion_id token in artifacts so the scoreboard row links back to the proposal.
    assert any(a.startswith("<fusion_id:fusion_0001>") for a in result.artifacts)


def test_stub_codex_calibration_path_unchanged(fixture_workspace: Path) -> None:
    """Backward compat: existing PR1 calibration packet still emits a
    submission.csv WITHOUT the fusion_id token."""
    provider = StubCodexProvider(workspace_root=fixture_workspace / "worktrees")
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Calibration baseline.",
        "inputs": ["fixtures/tabular_binary_v1/test.csv"],
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
    result = provider.invoke(packet)
    assert result.status == "success"
    assert any(a.endswith("submission.csv") for a in result.artifacts)
    # No fusion_id token on calibration runs.
    assert not any(a.startswith("<fusion_id:") for a in result.artifacts)
