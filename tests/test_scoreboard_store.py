from __future__ import annotations

from pathlib import Path

import pytest

from arena.scoreboard.store import ScoreboardStore


def test_applies_migrations_on_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "scoreboard.sqlite"
    store = ScoreboardStore(db)
    store.connect()  # triggers migrations
    cols = store.experiment_columns()
    expected = {
        "experiment_id",
        "run_id",
        "score",
        "metric_name",
        "status",
        "competition_slug",
        "task_id",
        "experiment_type",
        "provider",
        "provider_version",
        "valid_submission",
        "wall_seconds",
        "input_chars",
        "output_chars",
        "shell_commands",
        "failed_commands",
        "waste_events",
        "artifact_paths",
        "trace_path",
        "created_at",
    }
    assert expected.issubset(set(cols))


def test_migrations_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "scoreboard.sqlite"
    ScoreboardStore(db).connect()
    # second connect should not raise
    ScoreboardStore(db).connect()


def test_insert_and_fetch_run(tmp_path: Path) -> None:
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()
    store.insert_run(run_id="run_test", started_at="2026-04-30T10:00:00", status="initialized")
    row = store.get_run("run_test")
    assert row["status"] == "initialized"


def test_insert_and_update_experiment(tmp_path: Path) -> None:
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()
    store.insert_run(run_id="run_test", started_at="2026-04-30T10:00:00", status="initialized")
    store.insert_experiment(
        experiment_id="exp_0001",
        run_id="run_test",
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_type="calibration",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="completed",
        metric_name="roc_auc",
        valid_submission=True,
        artifact_paths=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        created_at="2026-04-30T10:01:00",
    )
    store.update_experiment_score("exp_0001", score=0.5)
    row = store.get_latest_experiment("tabular_binary_v1")
    assert row["score"] == pytest.approx(0.5)
    assert row["valid_submission"] == 1
    assert row["provider"] == "stub_codex"
