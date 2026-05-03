# tests/test_stub_claude_research_proxy.py
from __future__ import annotations

import json
from pathlib import Path

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _research_packet(
    *,
    phase: str,
    inputs: list[str] | None = None,
    workspace_root: Path,
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0001",
    task_id: str = "task_0001",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": phase,
        "objective": ("Generate a research-proxy artifact for the Phase 0 stub harness."),
        "inputs": inputs or ["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
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
        "required_outputs": ["research_question.json"],
        "success_criteria": ["valid"],
    }


def test_stub_claude_emits_research_question_json(tmp_path: Path) -> None:
    """phase=RESEARCH_QUESTION_CREATED → research_question.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(phase="RESEARCH_QUESTION_CREATED", workspace_root=tmp_path)
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    rq_path = next(p for p in artifact_paths if p.name == "research_question.json")
    assert rq_path.exists()
    payload = json.loads(rq_path.read_text(encoding="utf-8"))
    validate("research_question", payload)  # no raise = schema-valid
    assert payload["competition_slug"] == "tabular_binary_v1"
    assert payload["question_id"].startswith("rq_")


def test_stub_claude_emits_paper_digest_json(tmp_path: Path) -> None:
    """phase=METHOD_DIGEST_CREATED → paper_digest.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(
        phase="METHOD_DIGEST_CREATED",
        inputs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
        workspace_root=tmp_path,
    )
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    pd_path = next(p for p in artifact_paths if p.name == "paper_digest.json")
    assert pd_path.exists()
    payload = json.loads(pd_path.read_text(encoding="utf-8"))
    validate("paper_digest", payload)
    assert payload["digest_id"].startswith("pd_")
    assert payload["source_type"] == "local_method_note"
    assert payload["trusted_status"] == "trusted_fixture"
    assert len(payload["mechanisms"]) >= 1


def test_stub_claude_emits_fusion_proposal_json(tmp_path: Path) -> None:
    """phase=FUSION_PROPOSAL_CREATED → fusion_proposal.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(
        phase="FUSION_PROPOSAL_CREATED",
        inputs=[
            "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
        ],
        workspace_root=tmp_path,
    )
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    fp_path = next(p for p in artifact_paths if p.name == "fusion_proposal.json")
    assert fp_path.exists()
    payload = json.loads(fp_path.read_text(encoding="utf-8"))
    validate("fusion_proposal", payload)
    assert payload["fusion_id"].startswith("fusion_")
    # 2+ mechanisms is a fusion-proposal schema requirement; verify ours satisfies it.
    assert len(payload["mechanisms_combined"]) >= 2
    assert "smallest_proxy_test" in payload
    assert "ablation_plan" in payload
    assert len(payload["ablation_plan"]) >= 1
    assert "resource_estimate" in payload


def test_stub_claude_calibration_path_unchanged(tmp_path: Path) -> None:
    """Backward compat: non-research-proxy roles still produce the empty-payload result."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    # Reuse the calibration-style packet from PR1 (role=implementation, phase=CALIBRATION_TASK_CREATED).
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
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
    result = provider.invoke(packet)
    assert result.status == "success"
    # Calibration path: no artifacts (PR1 baseline behavior).
    assert result.artifacts == []
