# arena/self_improvement/freeze.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.self_improvement.scan import Finding


@dataclass(frozen=True)
class FreezeDecision:
    """Output of evaluate_freeze. `triggers` is a list of dicts shaped
    for the sentinel's JSON metadata block."""

    frozen: bool
    triggers: list[dict[str, Any]] = field(default_factory=list)


def evaluate_freeze(findings: list[Finding]) -> FreezeDecision:
    """Return frozen=True iff any finding is present.

    Phase-0 policy: ANY finding from scan_runs triggers freeze. PR7+
    may add severity-based filtering. Each trigger dict contains
    kind/severity/problem + evidence_refs."""
    if not findings:
        return FreezeDecision(frozen=False, triggers=[])
    triggers = [
        {
            "kind": f.kind,
            "severity": f.severity,
            "problem": f.problem,
            "evidence_refs": list(f.evidence_refs),
        }
        for f in findings
    ]
    return FreezeDecision(frozen=True, triggers=triggers)


def apply_freeze(
    decision: FreezeDecision,
    *,
    sentinel_path: Path = Path("SELF_IMPROVEMENT_FROZEN.md"),
    competition_slug: str = "",
) -> None:
    """Write the freeze sentinel atomically. Markdown body + fenced JSON
    metadata block.

    No-op if `decision.frozen` is False.
    """
    if not decision.frozen:
        return
    triggered_at = datetime.now(UTC).isoformat(timespec="seconds")
    metadata = {
        "frozen": True,
        "triggered_at": triggered_at,
        "competition_slug": competition_slug,
        "triggers": decision.triggers,
    }
    evidence_lines = []
    for trigger in decision.triggers:
        for ref in trigger["evidence_refs"]:
            evidence_lines.append(f"- {trigger['kind']}: {ref}")

    body = (
        "# Self-Improvement Frozen\n"
        "\n"
        "```json\n"
        f"{json.dumps(metadata, indent=2)}\n"
        "```\n"
        "\n"
        "## Evidence\n"
        "\n"
        f"{chr(10).join(evidence_lines) if evidence_lines else '- (no evidence refs)'}\n"
        "\n"
        "## Unfreeze\n"
        "\n"
        "Human review required. Delete this file after addressing the "
        "triggers above.\n"
    )
    # Atomic write: write to a temp file then rename.
    tmp = sentinel_path.with_suffix(sentinel_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(sentinel_path)


def is_frozen(
    sentinel_path: Path = Path("SELF_IMPROVEMENT_FROZEN.md"),
) -> bool:
    """Return True iff the sentinel file exists. Source of truth."""
    return sentinel_path.exists()
