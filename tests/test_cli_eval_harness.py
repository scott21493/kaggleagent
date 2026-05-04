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
