# tests/test_observability_waste.py
"""Acceptance test 5 from SECURITY_COST_REPRODUCIBILITY_SPEC.md §9:
Provider repeats failed command and the breaker triggers. The PR2 ceiling
is Phase0HardCeilings.repeated_same_failure_per_task = 2 with a strict-`>`
check, so the 4th identical failure tips it over. The test injects 4
events deliberately."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.stub_codex import StubCodexProvider
from arena.scoreboard.store import ScoreboardStore


def test_run_next_repeated_same_failure_trips_repeated_failure_breaker(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    # Force StubCodexProvider to emit 4 identical failed shell events.
    # Phase0HardCeilings.repeated_same_failure_per_task is 2 (PR2 default),
    # with a strict `>` check, so the 4th identical failure trips the cap.
    original_init = StubCodexProvider.__init__

    def _init(self: StubCodexProvider, workspace_root: Path | str = "worktrees", **kwargs):
        original_init(
            self,
            workspace_root,
            failed_commands=[
                ("ls /nonexistent", 2),
                ("ls /nonexistent", 2),
                ("ls /nonexistent", 2),
                ("ls /nonexistent", 2),
            ],
            **kwargs,
        )

    monkeypatch.setattr(StubCodexProvider, "__init__", _init)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code != 0
    assert "RepeatedFailureBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        exp = store.get_latest_experiment("tabular_binary_v1")
        assert exp is not None
        assert exp["status"] == "blocked"
        assert "<blocked:RepeatedFailureBreaker>" in exp["artifact_paths"]
    finally:
        store.close()
