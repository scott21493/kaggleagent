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
from arena.controller.state import Phase
from arena.controller.task_queue import TaskQueue
from arena.controller.watchdog import KillSwitchActive, Watchdog
from arena.controller.worktree import create_workspace
from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import compute_fixture_set_digest, validate_fixture_manifest
from arena.observability.replay import replay_run
from arena.observability.report import render_run_report
from arena.observability.trace_store import TraceStore
from arena.observability.version_baseline import record_fixture_hash, record_provider_version
from arena.providers.base import ProviderAdapter, ProviderResult, UsageProxy
from arena.providers.stub_claude import StubClaudeProvider
from arena.providers.stub_codex import StubCodexProvider
from arena.research_proxy.fusion_proposal import (
    make_fusion_proposal_packet,
    validate_fusion_proposal,
)
from arena.research_proxy.fusion_scorer import (
    MIN_FUSION_SCORE,
    is_eligible,
    score_fusion_proposal,
)
from arena.research_proxy.method_digest import (
    make_method_digest_packet,
    validate_paper_digest,
)
from arena.research_proxy.question_generator import make_research_question_packet
from arena.review.packet import make_review_packet, validate_research_review
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.runner import SandboxRunner, SandboxViolation
from arena.schemas.validate import validate as validate_schema
from arena.scoreboard.store import ScoreboardStore

app = typer.Typer(help="Kaggle Agent Arena Phase 0 harness CLI.")
console = Console()

DB_PATH = Path("scoreboard.sqlite")
RUNS_ROOT = Path("runs")
WORKTREE_ROOT = Path("worktrees")
FIXTURES_ROOT = Path("fixtures")
TRACES_ROOT = Path("traces")

# Tag appended to artifact_paths when provider_version drift is detected.
# NOT a Phase enum value — provider drift is informational, the run
# completes. The Phase enum is mirrored exactly by
# schemas/task_packet.schema.json's phase list (verified by
# tests/test_controller_state.py); adding a value without updating the
# schema would break the drift guard.
PROVIDER_VERSION_CHANGED_TAG = "PROVIDER_VERSION_CHANGED"

# Token prefix used in artifact_paths to link a research-proxy experiment
# row to its fusion proposal. Mirrors PROVIDER_VERSION_CHANGED_TAG: not a
# Phase enum value, just metadata in artifact_paths.
FUSION_ID_TAG_PREFIX = "fusion_id"


def _store() -> ScoreboardStore:
    s = ScoreboardStore(DB_PATH)
    s.connect()
    return s


def _new_run_id() -> str:
    """Mint a fresh run_id with microsecond precision.

    Two consecutive `arena init-fixture` calls in the same wall-second
    used to produce identical run_ids, causing the second `insert_run`
    to fail on the runs.run_id PRIMARY KEY constraint. Tests that
    exercise multi-run scenarios (e.g., the cross-run linkage regression
    in tests/test_cli_review.py) had to sleep ≥1s between init-fixtures
    to dodge the collision.

    Microsecond precision (%f → 6-digit microseconds) makes the run_id
    monotonically unique under any reasonable invocation rate. Lex sort
    over `runs/run_*` directories still works because microseconds
    preserve ordering within the same second.
    """
    return "run_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")


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


