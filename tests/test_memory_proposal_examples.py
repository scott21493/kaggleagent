# tests/test_memory_proposal_examples.py
"""Replaces scripts/validate_memory_examples.py.

Covers:
- All 4 `operation` enum paths: add, modify, deprecate, remove.
- The schema's `prior_claim` conditional (required on
  modify/deprecate/remove; optional on add).
- Contradiction detection: claim != prior_claim on modify
  (semantic check via arena.memory.validator.check_evidence).
"""

from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.memory.validator import check_evidence
from arena.schemas.validate import validate


def _base_proposal(operation: str, **overrides) -> dict:
    base = {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": operation,
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


def test_add_operation_passes_schema_without_prior_claim() -> None:
    """The schema's allOf branch makes prior_claim optional on add."""
    proposal = _base_proposal("add")
    validate("memory_update", proposal)


def test_add_operation_passes_schema_with_null_prior_claim() -> None:
    """add allows prior_claim=null (the second allOf branch)."""
    proposal = _base_proposal("add", prior_claim=None)
    validate("memory_update", proposal)


def test_modify_operation_requires_prior_claim() -> None:
    """The schema's allOf branch requires prior_claim (string,
    minLength=5) on modify."""
    proposal = _base_proposal("modify")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_modify_operation_passes_with_distinct_prior_claim() -> None:
    proposal = _base_proposal(
        "modify",
        claim="The new claim.",
        prior_claim="The old claim.",
    )
    validate("memory_update", proposal)
    assert check_evidence(proposal) == []


def test_deprecate_operation_requires_prior_claim() -> None:
    proposal = _base_proposal("deprecate")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_remove_operation_requires_prior_claim() -> None:
    proposal = _base_proposal("remove")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_modify_with_identical_claim_fails_semantic_validator() -> None:
    """check_evidence flags modify with claim==prior_claim (no-op).
    Schema accepts this; the semantic validator catches it."""
    proposal = _base_proposal(
        "modify",
        claim="Same claim",
        prior_claim="Same claim",
    )
    validate("memory_update", proposal)  # schema-valid
    issues = check_evidence(proposal)
    assert any("claim" in i.lower() and "prior_claim" in i.lower() for i in issues)
