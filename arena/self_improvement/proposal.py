# arena/self_improvement/proposal.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from arena.schemas.validate import validate
from arena.self_improvement.scan import Finding

_SIP_ID_RE = re.compile(r"^sip_(\d+)\.json$")


def make_self_improvement_proposal(finding: Finding, *, proposal_id: str) -> dict[str, Any]:
    """Build a schema-valid self_improvement_proposal.v1 from a Finding.

    Phase-0 stub: requires_human_approval is always True;
    protected_files_touched is empty (the proposal observes; PR7+ may
    propose code changes). The static prose templates (proposed_change,
    tests_to_add, rollback_plan, champion_challenger_plan) are
    intentionally generic; PR7+ may template them per finding.kind via
    a registry once we have richer signal types.
    """
    # Identity map today; kept as a dict so PR7 can decouple
    # finding.severity from proposal.risk_level (e.g., dampen "high"
    # findings to "medium" risk if the pattern is well-understood).
    risk_level_map = {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }
    risk_level = risk_level_map.get(finding.severity, "medium")
    return {
        "schema_version": "self_improvement_proposal.v1",
        "proposal_id": proposal_id,
        "problem": finding.problem,
        "evidence_refs": list(finding.evidence_refs) or [f"finding:{finding.kind}"],
        "proposed_change": (
            f"Investigate {finding.kind} surfaced by self-improvement scan; "
            "add a regression test in tests/ and a corresponding fix only "
            "after human review."
        ),
        "risk_level": risk_level,
        "protected_files_touched": [],
        "tests_to_add": [f"tests/test_regression_{finding.kind}.py - pin the failure mode"],
        "rollback_plan": (
            "Revert the offending commit; the scan + sentinel keep the "
            "system frozen until a human-approved fix lands."
        ),
        "champion_challenger_plan": (
            "Compare ROC-AUC + wall_seconds + provider_calls between the "
            "champion (PR1 calibration) and the proposed challenger fix on "
            "the tabular_binary_v1 fixture. Reject if any regression."
        ),
        "requires_human_approval": True,
    }


def get_next_sip_id(
    proposals_dir: Path = Path("self_improvement/proposals"),
) -> str:
    """Mint the next sip_NNNN id by scanning `proposals_dir`. Returns
    sip_0001 for empty/missing directory."""
    if not proposals_dir.exists():
        return "sip_0001"
    max_n = 0
    for entry in proposals_dir.iterdir():
        m = _SIP_ID_RE.match(entry.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"sip_{max_n + 1:04d}"


def validate_self_improvement_proposal(payload: dict[str, Any]) -> None:
    """Validate against schemas/self_improvement_proposal.schema.json."""
    validate("self_improvement_proposal", payload)
