# tests/test_cli_sandbox.py
"""Acceptance tests for PR3 sandbox enforcement in `arena run-next`.

Each test installs a misbehaving stub provider that calls
assert_sandbox_allowed(...) with a forbidden target inside its invoke,
runs the full init-fixture → plan → run-next sequence, and asserts the
run blocks with the right breaker tag in the experiment row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.stub_codex import StubCodexProvider
from arena.sandbox.runner import (
    SandboxAttempt,
    SandboxAttemptKind,
    assert_sandbox_allowed,
)
from arena.scoreboard.store import ScoreboardStore


def _make_misbehaving_invoke(kind: SandboxAttemptKind, target: str):
    """Return a replacement StubCodexProvider.invoke that registers a
    forbidden sandbox attempt before producing its normal output."""

    original_invoke = StubCodexProvider.invoke

    def misbehaving_invoke(self: StubCodexProvider, task_packet: dict):
        assert_sandbox_allowed(SandboxAttempt(kind=kind, target=target))
        # If the sandbox didn't fire, fall through to the normal path so
        # any test misconfiguration produces a successful baseline rather
        # than a misleading half-failure.
        return original_invoke(self, task_packet)

    return misbehaving_invoke


def test_run_next_secret_read_trips_secret_access_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #1: stub simulating ~/.kaggle/kaggle.json read trips
    SecretAccessBreaker; status=blocked row records the breaker tag."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setattr(
        StubCodexProvider,
        "invoke",
        _make_misbehaving_invoke(
            SandboxAttemptKind.SECRET_READ,
            str(Path("~/.kaggle/kaggle.json").expanduser()),
        ),
    )

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "SecretAccessBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:SecretAccessBreaker>" in exp["artifact_paths"]
    store.close()


def test_run_next_network_egress_trips_network_egress_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2: stub simulating curl https://example.com trips
    NetworkEgressBreaker."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setattr(
        StubCodexProvider,
        "invoke",
        _make_misbehaving_invoke(
            SandboxAttemptKind.NETWORK_EGRESS,
            "https://example.com",
        ),
    )

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "NetworkEgressBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:NetworkEgressBreaker>" in exp["artifact_paths"]
    store.close()


def test_run_next_protected_write_trips_protected_file_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #3: stub simulating a write outside its worktree trips
    ProtectedFileBreaker."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    bad_target = "/etc/passwd" if Path("/etc").exists() else "C:/Windows/System32/drivers/etc/hosts"
    monkeypatch.setattr(
        StubCodexProvider,
        "invoke",
        _make_misbehaving_invoke(SandboxAttemptKind.PROTECTED_WRITE, bad_target),
    )

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "ProtectedFileBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:ProtectedFileBreaker>" in exp["artifact_paths"]
    store.close()


# --- P1 plan-review regression: end-to-end "outside its worktree" -------------
# Acceptance #3 above uses /etc/passwd, which is outside any plausible
# allowed_writes set. These tests exercise the more dangerous case the
# original plan would have missed: a write into a *sibling* worktree (which
# falls under the run-level worktrees/ root but outside the active packet's
# allowed_paths) and a write into fixtures/. With packet-scoped allowed_writes,
# both MUST trip ProtectedFileBreaker.


def test_run_next_sibling_worktree_write_trips_protected_file_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider for exp_0001 attempting to write into worktrees/<slug>/exp_9999
    (a sibling under the same competition root) trips ProtectedFileBreaker.

    Regression for the P1 plan-review fix: pre-fix, allowed_paths included the
    whole worktrees/ tree, so this would have passed silently."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    sibling_target = str(
        fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_9999" / "stolen.csv"
    )
    monkeypatch.setattr(
        StubCodexProvider,
        "invoke",
        _make_misbehaving_invoke(SandboxAttemptKind.PROTECTED_WRITE, sibling_target),
    )

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "ProtectedFileBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:ProtectedFileBreaker>" in exp["artifact_paths"]
    store.close()


def test_run_next_fixture_write_trips_protected_file_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider attempting to write into fixtures/ (read-only inputs) trips
    ProtectedFileBreaker.

    Regression for the P1 plan-review fix: pre-fix, fixtures_root was in
    allowed_paths, so a malicious provider could mutate train.csv mid-run."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    fixture_target = str(fixture_workspace / "fixtures" / "tabular_binary_v1" / "train.csv")
    monkeypatch.setattr(
        StubCodexProvider,
        "invoke",
        _make_misbehaving_invoke(SandboxAttemptKind.PROTECTED_WRITE, fixture_target),
    )

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "ProtectedFileBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:ProtectedFileBreaker>" in exp["artifact_paths"]
    store.close()
