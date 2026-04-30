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


def test_partial_migration_recovers_missing_columns(tmp_path: Path) -> None:
    """If migration 0002 partially applied on a prior run (some columns
    exist, some do not, and schema_versions has no row for it),
    reconnecting must add the missing columns and only then mark the
    migration applied. Regression for code-review C1."""
    import sqlite3

    db = tmp_path / "scoreboard.sqlite"
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE schema_versions (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    # Apply migration 0001 by hand (creates runs + experiments).
    migration_0001 = (
        Path(__file__).resolve().parent.parent
        / "arena"
        / "scoreboard"
        / "migrations"
        / "0001_create_phase0_tables.sql"
    )
    raw.executescript(migration_0001.read_text(encoding="utf-8"))
    raw.execute(
        "INSERT INTO schema_versions (version, applied_at) VALUES (?, datetime('now'))",
        ("0001_create_phase0_tables",),
    )
    # Simulate partial 0002: add only the first 3 columns, do not record
    # 0002 in schema_versions.
    raw.execute("ALTER TABLE experiments ADD COLUMN competition_slug TEXT")
    raw.execute("ALTER TABLE experiments ADD COLUMN task_id TEXT")
    raw.execute("ALTER TABLE experiments ADD COLUMN experiment_type TEXT")
    raw.commit()
    raw.close()

    store = ScoreboardStore(db)
    store.connect()
    cols = set(store.experiment_columns())
    # All 15 columns from 0002 should now be present.
    assert {
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
    }.issubset(cols)
    store.close()


def test_close_is_idempotent_and_safe_when_never_connected(tmp_path: Path) -> None:
    """close() on a never-connected store is a no-op; double close is safe."""
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.close()  # never connected — no-op
    store.connect()
    store.close()
    store.close()  # double close — no-op


def test_methods_raise_runtime_error_when_not_connected(tmp_path: Path) -> None:
    """Calling SQL methods before connect() raises RuntimeError, not AttributeError."""
    store = ScoreboardStore(tmp_path / "s.sqlite")
    with pytest.raises(RuntimeError, match="connect"):
        store.experiment_columns()
    with pytest.raises(RuntimeError, match="connect"):
        store.get_run("anything")


def test_insert_experiment_accepts_usage_proxy(tmp_path: Path) -> None:
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
        input_chars=120,
        output_chars=80,
        wall_seconds=0.5,
        shell_commands=2,
        failed_commands=0,
        waste_events=0,
    )
    row = store.get_latest_experiment("tabular_binary_v1")
    assert row is not None
    assert row["input_chars"] == 120
    assert row["wall_seconds"] == pytest.approx(0.5)
    store.close()


def test_get_run_usage_totals_sums_within_run(tmp_path: Path) -> None:
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()
    store.insert_run(run_id="run_t", started_at="2026-04-30T10:00:00", status="initialized")
    for i, (provider, chars) in enumerate(
        [("stub_codex", 100), ("stub_codex", 200), ("stub_claude", 50)]
    ):
        store.insert_experiment(
            experiment_id=f"exp_{i:04d}",
            run_id="run_t",
            competition_slug="tabular_binary_v1",
            task_id=f"task_{i:04d}",
            experiment_type="calibration",
            provider=provider,
            provider_version=f"{provider}.v1",
            status="completed",
            metric_name="roc_auc",
            artifact_paths=[],
            created_at=f"2026-04-30T10:0{i + 1}:00",
            input_chars=chars,
        )
    totals = store.get_run_usage_totals("tabular_binary_v1", "run_t")
    assert totals["provider_calls"] == 3
    assert totals["codex_calls"] == 2
    assert totals["claude_calls"] == 1
    assert totals["input_chars"] == 350
    store.close()


def test_get_run_usage_totals_only_sums_specified_run(tmp_path: Path) -> None:
    """Two runs for the same slug must not bleed into each other's totals.
    A fresh init-fixture must start with zero accumulators."""
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()

    # Run 1: 1 experiment with 1000 input_chars.
    store.insert_run(run_id="run_1", started_at="2026-04-30T10:00:00", status="initialized")
    store.insert_experiment(
        experiment_id="exp_0001",
        run_id="run_1",
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_type="calibration",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="completed",
        metric_name="roc_auc",
        artifact_paths=[],
        created_at="2026-04-30T10:01:00",
        input_chars=1000,
    )

    # Run 2: 1 experiment with 2000 input_chars.
    store.insert_run(run_id="run_2", started_at="2026-04-30T11:00:00", status="initialized")
    store.insert_experiment(
        experiment_id="exp_0002",
        run_id="run_2",
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_type="calibration",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="completed",
        metric_name="roc_auc",
        artifact_paths=[],
        created_at="2026-04-30T11:01:00",
        input_chars=2000,
    )

    # Each run sees only its own totals.
    totals_1 = store.get_run_usage_totals("tabular_binary_v1", "run_1")
    assert totals_1["input_chars"] == 1000
    assert totals_1["provider_calls"] == 1

    totals_2 = store.get_run_usage_totals("tabular_binary_v1", "run_2")
    assert totals_2["input_chars"] == 2000
    assert totals_2["provider_calls"] == 1
    store.close()
