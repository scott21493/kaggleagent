# tests/test_stub_claude_review.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _review_packet(
    *,
    workspace_root: Path,
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0006",
    task_id: str = "task_0006",
    subject_experiment_id: str = "exp_0004",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "review",
        "phase": "FUSION_PROXY_REVIEWED",
        "objective": (
            f"Review proxy implementation {subject_experiment_id} against "
            "the originating fusion_proposal.json. Output must satisfy "
            "schemas/research_review.schema.json."
        ),
        "inputs": [
            f"worktrees/{competition_slug}/{subject_experiment_id}/submission.csv",
            f"worktrees/{competition_slug}/exp_0003/fusion_proposal.json",
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
            "max_wall_minutes": 5,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["research_review.json"],
        "success_criteria": ["valid_schema"],
    }


def test_stub_claude_emits_research_review_json(tmp_path: Path) -> None:
    """phase=FUSION_PROXY_REVIEWED → research_review.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    rr_path = next(p for p in artifact_paths if p.name == "research_review.json")
    assert rr_path.exists()
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    validate("research_review", payload)


def test_stub_claude_review_extracts_subject_id_from_inputs(tmp_path: Path) -> None:
    """subject_id is parsed from inputs[0]'s worktree path segment.

    Mirrors stub_codex._read_fusion_id_from_inputs: the stub does not
    invent identity — it reads it from the packet the CLI hands it.
    """
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(
        workspace_root=tmp_path,
        subject_experiment_id="exp_0004",
    )
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["subject_id"] == "exp_0004"


def test_stub_claude_review_default_decision_is_accept(tmp_path: Path) -> None:
    """Default deterministic stub verdict is decision=accept, risk=low,
    required_fixes=[]. Tests that need other decisions monkey-patch the
    module-level _RESEARCH_REVIEW_DEFAULT_* constants."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "accept"
    assert payload["risk_level"] == "low"
    assert payload["required_fixes"] == []


def test_stub_claude_review_decision_can_be_overridden_via_module_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkey-patching the module-level default constant changes the
    stub's verdict — same pattern PR5 uses for MIN_FUSION_SCORE."""
    monkeypatch.setattr("arena.providers.stub_claude._RESEARCH_REVIEW_DEFAULT_DECISION", "revise")
    monkeypatch.setattr(
        "arena.providers.stub_claude._RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES",
        ["Add a baseline ablation comparing GBDT-only vs the full ensemble."],
    )
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "revise"
    assert len(payload["required_fixes"]) == 1


def test_stub_claude_calibration_path_unchanged(tmp_path: Path) -> None:
    """Backward compat with PR1: the calibration packet (role=
    implementation, phase=CALIBRATION_TASK_CREATED) still produces the
    empty-payload result."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Calibration baseline.",
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
    result = provider.invoke(packet)
    assert result.status == "success"
    assert result.artifacts == []
