# tests/test_cli_doctor.py
"""arena doctor — readiness inventory.

Doctor exits 0 always; only the provider section's color/glyph reflects
status. `arena provider health <name>` is the fail-fast surface.
"""

from __future__ import annotations

from typer.testing import CliRunner

from arena.cli import app
from arena.providers.health import HealthCode, ProviderHealth


def test_doctor_exits_0_when_real_clis_missing(monkeypatch, fixture_workspace):
    """Doctor must NOT exit non-zero on NOT_FOUND — readiness inventory,
    not fail-fast."""
    not_found = ProviderHealth(
        provider="codex",
        code=HealthCode.NOT_FOUND,
        version=None,
        sandbox_mode=None,
        detail="not on PATH",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: not_found)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not installed" in result.output.lower() or "not on path" in result.output.lower()


def test_doctor_summary_says_complete_not_passed(monkeypatch, fixture_workspace):
    not_found = ProviderHealth(
        provider="codex",
        code=HealthCode.NOT_FOUND,
        version=None,
        sandbox_mode=None,
        detail="not on PATH",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: not_found)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert "complete" in result.output.lower()


def test_doctor_includes_provider_lines(monkeypatch, fixture_workspace):
    monkeypatch.setattr(
        "arena.cli.health_check",
        lambda name: ProviderHealth(
            provider=name,
            code=HealthCode.OK,
            version="x.y",
            sandbox_mode="ws",
            detail="ok",
            runbook=None,
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert "codex" in result.output.lower()
    assert "claude" in result.output.lower()
