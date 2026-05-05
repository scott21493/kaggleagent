# tests/test_cli_doctor.py
"""arena doctor — readiness inventory.

Doctor exits 0 always; only the provider section's color/label reflects
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


def test_doctor_exits_0_with_red_line_when_fixture_manifest_missing(tmp_path, monkeypatch):
    """The runbook contract is "always exits 0"; missing fixtures/
    directory used to crash with FileNotFoundError → exit 1, breaking
    the readiness-inventory semantics. Now wraps the validation in
    try/except and surfaces the failure as a red FAIL line."""
    # Isolate to an empty cwd with no fixtures/ dir
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "arena.cli.health_check",
        lambda name: ProviderHealth(
            provider=name,
            code=HealthCode.NOT_FOUND,
            version=None,
            sandbox_mode=None,
            detail="not on PATH",
            runbook="docs/phase0/runbooks/cli_regression.md",
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    # Inventory contract: always exits 0
    assert result.exit_code == 0, (
        f"doctor must exit 0 even when fixture manifest is missing; "
        f"got exit={result.exit_code}, output={result.output!r}"
    )
    # The failure surfaces as a red line in the inventory, not a crash
    assert "fixture manifest" in result.output.lower()
    # Doctor still completes the inventory + prints the summary line
    assert "complete" in result.output.lower()
