# tests/test_research_review_packet.py
from __future__ import annotations

from arena.review.packet import make_review_packet, validate_research_review
from arena.schemas.validate import validate


def test_make_review_packet_is_schema_valid_task_packet() -> None:
    packet = make_review_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0006",
        task_id="task_0006",
        review_id="rr_0001",
        subject_experiment_id="exp_0004",
        fusion_proposal_path="worktrees/tabular_binary_v1/exp_0003/fusion_proposal.json",
        submission_path="worktrees/tabular_binary_v1/exp_0004/submission.csv",
    )
    validate("task_packet", packet)
    assert packet["role"] == "review"
    assert packet["phase"] == "FUSION_PROXY_REVIEWED"
    # Submission path FIRST so stub_claude's _read_subject_id_from_inputs
    # picks it up (stub matches inputs[0]).
    assert packet["inputs"][0].endswith("submission.csv")
    assert any("fusion_proposal.json" in p for p in packet["inputs"])


def test_validate_research_review_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "research_review.v1",
        "review_id": "rr_0001",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept",
        "summary": "A 10+ char summary string.",
        "strengths": ["s1"],
        "weaknesses": ["w1"],
        "required_fixes": [],
        "follow_up_recommendations": ["f1"],
        "risk_level": "low",
    }
    validate_research_review(payload)  # no raise


def test_review_id_pattern_enforced_by_schema() -> None:
    """research_review schema enforces review_id ^rr_[0-9]{4,}$."""
    import pytest
    from jsonschema import ValidationError

    bad = {
        "schema_version": "research_review.v1",
        "review_id": "not_an_rr_id",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept",
        "summary": "A 10+ char summary string.",
        "strengths": [],
        "weaknesses": [],
        "required_fixes": [],
        "follow_up_recommendations": [],
        "risk_level": "low",
    }
    with pytest.raises(ValidationError):
        validate_research_review(bad)
