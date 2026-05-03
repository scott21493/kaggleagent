# tests/test_memory_proposal.py
from __future__ import annotations

from pathlib import Path

from arena.memory.proposal import (
    get_next_proposal_id,
    synthesize_memory_proposal,
    validate_memory_update,
)


def _review_payload(*, required_fixes: list[str] | None = None) -> dict:
    return {
        "schema_version": "research_review.v1",
        "review_id": "rr_0001",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept" if not required_fixes else "revise",
        "summary": "Reviewed proxy implementation; integrity confirmed.",
        "strengths": ["s1"],
        "weaknesses": ["w1"],
        "required_fixes": required_fixes or [],
        "follow_up_recommendations": [],
        "risk_level": "low",
    }


def test_synthesize_actionable_review_produces_add_in_research_namespace() -> None:
    """A review with required_fixes produces an add op claiming the
    first required_fix in the research namespace."""
    review = _review_payload(
        required_fixes=["Add a baseline ablation comparing GBDT-only vs ensemble."]
    )
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    validate_memory_update(proposal)
    assert proposal["proposal_id"] == "mem_0001"
    assert proposal["namespace"] == "research"
    assert proposal["operation"] == "add"
    assert "baseline ablation" in proposal["claim"]
    assert proposal["review_status"] == "proposed"


def test_synthesize_empty_review_produces_noop_observation() -> None:
    """A review with no required_fixes / follow_up_recommendations
    produces a schema-valid no-op observation. Captures audit trail."""
    review = _review_payload(required_fixes=[])
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    validate_memory_update(proposal)
    assert proposal["operation"] == "add"
    assert "no actionable findings" in proposal["claim"].lower()
    assert proposal["confidence"] == "low"
    assert proposal["risk"] == "low"


def test_synthesize_evidence_points_to_review() -> None:
    """The evidence array must reference the review (type=trace)."""
    review = _review_payload()
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert len(proposal["evidence"]) >= 1
    assert proposal["evidence"][0]["type"] == "trace"
    assert proposal["evidence"][0]["ref"] == "rr_0001"


def test_synthesize_namespace_defaults_to_research() -> None:
    review = _review_payload()
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert proposal["namespace"] == "research"


def test_synthesize_review_status_is_proposed() -> None:
    """No proposal is auto-accepted; review_status='proposed' always."""
    review = _review_payload(required_fixes=["fix one"])
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert proposal["review_status"] == "proposed"


def test_get_next_proposal_id_monotonic(tmp_path: Path) -> None:
    """Mints mem_0001 in an empty dir; mem_0002 after mem_0001 exists."""
    proposals_dir = tmp_path / "memory" / "proposals"
    assert get_next_proposal_id(proposals_dir) == "mem_0001"
    proposals_dir.mkdir(parents=True)
    (proposals_dir / "mem_0001.json").write_text("{}", encoding="utf-8")
    assert get_next_proposal_id(proposals_dir) == "mem_0002"
    (proposals_dir / "mem_0002.json").write_text("{}", encoding="utf-8")
    (proposals_dir / "mem_0009.json").write_text("{}", encoding="utf-8")
    assert get_next_proposal_id(proposals_dir) == "mem_0010"
