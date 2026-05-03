# arena/review/packet.py
from __future__ import annotations

from typing import Any

from arena.schemas.validate import validate


def make_review_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    review_id: str,
    subject_experiment_id: str,
    fusion_proposal_path: str,
    submission_path: str,
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude to emit a
    research_review.json reviewing the implementation row identified by
    `subject_experiment_id`.

    `submission_path` is placed at inputs[0] so the stub can extract
    subject_id via _read_subject_id_from_inputs (parses the worktree
    path segment). `fusion_proposal_path` is included so the reviewer
    can reference the originating proposal.

    `run_id` is accepted for forward-compat with Task 5's CLI
    orchestration (the review packet is consumed by the same
    arena run-next-style precheck flow). The packet schema does not
    have a run_id field; it lives at the run record level.
    """
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
            f"fusion {fusion_proposal_path}. Output must satisfy "
            "schemas/research_review.schema.json."
        ),
        "inputs": [submission_path, fusion_proposal_path],
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


def validate_research_review(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/research_review.schema.json.
    Thin wrapper over arena.schemas.validate.validate."""
    validate("research_review", payload)
