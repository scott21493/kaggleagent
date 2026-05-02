# tests/test_cli_report.py
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app


def test_report_prints_markdown_after_run_next(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])

    result = runner.invoke(app, ["report", "tabular_binary_v1"])
    assert result.exit_code == 0
    assert "# Run report:" in result.output
    assert "## Tasks" in result.output
    assert "stub_codex" in result.output
