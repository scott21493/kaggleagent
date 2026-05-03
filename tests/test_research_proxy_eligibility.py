# tests/test_research_proxy_eligibility.py
"""Acceptance tests for the §6.3 eligibility checklist (the spec's
'A fusion proposal is eligible only if it has...' list).

Two layers:

1. End-to-end acceptance (8 tests): run `arena research-proxy` and read
   the emitted fusion_proposal.json, asserting each §6.3 item is
   satisfied by the deterministic stub_claude output. These are the
   contract for PR7's real Claude — its proposals must continue to pass.

2. Unit-level negative coverage (5 tests): pin is_eligible's rejection
   for the §6.3 rules that Task 3 didn't cover with explicit tests
   (short smallest_proxy_test description, missing resource_estimate
   keys, short stop_condition, empty source_refs, forbidden
   untrusted-code-import patterns in algorithm_steps). Together with the
   four existing fusion_scorer tests, all 8 §6.3 rules now have
   explicit negative coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.research_proxy.fusion_scorer import is_eligible

# ──────────────────────────────────────────────────────────────────────
# End-to-end acceptance: each test runs the full CLI chain and reads the
# resulting fusion_proposal.json from disk.
# ──────────────────────────────────────────────────────────────────────


def _emit_proposal_via_cli(fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Run a research-proxy session and read the resulting fusion_proposal.json."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0
    # With the 4-row design, step 3 (exp_0001), step 4 (exp_0002), step 5 (exp_0003),
    # step 7 (exp_0004). fusion_proposal.json is written by the third invocation.
    fp_path = (
        fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0003" / "fusion_proposal.json"
    )
    return json.loads(fp_path.read_text(encoding="utf-8"))


def test_stub_proposal_has_two_or_more_mechanisms(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert len(proposal["mechanisms_combined"]) >= 2


def test_stub_proposal_has_smallest_proxy_test(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    spt = proposal["smallest_proxy_test"]
    assert len(spt["description"]) >= 20
    assert spt["dataset_slice"]
    assert spt["metric"]
    assert "value" in spt["success_threshold"]
    assert spt["max_runtime_minutes"] <= 60


def test_stub_proposal_has_ablation_plan(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert len(proposal["ablation_plan"]) >= 1
    for ablation in proposal["ablation_plan"]:
        assert "name" in ablation
        assert "remove_or_change" in ablation
        assert "expected_signal" in ablation


def test_stub_proposal_has_complete_resource_estimate(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    re_est = proposal["resource_estimate"]
    assert re_est["cost_class"] in {"tiny", "small", "medium", "large"}
    assert isinstance(re_est["gpu_required"], bool)
    assert re_est["max_runtime_minutes"] >= 1


def test_stub_proposal_has_risk_list_and_stop_condition(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert isinstance(proposal["risks"], list)
    assert len(proposal["stop_condition"]) >= 10


def test_stub_proposal_passes_is_eligible(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deterministic stub fusion proposal must pass the full §6.3
    checklist as encoded by is_eligible."""
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    passes, reasons = is_eligible(proposal)
    assert passes is True, f"reasons: {reasons}"


def test_stub_proposal_has_no_forbidden_network_dependency(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    deps = proposal["implementation_plan"]["dependencies"]
    forbidden = {"requests", "urllib", "httpx", "aiohttp"}
    assert not any(any(f in d for f in forbidden) for d in deps), deps


def test_stub_proposal_has_no_untrusted_code_import(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub_claude proposal must pass is_eligible's untrusted-code
    check. After Task 3's polish (commit 9e57ef8), is_eligible uses
    scoped detection — exact-match on normalized dependency names plus
    explicit-pattern matching on algorithm_steps — so this test invokes
    the real gate rather than reproducing a naive substring search.
    The reason format `forbidden untrusted-code use: ...` is the contract."""
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    _passes, reasons = is_eligible(proposal)
    assert not any("untrusted-code" in r.lower() for r in reasons), reasons


# ──────────────────────────────────────────────────────────────────────
# Unit-level is_eligible negative coverage. Task 3 had explicit negative
# tests for 4 of the 8 §6.3 rules (one mechanism, empty ablation_plan,
# forbidden network dep, dep-vs-prose distinction). The remaining 4 rules
# + the untrusted-imports check land here so the §6.3 enforcement is
# fully pinned in one spot.
# ──────────────────────────────────────────────────────────────────────


def _valid_proposal_dict() -> dict:
    """Build a known-good proposal that satisfies every is_eligible rule.
    Each unit test below mutates one field to verify the matching rule
    fires."""
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
        "ablation_plan": [{"name": "a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 10,
        },
        "risks": ["risk1"],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref_a"],
    }


def test_is_eligible_rejects_short_smallest_proxy_test_description() -> None:
    """§6.3: smallest_proxy_test.description must be non-trivial
    (≥20 chars). A single-word description shouldn't pass."""
    proposal = _valid_proposal_dict()
    proposal["smallest_proxy_test"]["description"] = "too short"
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("smallest proxy test" in r.lower() for r in reasons)


def test_is_eligible_rejects_missing_resource_estimate_key() -> None:
    """§6.3: resource_estimate must have cost_class, gpu_required, AND
    max_runtime_minutes. Dropping any one fires the rule."""
    proposal = _valid_proposal_dict()
    del proposal["resource_estimate"]["max_runtime_minutes"]
    passes, reasons = is_eligible(proposal)
    assert passes is False
    # Loose match on the field name (symmetric with the other 4 negative
    # tests) — robust against future reason-string rewordings.
    assert any("max_runtime_minutes" in r for r in reasons)


def test_is_eligible_rejects_short_stop_condition() -> None:
    """§6.3: stop_condition must be ≥10 chars. A trivial 'stop' isn't
    actionable."""
    proposal = _valid_proposal_dict()
    proposal["stop_condition"] = "stop"
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("stop_condition" in r.lower() for r in reasons)


def test_is_eligible_rejects_empty_source_refs() -> None:
    """§6.3: source_refs must be non-empty. A proposal with no source
    references can't be traced back to any digest."""
    proposal = _valid_proposal_dict()
    proposal["source_refs"] = []
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("source_refs" in r.lower() for r in reasons)


def test_is_eligible_rejects_forbidden_untrusted_import_in_algorithm_steps() -> None:
    """§6.3: untrusted-code imports (subprocess, os.system, eval(, exec()
    in algorithm_steps trip the gate. This pins the second forbidden-token
    category — Task 3 had network coverage but not untrusted-imports."""
    proposal = _valid_proposal_dict()
    proposal["implementation_plan"]["algorithm_steps"] = [
        "Read train.csv.",
        "import subprocess; subprocess.run(['ls'])",  # explicit pattern
    ]
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("untrusted-code" in r.lower() for r in reasons)
