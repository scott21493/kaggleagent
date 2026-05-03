# tests/test_research_proxy_fusion_scorer.py
from __future__ import annotations

from arena.research_proxy.fusion_scorer import (
    MIN_FUSION_SCORE,
    FusionScore,
    is_eligible,
    score_fusion_proposal,
)


def _valid_proposal() -> dict:
    return {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Valid fusion",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {"mechanism_name": "a", "source_ref": "r_a", "role_in_fusion": "primary."},
            {"mechanism_name": "b", "source_ref": "r_b", "role_in_fusion": "secondary."},
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": ["pandas"],
            "expected_outputs": ["submission.csv"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [{"name": "abl_a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 10,
        },
        "risks": ["risk1"],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref_a"],
    }


def test_score_fusion_proposal_returns_FusionScore_with_components() -> None:
    proposal = _valid_proposal()
    s = score_fusion_proposal(proposal)
    assert isinstance(s, FusionScore)
    assert 0.0 <= s.score <= 1.0
    assert 0.0 <= s.risk <= 1.0
    assert 0.0 <= s.cost <= 1.0
    assert 0.0 <= s.fit <= 1.0


def test_score_is_higher_for_low_cost_low_risk_high_fit() -> None:
    proposal = _valid_proposal()
    proposal["resource_estimate"]["cost_class"] = "tiny"
    proposal["risks"] = []
    s_low = score_fusion_proposal(proposal)

    proposal["resource_estimate"]["cost_class"] = "large"
    proposal["risks"] = ["r1", "r2", "r3", "r4", "r5"]
    s_high = score_fusion_proposal(proposal)

    assert s_low.score > s_high.score


def test_score_is_deterministic() -> None:
    proposal = _valid_proposal()
    a = score_fusion_proposal(proposal)
    b = score_fusion_proposal(proposal)
    assert a == b


def test_is_eligible_passes_for_well_formed_proposal() -> None:
    proposal = _valid_proposal()
    passes, reasons = is_eligible(proposal)
    assert passes is True
    assert reasons == []


def test_is_eligible_rejects_proposal_with_one_mechanism() -> None:
    proposal = _valid_proposal()
    proposal["mechanisms_combined"] = proposal["mechanisms_combined"][:1]
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("two or more mechanisms" in r.lower() for r in reasons)


def test_is_eligible_rejects_proposal_with_empty_ablation_plan() -> None:
    proposal = _valid_proposal()
    proposal["ablation_plan"] = []
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("ablation" in r.lower() for r in reasons)


def test_is_eligible_rejects_proposal_referencing_forbidden_network() -> None:
    """Per §6.3: no forbidden network dependency. Check that any literal
    URL or `import requests` in implementation_plan trips the gate."""
    proposal = _valid_proposal()
    proposal["implementation_plan"]["dependencies"].append("requests")
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("network" in r.lower() or "requests" in r.lower() for r in reasons)


def test_is_eligible_distinguishes_dep_from_algorithm_step_prose() -> None:
    """Forbidden-network scanning must NOT trip on natural-language prose
    in algorithm_steps. Only package-name dependencies (exact match,
    after normalization) and explicit live-network patterns
    (http://, https://, import requests, …) count. Otherwise everyday
    phrasing like 'requests careful calibration' or 'open the socket
    of options' would falsely block proposals.

    First half: prose with the words 'requests' and 'socket' in
    algorithm_steps but no network-package dep → eligible.
    Second half: same proposal plus 'requests' as a real dependency →
    not eligible. Pinning this distinction guards against accidental
    re-tightening that brings back the false positive.
    """
    proposal = _valid_proposal()
    proposal["implementation_plan"]["algorithm_steps"] = [
        "Stack OOF predictions; the meta-learner requests careful calibration.",
        "Open the socket of available cost-vs-fit options and pick one.",
        "Write submission.csv with id, target.",
    ]
    passes, reasons = is_eligible(proposal)
    assert passes is True, f"prose false-positive: {reasons}"

    proposal["implementation_plan"]["dependencies"] = ["pandas", "requests"]
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("network" in r.lower() and "requests" in r.lower() for r in reasons)


def test_min_fusion_score_constant_is_in_range() -> None:
    assert 0.0 < MIN_FUSION_SCORE < 1.0
