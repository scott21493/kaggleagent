# arena/research_proxy/fusion_proposal.py
from __future__ import annotations

from typing import Any

from arena.schemas.validate import validate


def make_fusion_proposal_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    fusion_id: str,
    digest_path: str,
    provider: str = "stub_claude",
) -> dict[str, Any]:
    """Build the task_packet that asks the configured Claude provider
    (stub_claude or real claude) to propose a method fusion grounded
    in a previously-emitted paper_digest. See question_generator.py
    for the rationale behind the `provider` parameter.

    The digest path is included in `inputs` so the sandbox treats it
    as a readable input (it lives under the experiment's own worktree
    after the previous step's Claude invocation). The output
    (fusion_proposal.json) lands alongside it.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": provider,
        "role": "research_proxy",
        "phase": "FUSION_PROPOSAL_CREATED",
        "objective": (
            f"Read the digest at {digest_path} and propose a method "
            "fusion combining at least two mechanisms. The output must "
            "satisfy schemas/fusion_proposal.schema.json including the "
            "§6.3 eligibility checklist (2+ mechanisms_combined, "
            "smallest_proxy_test, ablation_plan, resource_estimate, "
            "risks, stop_condition, source_refs)."
        ),
        "inputs": [digest_path],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 10,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["fusion_proposal.json"],
        "success_criteria": ["valid_schema", "two_or_more_mechanisms"],
    }


def validate_fusion_proposal(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/fusion_proposal.schema.json.
    Raises jsonschema.ValidationError on any failure. Thin wrapper over
    arena.schemas.validate."""
    validate("fusion_proposal", payload)
