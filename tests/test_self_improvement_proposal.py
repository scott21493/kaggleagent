# tests/test_self_improvement_proposal.py
from __future__ import annotations

from pathlib import Path

from arena.self_improvement.proposal import (
    get_next_sip_id,
    make_self_improvement_proposal,
    validate_self_improvement_proposal,
)
from arena.self_improvement.scan import Finding


def test_make_self_improvement_proposal_is_schema_valid() -> None:
    finding = Finding(
        kind="blocked_row",
        severity="medium",
        problem="Task task_0001 was blocked by OutputCharsBreaker.",
        evidence_refs=["scoreboard:exp_0001", "trace:run_x/task_0001"],
    )
    proposal = make_self_improvement_proposal(finding, proposal_id="sip_0001")
    validate_self_improvement_proposal(proposal)
    assert proposal["proposal_id"] == "sip_0001"
    assert proposal["requires_human_approval"] is True


def test_proposal_carries_evidence_refs() -> None:
    finding = Finding(
        kind="score_regression",
        severity="high",
        problem="exp_0004 score 0.42 below calibration baseline 0.5.",
        evidence_refs=["scoreboard:exp_0004"],
    )
    proposal = make_self_improvement_proposal(finding, proposal_id="sip_0002")
    assert "scoreboard:exp_0004" in proposal["evidence_refs"]


def test_get_next_sip_id_monotonic(tmp_path: Path) -> None:
    proposals_dir = tmp_path / "self_improvement" / "proposals"
    assert get_next_sip_id(proposals_dir) == "sip_0001"
    proposals_dir.mkdir(parents=True)
    (proposals_dir / "sip_0001.json").write_text("{}", encoding="utf-8")
    (proposals_dir / "sip_0007.json").write_text("{}", encoding="utf-8")
    assert get_next_sip_id(proposals_dir) == "sip_0008"
