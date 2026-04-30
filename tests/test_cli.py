from __future__ import annotations

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
