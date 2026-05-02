from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena.budget.kill_switch import Breaker
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.runner import (
    SandboxAttempt,
    SandboxAttemptKind,
    SandboxRunner,
    SandboxViolation,
)


def _expect_violation(
    runner: SandboxRunner, attempt: SandboxAttempt, expected_breaker: Breaker
) -> None:
    """Run an attempt that must raise; verify breaker matches."""
    try:
        runner.assert_allowed(attempt)
    except SandboxViolation as exc:
        if exc.breaker is not expected_breaker:
            raise SystemExit(
                f"FAIL: attempt {attempt!r} raised {exc.breaker.value} "
                f"but expected {expected_breaker.value}"
            ) from exc
        return
    raise SystemExit(f"FAIL: attempt {attempt!r} did NOT raise; expected {expected_breaker.value}")


def _expect_allowed(runner: SandboxRunner, attempt: SandboxAttempt) -> None:
    """Run an attempt that must pass."""
    try:
        runner.assert_allowed(attempt)
    except SandboxViolation as exc:
        raise SystemExit(f"FAIL: allowed attempt {attempt!r} raised {exc!r}") from exc


def main() -> None:
    """Exercise the SandboxRunner against representative allowed and
    forbidden actions. Replaces the prior static greps with real,
    end-to-end policy enforcement against a temp worktree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Build a synthetic packet workspace: only exp_0001 is writable.
        active_worktree = root / "worktrees" / "tabular_binary_v1" / "exp_0001"
        sibling_worktree = root / "worktrees" / "tabular_binary_v1" / "exp_9999"
        fixtures_dir = root / "fixtures" / "tabular_binary_v1"
        active_worktree.mkdir(parents=True)
        sibling_worktree.mkdir(parents=True)
        fixtures_dir.mkdir(parents=True)

        # Packet-scoped policy: ONLY active_worktree is in allowed_writes.
        policy = SandboxPolicy.for_writes(frozenset({active_worktree}))
        runner = SandboxRunner(policy)

        # Forbidden: secret read.
        _expect_violation(
            runner,
            SandboxAttempt(
                kind=SandboxAttemptKind.SECRET_READ,
                target=str(Path("~/.kaggle/kaggle.json").expanduser()),
            ),
            Breaker.SECRET_ACCESS,
        )

        # Forbidden: network egress (default deny).
        _expect_violation(
            runner,
            SandboxAttempt(
                kind=SandboxAttemptKind.NETWORK_EGRESS,
                target="https://example.com",
            ),
            Breaker.NETWORK_EGRESS,
        )

        # Forbidden: write to a system path far outside any worktree.
        bad_target = (
            "/etc/passwd" if Path("/etc").exists() else "C:/Windows/System32/drivers/etc/hosts"
        )
        _expect_violation(
            runner,
            SandboxAttempt(kind=SandboxAttemptKind.PROTECTED_WRITE, target=bad_target),
            Breaker.PROTECTED_FILE,
        )

        # Forbidden: write into a SIBLING worktree (P1 regression — pre-fix
        # this would have passed silently because the run-level worktrees/
        # root was in allowed_paths).
        _expect_violation(
            runner,
            SandboxAttempt(
                kind=SandboxAttemptKind.PROTECTED_WRITE,
                target=str(sibling_worktree / "stolen.csv"),
            ),
            Breaker.PROTECTED_FILE,
        )

        # Forbidden: write into fixtures (read-only inputs).
        _expect_violation(
            runner,
            SandboxAttempt(
                kind=SandboxAttemptKind.PROTECTED_WRITE,
                target=str(fixtures_dir / "train.csv"),
            ),
            Breaker.PROTECTED_FILE,
        )

        # Allowed: write under the active worktree.
        _expect_allowed(
            runner,
            SandboxAttempt(
                kind=SandboxAttemptKind.PROTECTED_WRITE,
                target=str(active_worktree / "submission.csv"),
            ),
        )

    print(
        "ok sandbox: 5 forbidden actions blocked (SecretAccess, NetworkEgress, "
        "ProtectedFile×3 — system, sibling-worktree, fixtures), 1 allowed worktree write passed"  # noqa: RUF001
    )


if __name__ == "__main__":
    main()
