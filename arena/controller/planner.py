from __future__ import annotations


def create_calibration_task_packet(
    competition_slug: str,
    task_id: str,
    experiment_id: str,
    provider: str,
) -> dict:
    """Return a deterministic schema-valid calibration task packet.

    The packet asks the implementation provider to produce a valid submission
    file for the given fixture. Budgets are scoped to per-task Phase 0 ceilings.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": provider,
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": (
            "Produce a calibration baseline submission for the "
            f"{competition_slug} fixture: predict target probabilities for every "
            "row in test.csv."
        ),
        "inputs": [
            f"fixtures/{competition_slug}/train.csv",
            f"fixtures/{competition_slug}/test.csv",
            f"fixtures/{competition_slug}/sample_submission.csv",
            f"fixtures/{competition_slug}/competition.yaml",
            f"fixtures/{competition_slug}/rules.md",
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
        "success_criteria": [
            "submission.csv has columns id,target",
            "all target values are in [0, 1]",
            "row count matches test.csv",
        ],
    }
