# arena/research_proxy/question_generator.py
from __future__ import annotations

from typing import Any


def generate_research_question(
    *,
    competition_slug: str,
    question_id: str,
    source_refs: list[str],
) -> dict[str, Any]:
    """Build a deterministic schema-valid research_question payload.

    Phase 0 stub: returns a fixed question keyed to the tabular_binary_v1
    fixture's two method notes. Real Claude adapters can replace this
    deterministic builder in production runs.
    """
    return {
        "schema_version": "research_question.v1",
        "question_id": question_id,
        "competition_slug": competition_slug,
        "question": (
            "Does combining a monotonic GBDT with a stacked logistic-regression "
            "meta-learner reduce CV ROC-AUC variance on the small "
            f"{competition_slug} fixture compared to a free-form GBDT baseline?"
        ),
        "motivation": (
            "The fixture is small (50 train rows) so variance dominates. "
            "Method note 001 argues monotonic constraints reduce variance; "
            "method note 002 argues stacked diverse base learners reduce bias. "
            "Combining both should outperform either alone."
        ),
        "expected_mechanisms": [
            "monotonic gradient-boosted decision trees",
            "stacked logistic-regression meta-learner",
        ],
        "expected_cost": "small",
        "risk": "low",
        "smallest_test": (
            "5-fold CV on train.csv comparing baseline GBDT vs monotonic-GBDT "
            "+ stacked-LR ensemble; report ROC-AUC mean + std."
        ),
        "stop_condition": (
            "Stop if ensemble CV mean is below baseline by more than 0.01 OR "
            "training wall time exceeds 5 minutes per fold."
        ),
        "source_refs": list(source_refs),
    }


def make_research_question_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    question_id: str,
    source_refs: list[str],
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude (or real Claude in production)
    to emit a research_question.json artifact for `competition_slug`.

    The source_refs become the packet's `inputs` so the sandbox sees the
    method notes as readable. The packet's allowed_paths is the experiment's
    own worktree.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": "RESEARCH_QUESTION_CREATED",
        "objective": (
            f"Generate a research question for {competition_slug} "
            "based on the listed method-note source refs. The output "
            "must satisfy schemas/research_question.schema.json."
        ),
        "inputs": list(source_refs),
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
        "required_outputs": ["research_question.json"],
        "success_criteria": ["valid_schema"],
    }
