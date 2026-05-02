# tests/test_cli_replay.py
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.stub_codex import StubCodexProvider  # noqa: F401  (ensures provider available)


def test_replay_after_run_next_succeeds(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])

    # Find the run id from runs/. Filter out hidden dirs (e.g., .baselines/).
    runs = [
        p
        for p in (fixture_workspace / "runs").iterdir()
        if p.is_dir() and p.name.startswith("run_")
    ]
    assert runs, "expected at least one run"
    run_id = runs[0].name

    result = runner.invoke(app, ["replay", run_id])
    assert result.exit_code == 0
    assert run_id in result.output
    assert "task_0001" in result.output
    assert "stub_codex" in result.output


def test_replay_reconstructs_evaluated_score_after_evaluate(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: init-fixture → plan → run-next → evaluate → replay must
    surface the deterministic 0.5 score and metric_name in the replayed
    task summary. Without `evaluate` emitting a score_recorded event, the
    replay path would never prove that evaluated scores are reconstructible
    from traces.

    Regression for the PR4 plan-review P2 finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])

    # Evaluate the latest experiment — this must emit score_recorded into the trace.
    eval_result = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert eval_result.exit_code == 0
    assert "score=0.500000" in eval_result.output

    # Find the run id and replay.
    runs = [
        p
        for p in (fixture_workspace / "runs").iterdir()
        if p.is_dir() and p.name.startswith("run_")
    ]
    assert runs, "expected at least one run"
    run_id = runs[0].name

    replay_result = runner.invoke(app, ["replay", run_id])
    assert replay_result.exit_code == 0
    # The replay must show the evaluated score, not just task lifecycle events.
    assert "score=0.5" in replay_result.output
