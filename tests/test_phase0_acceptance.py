# tests/test_phase0_acceptance.py
"""Phase 0 closure-condition acceptance suite.

One test per §1.2 closure condition from
docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md. Each test is a focused
assertion against EXISTING production-facing CLI/APIs under stub
providers; no test introduces production code. Tests are independent
(pytest-xdist-safe, `pytest -k condition_06` filterable). Mutable state
isolation: per-test `fixture_workspace` (chdirs to tmp_path); kill
switch via env-var override; freeze sentinel via in-test
`sentinel.unlink()` cleanup.

§1.2 closure conditions (verbatim — drift detection by-eyeball):

01 — Controller creates task packets from deterministic templates.
02 — Codex can be invoked through a provider adapter, or a stub provider can simulate Codex in CI.
03 — Claude can be invoked through `claude -p` for bounded advisory/review tasks, or a stub provider can simulate Claude in CI.
04 — Provider stdout/stderr is captured, scrubbed, hashed, and replayable.
05 — A fake tabular competition fixture can be initialized, evaluated, and scored.
06 — At least one calibration baseline task completes.
07 — At least one bounded research-fusion proxy task completes.
08 — Claude reviews at least one implementation or research-fusion output.
09 — The scoreboard records metrics, cost proxies, wall time, artifacts, and provider versions.
10 — The usage governor enforces hard call, wall-clock, command-count, and proxy-token ceilings.
11 — The kill switch can stop the run without asking an LLM.
12 — The sandbox denies access to secrets and blocks unapproved network egress.
13 — Memory updates are proposed as deltas, not auto-merged.
14 — Self-improvement is blocked unless champion/challenger fixture evaluation passes.
15 — CI passes using stub providers without requiring real Codex/Claude authentication.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.budget.kill_switch import KillSwitch
from arena.cli import app
from arena.controller.task_queue import TaskQueue
from arena.fixture.evaluator import evaluate_fixture_submission
from arena.providers.stub_claude import StubClaudeProvider
from arena.providers.stub_codex import StubCodexProvider
from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.secrets import is_secret_read
from arena.schemas.validate import validate as validate_schema
from arena.scoreboard.store import ScoreboardStore


def _stub_packet(
    *,
    role: str = "implementation",
    phase: str = "CALIBRATION_TASK_CREATED",
    provider: str = "stub_codex",
    task_id: str = "task_0001",
    experiment_id: str = "exp_0001",
    competition_slug: str = "tabular_binary_v1",
) -> dict:
    """Build a schema-valid task_packet for direct stub invocations.

    Stub adapters require `experiment_id` to be a non-null string
    (StubCodexProvider/StubClaudeProvider raise ValueError if it's None).
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": provider,
        "role": role,
        "phase": phase,
        "objective": "phase0 acceptance test packet",
        "inputs": [],
        "allowed_paths": [],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 100,
            "max_failed_commands": 10,
            "max_input_chars": 100_000,
            "max_output_chars": 100_000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": [],
    }


def _bootstrap_calibration(runner: CliRunner) -> None:
    """init-fixture + plan + run-next under stub_codex (calibration completed)."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])


def _bootstrap_research_proxy(runner: CliRunner) -> None:
    """init-fixture + research-proxy under stub_claude (4 rows, impl at exp_0004)."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])


def _bootstrap_review(runner: CliRunner) -> None:
    """init-fixture + research-proxy + review (review row at exp_0005)."""
    _bootstrap_research_proxy(runner)
    runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )


