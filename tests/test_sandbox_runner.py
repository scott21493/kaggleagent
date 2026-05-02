from __future__ import annotations

import os
from pathlib import Path

import pytest

from arena.budget.kill_switch import Breaker
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.runner import (
    SandboxAttempt,
    SandboxAttemptKind,
    SandboxRunner,
    SandboxViolation,
    assert_sandbox_allowed,
    get_active_sandbox,
)


def _policy(tmp_path: Path) -> SandboxPolicy:
    """Packet-scoped policy: only worktrees/tabular_binary_v1/exp_0001/ is writable."""
    return SandboxPolicy.from_packet(
        {"allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"]},
        workspace_root=tmp_path,
    )


def test_assert_allowed_raises_on_secret_read(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    with pytest.raises(SandboxViolation) as exc:
        runner.assert_allowed(
            SandboxAttempt(
                kind=SandboxAttemptKind.SECRET_READ,
                target=str(Path("~/.kaggle/kaggle.json").expanduser()),
            )
        )
    assert exc.value.breaker is Breaker.SECRET_ACCESS
    assert ".kaggle" in str(exc.value)


def test_assert_allowed_raises_on_network_egress(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    with pytest.raises(SandboxViolation) as exc:
        runner.assert_allowed(
            SandboxAttempt(
                kind=SandboxAttemptKind.NETWORK_EGRESS,
                target="https://example.com",
            )
        )
    assert exc.value.breaker is Breaker.NETWORK_EGRESS


def test_assert_allowed_raises_on_protected_write(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    bad = "C:/Windows/System32/drivers/etc/hosts" if os.name == "nt" else "/etc/passwd"
    with pytest.raises(SandboxViolation) as exc:
        runner.assert_allowed(SandboxAttempt(kind=SandboxAttemptKind.PROTECTED_WRITE, target=bad))
    assert exc.value.breaker is Breaker.PROTECTED_FILE


def test_assert_allowed_raises_on_sibling_worktree_write(tmp_path: Path) -> None:
    """Provider for exp_0001 cannot write into exp_9999 — packet-scoped allowed_writes."""
    runner = SandboxRunner(_policy(tmp_path))
    sibling = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_9999" / "submission.csv"
    with pytest.raises(SandboxViolation) as exc:
        runner.assert_allowed(
            SandboxAttempt(kind=SandboxAttemptKind.PROTECTED_WRITE, target=str(sibling))
        )
    assert exc.value.breaker is Breaker.PROTECTED_FILE


def test_assert_allowed_passes_on_worktree_write(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    workspace_file = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    runner.assert_allowed(
        SandboxAttempt(kind=SandboxAttemptKind.PROTECTED_WRITE, target=str(workspace_file))
    )  # must not raise


def test_get_active_sandbox_is_none_by_default() -> None:
    assert get_active_sandbox() is None


def test_runner_context_activates_and_deactivates(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    assert get_active_sandbox() is None
    with runner.context():
        assert get_active_sandbox() is runner
    assert get_active_sandbox() is None


def test_runner_context_deactivates_on_exception(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    with pytest.raises(RuntimeError), runner.context():
        raise RuntimeError("boom")
    assert get_active_sandbox() is None


def test_assert_sandbox_allowed_is_noop_with_no_active_sandbox() -> None:
    """When no sandbox is active (e.g., direct stub-provider unit tests),
    assert_sandbox_allowed must not raise — it's a no-op."""
    assert_sandbox_allowed(SandboxAttempt(kind=SandboxAttemptKind.SECRET_READ, target="/anything"))


def test_assert_sandbox_allowed_delegates_to_active_runner(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    with runner.context():
        with pytest.raises(SandboxViolation) as exc:
            assert_sandbox_allowed(
                SandboxAttempt(kind=SandboxAttemptKind.NETWORK_EGRESS, target="https://example.com")
            )
        assert exc.value.breaker is Breaker.NETWORK_EGRESS


def test_sandbox_violation_carries_attempt(tmp_path: Path) -> None:
    runner = SandboxRunner(_policy(tmp_path))
    attempt = SandboxAttempt(
        kind=SandboxAttemptKind.SECRET_READ,
        target=str(Path("~/.kaggle/kaggle.json").expanduser()),
    )
    with pytest.raises(SandboxViolation) as exc:
        runner.assert_allowed(attempt)
    assert exc.value.attempt is attempt
    assert exc.value.breaker is Breaker.SECRET_ACCESS
