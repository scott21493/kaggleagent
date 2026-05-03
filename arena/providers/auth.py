# arena/providers/auth.py
"""Auth-expiry stderr-pattern fallback classifier.

Patterns are CONSERVATIVE SEEDS, NOT real-provider-verified. The first
real auth-failure observation MUST refresh this list (see
docs/phase0/runbooks/auth_expiry.md "Maintenance loop").

Used by `arena/providers/{codex,claude,health}.py` as the fallback
classification layer. Wrappers prefer explicit exit-code semantics
first (≥64 → BLOCKED_AUTH, 0/1/2 → success/failure/blocked); the
regex layer here only fires on the exit=1 ambiguous case.
"""

from __future__ import annotations

import re

# Conservative seed patterns derived from common CLI auth-failure
# phrasing. NOT verified against real codex/claude stderr — first real
# auth-expiry observation MUST refresh this list. See:
# docs/phase0/runbooks/auth_expiry.md (Maintenance loop section).
AUTH_EXPIRY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(authentication|credential|session|token|auth)\b.*\b"
        r"(failed|expired|invalid|denied|required)",
        re.IGNORECASE,
    ),
    # "login" pattern is narrower — only matches in the explicit
    # "please (re-)?(authenticate|log in)" construction, not bare
    # "logged in" or "--login=" usages. Negative tests pin this.
    re.compile(r"please (re-?)?(authenticate|log\s*in)", re.IGNORECASE),
    re.compile(r"\b401\b"),
    re.compile(r"\bnot (logged in|signed in)\b", re.IGNORECASE),
)


def matches_auth_expiry(stderr: str) -> bool:
    """Return True iff `stderr` contains a known auth-expiry phrase.

    The wrapper calls this only when explicit exit-code semantics did
    NOT classify the result already. False on empty input; case-
    insensitive on the first three patterns. New patterns added on
    first real-CLI auth-failure observation per the runbook.
    """
    if not stderr:
        return False
    return any(p.search(stderr) for p in AUTH_EXPIRY_PATTERNS)
