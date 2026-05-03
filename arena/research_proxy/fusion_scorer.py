# arena/research_proxy/fusion_scorer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Minimum score below which the controller halts the research-proxy chain
# at step 6 (between fusion proposal and proxy implementation). Tuned to
# accept the deterministic stub fusion (score ~ 0.7) and reject obviously
# bad proposals (large cost + many risks).
MIN_FUSION_SCORE = 0.4


@dataclass(frozen=True)
class FusionScore:
    """Decomposed fusion score: each component in [0, 1], `score` is the
    weighted average. Caller compares score against MIN_FUSION_SCORE."""

    score: float
    risk: float
    cost: float
    fit: float


_COST_RANK = {"tiny": 1.0, "small": 0.8, "medium": 0.5, "large": 0.2}
_FIT_RANK = {"high": 1.0, "medium": 0.6, "low": 0.2}

_FORBIDDEN_NETWORK_TOKENS = (
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "http://",
    "https://",
    "socket",
)
_FORBIDDEN_UNTRUSTED_IMPORTS = (
    "subprocess",  # Phase 0: no shelling out from research-proxy code
    "os.system",
    "eval(",
    "exec(",
)


def score_fusion_proposal(proposal: dict[str, Any]) -> FusionScore:
    """Deterministic scoring: cost, risk, fit components → weighted score.

    Higher score = better. cost component = 1 - normalized cost class
    rank; risk component = 1 - clamped(len(risks)/5); fit component
    derived from applicability (digest field — but proposal doesn't
    carry it; use mechanism count as a fit proxy: more mechanisms
    combined = better fit signal).
    """
    cost_class = proposal["resource_estimate"]["cost_class"]
    cost = _COST_RANK.get(cost_class, 0.5)

    n_risks = len(proposal.get("risks", []))
    risk = max(0.0, 1.0 - min(n_risks, 5) / 5.0)

    n_mech = len(proposal.get("mechanisms_combined", []))
    fit = min(1.0, n_mech / 3.0)  # 2 mechs → 0.67, 3 → 1.0

    # Equal-weighted average; tweakable in PR7.
    score = (cost + risk + fit) / 3.0
    return FusionScore(score=score, risk=risk, cost=cost, fit=fit)


def is_eligible(proposal: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check the §6.3 eligibility checklist.

    Returns (passes, reasons). Each reason is a short string explaining
    one rule that failed. An eligible proposal returns (True, []).

    Checks:
    - 2+ mechanisms_combined (also a schema requirement; double-check)
    - smallest_proxy_test present + non-trivial
    - ablation_plan non-empty
    - resource_estimate present with all required fields
    - risks is a list (may be empty; spec only says "risk list")
    - stop_condition non-empty
    - source_refs non-empty
    - No forbidden network token in implementation_plan.dependencies or
      .algorithm_steps
    - No forbidden untrusted-code-import token in algorithm_steps
    """
    reasons: list[str] = []

    if len(proposal.get("mechanisms_combined", [])) < 2:
        reasons.append("two or more mechanisms required")

    spt = proposal.get("smallest_proxy_test", {})
    if not spt or len(spt.get("description", "")) < 20:
        reasons.append("smallest proxy test missing or too short")

    if len(proposal.get("ablation_plan", [])) < 1:
        reasons.append("ablation plan missing")

    re_est = proposal.get("resource_estimate", {})
    for required in ("cost_class", "gpu_required", "max_runtime_minutes"):
        if required not in re_est:
            reasons.append(f"resource_estimate missing {required}")

    if "risks" not in proposal:
        reasons.append("risk list missing")

    if len(proposal.get("stop_condition", "")) < 10:
        reasons.append("stop_condition missing or too short")

    if not proposal.get("source_refs"):
        reasons.append("source_refs empty")

    impl = proposal.get("implementation_plan", {})
    haystack_parts: list[str] = []
    haystack_parts.extend(impl.get("dependencies", []))
    haystack_parts.extend(impl.get("algorithm_steps", []))
    haystack = " ".join(haystack_parts).lower()
    for token in _FORBIDDEN_NETWORK_TOKENS:
        if token in haystack:
            reasons.append(f"forbidden network dependency token: {token}")
            break
    for token in _FORBIDDEN_UNTRUSTED_IMPORTS:
        if token in haystack:
            reasons.append(f"forbidden untrusted-code import: {token}")
            break

    return (len(reasons) == 0, reasons)
