# tests/test_observability_provider_drift.py
"""Acceptance test 10 from SECURITY_COST_REPRODUCIBILITY_SPEC.md §9:
Provider version changes → run flagged."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.stub_codex import StubCodexProvider
from arena.scoreboard.store import ScoreboardStore


def test_provider_version_drift_flags_experiment_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    # First run: baseline recorded, no drift tag.
    result1 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result1.exit_code == 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp1 = store.get_latest_experiment("tabular_binary_v1")
        assert exp1 is not None
        assert "PROVIDER_VERSION_CHANGED" not in (exp1["artifact_paths"] or "")
    finally:
        store.close()

    # Force a version change for the second invocation.
    monkeypatch.setattr(StubCodexProvider, "version", property(lambda self: "stub_codex.v999"))

    # Plan a second task and run.
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result2 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result2.exit_code == 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp2 = store.get_latest_experiment("tabular_binary_v1")
        assert exp2 is not None
        assert "PROVIDER_VERSION_CHANGED" in (exp2["artifact_paths"] or "")
        assert "from=stub_codex.v1" in (exp2["artifact_paths"] or "")
    finally:
        store.close()
