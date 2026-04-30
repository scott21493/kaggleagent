from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.budget.kill_switch import KILL_SWITCH_ENV, KillSwitch
from arena.cli import app


def test_kill_activates_then_unkill_deactivates(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    runner = CliRunner()
    assert KillSwitch.is_active() is False

    kill = runner.invoke(app, ["kill"])
    assert kill.exit_code == 0
    assert KillSwitch.is_active() is True

    # unkill without --human-confirm fails
    bad_unkill = runner.invoke(app, ["unkill"])
    assert bad_unkill.exit_code != 0
    assert KillSwitch.is_active() is True

    # unkill with --human-confirm deactivates
    good_unkill = runner.invoke(app, ["unkill", "--human-confirm"])
    assert good_unkill.exit_code == 0
    assert KillSwitch.is_active() is False


def test_budget_status_reports_ceilings_and_accumulators(fixture_workspace: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    result = runner.invoke(app, ["budget", "status"])
    assert result.exit_code == 0, result.output
    # No experiments yet — every accumulator is 0.
    assert "provider_calls: 0 / 12" in result.output
    assert "codex_calls: 0 / 6" in result.output
    assert "claude_calls: 0 / 6" in result.output
    assert "kill_switch:" in result.output


def test_budget_unknown_action_rejected(fixture_workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["budget", "reset"])
    assert result.exit_code != 0
