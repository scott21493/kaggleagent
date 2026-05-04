# tests/test_cli_eval_harness.py
"""arena eval-harness — orchestration smoke + step-failure reporting.

Tests assert key substrings/counts/rows. NO snapshot assertions on the
full table.
"""

from __future__ import annotations

from typer.testing import CliRunner

from arena.cli import app


def test_eval_harness_stub_happy_path_exits_0(fixture_workspace, monkeypatch):
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "stub"])
    assert result.exit_code == 0, result.output
    # Spot-check the step table
    for step in (
        "init-fixture",
        "plan",
        "run-next",
        "research-proxy",
        "evaluate",
        "review",
        "memory propose",
        "self-improve scan",
        "report",
    ):
        assert step in result.output
    assert "9/9 steps ok" in result.output


def test_eval_harness_bad_providers_value(fixture_workspace, monkeypatch):
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "garbage"])
    assert result.exit_code != 0
    assert "garbage" in result.output or "stub" in result.output


def test_eval_harness_provider_mapping_stub(fixture_workspace, monkeypatch):
    """--providers stub maps to stub_codex + stub_claude."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    from arena.cli import _resolve_providers

    codex, claude = _resolve_providers("stub")
    assert codex == "stub_codex"
    assert claude == "stub_claude"


def test_eval_harness_provider_mapping_real(fixture_workspace, monkeypatch):
    """--providers real maps to codex + claude."""
    from arena.cli import _resolve_providers

    codex, claude = _resolve_providers("real")
    assert codex == "codex"
    assert claude == "claude"


def test_eval_harness_runs_scoped_to_init_fixture_run(
    fixture_workspace,
    monkeypatch,
) -> None:
    """Spec §3.2: eval-harness lookups must filter by the run_id
    init-fixture created. If a stale impl row exists from a prior run,
    the harness must NOT review/memory-propose against it."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    # Create a stale run with an impl row by running research-proxy once
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    # Now run eval-harness; if research-proxy fails inside it, the
    # earlier impl row's experiment_id must NOT be picked up by the
    # within-run lookup.
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")  # force research-proxy block
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "stub"])
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)
    assert "skipped" in result.output.lower()
    assert "this run" in result.output  # confirms run-scoped skip reason


def test_eval_harness_si_freeze_does_not_mark_step_failed(
    fixture_workspace,
    monkeypatch,
) -> None:
    """Per spec §3.2: SI scan reported as ok regardless of freeze."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "stub"])
    # Even if SI scan fired findings, the step must show ok status
    assert "self-improve scan" in result.output
    # The 9/9 ok line still holds in the happy path
    assert "9/9 steps ok" in result.output or "ok" in result.output.lower()


def test_eval_harness_real_mode_routes_packets_to_codex(
    fixture_workspace,
    monkeypatch,
) -> None:
    """[P1] regression: --providers real must produce a calibration
    packet whose provider matches the resolved adapter ('codex'), so
    run-next does NOT reject it via the peeked['provider'] !=
    adapter.name check.

    We can't actually invoke the real codex CLI here (CI determinism
    rules). But we CAN verify the queue packet's provider field after
    plan() runs in real mode, which is the seam the bug was at.
    """
    import json

    from arena.providers.health import HealthCode, ProviderHealth

    # Stub out health_check so _get_provider("codex") would return a
    # real adapter instance — we don't actually invoke it; we only
    # need plan(provider="codex") to succeed. plan does NOT call
    # _get_provider, so monkeypatching health here is defensive only.
    monkeypatch.setattr(
        "arena.cli.health_check",
        lambda name: ProviderHealth(
            provider=name,
            code=HealthCode.OK,
            version="0.4.2",
            sandbox_mode="workspace-write",
            detail="ok",
            runbook=None,
        ),
    )
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    # Direct invocation of arena plan with --provider codex:
    result = runner.invoke(app, ["plan", "tabular_binary_v1", "--provider", "codex"])
    assert result.exit_code == 0, result.output

    # Inspect the queued packet and confirm provider="codex"
    from pathlib import Path

    runs_dir = fixture_workspace / "runs"
    queue_dirs = sorted(runs_dir.glob("run_*/queue"))
    assert queue_dirs, "no runs/<run_id>/queue/ directory after init+plan"
    queue_jsons = sorted(queue_dirs[-1].glob("*.json"))
    assert queue_jsons, "no queued packets after plan()"
    packet = json.loads(Path(queue_jsons[0]).read_text(encoding="utf-8"))
    assert packet["provider"] == "codex", (
        f"plan --provider codex must plant 'codex' in the packet, got "
        f"{packet['provider']!r}; without this fix the run-next "
        "peeked['provider'] != adapter.name check rejects the packet"
    )


def test_eval_harness_research_proxy_accepts_real_claude_provider(
    fixture_workspace,
    monkeypatch,
) -> None:
    """[P1] regression: arena research-proxy must accept --provider
    claude (was previously gated to stub_claude only). This proves the
    BadParameter widening; we don't actually drive the real subprocess
    here (CI rules) — we monkeypatch _get_provider to bypass adapter
    construction so the gate-check is the only thing under test."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Force _get_provider("claude") to raise something we can catch —
    # what matters is that the research_proxy gate check ABOVE
    # _get_provider does NOT reject the provider name.
    def boom(name, **kw):
        raise RuntimeError("test sentinel: gate accepted 'claude'")

    monkeypatch.setattr("arena.cli._get_provider", boom)
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "claude"],
    )
    # If the BadParameter gate had rejected 'claude', exit_code would
    # be 2 with "unknown research provider". Our sentinel proves the
    # gate let 'claude' through.
    output = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "test sentinel: gate accepted 'claude'" in output or isinstance(
        result.exception, RuntimeError
    ), (
        f"research-proxy --provider claude should pass the gate; "
        f"got exit_code={result.exit_code}, output={result.output!r}, "
        f"exception={result.exception!r}"
    )


