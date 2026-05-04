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
# Forward-compat: a future PR may switch the fit component from
# mechanism-count (the current Phase-0 proxy) to a lookup against the
# digest's applicability.fit value via this table. Kept here so the
# constant doesn't need to be reintroduced; safe to leave unused.
_FIT_RANK = {"high": 1.0, "medium": 0.6, "low": 0.2}

# Forbidden tokens are scoped: package-style names match exactly against
# normalized dependency names; live-network patterns and explicit-import
# patterns match as substrings only against algorithm_steps prose. This
# split prevents English words like "requests" or "socket" appearing in
# step descriptions ("the meta-learner requests careful calibration")
# from falsely tripping the eligibility gate. Real package usage still
# trips it via the dependency check.
_FORBIDDEN_NETWORK_DEPS = frozenset({"requests", "urllib", "httpx", "aiohttp", "socket"})
_FORBIDDEN_NETWORK_STEP_PATTERNS = (
    "http://",
    "https://",
    "import requests",
    "import urllib",
    "import httpx",
    "import aiohttp",
    "import socket",
)
_FORBIDDEN_UNTRUSTED_DEPS = frozenset(
    {"subprocess"}  # Phase 0: no shelling out from research-proxy code
)
_FORBIDDEN_UNTRUSTED_STEP_PATTERNS = (
    "import subprocess",
    "os.system",
    "eval(",
    "exec(",
)


def _normalize_dep(dep: str) -> str:
    """Strip version specifiers, extras, and surrounding whitespace from
    a pip-style dependency string and lowercase the result. Returns the
    base package name so `_FORBIDDEN_NETWORK_DEPS` membership is a clean
    exact match.

    Examples: 'requests' → 'requests'; 'requests>=2.0' → 'requests';
    'Requests[security]==2.31.0' → 'requests'.
    """
    name = dep.strip().lower()
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if sep in name:
            name = name.split(sep, 1)[0]
    if "[" in name:
        name = name.split("[", 1)[0]
    return name.strip()


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

    # Equal-weighted average; tweakable in a future PR.
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
    - No forbidden network use: dependency names match exactly against
      _FORBIDDEN_NETWORK_DEPS (after normalization); algorithm_steps are
      scanned only for explicit live-network patterns (http://, https://,
      import requests, etc.) so plain English words don't false-trigger.
    - No forbidden untrusted-code use: same dep-vs-step split with
      _FORBIDDEN_UNTRUSTED_{DEPS,STEP_PATTERNS}.

    First match per category wins (one reason per failure axis to keep
    the breaker UX short; broader negative coverage lives in Task 6).
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
    deps = [_normalize_dep(d) for d in impl.get("dependencies", [])]
    steps_haystack = " ".join(impl.get("algorithm_steps", [])).lower()

    # Network category: deps exact-match, then steps substring-match.
    network_hit: str | None = None
    for dep in deps:
        if dep in _FORBIDDEN_NETWORK_DEPS:
            network_hit = f"dependency {dep!r}"
            break
    if network_hit is None:
        for pattern in _FORBIDDEN_NETWORK_STEP_PATTERNS:
            if pattern in steps_haystack:
                network_hit = f"algorithm step contains {pattern!r}"
                break
    if network_hit is not None:
        reasons.append(f"forbidden network use: {network_hit}")

    # Untrusted-code category: same dep-vs-step split.
    untrusted_hit: str | None = None
    for dep in deps:
        if dep in _FORBIDDEN_UNTRUSTED_DEPS:
            untrusted_hit = f"dependency {dep!r}"
            break
    if untrusted_hit is None:
        for pattern in _FORBIDDEN_UNTRUSTED_STEP_PATTERNS:
            if pattern in steps_haystack:
                untrusted_hit = f"algorithm step contains {pattern!r}"
                break
    if untrusted_hit is not None:
        reasons.append(f"forbidden untrusted-code use: {untrusted_hit}")

    return (len(reasons) == 0, reasons)
