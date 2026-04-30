from __future__ import annotations

import itertools
from enum import StrEnum


class Phase(StrEnum):
    NEW = "NEW"
    FIXTURE_INITIALIZED = "FIXTURE_INITIALIZED"
    PLAN_CREATED = "PLAN_CREATED"
    CALIBRATION_TASK_CREATED = "CALIBRATION_TASK_CREATED"
    CALIBRATION_IMPLEMENTED = "CALIBRATION_IMPLEMENTED"
    CALIBRATION_EVALUATED = "CALIBRATION_EVALUATED"
    CALIBRATION_REVIEWED = "CALIBRATION_REVIEWED"
    RESEARCH_QUESTION_CREATED = "RESEARCH_QUESTION_CREATED"
    METHOD_DIGEST_CREATED = "METHOD_DIGEST_CREATED"
    FUSION_PROPOSAL_CREATED = "FUSION_PROPOSAL_CREATED"
    FUSION_PROXY_IMPLEMENTED = "FUSION_PROXY_IMPLEMENTED"
    FUSION_PROXY_EVALUATED = "FUSION_PROXY_EVALUATED"
    FUSION_PROXY_REVIEWED = "FUSION_PROXY_REVIEWED"
    MEMORY_PROPOSAL_CREATED = "MEMORY_PROPOSAL_CREATED"
    SELF_IMPROVEMENT_SCAN_COMPLETED = "SELF_IMPROVEMENT_SCAN_COMPLETED"
    HARNESS_EVAL_COMPLETED = "HARNESS_EVAL_COMPLETED"
    PHASE0_COMPLETE = "PHASE0_COMPLETE"
    BLOCKED_AUTH = "BLOCKED_AUTH"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"
    BLOCKED_SANDBOX = "BLOCKED_SANDBOX"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_SECRET_ACCESS = "BLOCKED_SECRET_ACCESS"
    BLOCKED_NETWORK = "BLOCKED_NETWORK"
    BLOCKED_PROTECTED_FILE = "BLOCKED_PROTECTED_FILE"
    BLOCKED_KILL_SWITCH = "BLOCKED_KILL_SWITCH"
    BLOCKED_REPRODUCIBILITY = "BLOCKED_REPRODUCIBILITY"
    NEEDS_HUMAN = "NEEDS_HUMAN"


_BLOCKED = {
    Phase.BLOCKED_AUTH,
    Phase.BLOCKED_BUDGET,
    Phase.BLOCKED_SANDBOX,
    Phase.BLOCKED_SCHEMA,
    Phase.BLOCKED_SECRET_ACCESS,
    Phase.BLOCKED_NETWORK,
    Phase.BLOCKED_PROTECTED_FILE,
    Phase.BLOCKED_KILL_SWITCH,
    Phase.BLOCKED_REPRODUCIBILITY,
}

# Forward edges through the happy path.
_FORWARD = [
    Phase.NEW,
    Phase.FIXTURE_INITIALIZED,
    Phase.PLAN_CREATED,
    Phase.CALIBRATION_TASK_CREATED,
    Phase.CALIBRATION_IMPLEMENTED,
    Phase.CALIBRATION_EVALUATED,
    Phase.CALIBRATION_REVIEWED,
    Phase.RESEARCH_QUESTION_CREATED,
    Phase.METHOD_DIGEST_CREATED,
    Phase.FUSION_PROPOSAL_CREATED,
    Phase.FUSION_PROXY_IMPLEMENTED,
    Phase.FUSION_PROXY_EVALUATED,
    Phase.FUSION_PROXY_REVIEWED,
    Phase.MEMORY_PROPOSAL_CREATED,
    Phase.SELF_IMPROVEMENT_SCAN_COMPLETED,
    Phase.HARNESS_EVAL_COMPLETED,
    Phase.PHASE0_COMPLETE,
]

# PHASE0_COMPLETE is intentionally terminal: once the run is complete, no
# transitions out (including to BLOCKED_*) are allowed. itertools.pairwise
# below naturally yields no edges from the last element of _FORWARD; this
# is the intended semantics, not an accident.

ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {}

for src, dst in itertools.pairwise(_FORWARD):
    # Each forward step is allowed; from any forward step you can also enter a BLOCKED_* state.
    ALLOWED_TRANSITIONS.setdefault(src, set()).add(dst)
    for blocked in _BLOCKED:
        ALLOWED_TRANSITIONS.setdefault(src, set()).add(blocked)

# From BLOCKED_*, only NEEDS_HUMAN is reachable (and from NEEDS_HUMAN, you can resume).
for blocked in _BLOCKED:
    ALLOWED_TRANSITIONS[blocked] = {Phase.NEEDS_HUMAN}

ALLOWED_TRANSITIONS[Phase.NEEDS_HUMAN] = set(_FORWARD)


def transition(src: Phase, dst: Phase) -> None:
    """Raise ValueError if transitioning from src to dst is disallowed."""
    if dst not in ALLOWED_TRANSITIONS.get(src, set()):
        raise ValueError(f"disallowed phase transition: {src.value} -> {dst.value}")
