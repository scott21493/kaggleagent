from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from arena.budget.governor import BudgetExceeded, BudgetGovernor, RunAccumulators
from arena.budget.kill_switch import KillSwitch
from arena.budget.policy import Phase0HardCeilings
from arena.controller.planner import create_calibration_task_packet
from arena.controller.task_queue import TaskQueue
from arena.controller.watchdog import KillSwitchActive, Watchdog
from arena.controller.worktree import create_workspace
from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest
from arena.providers.base import ProviderAdapter, UsageProxy
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
    """Pop the next task from the queue, invoke the provider through the
    watchdog (kill switch + budget governor), persist the experiment.

    Pre-dequeue checks (kill switch + run-level caps) raise without
    touching the queue, so a blocked invoke leaves the task retryable
    after `arena unkill --human-confirm` or after the run-level usage
    drops (won't happen in PR2 since accumulators are persistent within
    a run, but the structure is forward-compatible).
    """
    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(f"no run for {slug}")

    # Resolve the provider BEFORE dequeue so a CLI typo doesn't corrupt the queue.
    adapter = _get_provider(provider)

    # Build the governor seeded with this run's accumulated usage.
    ceilings = Phase0HardCeilings.from_env()
    store = _store()
    totals = store.get_run_usage_totals(slug, run_id)
    accumulators = RunAccumulators(
        provider_calls=int(totals["provider_calls"]),
        codex_calls=int(totals["codex_calls"]),
        claude_calls=int(totals["claude_calls"]),
        wall_seconds=float(totals["wall_seconds"]),
        input_chars=int(totals["input_chars"]),
        output_chars=int(totals["output_chars"]),
        waste_events=int(totals["waste_events"]),
    )
    governor = BudgetGovernor(ceilings, accumulators=accumulators)
    watchdog = Watchdog(governor=governor)

    # Pre-dequeue: kill switch + run-level cap check. Block here means the
    # queue is untouched and the task can be retried.
    try:
        watchdog.check_can_invoke(adapter.name)
    except KillSwitchActive as exc:
        console.print(f"[red]kill switch active; task left in queue: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except BudgetExceeded as exc:
        console.print(
            f"[red]pre-invoke budget block ({exc.breaker.value}); task left in queue: {exc}[/red]"
        )
        raise typer.Exit(code=2) from exc

    queue = TaskQueue(RUNS_ROOT / run_id / "queue")
    peeked = queue.peek()
    if peeked is None:
        raise typer.BadParameter(f"queue is empty for {run_id}")

    if peeked["provider"] != adapter.name:
        raise typer.BadParameter(
            f"packet provider {peeked['provider']!r} does not match --provider "
            f"{adapter.name!r}; task {peeked['task_id']} left in queue — retry "
            f"with `--provider {peeked['provider']}`"
        )

    packet = queue.dequeue()
    assert packet is not None  # peek confirmed presence

    create_workspace(WORKTREE_ROOT, packet["competition_slug"], packet["experiment_id"])

    # Post-dequeue: invoke + post-invoke per-task cap check. A breaker here
    # persists a status=blocked row because the task DID run and consume
    # budget — there is no clean way to unwind that.
    try:
        result = watchdog.wrap_invoke(adapter, packet)
    except BudgetExceeded as exc:
        _persist_blocked_experiment(
            store=store,
            packet=packet,
            run_id=run_id,
            adapter=adapter,
            breaker_or_reason=exc.breaker.value,
            message=str(exc),
            usage_proxy=exc.usage_proxy,
        )
        console.print(f"[red]task {packet['task_id']} blocked by {exc.breaker.value}: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    results_dir = RUNS_ROOT / run_id / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{packet['task_id']}.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8"
    )

    store.insert_experiment(
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
        input_chars=result.usage_proxy["input_chars"],
        output_chars=result.usage_proxy["output_chars"],
        wall_seconds=result.usage_proxy["wall_seconds"],
        shell_commands=result.usage_proxy["shell_commands"],
        failed_commands=result.usage_proxy["failed_commands"],
        waste_events=result.usage_proxy["waste_events"],
    )
    console.print(f"[green]ran {packet['task_id']} on {provider}[/green]")


def _persist_blocked_experiment(
    *,
    store: ScoreboardStore,
    packet: dict,
    run_id: str,
    adapter: ProviderAdapter,
    breaker_or_reason: str,
    message: str,
    usage_proxy: UsageProxy | None = None,
) -> None:
    """Persist a status=blocked experiment row carrying the breaker name as
    the first artifact path entry. Only called when the watchdog raises
    AFTER dequeue (post-invoke cap violation). PR4's event log will
    replace this with a structured event when it lands.

    If usage_proxy is provided (always the case for post-invoke
    BudgetExceeded), persist the offending usage so `arena budget status`
    correctly reflects the consumed budget. Without it, the blocked row
    would silently underreport consumed chars/wall/waste.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    artifact_paths = [f"<blocked:{breaker_or_reason}>", f"<message:{message[:200]}>"]
    usage: dict = dict(usage_proxy) if usage_proxy is not None else {}
    store.insert_experiment(
        experiment_id=packet["experiment_id"],
        run_id=run_id,
        competition_slug=packet["competition_slug"],
        task_id=packet["task_id"],
        experiment_type="calibration",
        provider=adapter.name,
        provider_version=adapter.version,
        status="blocked",
        metric_name="roc_auc",
        valid_submission=None,
        artifact_paths=artifact_paths,
        trace_path=None,
        created_at=now,
        input_chars=int(usage.get("input_chars", 0)),
        output_chars=int(usage.get("output_chars", 0)),
        wall_seconds=float(usage.get("wall_seconds", 0.0)),
        shell_commands=int(usage.get("shell_commands", 0)),
        failed_commands=int(usage.get("failed_commands", 0)),
        waste_events=int(usage.get("waste_events", 0)),
    )


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


@app.command("kill")
def kill() -> None:
    """Activate the kill switch. In-flight tasks halt at the next watchdog poll."""
    KillSwitch.activate()
    console.print("[yellow]kill switch ACTIVATED[/yellow]")


@app.command("unkill")
def unkill(
    human_confirm: bool = typer.Option(False, "--human-confirm", help="Required to deactivate"),
) -> None:
    """Deactivate the kill switch. Requires --human-confirm to prevent accidental scripting."""
    if not human_confirm:
        raise typer.BadParameter("--human-confirm is required to deactivate the kill switch")
    KillSwitch.deactivate()
    console.print("[green]kill switch deactivated[/green]")


@app.command("budget")
def budget_status(
    action: str = typer.Argument(..., help="Subcommand: status"),
    slug: str = typer.Option("tabular_binary_v1", "--slug", help="Competition slug"),
) -> None:
    """Show current budget accumulators against ceilings for the latest run.

    Phase 0 supports `arena budget status` only; future actions
    (`arena budget reset`, etc.) are PR2+ work.
    """
    if action != "status":
        raise typer.BadParameter(f"unknown budget action: {action!r}; only 'status' is supported")

    ceilings = Phase0HardCeilings.from_env()
    run_id = _latest_run_id()
    if run_id is None:
        # No runs yet — show all-zero accumulators against ceilings.
        accumulators = RunAccumulators()
    else:
        totals = _store().get_run_usage_totals(slug, run_id)
        accumulators = RunAccumulators(
            provider_calls=int(totals["provider_calls"]),
            codex_calls=int(totals["codex_calls"]),
            claude_calls=int(totals["claude_calls"]),
            wall_seconds=float(totals["wall_seconds"]),
            input_chars=int(totals["input_chars"]),
            output_chars=int(totals["output_chars"]),
            waste_events=int(totals["waste_events"]),
        )
    governor = BudgetGovernor(ceilings, accumulators=accumulators)
    snap = governor.status()
    kill_active = KillSwitch.is_active()
    console.print(f"[bold]Budget status for {slug}[/bold]")
    for key, val in snap.items():
        console.print(f"  {key}: {val['used']} / {val['ceiling']}")
    color = "red" if kill_active else "green"
    state = "ACTIVE" if kill_active else "inactive"
    console.print(f"  kill_switch: [{color}]{state}[/{color}]")
