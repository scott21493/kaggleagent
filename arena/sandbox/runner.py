from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from arena.budget.kill_switch import Breaker
from arena.sandbox.network import is_unapproved_egress
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.secrets import is_protected_write, is_secret_read


class SandboxAttemptKind(StrEnum):
    SECRET_READ = "secret_read"
    NETWORK_EGRESS = "network_egress"
    PROTECTED_WRITE = "protected_write"


@dataclass(frozen=True)
class SandboxAttempt:
    """An action a provider intends to perform that the sandbox must verify.

    Providers (PR3 stubs, PR7 subprocess wrappers) construct a
    SandboxAttempt and call assert_sandbox_allowed(attempt) — the active
    sandbox raises immediately on violation, before the operation proceeds.
    """

    kind: SandboxAttemptKind
    target: str  # path string or URL


class SandboxViolation(Exception):
    """Raised by SandboxRunner.assert_allowed when an attempt violates the policy.

    Carries the offending Breaker (Breaker.SECRET_ACCESS, Breaker.NETWORK_EGRESS,
    or Breaker.PROTECTED_FILE) and the SandboxAttempt that triggered it.
    Distinct from BudgetExceeded because it has different recovery semantics —
    a sandbox breach is a security event, not a cap overflow.
    """

    def __init__(self, breaker: Breaker, attempt: SandboxAttempt, message: str) -> None:
        super().__init__(message)
        self.breaker = breaker
        self.attempt = attempt


_active_sandbox: contextvars.ContextVar[SandboxRunner | None] = contextvars.ContextVar(
    "_active_sandbox", default=None
)


def get_active_sandbox() -> SandboxRunner | None:
    """Return the SandboxRunner active for the current context, or None."""
    return _active_sandbox.get()


def assert_sandbox_allowed(attempt: SandboxAttempt) -> None:
    """Convenience: assert against the active sandbox.

    No-op when no sandbox is active (e.g., direct unit tests of stub
    providers). Providers and subprocess wrappers call this to register
    intent without needing to know whether the watchdog has activated a
    sandbox.

    WARNING: production callers must ensure a runner is activated via
    runner.context() before calling. There is no runtime check that
    enforcement is actually in effect — a forgotten activation produces
    silent allow.
    """
    runner = get_active_sandbox()
    if runner is not None:
        runner.assert_allowed(attempt)


class SandboxRunner:
    """Run-level sandbox enforcer.

    Holds a SandboxPolicy and validates SandboxAttempts against it. The
    Watchdog activates a runner via runner.context() for the duration of
    each provider invoke; providers (stubs or subprocess wrappers) call
    assert_sandbox_allowed(attempt) to register intent. On a policy
    violation, the runner raises SandboxViolation immediately, before the
    operation completes.
    """

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy

    def assert_allowed(self, attempt: SandboxAttempt) -> None:
        """Raise SandboxViolation if attempt violates the policy."""
        if attempt.kind is SandboxAttemptKind.SECRET_READ:
            if is_secret_read(attempt.target, self._policy):
                raise SandboxViolation(
                    Breaker.SECRET_ACCESS,
                    attempt,
                    f"sandbox secret read denied: {attempt.target}",
                )
        elif attempt.kind is SandboxAttemptKind.NETWORK_EGRESS:
            if is_unapproved_egress(attempt.target, self._policy):
                raise SandboxViolation(
                    Breaker.NETWORK_EGRESS,
                    attempt,
                    f"sandbox network egress denied: {attempt.target}",
                )
        elif attempt.kind is SandboxAttemptKind.PROTECTED_WRITE:
            if is_protected_write(attempt.target, self._policy):
                raise SandboxViolation(
                    Breaker.PROTECTED_FILE,
                    attempt,
                    f"sandbox protected-file write denied: {attempt.target}",
                )
        else:
            # Defensive: every SandboxAttemptKind member must have a branch above.
            # If a future PR adds a kind without updating this dispatch, fail loudly
            # rather than silently allowing the attempt.
            raise AssertionError(f"unhandled SandboxAttemptKind: {attempt.kind!r}")

    @contextmanager
    def context(self) -> Iterator[SandboxRunner]:
        """Context manager: set this runner as active for the duration of the block.

        Exception-safe: deactivates the sandbox even if the block raises.
        Uses contextvars rather than thread-local so the runner is correctly
        scoped under any async or threaded callers in future PRs.
        """
        token = _active_sandbox.set(self)
        try:
            yield self
        finally:
            _active_sandbox.reset(token)
