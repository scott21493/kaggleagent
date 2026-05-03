# arena/memory/proposal.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from arena.schemas.validate import validate

_PROPOSAL_ID_RE = re.compile(r"^mem_(\d+)\.json$")
# Indirection so PR7+ can decouple confidence from risk if needed
# (e.g., risk=high → confidence=low because we're less certain about
# high-risk claims). For Phase 0 this is the identity mapping.
_CONFIDENCE_BY_RISK = {"low": "low", "medium": "medium", "high": "high"}


def synthesize_memory_proposal(
    review_payload: dict[str, Any],
    *,
    proposal_id: str,
    namespace: str = "research",
) -> dict[str, Any]:
    """Build a deterministic schema-valid memory_update.v1 payload from
    a research_review.json review payload.

    If the review has actionable content (required_fixes non-empty or
    follow_up_recommendations non-empty), build an `add` op claiming
    the first actionable item — `required_fixes[0]` if non-empty,
    otherwise `follow_up_recommendations[0]`. Otherwise emit a no-op
    observation that's still schema-valid (audit trail).

    Phase 0 always emits `operation="add"` — the synthesizer never
    produces modify/deprecate/remove. Those operations are reserved
    for human-authored proposals (or PR7+ flows) and would round-trip
    through the same schema + check_evidence semantic checks.

    Phase 0: namespace is always "research" (PR6 reviews are
    research-proxy outputs). PR7+ may derive from review subject type.
    """
    review_id = review_payload["review_id"]
    summary = review_payload["summary"]
    risk_level = review_payload.get("risk_level", "low")
    fixes: list[str] = review_payload.get("required_fixes") or []
    recs: list[str] = review_payload.get("follow_up_recommendations") or []

    actionable = fixes[0] if fixes else (recs[0] if recs else None)
    if actionable is not None:
        claim = actionable
        delta = f"Add this constraint to the {namespace} namespace based on review {review_id}."
        confidence = _CONFIDENCE_BY_RISK.get(risk_level, "medium")
        risk = risk_level
    else:
        claim = f"No actionable findings from review {review_id}."
        delta = "No-op observation; review accepted with no required changes."
        confidence = "low"
        risk = "low"

    return {
        "schema_version": "memory_update.v1",
        "proposal_id": proposal_id,
        "namespace": namespace,
        "operation": "add",
        "claim": claim,
        "delta": delta,
        "evidence": [
            {
                "type": "trace",
                "ref": review_id,
                "quote_or_summary": summary,
            }
        ],
        "confidence": confidence,
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": risk,
        "review_status": "proposed",
    }


def get_next_proposal_id(proposals_dir: Path = Path("memory/proposals")) -> str:
    """Mint the next mem_NNNN id by scanning `proposals_dir` for files
    matching `mem_<digits>.json`. Returns mem_0001 for an empty / missing
    directory.
    """
    if not proposals_dir.exists():
        return "mem_0001"
    max_n = 0
    for entry in proposals_dir.iterdir():
        m = _PROPOSAL_ID_RE.match(entry.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"mem_{max_n + 1:04d}"


def validate_memory_update(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/memory_update.schema.json.
    Thin wrapper over arena.schemas.validate.validate."""
    validate("memory_update", payload)
