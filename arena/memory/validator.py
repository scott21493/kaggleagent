# arena/memory/validator.py
from __future__ import annotations

from typing import Any

_OPS_REQUIRING_PRIOR_CLAIM = {"modify", "deprecate", "remove"}


def check_evidence(proposal: dict[str, Any]) -> list[str]:
    """Run semantic checks on a memory_update proposal beyond what the
    schema enforces.

    Returns a list of issue strings; empty list means valid.

    NOTE: this is a SEMANTIC check only; structural/schema validation
    lives in arena.memory.proposal.validate_memory_update. Callers must
    run BOTH (schema first, then semantic) to fully validate a proposal
    — a proposal can be schema-valid but semantically a no-op (e.g., a
    modify with claim == prior_claim).

    Checks:
    - operation in {modify, deprecate, remove} requires non-empty
      prior_claim.
    - operation=modify must have claim != prior_claim (otherwise it's
      a no-op).
    - evidence list must be non-empty (also a schema constraint;
      double-checked here for clearer error messages).
    """
    issues: list[str] = []
    operation = proposal.get("operation")
    claim = proposal.get("claim", "")
    prior_claim = proposal.get("prior_claim")

    if operation in _OPS_REQUIRING_PRIOR_CLAIM:
        if not prior_claim:
            issues.append(f"operation={operation!r} requires a non-empty prior_claim")
        elif operation == "modify" and prior_claim == claim:
            issues.append(
                "operation=modify with identical claim and prior_claim is a "
                "no-op; modify must change the claim"
            )

    evidence = proposal.get("evidence") or []
    if not evidence:
        issues.append("evidence array must be non-empty")

    return issues
