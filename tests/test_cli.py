from __future__ import annotations

import json
from pathlib import Path

import pytest
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

    runs = [p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_")]
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

    runs = sorted(p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_"))
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
    runs = sorted(p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_"))
    queue_dir = runs[0] / "queue"
    assert len(list(queue_dir.glob("*.json"))) == 1

    # A bad --provider must fail without dequeueing.
    result = runner.invoke(
        app, ["run-next", "tabular_binary_v1", "--provider", "definitely_not_a_provider"]
    )
    assert result.exit_code != 0

    # Queue still has the original task (not dequeued by the failed CLI invocation).
    assert len(list(queue_dir.glob("*.json"))) == 1


def test_run_next_trips_shell_command_breaker_on_misbehaving_provider(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion #1: a misbehaving stub provider that emits 100
    shell command events trips ShellCommandBreaker; run halts with status=blocked.

    We don't have a misbehaving real provider, so monkeypatch StubCodexProvider
    to return shell_commands=100 in its usage_proxy.
    """
    from datetime import UTC, datetime

    from arena.cli import app
    from arena.providers.base import ProviderResult
    from arena.providers.parser import build_result
    from arena.providers.stub_codex import StubCodexProvider
    from arena.scoreboard.store import ScoreboardStore

    original_invoke = StubCodexProvider.invoke

    def misbehaving_invoke(self: StubCodexProvider, task_packet: dict) -> ProviderResult:
        result = original_invoke(self, task_packet)
        # Mutate the usage_proxy to simulate a runaway provider.
        # Frozen dataclass — rebuild via build_result.
        now = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=result.task_id,
            provider=result.provider,
            provider_version=result.provider_version,
            status=result.status,
            stdout_path=result.stdout_path,
            stderr_path=result.stderr_path,
            artifacts=result.artifacts,
            input_chars=result.usage_proxy["input_chars"],
            output_chars=result.usage_proxy["output_chars"],
            wall_seconds=result.usage_proxy["wall_seconds"],
            shell_commands=100,  # exceeds 35 ceiling
            failed_commands=result.usage_proxy["failed_commands"],
            waste_events=result.usage_proxy["waste_events"],
            started_at=result.started_at,
            finished_at=now,
        )

    monkeypatch.setattr(StubCodexProvider, "invoke", misbehaving_invoke)
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "ShellCommandBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    assert "<blocked:ShellCommandBreaker>" in exp["artifact_paths"]
    store.close()


def test_run_next_halted_by_kill_switch_leaves_task_retryable(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion #2: kill switch active before run-next halts
    the task without dequeueing. After unkill --human-confirm, retrying
    run-next succeeds. No status=blocked experiment is persisted because
    the task never started."""
    from arena.budget.kill_switch import KILL_SWITCH_ENV, KillSwitch
    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])

    runs = sorted(p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_"))
    queue_dir = runs[0] / "queue"
    assert len(list(queue_dir.glob("*.json"))) == 1, "task not enqueued by plan"

    KillSwitch.activate()
    blocked = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert blocked.exit_code != 0
    assert "kill switch" in blocked.output.lower()
    # Queue still has the task — pre-dequeue check fired.
    assert len(list(queue_dir.glob("*.json"))) == 1, (
        "kill-switched run must NOT dequeue; task left in queue"
    )

    # Unkill and retry succeeds.
    runner.invoke(app, ["unkill", "--human-confirm"])
    success = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert success.exit_code == 0, success.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    # The retry's experiment is "completed", and there is no <blocked:KillSwitch>
    # row from the first attempt because it never dequeued.
    assert exp["status"] == "completed"
    store.close()


def test_run_next_with_valid_but_wrong_provider_leaves_task_in_queue(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid provider name that doesn't match the planned packet's
    provider field must NOT dequeue the task. Regression for the P1 bug
    where --provider stub_claude against a stub_codex-planned packet
    consumed the queued file and left run-next with nothing to retry."""
    from arena.cli import app

    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])  # plans for stub_codex

    runs = sorted(p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_"))
    queue_dir = runs[0] / "queue"
    assert len(list(queue_dir.glob("*.json"))) == 1, "task not enqueued by plan"

    # Wrong-but-valid provider: stub_claude against a stub_codex-planned packet.
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_claude"])
    assert result.exit_code != 0
    assert "does not match" in result.output
    assert "left in queue" in result.output

    # Queue still has the task.
    assert len(list(queue_dir.glob("*.json"))) == 1, (
        "valid-but-wrong --provider must NOT dequeue; task left in queue"
    )

    # Retry with the correct provider succeeds.
    success = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert success.exit_code == 0, success.output


def test_run_next_post_invoke_block_persists_usage_that_tripped_it(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When wrap_invoke raises BudgetExceeded, the blocked experiment
    row must record the actual usage_proxy fields (not zeros) so that a
    subsequent `arena budget status` reflects the consumed budget.

    Regression for the P1 bug where blocked rows were persisted with
    default 0 usage, causing budget status to underreport."""
    from datetime import UTC, datetime

    from arena.cli import app
    from arena.providers.base import ProviderResult
    from arena.providers.parser import build_result
    from arena.providers.stub_codex import StubCodexProvider
    from arena.scoreboard.store import ScoreboardStore

    original_invoke = StubCodexProvider.invoke

    def runaway_input_chars(self: StubCodexProvider, task_packet: dict) -> ProviderResult:
        result = original_invoke(self, task_packet)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=result.task_id,
            provider=result.provider,
            provider_version=result.provider_version,
            status=result.status,
            stdout_path=result.stdout_path,
            stderr_path=result.stderr_path,
            artifacts=result.artifacts,
            input_chars=950_000,  # exceeds per-run input_chars_total=900_000
            output_chars=result.usage_proxy["output_chars"],
            wall_seconds=result.usage_proxy["wall_seconds"],
            shell_commands=result.usage_proxy["shell_commands"],
            failed_commands=result.usage_proxy["failed_commands"],
            waste_events=result.usage_proxy["waste_events"],
            started_at=result.started_at,
            finished_at=now,
        )

    monkeypatch.setattr(StubCodexProvider, "invoke", runaway_input_chars)
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "ProviderCallBreaker" in result.output  # input_chars overflow maps to PROVIDER_CALL

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["status"] == "blocked"
    # Critical assertion: the blocked row carries the offending usage,
    # not default zeros.
    assert exp["input_chars"] == 950_000
    store.close()


def test_two_init_fixture_plan_run_next_sequences_in_one_scoreboard(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two complete init-fixture -> plan -> run-next sequences against the
    same scoreboard must produce distinct experiment_ids (exp_0001 and
    exp_0002). Regression for the P2 bug where the second run-next
    failed with `UNIQUE constraint failed: experiments.experiment_id`
    because plan() always hardcoded `exp_0001`."""
    from arena.cli import app
    from arena.scoreboard.store import ScoreboardStore

    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()

    # First sequence.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    r1 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert r1.exit_code == 0, r1.output

    # Second sequence — same scoreboard.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    r2 = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert r2.exit_code == 0, r2.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    # Direct SQL — the store doesn't expose a list_experiments_by_slug method.
    rows = store._conn.execute(
        "SELECT experiment_id FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
        ("tabular_binary_v1",),
    ).fetchall()
    exp_ids = [r["experiment_id"] for r in rows]
    assert exp_ids == ["exp_0001", "exp_0002"], f"expected fresh exp_ids per run; got {exp_ids}"
    store.close()
