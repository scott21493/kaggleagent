# tests/test_observability_fixture_drift.py
"""Acceptance test 9 from SECURITY_COST_REPRODUCIBILITY_SPEC.md §9:
Fixture hash changes unexpectedly → run blocked."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def test_run_next_blocks_on_fixture_hash_drift(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    # First run: baseline recorded.
    result1 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result1.exit_code == 0

    # Mutate an actual fixture FILE (not just the manifest YAML) to
    # exercise the file-content digest path. Appending bytes to train.csv
    # changes its sha256, which changes the fixture-set digest, which
    # trips the per-slug baseline.
    train_csv = fixture_workspace / "fixtures" / "tabular_binary_v1" / "train.csv"
    train_csv.write_bytes(train_csv.read_bytes() + b"\n0,0,0,0,0,0\n")

    # Plan another task and re-run.
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result2 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result2.exit_code != 0
    assert "BLOCKED_REPRODUCIBILITY" in result2.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp = store.get_latest_experiment("tabular_binary_v1")
        assert exp is not None
        assert exp["status"] == "blocked"
        assert "<blocked:BLOCKED_REPRODUCIBILITY>" in exp["artifact_paths"]
    finally:
        store.close()


def test_run_next_blocks_cleanly_when_fixture_manifest_is_missing(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the fixture manifest is missing post-dequeue (fixtures dir
    deleted, slug typo, pipeline corruption), run-next must persist a
    blocked row and exit code 2 — NOT raise an unhandled FileNotFoundError
    that loses the task from the queue with a traceback."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])

    # Delete the manifest BETWEEN plan (which doesn't read the manifest)
    # and run-next (which does).
    manifest = fixture_workspace / "fixtures" / "tabular_binary_v1" / "fixture_manifest.yaml"
    manifest.unlink()

    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    # Clean error message, not a traceback.
    assert "BLOCKED_REPRODUCIBILITY" in result.output
    assert "fixture manifest missing" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp = store.get_latest_experiment("tabular_binary_v1")
        assert exp is not None
        assert exp["status"] == "blocked"
        assert "<blocked:BLOCKED_REPRODUCIBILITY>" in exp["artifact_paths"]
    finally:
        store.close()
