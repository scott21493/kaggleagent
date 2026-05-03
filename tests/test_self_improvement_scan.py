# tests/test_self_improvement_scan.py
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore
from arena.self_improvement.scan import scan_runs


def _bootstrap_clean_run(runner: CliRunner) -> None:
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])


def test_scan_clean_scoreboard_returns_no_findings(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scoreboard with only completed rows and score >= calibration
    baseline produces zero findings."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert findings == []


def test_scan_detects_blocked_row(fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocked row in the scoreboard surfaces as a Finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Force a post-invoke BudgetExceeded so research-proxy persists a
    # blocked row at exp_0001 (the question step).
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "blocked_row" for f in findings)
    blocked = next(f for f in findings if f.kind == "blocked_row")
    assert any("exp_0001" in r for r in blocked.evidence_refs)


def test_scan_detects_score_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose max(score) is below the calibration baseline (0.5)
    surfaces a score_regression finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    # Manually downgrade the implementation row's score to simulate
    # regression.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        store._require_conn().execute(
            "UPDATE experiments SET score = 0.42 WHERE experiment_id = ?",
            ("exp_0004",),
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "score_regression" for f in findings)


def test_scan_detects_invalid_submission(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row with valid_submission=False (fixture-success-rate
    regression) surfaces as an invalid_submission finding (§7.3 'lower
    fixture success rate than champion')."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        store._require_conn().execute(
            "UPDATE experiments SET valid_submission = 0 WHERE experiment_id = ?",
            ("exp_0004",),
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "invalid_submission" for f in findings)


def test_scan_detects_wall_clock_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When non-calibration rows' aggregated wall_seconds exceeds the
    calibration champion's by >20% AND there's no score improvement,
    scan_runs surfaces a wall_clock_regression finding (§7.3
    'wall-clock increase over 20% without score/safety improvement').

    The PR1 calibration row exists at exp_0001; we plant a non-zero
    wall_seconds on it as the champion baseline, then inflate one
    research-proxy row's wall_seconds to trip the threshold."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        # Champion (calibration): 1 row, wall_seconds=1.0, score=0.5.
        store._require_conn().execute(
            "UPDATE experiments SET wall_seconds = 1.0 WHERE experiment_id = 'exp_0001'"
        )
        # Challenger: 4 rows, summed wall_seconds=2.0 (>1.20 * 1.0 and
        # score not improved over champion's 0.5 — research-proxy impl
        # row's score is also 0.5).
        store._require_conn().execute(
            "UPDATE experiments SET wall_seconds = 0.5 "
            "WHERE experiment_id IN ('exp_0002','exp_0003','exp_0004','exp_0005')"
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "wall_clock_regression" for f in findings), [f.kind for f in findings]


def test_scan_detects_provider_calls_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When non-calibration row count exceeds the calibration row count
    by >20% AND there's no score improvement, scan_runs surfaces a
    provider_calls_regression finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    # Champion: 1 calibration row. Challenger: 4 research-proxy rows
    # = 4x the champion = >20% increase. Score at exp_0004 is the
    # calibration baseline 0.5, so no improvement.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "provider_calls_regression" for f in findings), [f.kind for f in findings]


def test_scan_treats_missing_trace_as_failed_replay(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose task_id has no events.jsonl on disk MUST surface a
    failed_replay finding. The chain cannot be replayed, so per §7.3
    this is a freeze trigger. Regression for the original 'no trace =
    OK to skip' bug.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)

    # Locate exp_0004's trace file and delete it.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        impl = (
            store._require_conn()
            .execute(
                "SELECT run_id, task_id FROM experiments WHERE experiment_id = ?",
                ("exp_0004",),
            )
            .fetchone()
        )
    finally:
        store.close()

    canonical = fixture_workspace / "traces" / impl["run_id"] / impl["task_id"] / "events.jsonl"
    if canonical.exists():
        canonical.unlink()
    nested = (
        fixture_workspace / "runs" / impl["run_id"] / "traces" / impl["task_id"] / "events.jsonl"
    )
    if nested.exists():
        nested.unlink()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "failed_replay" for f in findings), [f.kind for f in findings]
