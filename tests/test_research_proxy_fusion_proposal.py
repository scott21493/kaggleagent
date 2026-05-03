# tests/test_research_proxy_fusion_proposal.py
from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.research_proxy.fusion_proposal import (
    make_fusion_proposal_packet,
    validate_fusion_proposal,
)
from arena.schemas.validate import validate


def test_make_fusion_proposal_packet_is_schema_valid_task_packet() -> None:
    packet = make_fusion_proposal_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        fusion_id="fusion_0001",
        digest_path="worktrees/tabular_binary_v1/exp_0001/paper_digest.json",
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "FUSION_PROPOSAL_CREATED"
    assert "paper_digest.json" in packet["inputs"][0]


def test_validate_fusion_proposal_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Test fusion title",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {
                "mechanism_name": "mech_a",
                "source_ref": "ref_a",
                "role_in_fusion": "primary base learner role.",
            },
            {
                "mechanism_name": "mech_b",
                "source_ref": "ref_b",
                "role_in_fusion": "secondary stacking role.",
            },
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["step1.", "step2."],
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
    validate_fusion_proposal(payload)  # no raise


def test_validate_fusion_proposal_rejects_one_mechanism() -> None:
    """Schema requires minItems=2 on mechanisms_combined."""
    payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Bad fusion",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {
                "mechanism_name": "lonely_mech",
                "source_ref": "ref",
                "role_in_fusion": "the only mechanism here.",
            }
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["a"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": [],
            "expected_outputs": ["o"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [{"name": "a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 5,
        },
        "risks": [],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref"],
    }
    with pytest.raises(ValidationError):
        validate_fusion_proposal(payload)