def _get_provider(
    name: str,
    *,
    event_emitter: TraceStore | None = None,
) -> ProviderAdapter:
    if name == "stub_codex":
        return StubCodexProvider(workspace_root=WORKTREE_ROOT, event_emitter=event_emitter)
    if name == "stub_claude":
        return StubClaudeProvider(workspace_root=WORKTREE_ROOT, event_emitter=event_emitter)
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

    # experiment_id increments across runs — the PRIMARY KEY in
    # experiments would collide if every plan() hardcoded "exp_0001".
    exp_id = _store().get_next_experiment_id(slug)

    packet = create_calibration_task_packet(
        competition_slug=slug,
        task_id="task_0001",
        experiment_id=exp_id,
        provider="stub_codex",
    )
    queue.enqueue(packet)
    console.print(f"[green]planned task_0001 ({exp_id}) for {run_id}[/green]")


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

    # Resolve the provider BEFORE dequeue so a CLI typo doesn't corrupt the
    # queue. NOTE: this adapter is for name validation only — it is rebuilt
    # AFTER the trace store is constructed (see below) so it can wire in
    # event_emitter for shell_command_observed emission. If the rebuild
    # below ever returns a different name, the packet provider check at
    # `peeked["provider"] != adapter.name` would catch the divergence.
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

    # Build the packet-scoped sandbox AFTER dequeue: allowed_writes is the
    # active packet's allowed_paths (this experiment's worktree only).
    # Writes to a sibling worktree, a different competition, or fixtures
    # all trip ProtectedFileBreaker.
    #
    # Path.cwd() resolves the packet's relative paths: in production the CLI
    # runs from the workspace root; in tests, fixture_workspace's
    # monkeypatch.chdir(tmp_path) makes Path.cwd() the per-test tmp dir.
    sandbox_policy = SandboxPolicy.from_packet(packet, workspace_root=Path.cwd())
    sandbox = SandboxRunner(sandbox_policy)

    # PR4 observability: build the trace store, record provider-version
    # baseline (per-slug, persists across init-fixture cycles), and emit
    # the provider_version_recorded event. Drift produces a warning event
    # and a PROVIDER_VERSION_CHANGED tag in artifact_paths but does NOT
    # halt the run (informational, not a breaker).
    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)

    # PR4 Task 7 (security spec §9 #9): compute fixture-set digest, compare
    # to per-slug baseline, halt on drift. The digest covers actual file
    # contents (sorted (rel_path, sha256(file_contents)) pairs from the
    # manifest), so corrupting train.csv after init-fixture is detected.
    try:
        fixture_hash = compute_fixture_set_digest(FIXTURES_ROOT / slug)
        _is_new_fixture, drifted_from_fixture = record_fixture_hash(
            competition_slug=slug,
            fixture_hash=fixture_hash,
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        # Two failure modes converge here:
        # 1. FileNotFoundError: fixture manifest missing post-dequeue
        #    (fixtures dir deleted, slug typo, pipeline corruption).
        # 2. JSONDecodeError: runs/.baselines/<slug>/fixture_hash.json is
        #    corrupt (incomplete write, manual edit, disk corruption).
        # Either way, the task is already dequeued — without this guard
        # the exception would propagate as an unhandled traceback,
        # leaving the task permanently lost. Persist a blocked row and
        # exit cleanly. Corrupt baseline = reproducibility violation.
        if isinstance(exc, FileNotFoundError):
            message = f"fixture manifest missing for {slug}: {exc}"
        else:
            message = f"fixture state read failed for {slug}: {exc}"
        _persist_blocked_experiment(
            store=store,
            packet=packet,
            run_id=run_id,
            adapter=adapter,
            breaker_or_reason=Phase.BLOCKED_REPRODUCIBILITY.value,
            message=message,
            usage_proxy=None,
        )
        console.print(
            f"[red]task {packet['task_id']} blocked: "
            f"{Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc
    trace_store.emit(
        event_type="run_started",
        severity="info" if not drifted_from_fixture else "error",
        payload={
            "sha256": fixture_hash,
            "previous_hash": drifted_from_fixture or "",
            "phase": Phase.NEW.value
            if not drifted_from_fixture
            else Phase.BLOCKED_REPRODUCIBILITY.value,
        },
    )
    if drifted_from_fixture:
        # Fixture drift = halt the run. The blocked row tells operators
        # which slug + which previous digest diverged. The baseline is
        # sticky (per-slug, not per-run), so this fires consistently
        # until a human deliberately resets the baseline.
        _persist_blocked_experiment(
            store=store,
            packet=packet,
            run_id=run_id,
            adapter=adapter,
            breaker_or_reason=Phase.BLOCKED_REPRODUCIBILITY.value,
            message=(
                f"fixture digest drift for {slug}: was {drifted_from_fixture}, now {fixture_hash}"
            ),
            usage_proxy=None,
        )
        console.print(
            f"[red]task {packet['task_id']} blocked: "
            f"{Phase.BLOCKED_REPRODUCIBILITY.value} (fixture drift on {slug})[/red]"
        )
        raise typer.Exit(code=2)

    try:
        _is_new, drifted_from = record_provider_version(
            competition_slug=slug,
            provider=adapter.name,
            version=adapter.version,
        )
    except json.JSONDecodeError as exc:
        # runs/.baselines/<slug>/provider_versions.json is corrupt.
        # Same task-loss risk as the fixture-hash block above; same fix.
        _persist_blocked_experiment(
            store=store,
            packet=packet,
            run_id=run_id,
            adapter=adapter,
            breaker_or_reason=Phase.BLOCKED_REPRODUCIBILITY.value,
            message=f"provider version baseline corrupt for {slug}: {exc}",
            usage_proxy=None,
        )
        console.print(
            f"[red]task {packet['task_id']} blocked: "
            f"{Phase.BLOCKED_REPRODUCIBILITY.value} (provider version baseline corrupt for {slug})[/red]"
        )
        raise typer.Exit(code=2) from exc
    trace_store.emit(
        event_type="provider_version_recorded",
        severity="warning" if drifted_from else "info",
        task_id=packet["task_id"],
        payload={
            "provider": adapter.name,
            "provider_version": adapter.version,
            # previous_hash field is reused from the fixture-drift use case
            # (Task 7); here it carries the previous PROVIDER VERSION string,
            # not a content hash. The field is generic in event.schema.json.
            "previous_hash": drifted_from or "",
        },
    )
    version_drift_tag = (
        f"<{PROVIDER_VERSION_CHANGED_TAG}:from={drifted_from}>" if drifted_from else ""
    )

    # PR4 Task 6: rebuild the adapter with the trace store wired in so it
    # can emit shell_command_observed events that the watchdog picks up
    # via the live waste observer callback.
    adapter = _get_provider(provider, event_emitter=trace_store)

    # Post-dequeue: invoke + post-invoke per-task cap check. A breaker here
    # persists a status=blocked row because the task DID run and consume
    # budget — there is no clean way to unwind that.
    try:
        result = watchdog.wrap_invoke(adapter, packet, sandbox=sandbox, event_emitter=trace_store)
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
    except SandboxViolation as exc:
        # exc message already includes the target ("sandbox <kind> denied: <target>"),
        # so str(exc) is the right human-triage payload — no need to append target again.
        _persist_blocked_experiment(
            store=store,
            packet=packet,
            run_id=run_id,
            adapter=adapter,
            breaker_or_reason=exc.breaker.value,
            message=str(exc),
            usage_proxy=None,
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
        artifact_paths=[
            *result.artifacts,
            *([version_drift_tag] if version_drift_tag else []),
        ],
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

    # PR4: emit score_recorded into the run's trace store so `arena replay`
    # can reconstruct the actual evaluated score. Pull the run_id off the
    # experiment row (run_next persisted it). When run_id is missing
    # (e.g., a manually-seeded experiment from a fixture-smoke test) the
    # emit is skipped — no run, no trace dir.
    run_id_for_event = exp["run_id"]
    if run_id_for_event:
        evaluate_trace_store = TraceStore(run_id=run_id_for_event, root=TRACES_ROOT)
        evaluate_trace_store.emit(
            event_type="score_recorded",
            severity="info",
            task_id=exp["task_id"],
            payload={
                "score": eval_result.score,
                "metric_name": exp["metric_name"] or "",
                "experiment_id": experiment_id,
                "status": "valid" if eval_result.valid_submission else "invalid",
            },
        )

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


@app.command()
def replay(run_id: str) -> None:
    """Reconstruct a run's view from its event traces."""
    view = replay_run(run_id=run_id, root=TRACES_ROOT)
    console.print(f"[green]Run {view.run_id}: {len(view.tasks)} task(s)[/green]")
    if view.fixture_manifest_hash:
        console.print(f"fixture_manifest_hash: {view.fixture_manifest_hash}")
    for task in view.tasks:
        breakers = ", ".join(task.breakers) or "none"
        console.print(
            f"  {task.task_id}: status={task.status} score={task.score} "
            f"provider={task.provider}@{task.provider_version} breakers={breakers}"
        )
    if view.breaker_counts:
        for breaker, count in sorted(view.breaker_counts.items()):
            console.print(f"[red]{breaker}: {count}[/red]")


@app.command()
def report(competition_slug: str) -> None:
    """Render a markdown run report for the latest run of `competition_slug`."""
    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(f"no run for {competition_slug}")
    view = replay_run(run_id=run_id, root=TRACES_ROOT)
    # Bare print, NOT console.print: Rich would reformat markdown markers
    # (#, |, etc.) as Rich markup and break the output for downstream
    # tools that expect plain markdown.
    print(render_run_report(view))


@app.command("research-proxy")
def research_proxy(
    competition_slug: str,
    provider: str = typer.Option(
        "stub_claude",
        "--provider",
        help="Provider to use for the research/digest/fusion steps. The "
        "implementation step (step 7) always uses stub_codex in PR5.",
    ),
) -> None:
    """Run the §6.2 research-fusion proxy loop steps 1-8 against the
    first method note in fixtures/<slug>/paper_bundle/.

    Persists FOUR experiment rows under one run_id — one per provider
    invocation. Every row uses experiment_type="research_proxy" (the
    schema-allowed enum value) and the per-step distinction is encoded
    in artifact_paths as a <step:NAME> token where NAME is "question",
    "digest", "fusion", or "implementation" (mirrors PR4's
    <PROVIDER_VERSION_CHANGED:...> pattern; no schema migration). Each
    row carries its own usage_proxy from the corresponding ProviderResult,
    so `arena budget status` and pre-invoke caps see all four calls. The
    fusion_id token appears in artifact_paths starting from row 3 (when
    fusion_id is first known). The implementation row (row 4) gets the
    score via `arena evaluate`'s flow.

    On step-6 gate failure, rows 1-3 are completed and NO row 4 is
    inserted — stub_codex was never invoked, so provider_calls (derived
    from COUNT(*) by get_run_usage_totals) must not increment. On
    POST-invoke exception (BudgetExceeded from record_post_invoke,
    SandboxViolation inside wrap_invoke), the in-flight step's row is
    inserted as status=blocked with the partial state captured AND
    usage_proxy threaded through from the exception. Pre-invoke
    exceptions (KillSwitchActive, ProviderCallBreaker tripped in
    check_can_invoke) leave the scoreboard untouched. Mirrors
    arena run-next in arena/cli.py:185-377.
    """
    if provider not in {"stub_claude"}:
        raise typer.BadParameter(
            f"unknown research provider {provider!r}; PR5 supports only stub_claude"
        )

    method_note_path = f"fixtures/{competition_slug}/paper_bundle/method_note_001.md"
    if not Path(method_note_path).exists():
        raise typer.BadParameter(f"method note missing: {method_note_path}")

    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(
            f"no run for {competition_slug}; run `arena init-fixture {competition_slug}` first"
        )
    store = _store()

    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)

    research_adapter = _get_provider(provider, event_emitter=trace_store)
    impl_adapter = _get_provider("stub_codex", event_emitter=trace_store)

    # PR4 reproducibility precheck — mirrors arena run-next at
    # arena/cli.py:235-340. Fixture-digest drift OR corrupt baseline halts
    # the chain before any provider invocation. Provider-version drift
    # (per adapter) tags the rows that adapter actually drove. Pre-invoke
    # failures here do NOT persist scoreboard rows — same discipline as
    # KillSwitchActive: no provider call happened.
    try:
        fixture_hash = compute_fixture_set_digest(FIXTURES_ROOT / competition_slug)
        _is_new_fixture, drifted_from_fixture = record_fixture_hash(
            competition_slug=competition_slug,
            fixture_hash=fixture_hash,
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        if isinstance(exc, FileNotFoundError):
            message = f"fixture manifest missing for {competition_slug}: {exc}"
        else:
            message = f"fixture state read failed for {competition_slug}: {exc}"
        console.print(
            f"[red]research-proxy blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    if drifted_from_fixture:
        message = (
            f"fixture digest drift for {competition_slug}: "
            f"was {drifted_from_fixture}, now {fixture_hash}"
        )
        console.print(
            f"[red]research-proxy blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2)

    trace_store.emit(
        event_type="run_started",
        severity="info",
        payload={
            "sha256": fixture_hash,
            "previous_hash": drifted_from_fixture or "",
            "phase": Phase.NEW.value,
        },
    )

    # Provider-version baselines: research_adapter (steps 2/4/5) +
    # impl_adapter (step 7). Drift tags propagate into artifact_paths on
    # the rows that adapter drove.
    try:
        _is_new_research, drifted_from_research = record_provider_version(
            competition_slug=competition_slug,
            provider=research_adapter.name,
            version=research_adapter.version,
        )
        _is_new_impl, drifted_from_impl = record_provider_version(
            competition_slug=competition_slug,
            provider=impl_adapter.name,
            version=impl_adapter.version,
        )
    except json.JSONDecodeError as exc:
        message = f"provider version baseline corrupt for {competition_slug}: {exc}"
        console.print(
            f"[red]research-proxy blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    trace_store.emit(
        event_type="provider_version_recorded",
        severity="warning" if drifted_from_research else "info",
        payload={
            "provider": research_adapter.name,
            "provider_version": research_adapter.version,
            "previous_hash": drifted_from_research or "",
        },
    )
    trace_store.emit(
        event_type="provider_version_recorded",
        severity="warning" if drifted_from_impl else "info",
        payload={
            "provider": impl_adapter.name,
            "provider_version": impl_adapter.version,
            "previous_hash": drifted_from_impl or "",
        },
    )

    research_drift_tag = (
        f"<{PROVIDER_VERSION_CHANGED_TAG}:from={drifted_from_research}>"
        if drifted_from_research
        else None
    )
    impl_drift_tag = (
        f"<{PROVIDER_VERSION_CHANGED_TAG}:from={drifted_from_impl}>" if drifted_from_impl else None
    )
    research_drift_extras = [research_drift_tag] if research_drift_tag else []
    impl_drift_extras = [impl_drift_tag] if impl_drift_tag else []

    # Seed governor accumulators from prior usage on this run so PR5
    # respects run-level provider-call caps already consumed by
    # calibration or earlier research-proxy invocations.
    totals = store.get_run_usage_totals(competition_slug, run_id)
    accumulators = RunAccumulators(
        provider_calls=int(totals["provider_calls"]),
        codex_calls=int(totals["codex_calls"]),
        claude_calls=int(totals["claude_calls"]),
        wall_seconds=float(totals["wall_seconds"]),
        input_chars=int(totals["input_chars"]),
        output_chars=int(totals["output_chars"]),
        waste_events=int(totals["waste_events"]),
    )
    governor = BudgetGovernor(Phase0HardCeilings.from_env(), accumulators=accumulators)
    watchdog = Watchdog(governor=governor)

    def _step_ids() -> tuple[str, str]:
        """Mint matched (experiment_id, task_id) for the next step.

        Each invocation gets its own row in the experiments table so
        provider_calls (derived from COUNT(*) by get_run_usage_totals) is
        accurate. task_id matches the numeric suffix so trace events
        cluster by step in arena replay output.
        """
        exp_id = store.get_next_experiment_id(competition_slug)
        task_id = exp_id.replace("exp_", "task_")
        return exp_id, task_id

    def _guarded_invoke(adapter: ProviderAdapter, packet: dict) -> ProviderResult:
        """Run check_can_invoke + wrap_invoke with the same sandbox.

        check_can_invoke catches kill-switch + pre-invoke provider-call cap
        BEFORE any invoke work runs. wrap_invoke catches SandboxViolation,
        mid-invoke BudgetExceeded (live waste detector), and post-invoke
        BudgetExceeded.

        Sets in_flight["invocation_started"] = True ONLY after
        check_can_invoke succeeds, so the outer except handlers can tell
        whether the failure was pre-invoke (no row) vs post-invoke (row
        with usage_proxy). Mirrors arena/cli.py:185-194 (run-next).
        """
        # Build a packet-scoped sandbox: each step's allowed_paths is its
        # own experiment worktree.
        per_step_sandbox = SandboxRunner(
            SandboxPolicy.from_packet(packet, workspace_root=Path.cwd())
        )
        # Pre-invoke: kill switch + run-level provider-call cap.
        # Failures here mean NO invocation happened, so the outer except
        # must NOT persist a blocked row. invocation_started stays False.
        watchdog.check_can_invoke(adapter.name)
        # Past this point, we are about to invoke. From here on, an
        # exception (BudgetExceeded post-invoke, SandboxViolation, etc.)
        # reflects work that actually started, and a blocked row is
        # appropriate.
        in_flight["invocation_started"] = True
        return watchdog.wrap_invoke(
            adapter, packet, sandbox=per_step_sandbox, event_emitter=trace_store
        )

    def _persist_row(
        *,
        experiment_id: str,
        task_id: str,
        experiment_type: str,
        adapter_name: str,
        adapter_version: str,
        status: str,
        artifact_paths: list[str],
        usage_proxy: UsageProxy | None,
        score: float | None = None,
        valid_submission: bool | None = None,
    ) -> None:
        """Insert one research-proxy experiment row with consistent shape.

        usage_proxy=None means no usage was reported (e.g. SandboxViolation
        with no usage attached); the row records zeros. For post-invoke
        BudgetExceeded the caller MUST pass usage_proxy=exc.usage_proxy
        so the consumed usage is durable for the next run's seeded
        accumulators.
        """
        store.insert_experiment(
            experiment_id=experiment_id,
            run_id=run_id,
            competition_slug=competition_slug,
            task_id=task_id,
            experiment_type=experiment_type,
            provider=adapter_name,
            provider_version=adapter_version,
            status=status,
            metric_name="roc_auc",
            valid_submission=valid_submission,
            artifact_paths=artifact_paths,
            trace_path=None,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            input_chars=int(usage_proxy["input_chars"]) if usage_proxy else 0,
            output_chars=int(usage_proxy["output_chars"]) if usage_proxy else 0,
            wall_seconds=float(usage_proxy["wall_seconds"]) if usage_proxy else 0.0,
            shell_commands=int(usage_proxy["shell_commands"]) if usage_proxy else 0,
            failed_commands=int(usage_proxy["failed_commands"]) if usage_proxy else 0,
            waste_events=int(usage_proxy["waste_events"]) if usage_proxy else 0,
        )
        if score is not None:
            store.update_experiment_score(experiment_id, score=score)

    # Track the in-flight step so a POST-invoke exception can persist a
    # blocked row for the failing step. invocation_started is set to True
    # ONLY after check_can_invoke succeeds in _guarded_invoke; pre-invoke
    # failures (KillSwitchActive, ProviderCallBreaker in check_can_invoke)
    # leave it False so no row is inserted — keeps COUNT(*)-derived
    # provider_calls accurate. Mirrors arena run-next in arena/cli.py.
    in_flight: dict[str, str | bool | None] = {
        "experiment_id": None,
        "task_id": None,
        "step": None,  # "question" / "digest" / "fusion" / "implementation"
        "adapter_name": None,
        "adapter_version": None,
        "invocation_started": False,
    }

    fusion_id_known: str | None = None  # populated after step 5

    try:
        # Step 1+2+3: research_question task → stub_claude → validate.
        rq_exp, rq_task = _step_ids()
        in_flight.update(
            experiment_id=rq_exp,
            task_id=rq_task,
            step="question",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            # Reset for each new step; _guarded_invoke flips this to True
            # only after check_can_invoke succeeds.
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, rq_exp)
        rq_packet = make_research_question_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=rq_exp,
            task_id=rq_task,
            question_id="rq_0001",
            source_refs=[method_note_path],
        )
        rq_result = _guarded_invoke(research_adapter, rq_packet)
        rq_artifact = _require_artifact(
            rq_result.artifacts,
            suffix="research_question.json",
            step_label="step 1-3",
            provider_name=research_adapter.name,
        )
        rq_payload = json.loads(Path(rq_artifact).read_text(encoding="utf-8"))
        validate_schema("research_question", rq_payload)
        _persist_row(
            experiment_id=rq_exp,
            task_id=rq_task,
            experiment_type="research_proxy",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=["<step:question>", rq_artifact, *research_drift_extras],
            usage_proxy=rq_result.usage_proxy,
        )
        console.print(f"[green]step 1-3 ok[/green]: research_question {rq_payload['question_id']}")

        # Step 4: digest → paper_digest.json.
        digest_exp, digest_task = _step_ids()
        in_flight.update(
            experiment_id=digest_exp,
            task_id=digest_task,
            step="digest",
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, digest_exp)
        digest_packet = make_method_digest_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=digest_exp,
            task_id=digest_task,
            digest_id="pd_0001",
            method_note_path=method_note_path,
        )
        digest_result = _guarded_invoke(research_adapter, digest_packet)
        digest_artifact = _require_artifact(
            digest_result.artifacts,
            suffix="paper_digest.json",
            step_label="step 4",
            provider_name=research_adapter.name,
        )
        digest_payload = json.loads(Path(digest_artifact).read_text(encoding="utf-8"))
        validate_paper_digest(digest_payload)
        _persist_row(
            experiment_id=digest_exp,
            task_id=digest_task,
            experiment_type="research_proxy",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=["<step:digest>", digest_artifact, *research_drift_extras],
            usage_proxy=digest_result.usage_proxy,
        )
        console.print(f"[green]step 4 ok[/green]: paper_digest {digest_payload['digest_id']}")

        # Step 5: fusion proposal → fusion_proposal.json.
        fp_exp, fp_task = _step_ids()
        in_flight.update(
            experiment_id=fp_exp,
            task_id=fp_task,
            step="fusion",
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, fp_exp)
        fp_packet = make_fusion_proposal_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=fp_exp,
            task_id=fp_task,
            fusion_id="fusion_0001",
            digest_path=digest_artifact,
        )
        fp_result = _guarded_invoke(research_adapter, fp_packet)
        fp_artifact = _require_artifact(
            fp_result.artifacts,
            suffix="fusion_proposal.json",
            step_label="step 5",
            provider_name=research_adapter.name,
        )
        fp_payload = json.loads(Path(fp_artifact).read_text(encoding="utf-8"))
        validate_fusion_proposal(fp_payload)
        fusion_id_known = fp_payload["fusion_id"]
        fusion_token = f"<{FUSION_ID_TAG_PREFIX}:{fusion_id_known}>"
        _persist_row(
            experiment_id=fp_exp,
            task_id=fp_task,
            experiment_type="research_proxy",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=["<step:fusion>", fp_artifact, fusion_token, *research_drift_extras],
            usage_proxy=fp_result.usage_proxy,
        )
        console.print(f"[green]step 5 ok[/green]: fusion_proposal {fusion_id_known}")

        # Step 6: deterministic gate. Halt before stub_codex if score is
        # too low OR is_eligible returns False. NO row is inserted because
        # stub_codex was never invoked — provider_calls (derived from
        # COUNT(*) by get_run_usage_totals) must not increment for a
        # would-be call that never happened.
        fusion_score = score_fusion_proposal(fp_payload)
        eligible, reasons = is_eligible(fp_payload)
        console.print(
            f"[blue]step 6 score={fusion_score.score:.3f} "
            f"(cost={fusion_score.cost:.2f} risk={fusion_score.risk:.2f} "
            f"fit={fusion_score.fit:.2f}) eligible={eligible}[/blue]"
        )
        if fusion_score.score < MIN_FUSION_SCORE or not eligible:
            gate_message = (
                f"fusion gate failed: score={fusion_score.score:.3f} "
                f"(min={MIN_FUSION_SCORE}); reasons={reasons or ['low score']}"
            )
            # NO row inserted: stub_codex was never invoked, so
            # provider_calls must not increment. The 3 successful rows
            # (question, digest, fusion) already in scoreboard tell the
            # operator exactly how far the chain got. The fusion row's
            # artifact_paths carries the fusion_proposal JSON path; the
            # gate decision is reproducible from that payload via
            # arena replay or score_fusion_proposal.
            console.print(f"[red]{gate_message}[/red]")
            raise typer.Exit(code=2)

        # Step 7: stub_codex implements the proxy.
        proxy_exp, proxy_task = _step_ids()
        in_flight.update(
            experiment_id=proxy_exp,
            task_id=proxy_task,
            step="implementation",
            adapter_name=impl_adapter.name,
            adapter_version=impl_adapter.version,
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, proxy_exp)
        proxy_packet = {
            "schema_version": "task_packet.v1",
            "task_id": proxy_task,
            "competition_slug": competition_slug,
            "experiment_id": proxy_exp,
            "provider": "stub_codex",
            "role": "implementation",
            "phase": "FUSION_PROXY_IMPLEMENTED",
            "objective": (
                f"Implement the smallest proxy test from fusion_proposal "
                f"{fusion_id_known}. Inputs[0] is the fusion proposal "
                "path; emit submission.csv that satisfies "
                "fixtures/<slug>/sample_submission.csv columns."
            ),
            "inputs": [fp_artifact, f"fixtures/{competition_slug}/test.csv"],
            "allowed_paths": [f"worktrees/{competition_slug}/{proxy_exp}/"],
            "blocked_paths": [
                "~/.kaggle/",
                "~/.codex/",
                "~/.claude/",
                ".env",
                f"fixtures/{competition_slug}/hidden_labels.csv",
            ],
            "budgets": {
                "max_wall_minutes": 20,
                "max_shell_commands": 35,
                "max_failed_commands": 5,
                "max_input_chars": 75000,
                "max_output_chars": 25000,
            },
            "required_outputs": ["submission.csv"],
            "success_criteria": ["valid"],
        }
        proxy_result = _guarded_invoke(impl_adapter, proxy_packet)
        submission_path = _require_artifact(
            proxy_result.artifacts,
            suffix="submission.csv",
            step_label="step 7",
            provider_name=impl_adapter.name,
        )
        # stub_codex appends <fusion_id:fusion_NNNN> on FUSION_PROXY_IMPLEMENTED.
        # Use the existing token if present, otherwise fall through to fusion_token.
        fusion_id_token = next(
            (a for a in proxy_result.artifacts if a.startswith(f"<{FUSION_ID_TAG_PREFIX}:")),
            fusion_token,
        )
        console.print(f"[green]step 7 ok[/green]: proxy submission {submission_path}")

        # Step 8: evaluate the proxy submission.
        hidden = FIXTURES_ROOT / competition_slug / "hidden_labels.csv"
        eval_result = evaluate_fixture_submission(submission_path, hidden)
        if not eval_result.valid_submission:
            _persist_row(
                experiment_id=proxy_exp,
                task_id=proxy_task,
                experiment_type="research_proxy",
                adapter_name=impl_adapter.name,
                adapter_version=impl_adapter.version,
                status="blocked",
                artifact_paths=[
                    "<step:implementation>",
                    submission_path,
                    fusion_id_token,
                    "<blocked:InvalidSubmission>",
                    f"<message:{(eval_result.error or 'invalid')[:200]}>",
                    *impl_drift_extras,
                ],
                usage_proxy=proxy_result.usage_proxy,
            )
            console.print(f"[red]step 8 invalid submission: {eval_result.error}[/red]")
            raise typer.Exit(code=1)
        assert eval_result.score is not None
        console.print(f"[green]step 8 ok[/green]: score={eval_result.score:.6f}")

        _persist_row(
            experiment_id=proxy_exp,
            task_id=proxy_task,
            experiment_type="research_proxy",
            adapter_name=impl_adapter.name,
            adapter_version=impl_adapter.version,
            status="completed",
            artifact_paths=[
                "<step:implementation>",
                submission_path,
                fusion_id_token,
                *impl_drift_extras,
            ],
            usage_proxy=proxy_result.usage_proxy,
            score=eval_result.score,
            valid_submission=True,
        )

        # Emit score_recorded for replay (mirrors the evaluate command).
        trace_store.emit(
            event_type="score_recorded",
            severity="info",
            task_id=proxy_task,
            payload={
                "score": eval_result.score,
                "metric_name": "roc_auc",
                "experiment_id": proxy_exp,
                "status": "valid",
            },
        )

        console.print(
            f"[bold green]research-proxy complete[/bold green] — "
            f"fusion_id={fusion_id_known} score={eval_result.score:.6f}"
        )
    except KillSwitchActive as exc:
        # Always pre-invoke (check_can_invoke is the only place that
        # raises this). No provider call happened → no row.
        console.print(f"[red]kill switch active: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except BudgetExceeded as exc:
        if in_flight["invocation_started"]:
            # Post-invoke: provider returned, then per-task cap or
            # post-invoke run-level cap tripped in record_post_invoke.
            # Persist with usage_proxy from the exception so consumed
            # usage is durable for the next run's seeded accumulators.
            _persist_inflight_blocked(
                _persist_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=exc.usage_proxy,
            )
        # Pre-invoke (ProviderCallBreaker tripped in check_can_invoke):
        # no row — that would inflate COUNT(*) into a fake provider call.
        console.print(f"[red]budget exceeded ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except SandboxViolation as exc:
        # SandboxViolation only fires from inside wrap_invoke (sandbox is
        # active during adapter.invoke), so invocation_started is always
        # True here. Defensive guard kept for symmetry.
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=None,
            )
        console.print(f"[red]sandbox violation ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc


@app.command("review")
def review(
    competition_slug: str,
    provider: str = typer.Option(
        "stub_claude",
        "--provider",
        help="Provider to use for the review step. PR6 supports only stub_claude.",
    ),
    experiment: str = typer.Option(
        ...,
        "--experiment",
        help="experiment_id of the research-proxy implementation row to review.",
    ),
) -> None:
    """Run §6.2 step 9 (Claude review) against a previously-completed
    research-proxy implementation row.

    Resolves the impl row from the scoreboard, extracts its
    <fusion_id:...> token + submission.csv artifact, locates the
    originating fusion_proposal.json, and invokes stub_claude with
    role="review" + phase="FUSION_PROXY_REVIEWED" to emit a
    research_review.json artifact.

    Persists ONE scoreboard row (experiment_type="research_proxy",
    <step:review> token in artifact_paths). Mirrors arena run-next /
    arena research-proxy's pre-invoke vs post-invoke discipline:
    KillSwitchActive / pre-invoke ProviderCallBreaker / fixture-digest
    drift = no row; post-invoke BudgetExceeded with usage_proxy =
    blocked row WITH consumed usage threaded through.
    """
    if provider not in {"stub_claude"}:
        raise typer.BadParameter(
            f"unknown review provider {provider!r}; PR6 supports only stub_claude"
        )

    store = _store()

    # Resolve the impl row + its run_id FIRST. The review row must be
    # attached to the SAME run as the impl row (not _latest_run_id()),
    # otherwise a second `arena init-fixture` followed by `arena review
    # --experiment exp_0004` would attach the review to the new run
    # while reading impl artifacts from the old one. The fusion_token
    # is also deterministic (fusion_0001) across runs, so the fusion
    # lookup MUST also filter by run_id to avoid cross-run linkage.
    impl_row = (
        store._require_conn()
        .execute(
            "SELECT experiment_id, run_id, artifact_paths FROM experiments "
            "WHERE competition_slug = ? AND experiment_id = ?",
            (competition_slug, experiment),
        )
        .fetchone()
    )
    if impl_row is None:
        raise typer.BadParameter(f"experiment {experiment} not found for {competition_slug}")
    run_id = impl_row["run_id"]
    if not run_id:
        raise typer.BadParameter(f"experiment {experiment} has no run_id (corrupt scoreboard?)")
    impl_paths: list[str] = json.loads(impl_row["artifact_paths"])

    fusion_token = next(
        (p for p in impl_paths if p.startswith(f"<{FUSION_ID_TAG_PREFIX}:")),
        None,
    )
    if fusion_token is None:
        raise typer.BadParameter(
            f"experiment {experiment} is not a research-proxy implementation "
            f"row (no <{FUSION_ID_TAG_PREFIX}:...> token in artifact_paths)"
        )
    fusion_id = fusion_token[len(f"<{FUSION_ID_TAG_PREFIX}:") : -1]

    submission_path = next(
        (p for p in impl_paths if p.endswith("submission.csv")),
        None,
    )
    if submission_path is None:
        raise typer.BadParameter(f"experiment {experiment} has no submission.csv in artifact_paths")

    # Find the fusion row whose artifact_paths contains the same fusion_token
    # AND has the <step:fusion> marker AND lives in the SAME run as the
    # impl row. fusion_token is deterministic across runs (fusion_0001),
    # so without the run_id filter we could match a different run's row.
    fusion_row = (
        store._require_conn()
        .execute(
            "SELECT experiment_id, artifact_paths FROM experiments "
            "WHERE competition_slug = ? AND run_id = ? "
            "AND artifact_paths LIKE ? AND artifact_paths LIKE ?",
            # Anchor LIKE to the JSON-list quoting (`"<token>"`) so a
            # future debug/blocked artifact_paths value that happens to
            # embed `<step:fusion>` as a substring (e.g., inside a
            # message token) cannot false-positive. artifact_paths is
            # JSON-encoded by ScoreboardStore.insert_experiment, so the
            # token always appears with surrounding double-quotes.
            (
                competition_slug,
                run_id,
                f'%"{fusion_token}"%',
                '%"<step:fusion>"%',
            ),
        )
        .fetchone()
    )
    if fusion_row is None:
        raise typer.BadParameter(
            f"could not locate originating fusion_proposal.json for "
            f"{fusion_id} (corrupt scoreboard?)"
        )
    fusion_paths: list[str] = json.loads(fusion_row["artifact_paths"])
    fusion_proposal_path = next(
        (p for p in fusion_paths if p.endswith("fusion_proposal.json")),
        None,
    )
    if fusion_proposal_path is None:
        raise typer.BadParameter(
            f"fusion row {fusion_row['experiment_id']} has no "
            "fusion_proposal.json in artifact_paths (corrupt scoreboard?)"
        )

    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)
    review_adapter = _get_provider(provider, event_emitter=trace_store)

    # PR4 reproducibility precheck — same shape as arena research-proxy.
    try:
        fixture_hash = compute_fixture_set_digest(FIXTURES_ROOT / competition_slug)
        _is_new_fixture, drifted_from_fixture = record_fixture_hash(
            competition_slug=competition_slug,
            fixture_hash=fixture_hash,
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        if isinstance(exc, FileNotFoundError):
            message = f"fixture manifest missing for {competition_slug}: {exc}"
        else:
            message = f"fixture state read failed for {competition_slug}: {exc}"
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    if drifted_from_fixture:
        message = (
            f"fixture digest drift for {competition_slug}: "
            f"was {drifted_from_fixture}, now {fixture_hash}"
        )
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2)

    trace_store.emit(
        event_type="run_started",
        severity="info",
        payload={
            "sha256": fixture_hash,
            "previous_hash": drifted_from_fixture or "",
            "phase": Phase.NEW.value,
        },
    )

    try:
        _is_new_review, drifted_from_review = record_provider_version(
            competition_slug=competition_slug,
            provider=review_adapter.name,
            version=review_adapter.version,
        )
    except json.JSONDecodeError as exc:
        message = f"provider version baseline corrupt for {competition_slug}: {exc}"
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    trace_store.emit(
        event_type="provider_version_recorded",
        severity="warning" if drifted_from_review else "info",
        payload={
            "provider": review_adapter.name,
            "provider_version": review_adapter.version,
            "previous_hash": drifted_from_review or "",
        },
    )

    review_drift_tag = (
        f"<{PROVIDER_VERSION_CHANGED_TAG}:from={drifted_from_review}>"
        if drifted_from_review
        else None
    )
    review_drift_extras = [review_drift_tag] if review_drift_tag else []

    # Seed governor accumulators from prior usage on this run.
    totals = store.get_run_usage_totals(competition_slug, run_id)
    accumulators = RunAccumulators(
        provider_calls=int(totals["provider_calls"]),
        codex_calls=int(totals["codex_calls"]),
        claude_calls=int(totals["claude_calls"]),
        wall_seconds=float(totals["wall_seconds"]),
        input_chars=int(totals["input_chars"]),
        output_chars=int(totals["output_chars"]),
        waste_events=int(totals["waste_events"]),
    )
    governor = BudgetGovernor(Phase0HardCeilings.from_env(), accumulators=accumulators)
    watchdog = Watchdog(governor=governor)

    rev_exp = store.get_next_experiment_id(competition_slug)
    rev_task = rev_exp.replace("exp_", "task_")
    create_workspace(WORKTREE_ROOT, competition_slug, rev_exp)

    rev_packet = make_review_packet(
        competition_slug=competition_slug,
        run_id=run_id,
        experiment_id=rev_exp,
        task_id=rev_task,
        review_id="rr_0001",
        subject_experiment_id=experiment,
        fusion_proposal_path=fusion_proposal_path,
        submission_path=submission_path,
    )

    in_flight: dict[str, str | bool | None] = {
        "experiment_id": rev_exp,
        "task_id": rev_task,
        "step": "review",
        "adapter_name": review_adapter.name,
        "adapter_version": review_adapter.version,
        "invocation_started": False,
    }

    def _persist_review_row(
        *,
        experiment_id: str,
        task_id: str,
        experiment_type: str,
        adapter_name: str,
        adapter_version: str,
        status: str,
        artifact_paths: list[str],
        usage_proxy: UsageProxy | None,
        score: float | None = None,
        valid_submission: bool | None = None,
    ) -> None:
        store.insert_experiment(
            experiment_id=experiment_id,
            run_id=run_id,
            competition_slug=competition_slug,
            task_id=task_id,
            experiment_type=experiment_type,
            provider=adapter_name,
            provider_version=adapter_version,
            status=status,
            metric_name="roc_auc",
            valid_submission=valid_submission,
            artifact_paths=artifact_paths,
            trace_path=None,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            input_chars=int(usage_proxy["input_chars"]) if usage_proxy else 0,
            output_chars=int(usage_proxy["output_chars"]) if usage_proxy else 0,
            wall_seconds=float(usage_proxy["wall_seconds"]) if usage_proxy else 0.0,
            shell_commands=int(usage_proxy["shell_commands"]) if usage_proxy else 0,
            failed_commands=int(usage_proxy["failed_commands"]) if usage_proxy else 0,
            waste_events=int(usage_proxy["waste_events"]) if usage_proxy else 0,
        )
        if score is not None:
            store.update_experiment_score(experiment_id, score=score)

    try:
        per_step_sandbox = SandboxRunner(
            SandboxPolicy.from_packet(rev_packet, workspace_root=Path.cwd())
        )
        watchdog.check_can_invoke(review_adapter.name)
        in_flight["invocation_started"] = True
        rev_result = watchdog.wrap_invoke(
            review_adapter,
            rev_packet,
            sandbox=per_step_sandbox,
            event_emitter=trace_store,
        )
        rev_artifact = _require_artifact(
            rev_result.artifacts,
            suffix="research_review.json",
            step_label="review",
            provider_name=review_adapter.name,
        )
        rev_payload = json.loads(Path(rev_artifact).read_text(encoding="utf-8"))
        validate_research_review(rev_payload)

        _persist_review_row(
            experiment_id=rev_exp,
            task_id=rev_task,
            experiment_type="research_proxy",
            adapter_name=review_adapter.name,
            adapter_version=review_adapter.version,
            status="completed",
            artifact_paths=["<step:review>", rev_artifact, *review_drift_extras],
            usage_proxy=rev_result.usage_proxy,
        )
        trace_store.emit(
            event_type="review_recorded",
            severity="info",
            task_id=rev_task,
            payload={
                "review_id": rev_payload["review_id"],
                "experiment_id": rev_exp,
                # status is the row state ("completed"), consistent with
                # how score_recorded uses status for valid/invalid. The
                # review's decision (accept/revise/...) goes in `reason`
                # so a single observability consumer reading payload.status
                # sees one vocabulary across event types.
                "status": "completed",
                "reason": f"decision={rev_payload['decision']}",
                "path": rev_artifact,
            },
        )
        console.print(
            f"[bold green]review complete[/bold green] — review_id="
            f"{rev_payload['review_id']} decision={rev_payload['decision']}"
        )
    except KillSwitchActive as exc:
        console.print(f"[red]kill switch active: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except BudgetExceeded as exc:
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_review_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=exc.usage_proxy,
            )
        console.print(f"[red]budget exceeded ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except SandboxViolation as exc:
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_review_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=None,
            )
        console.print(f"[red]sandbox violation ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc


def _require_artifact(
    artifacts: list[str], *, suffix: str, step_label: str, provider_name: str
) -> str:
    """Find the first artifact whose path ends with `suffix`. Raise a clear
    BadParameter if no match is found.

    The bare `next(...)` form would raise StopIteration on a regression
    where a provider drops the expected artifact (e.g., stub_claude
    silently fails to write research_question.json). StopIteration
    propagates as `RuntimeError: generator raised StopIteration` which
    isn't caught by the chain's outer except handlers and gives the
    operator no actionable message. This helper turns that into a
    typer.BadParameter naming the step + provider so the failure is
    self-describing.
    """
    match = next((a for a in artifacts if a.endswith(suffix)), None)
    if match is None:
        raise typer.BadParameter(
            f"{step_label}: provider {provider_name!r} did not emit a "
            f"{suffix!r} artifact (got {artifacts!r})"
        )
    return match


def _persist_inflight_blocked(
    persist_row,
    in_flight: dict,
    breaker_or_reason: str,
    message: str,
    *,
    usage_proxy: UsageProxy | None = None,
) -> None:
    """Insert a status=blocked row for the in-flight step on mid-chain
    exception. Skips if no step has started yet OR if invocation never
    began (check_can_invoke raised pre-invoke). When usage_proxy is
    provided (post-invoke BudgetExceeded), the row records the consumed
    usage so arena budget status reflects what the failing call cost.

    All research-proxy rows use experiment_type='research_proxy' (the
    schema enum value); the step name lives in artifact_paths as a
    <step:NAME> token, mirroring PR4's <PROVIDER_VERSION_CHANGED:...>
    pattern."""
    if in_flight["experiment_id"] is None:
        return
    if not in_flight.get("invocation_started"):
        # Defense-in-depth: if the caller forgot to gate on this flag,
        # we still skip the row insertion to avoid inflating provider_calls.
        return
    persist_row(
        experiment_id=in_flight["experiment_id"],
        task_id=in_flight["task_id"],
        experiment_type="research_proxy",
        adapter_name=in_flight["adapter_name"] or "unknown",
        adapter_version=in_flight["adapter_version"] or "unknown",
        status="blocked",
        artifact_paths=[
            f"<step:{in_flight['step']}>",
            f"<blocked:{breaker_or_reason}>",
            f"<message:{message[:200]}>",
        ],
        usage_proxy=usage_proxy,
    )
