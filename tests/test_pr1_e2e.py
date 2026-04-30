"""End-to-end test for PR1 acceptance: init-fixture -> plan -> run-next -> evaluate.

Asserts every PR1 acceptance criterion from
docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md §5.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from arena.cli import app
from arena.schemas.validate import validate
from arena.scoreboard.store import ScoreboardStore


def test_pr1_full_loop_on_clean_workspace(fixture_workspace: Path) -> None:
    runner = CliRunner()

    init = runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert init.exit_code == 0, init.output

    plan = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert plan.exit_code == 0, plan.output

    run = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert run.exit_code == 0, run.output

    evaluate = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert evaluate.exit_code == 0, evaluate.output
    assert "score=" in evaluate.output

    # Acceptance criterion: scoreboard persists, experiment fully populated.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["task_id"] == "task_0001"
    assert exp["experiment_type"] == "calibration"
    assert exp["provider"] == "stub_codex"
    assert exp["provider_version"] == "stub_codex.v1"
    assert exp["valid_submission"] == 1
    assert exp["score"] == 0.5
    assert exp["status"] == "completed"

    # Acceptance criterion: ProviderResult on disk is schema-valid.
    runs = sorted((fixture_workspace / "runs").iterdir())
    result_path = runs[0] / "results" / "task_0001.json"
    payload = json.loads(result_path.read_text())
    validate("provider_result", payload)

    # Acceptance criterion: queue has been drained.
    queue_dir = runs[0] / "queue"
    assert list(queue_dir.glob("*.json")) == []
