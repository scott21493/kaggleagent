from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from arena.controller.planner import create_calibration_task_packet
from arena.controller.task_queue import TaskQueue
from arena.controller.worktree import create_workspace
from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest
from arena.providers.base import ProviderAdapter
from arena.providers.stub_claude import StubClaudeProvider
from arena.providers.stub_codex import StubCodexProvider
from arena.scoreboard.store import ScoreboardStore

app = typer.Typer(help="Kaggle Agent Arena Phase 0 harness CLI.")
console = Console()

DB_PATH = Path("scoreboard.sqlite")
RUNS_ROOT = Path("runs")
WORKTREE_ROOT = Path("worktrees")
FIXTURES_ROOT = Path("fixtures")


def _store() -> ScoreboardStore:
    s = ScoreboardStore(DB_PATH)
    s.connect()
    return s


def _new_run_id() -> str:
    return "run_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _latest_run_id() -> str | None:
    """Return the most recent run_id by lex-sorted directory name.

    Phase 0 single-fixture assumption: there is at most one fixture per
    branch, so the lex-greatest run_id under runs/ is the active one.
    PR2+ that introduces a second fixture should add a slug filter (or
    join against the runs table by competition_slug).
    """
    if not RUNS_ROOT.exists():
        return None
    runs = sorted(RUNS_ROOT.glob("run_*"))
    return runs[-1].name if runs else None


def _get_provider(name: str) -> ProviderAdapter:
    if name == "stub_codex":
        return StubCodexProvider(workspace_root=WORKTREE_ROOT)
    if name == "stub_claude":
        return StubClaudeProvider(workspace_root=WORKTREE_ROOT)
    raise typer.BadParameter(f"unknown provider: {name}")


@app.command()
def doctor() -> None:
    """Run lightweight local readiness checks."""
    validate_fixture_manifest("fixtures/tabular_binary_v1")
    console.print("[green]arena doctor passed[/green]")


@app.command("fixture-smoke")
def fixture_smoke(
    submission: str = "fixtures/tabular_binary_v1/sample_submission.csv",
    labels: str = "fixtures/tabular_binary_v1/hidden_labels.csv",
) -> None:
    """Evaluate the bundled fake tabular fixture submission."""
    result = evaluate_fixture_submission(submission, labels)
    if not result.valid_submission:
        raise typer.Exit(code=1)
    console.print(f"fixture score={result.score:.6f}")


@app.command("init-fixture")
def init_fixture(slug: str) -> None:
    """Initialize a new run for the given fixture slug."""
    fixture_dir = FIXTURES_ROOT / slug
    if not fixture_dir.exists():
        raise typer.BadParameter(f"fixture not found: {fixture_dir}")
    validate_fixture_manifest(fixture_dir)

    run_id = _new_run_id()
    (RUNS_ROOT / run_id / "queue").mkdir(parents=True, exist_ok=True)
    (RUNS_ROOT / run_id / "results").mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    _store().insert_run(run_id=run_id, started_at=started_at, status="initialized")
    console.print(f"[green]initialized {run_id}[/green]")


@app.command("plan")
def plan(slug: str) -> None:
    """Create a calibration task packet for the latest run."""
    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(f"no initialized run for {slug}; run init-fixture first")
    queue = TaskQueue(RUNS_ROOT / run_id / "queue")
    if queue.size() > 0:
        raise typer.BadParameter(f"queue is non-empty for {run_id}")
    packet = create_calibration_task_packet(
        competition_slug=slug,
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    queue.enqueue(packet)
    console.print(f"[green]planned task_0001 for {run_id}[/green]")


@app.command("run-next")
def run_next(slug: str, provider: str = typer.Option(..., "--provider")) -> None:
    """Pop the next task from the queue, invoke the provider, persist the experiment."""
    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(f"no run for {slug}")
    queue = TaskQueue(RUNS_ROOT / run_id / "queue")
    packet = queue.dequeue()
    if packet is None:
        raise typer.BadParameter(f"queue is empty for {run_id}")

    create_workspace(WORKTREE_ROOT, packet["competition_slug"], packet["experiment_id"])
    adapter = _get_provider(provider)
    result = adapter.invoke(packet)

    results_dir = RUNS_ROOT / run_id / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{packet['task_id']}.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8"
    )

    _store().insert_experiment(
        experiment_id=packet["experiment_id"],
        run_id=run_id,
        competition_slug=packet["competition_slug"],
        task_id=packet["task_id"],
        experiment_type="calibration",
        provider=adapter.name,
        provider_version=adapter.version,
        status="completed" if result.status == "success" else result.status,
        metric_name="roc_auc",
        valid_submission=None,
        artifact_paths=result.artifacts,
        trace_path=None,
        created_at=result.finished_at,
    )
    console.print(f"[green]ran {packet['task_id']} on {provider}[/green]")


@app.command("evaluate")
def evaluate(
    slug: str,
    latest: bool = typer.Option(False, "--latest", help="Evaluate the latest experiment"),
) -> None:
    """Score the latest experiment's submission against hidden labels."""
    if not latest:
        raise typer.BadParameter("only --latest is supported in PR1")

    store = _store()
    exp = store.get_latest_experiment(slug)
    if exp is None:
        raise typer.BadParameter(f"no experiment recorded for {slug}")
    raw_paths = exp["artifact_paths"]
    artifacts: list[str] = json.loads(raw_paths) if raw_paths else []
    submission = next((p for p in artifacts if p.endswith("submission.csv")), None)
    if submission is None:
        raise typer.BadParameter("no submission.csv among experiment artifacts")

    hidden = FIXTURES_ROOT / slug / "hidden_labels.csv"
    eval_result = evaluate_fixture_submission(submission, hidden)
    if not eval_result.valid_submission:
        console.print(f"[red]invalid submission: {eval_result.error}[/red]")
        raise typer.Exit(code=1)
    assert eval_result.score is not None  # narrow Optional[float] -> float for mypy

    experiment_id: str = exp["experiment_id"]
    store.update_experiment_score(experiment_id, score=eval_result.score)
    store.update_experiment_validation(experiment_id, valid_submission=True)
    console.print(f"score={eval_result.score:.6f}")
