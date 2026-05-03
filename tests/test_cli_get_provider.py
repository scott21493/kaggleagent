# tests/test_cli_get_provider.py
"""_get_provider real-provider resolution + ProviderUnavailable raises.

Stub paths return their respective providers without health-check.
Real paths run provider_health.check first and raise ProviderUnavailable
on any non-OK HealthCode."""

from __future__ import annotations

import pytest

from arena.cli import _get_provider
from arena.providers.base import ProviderUnavailable
from arena.providers.codex import RealCodexProvider
from arena.providers.health import HealthCode, ProviderHealth
from arena.providers.stub_claude import StubClaudeProvider
from arena.providers.stub_codex import StubCodexProvider


def test_get_provider_stub_codex_no_health_check():
    p = _get_provider("stub_codex")
    assert isinstance(p, StubCodexProvider)


def test_get_provider_stub_claude_no_health_check():
    p = _get_provider("stub_claude")
    assert isinstance(p, StubClaudeProvider)


def test_get_provider_codex_ok_returns_real_adapter(monkeypatch: pytest.MonkeyPatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.OK,
        version="0.4.2",
        sandbox_mode="workspace-write",
        detail="auth ok",
        runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    p = _get_provider("codex")
    assert isinstance(p, RealCodexProvider)
    assert p.version == "0.4.2"


def test_get_provider_codex_not_found_raises_provider_unavailable(monkeypatch):
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.NOT_FOUND,
        version=None,
        sandbox_mode=None,
        detail="codex not on PATH",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    with pytest.raises(ProviderUnavailable) as exc:
        _get_provider("codex")
    assert exc.value.code == "not_found"
    assert "cli_regression.md" in str(exc.value)


def test_get_provider_codex_ok_with_none_version_raises_error(monkeypatch):
    """Per spec §5: if HealthCode.OK but version is None, treat as ERROR
    (protects baseline file from null version writes)."""
    fake = ProviderHealth(
        provider="codex",
        code=HealthCode.OK,
        version=None,
        sandbox_mode="workspace-write",
        detail="auth ok",
        runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    with pytest.raises(ProviderUnavailable) as exc:
        _get_provider("codex")
    assert exc.value.code == "error"


def test_get_provider_unknown_raises_bad_parameter(monkeypatch):
    import typer

    with pytest.raises(typer.BadParameter):
        _get_provider("unknown_xyz")
