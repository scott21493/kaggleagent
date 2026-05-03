# arena/research_proxy/method_digest.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from arena.schemas.validate import validate


def read_method_note(path: str | Path) -> str:
    """Read the contents of a local method note file.

    Phase 0 method notes are trusted fixture inputs at
    fixtures/<slug>/paper_bundle/method_note_NNN.md. Returns the raw text.
    Caller is responsible for passing it as `inputs` in the task packet
    so the sandbox sees it as a readable input.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"missing method note: {p}")
    return p.read_text(encoding="utf-8")


def make_method_digest_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    digest_id: str,
    method_note_path: str,
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude to digest one local
    method note into a paper_digest.json artifact.

    The method note path is included in `inputs` so the sandbox treats
    it as a readable input. The output (paper_digest.json) lands under
    the experiment's worktree.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": "METHOD_DIGEST_CREATED",
        "objective": (
            f"Read the method note at {method_note_path} and produce a "
            "paper_digest.json that satisfies "
            "schemas/paper_digest.schema.json. Set source_type to "
            "local_method_note and trusted_status to trusted_fixture."
        ),
        "inputs": [method_note_path],
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
        "required_outputs": ["paper_digest.json"],
        "success_criteria": ["valid_schema"],
    }


def validate_paper_digest(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/paper_digest.schema.json. Raises
    jsonschema.ValidationError on any failure. Thin wrapper for caller
    convenience; equivalent to `validate("paper_digest", payload)`."""
    validate("paper_digest", payload)