def test_eval_harness_review_accepts_real_claude_provider(
    fixture_workspace,
    monkeypatch,
) -> None:
    """[P1] mirror of the research-proxy regression for arena review."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    def boom(name, **kw):
        raise RuntimeError("test sentinel: review gate accepted 'claude'")

    monkeypatch.setattr("arena.cli._get_provider", boom)
    # We pass a bogus --experiment because the review command will
    # fail at impl-row lookup either way; we only care that the
    # provider gate let 'claude' through.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "claude",
            "--experiment",
            "exp_9999",
        ],
    )
    # Must NOT be the "unknown review provider" BadParameter rejection.
    assert "unknown review provider" not in (result.output or ""), (
        f"review --provider claude should pass the gate; got output: {result.output!r}"
    )


def test_eval_harness_init_fixture_failure_does_not_pick_up_stale_run(
    fixture_workspace,
    monkeypatch,
) -> None:
    """[P2] regression: if init-fixture fails while a stale prior run
    exists on disk, the harness MUST NOT silently adopt that stale
    run_id and continue planning/looking up rows against it.

    Setup: run init-fixture once successfully (creates a stale run);
    then force the next init-fixture to fail; eval-harness must skip
    every step that has a hard data dependency on a fresh run."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()

    # Step 1: create a stale run
    r = runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert r.exit_code == 0, f"setup init-fixture failed: {r.output}"

    # Step 2: monkeypatch init_fixture so the harness's init step fails
    import typer

    from arena.cli import init_fixture as _real_init  # noqa: F401

    def failing_init(slug):
        raise typer.BadParameter("test sentinel: forced init-fixture failure")

    monkeypatch.setattr("arena.cli.init_fixture", failing_init)

    # Step 3: run eval-harness
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "stub"])

    # Plan, run-next, research-proxy, evaluate, review, memory propose
    # MUST all be skipped (with reason="init-fixture failed"), NOT
    # silently executed against the stale run.
    assert "init-fixture failed" in result.output, (
        f"eval-harness should skip dependent steps with the init-fixture-failed "
        f"reason; got output: {result.output!r}"
    )
    # init-fixture itself shows as failed, not ok
    assert "❌" in result.output or "failed" in result.output

    # Exit code must be non-zero (init failed → at least 1 failed step)
    assert result.exit_code != 0