def test_condition_01_controller_creates_task_packets(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#01: Controller creates task packets from deterministic templates.

    `arena init-fixture` + `arena plan` produces a queued packet that is
    schema-valid, role=implementation, phase=CALIBRATION_TASK_CREATED.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    plan = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert plan.exit_code == 0, plan.output

    runs = sorted(p for p in (fixture_workspace / "runs").iterdir() if p.name.startswith("run_"))
    queue = TaskQueue(runs[0] / "queue")
    packet = queue.peek()
    assert packet is not None
    validate_schema("task_packet", packet)  # schema-valid by construction
    assert packet["role"] == "implementation"
    assert packet["phase"] == "CALIBRATION_TASK_CREATED"


def test_condition_02_stub_codex_callable(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#02: Codex via adapter or a stub provider in CI.

    StubCodexProvider constructs without env vars and produces
    status="success" on a calibration packet. Proves the stub seam exists
    so CI can run without real Codex authentication.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    provider = StubCodexProvider(workspace_root=fixture_workspace / "worktrees")
    result = provider.invoke(_stub_packet())
    assert result.status == "success"
    assert provider.version == "stub_codex.v1"


def test_condition_03_stub_claude_callable(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#03: Claude via `claude -p` or a stub provider in CI.

    StubClaudeProvider constructs without env vars and produces
    status="success" on a research_proxy packet. Proves the stub seam
    exists so CI can run without real Claude authentication.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    provider = StubClaudeProvider(workspace_root=fixture_workspace / "worktrees")
    packet = _stub_packet(
        role="research_proxy",
        phase="RESEARCH_QUESTION_CREATED",
        provider="stub_claude",
    )
    result = provider.invoke(packet)
    assert result.status == "success"
    assert provider.version == "stub_claude.v1"


def test_condition_04_provider_streams_captured_scrubbed_replayable(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#04: Provider stdout/stderr captured, scrubbed, hashed, replayable.

    Two-part proof under stubs:
      (a) `arena run-next` produces a per-task `events.jsonl` trace under
          traces/<run_id>/<task_id>/ — the replayable record.
      (b) TraceStore.write_provider_streams writes the
          `{stdout,stderr}.{raw,scrubbed}` quadruple at the canonical
          location, scrubbing the input before persistence.
    """
    from arena.observability.trace_store import TraceStore

    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    _bootstrap_calibration(runner)

    # (a) replayable: per-task events.jsonl exists under traces/<run_id>/<task_id>/
    traces_root = fixture_workspace / "traces"
    per_task_logs = list(traces_root.rglob("task_0001/events.jsonl"))
    assert per_task_logs, f"no per-task events.jsonl under {traces_root}"

    # (b) captured/scrubbed: write_provider_streams writes the four
    # canonical artifacts at traces/<run_id>/<task_id>/.
    store = TraceStore(run_id="run_acceptance_04", root=fixture_workspace / "traces")
    paths = store.write_provider_streams(
        task_id="task_0001",
        raw_stdout="raw out",
        raw_stderr="raw err",
        scrubbed_stdout="scrub out",
        scrubbed_stderr="scrub err",
    )
    assert paths.stdout_scrubbed.exists()
    assert paths.stderr_scrubbed.exists()
    assert paths.stdout_raw.exists()
    assert paths.stderr_raw.exists()
    assert paths.stdout_scrubbed.read_text(encoding="utf-8") == "scrub out"


def test_condition_05_fixture_init_eval_score(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#05: Fake tabular competition fixture initialized, evaluated, scored.

    `arena init-fixture` + `arena fixture-smoke` exits 0 and reports a
    valid roc_auc score in (0, 1). The bundled sample_submission.csv
    against hidden_labels.csv yields 0.5 by construction.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    init = runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert init.exit_code == 0, init.output
    smoke = runner.invoke(app, ["fixture-smoke"])
    assert smoke.exit_code == 0, smoke.output

    # Direct evaluator call to assert the numeric score is in (0, 1).
    result = evaluate_fixture_submission(
        "fixtures/tabular_binary_v1/sample_submission.csv",
        "fixtures/tabular_binary_v1/hidden_labels.csv",
    )
    assert result.valid_submission is True
    assert result.score is not None
    assert 0.0 < result.score < 1.0


def test_condition_06_calibration_completes(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#06: At least one calibration baseline task completes.

    `arena run-next --provider stub_codex` against a planned calibration
    packet persists an experiment row with status="completed" (NOT "ok").
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    _bootstrap_calibration(runner)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp = store.get_latest_experiment("tabular_binary_v1")
    finally:
        store.close()
    assert exp is not None
    assert exp["experiment_type"] == "calibration"
    assert exp["status"] == "completed"


def test_condition_07_research_proxy_completes(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#07: At least one bounded research-fusion proxy task completes.

    `arena research-proxy` produces a row with the `<step:implementation>`
    token in artifact_paths (the row that drove the fusion-grounded
    submission).
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_research_proxy(runner)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT status, artifact_paths FROM experiments "
                "WHERE competition_slug = ? AND experiment_type = ?",
                ("tabular_binary_v1", "research_proxy"),
            )
            .fetchall()
        )
    finally:
        store.close()
    impl_rows = [r for r in rows if "<step:implementation>" in json.loads(r["artifact_paths"])]
    assert len(impl_rows) >= 1
    assert impl_rows[0]["status"] == "completed"


def test_condition_08_claude_reviews_implementation(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#08: Claude reviews at least one implementation/research-fusion output.

    `arena review --experiment <impl>` persists a row with
    `<step:review>` token in artifact_paths and status="completed".
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT status, artifact_paths FROM experiments WHERE competition_slug = ?",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
    finally:
        store.close()
    review_rows = [r for r in rows if "<step:review>" in json.loads(r["artifact_paths"])]
    assert len(review_rows) >= 1
    assert review_rows[0]["status"] == "completed"


def test_condition_09_scoreboard_records_full_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#09: Scoreboard records metrics, cost proxies, wall time, artifacts, versions.

    After run-next + evaluate, the experiment row has non-null
    `wall_seconds`, `provider_version`, `artifact_paths`, and
    `valid_submission`. `valid_submission` is populated by `arena
    evaluate` (run-next defers scoring to keep watchdog post-conditions
    independent of metric availability).
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    _bootstrap_calibration(runner)
    runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp = store.get_latest_experiment("tabular_binary_v1")
    finally:
        store.close()
    assert exp is not None
    assert exp["wall_seconds"] is not None
    assert exp["provider_version"] is not None
    assert exp["provider_version"] == "stub_codex.v1"
    assert exp["artifact_paths"] is not None
    paths = json.loads(exp["artifact_paths"])
    assert isinstance(paths, list) and len(paths) >= 1
    assert exp["valid_submission"] is not None


def test_condition_10_governor_enforces_call_cap(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#10: Governor enforces hard call/wall/command/token ceilings.

    Setting ARENA_PHASE0_PROVIDER_CALL_CAP=0 and invoking `arena run-next`
    causes the run-level provider-call cap (ProviderCallBreaker) to fire
    in pre-invoke; exit code is non-zero.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])

    monkeypatch.setenv("ARENA_PHASE0_PROVIDER_CALL_CAP", "0")
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "budget exceeded" in result.output.lower() or "ProviderCallBreaker" in result.output, (
        result.output
    )


def test_condition_11_kill_switch_halts_without_llm(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#11: Kill switch can stop the run without asking an LLM.

    `arena kill` exits 0 and KillSwitch.is_active() returns True without
    invoking any provider. fixture_workspace chdirs to tmp_path, so the
    .arena/KILL_SWITCH file is isolated to this test.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["kill"])
    assert result.exit_code == 0, result.output
    assert KillSwitch.is_active() is True
    # Cleanup: deactivate so the cwd-relative .arena/KILL_SWITCH file
    # doesn't leak into adjacent tests via tmp_path's lifecycle.
    KillSwitch.deactivate()


def test_condition_12_sandbox_denies_secrets(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#12: Sandbox denies access to secrets and blocks network egress.

    SandboxPolicy.from_packet() installs the canonical blocked_paths
    (~/.kaggle, ~/.codex, ~/.claude, .env, traces/). is_secret_read of
    a path under ~/.kaggle/ returns True regardless of packet contents.
    """
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    packet = {"allowed_paths": [], "blocked_paths": []}
    policy = SandboxPolicy.from_packet(packet, workspace_root=fixture_workspace)
    home = Path("~").expanduser().resolve()
    assert is_secret_read(home / ".kaggle" / "key", policy) is True
    # Default allowed_network_domains is empty (deny-all) — Phase 0
    # default per docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §5.
    assert policy.allowed_network_domains == frozenset()


def test_condition_13_memory_updates_proposed_as_deltas(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#13: Memory updates proposed as deltas, not auto-merged.

    `arena memory propose --review <id>` writes mem_NNNN.json under
    memory/proposals/ and emits a memory_proposal_created trace event.
    The canonical research-namespace memory file (memory/research.md) is
    NOT mutated by propose — only the proposal JSON is durable; merging
    is a separate operator action.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    research_md = fixture_workspace / "memory" / "research.md"
    research_md_existed_before = research_md.exists()
    research_md_text_before = (
        research_md.read_text(encoding="utf-8") if research_md_existed_before else None
    )

    result = runner.invoke(
        app,
        ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"],
    )
    assert result.exit_code == 0, result.output

    proposals = list((fixture_workspace / "memory" / "proposals").glob("mem_*.json"))
    assert proposals, "no mem_*.json proposal artifact written"

    # research.md is not mutated by `memory propose` — either the file
    # didn't exist before and still doesn't, or its content is unchanged.
    if research_md_existed_before:
        assert research_md.read_text(encoding="utf-8") == research_md_text_before
    else:
        assert not research_md.exists()


def test_condition_14_self_improvement_freeze_fires(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#14: Self-improvement blocked unless champion/challenger passes.

    Induce a blocked research-proxy row by setting
    ARENA_PHASE0_OUTPUT_CHARS_CAP=1, then run `arena self-improve scan`:
    the freeze sentinel SELF_IMPROVEMENT_FROZEN.md is written at the
    workspace root. Cleanup: sentinel.unlink() so this test does not
    leak the freeze state into adjacent tests sharing the cwd.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    result = runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    sentinel = fixture_workspace / "SELF_IMPROVEMENT_FROZEN.md"
    try:
        assert sentinel.exists(), f"freeze sentinel missing at {sentinel}"
    finally:
        # In-test cleanup so the cwd-anchored sentinel does not bleed
        # into adjacent tests under xdist or sequential ordering.
        sentinel.unlink(missing_ok=True)


def test_condition_15_ci_passes_with_stubs(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§1.2#15: CI passes using stubs without real Codex/Claude auth.

    Construct StubCodexProvider with a tmp_path-derived workspace_root
    (Windows-safe — never `/tmp`), invoke against a schema-valid packet,
    and assert status="success" + version="stub_codex.v1". Proves the
    stub-only CI path is independent of real-CLI credentials.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    # tmp_path-anchored workspace, NOT Path("/tmp") (Windows-incompatible).
    provider = StubCodexProvider(workspace_root=fixture_workspace / "worktrees")
    result = provider.invoke(_stub_packet())
    assert result.status == "success"
    assert provider.version == "stub_codex.v1"
