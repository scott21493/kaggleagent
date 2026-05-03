# tests/test_cli_provider_health.py
"""arena provider health <name> — text output + exit codes.

Stub paths exit 0 with a green checkmark line. Real paths use
monkeypatch on provider_health.check to simulate each HealthCode.
"""

from __future__ import annotations

from typer.testing import CliRunner

from arena.cli import app
from arena.providers.health import HealthCode, ProviderHealth


def test_provider_health_stub_codex_exits_0_green():
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "stub_codex"])
    assert result.exit_code == 0, result.output
    assert "stub_codex" in result.output
    assert "stub_codex.v1" in result.output


def test_provider_health_stub_claude_exits_0_green():
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "stub_claude"])
    assert result.exit_code == 0


def test_provider_health_codex_not_found_exits_1_with_runbook(monkeypatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.NOT_FOUND,
        version=None,
        sandbox_mode=None,
        detail="codex not on PATH",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "codex"])
    assert result.exit_code == 1
    assert "NOT FOUND" in result.output
    assert "cli_regression.md" in result.output


def test_provider_health_codex_blocked_auth_exits_1_with_auth_runbook(monkeypatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.BLOCKED_AUTH,
        version=None,
        sandbox_mode=None,
        detail="auth check failed",
        runbook="docs/phase0/runbooks/auth_expiry.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "codex"])
    assert result.exit_code == 1
    assert "BLOCKED AUTH" in result.output
    assert "auth_expiry.md" in result.output


def test_provider_health_codex_blocked_capability_exits_1(monkeypatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.BLOCKED_PROVIDER_CAPABILITY,
        version="0.4.2",
        sandbox_mode=None,
        detail="CLI rejected probe arguments",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "codex"])
    assert result.exit_code == 1
    assert "BLOCKED PROVIDER CAPABILITY" in result.output


def test_provider_health_codex_ok_exits_0_with_version(monkeypatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.OK,
        version="0.4.2",
        sandbox_mode="workspace-write",
        detail="auth ok",
        runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "codex"])
    assert result.exit_code == 0
    assert "0.4.2" in result.output
    assert "workspace-write" in result.output
