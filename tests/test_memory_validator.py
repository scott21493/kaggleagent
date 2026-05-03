# tests/test_memory_validator.py
from __future__ import annotations

from arena.memory.validator import check_evidence


def _proposal(**overrides) -> dict:
    base = {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": "add",
        "claim": "A non-trivial claim string.",
        "delta": "A non-trivial delta string.",
        "evidence": [
            {
                "type": "trace",
                "ref": "rr_0001",
                "quote_or_summary": "summary here",
            }
        ],
        "confidence": "medium",
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": "low",
        "review_status": "proposed",
    }
    base.update(overrides)
    return base


def test_valid_add_proposal_passes() -> None:
    issues = check_evidence(_proposal(operation="add"))
    assert issues == []


def test_modify_without_prior_claim_fails() -> None:
    """The schema's allOf branch already requires prior_claim on modify;
    check_evidence ALSO surfaces this as a semantic issue (defense in
    depth + clearer message)."""
    proposal = _proposal(operation="modify")
    # No prior_claim set.
    issues = check_evidence(proposal)
    assert any("prior_claim" in i.lower() for i in issues)


def test_modify_with_identical_claim_and_prior_claim_fails() -> None:
    """A 'modify' that doesn't actually change the claim is a no-op
    that should be rejected."""
    proposal = _proposal(
        operation="modify",
        claim="Same claim",
        prior_claim="Same claim",
    )
    issues = check_evidence(proposal)
    assert any("claim" in i.lower() and "prior_claim" in i.lower() for i in issues)


def test_empty_evidence_fails() -> None:
    """Schema requires minItems=1; the validator double-checks."""
    proposal = _proposal()
    proposal["evidence"] = []
    issues = check_evidence(proposal)
    assert any("evidence" in i.lower() for i in issues)
