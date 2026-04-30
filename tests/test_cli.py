from __future__ import annotations

import json
from pathlib import Path

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


def test_init_fixture_creates_run_record(tmp_path, monkeypatch):
    """`arena init-fixture <slug>` creates a runs/<run_id>/ tree and
    inserts a row into the scoreboard."""
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv",
        "test.csv",
        "sample_submission.csv",
        "hidden_labels.csv",
        "competition.yaml",
        "rules.md",
        "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    (target / "paper_bundle").mkdir()
    for name in ["method_note_001.md", "method_note_002.md"]:
        (target / "paper_bundle" / name).write_bytes(
            (fixture_src / "paper_bundle" / name).read_bytes()
        )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    row = store.get_run(run_id)
    assert row is not None
    assert row["status"] == "initialized"


def test_plan_writes_calibration_task_packet(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from arena.cli import app

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv",
        "test.csv",
        "sample_submission.csv",
        "hidden_labels.csv",
        "competition.yaml",
        "rules.md",
        "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    (target / "paper_bundle").mkdir()
    for name in ["method_note_001.md", "method_note_002.md"]:
        (target / "paper_bundle" / name).write_bytes(
            (fixture_src / "paper_bundle" / name).read_bytes()
        )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = sorted((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    queue_files = list((runs[0] / "queue").glob("*.json"))
    assert len(queue_files) == 1
    packet = json.loads(queue_files[0].read_text())
    assert packet["role"] == "implementation"
    assert packet["competition_slug"] == "tabular_binary_v1"
    assert packet["task_id"] == "task_0001"


def test_run_next_invokes_provider_and_persists_experiment(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv",
        "test.csv",
        "sample_submission.csv",
        "hidden_labels.csv",
        "competition.yaml",
        "rules.md",
        "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    (target / "paper_bundle").mkdir()
    for name in ["method_note_001.md", "method_note_002.md"]:
        (target / "paper_bundle" / name).write_bytes(
            (fixture_src / "paper_bundle" / name).read_bytes()
        )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code == 0, result.output

    submission = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    assert submission.exists()
    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["task_id"] == "task_0001"
    assert exp["provider"] == "stub_codex"
    assert exp["score"] is None  # evaluate hasn't run yet
    assert exp["status"] == "completed"


def test_evaluate_updates_score(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv",
        "test.csv",
        "sample_submission.csv",
        "hidden_labels.csv",
        "competition.yaml",
        "rules.md",
        "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    (target / "paper_bundle").mkdir()
    for name in ["method_note_001.md", "method_note_002.md"]:
        (target / "paper_bundle" / name).write_bytes(
            (fixture_src / "paper_bundle" / name).read_bytes()
        )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    result = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["score"] is not None
    # Constant 0.5 predictions yield ROC-AUC ~ 0.5; allow a tolerance because
    # sklearn's roc_auc_score with all-equal scores is exactly 0.5.
    assert exp["score"] == 0.5
    assert exp["valid_submission"] == 1
