from __future__ import annotations

import json

from typer.testing import CliRunner

from arena.cli import app


def test_doctor_command() -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "arena doctor passed" in result.output


def test_fixture_smoke_command() -> None:
    result = CliRunner().invoke(app, ["fixture-smoke"])
    assert result.exit_code == 0
    assert "fixture score=" in result.output


def test_init_fixture_creates_run_record(fixture_workspace):
    """`arena init-fixture <slug>` creates a runs/<run_id>/ tree and
    inserts a row into the scoreboard."""
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    result = CliRunner().invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = list((fixture_workspace / "runs").iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    row = store.get_run(run_id)
    assert row is not None
    assert row["status"] == "initialized"


def test_plan_writes_calibration_task_packet(fixture_workspace):
    from typer.testing import CliRunner

    from arena.cli import app

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = sorted((fixture_workspace / "runs").iterdir())
    assert len(runs) == 1
    queue_files = list((runs[0] / "queue").glob("*.json"))
    assert len(queue_files) == 1
    packet = json.loads(queue_files[0].read_text())
    assert packet["role"] == "implementation"
    assert packet["competition_slug"] == "tabular_binary_v1"
    assert packet["task_id"] == "task_0001"


def test_run_next_invokes_provider_and_persists_experiment(fixture_workspace):
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code == 0, result.output

    submission = (
        fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    )
    assert submission.exists()
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["task_id"] == "task_0001"
    assert exp["provider"] == "stub_codex"
    assert exp["score"] is None  # evaluate hasn't run yet
    assert exp["status"] == "completed"


def test_evaluate_updates_score(fixture_workspace):
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    result = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["score"] is not None
    # Constant 0.5 predictions yield ROC-AUC ~ 0.5; allow a tolerance because
    # sklearn's roc_auc_score with all-equal scores is exactly 0.5.
    assert exp["score"] == 0.5
    assert exp["valid_submission"] == 1


def test_run_next_with_unknown_provider_does_not_dequeue(fixture_workspace):
    """Validating --provider before dequeue prevents queue corruption on typo."""
    from typer.testing import CliRunner

    from arena.cli import app

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])

    # Confirm queue has the single task before run-next is attempted.
    runs = sorted((fixture_workspace / "runs").iterdir())
    queue_dir = runs[0] / "queue"
    assert len(list(queue_dir.glob("*.json"))) == 1

    # A bad --provider must fail without dequeueing.
    result = runner.invoke(
        app, ["run-next", "tabular_binary_v1", "--provider", "definitely_not_a_provider"]
    )
    assert result.exit_code != 0

    # Queue still has the original task (not dequeued by the failed CLI invocation).
    assert len(list(queue_dir.glob("*.json"))) == 1
