# tests/test_provider_health.py
"""Provider health typed core. Stub paths short-circuit; real paths
exercise --version + --help via monkeypatch (Task 5 adds shim
integration tests).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arena.providers.health import HealthCode, ProviderHealth, check


@pytest.fixture(autouse=True)
def _bypass_resolve_provider_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing subprocess-mock tests assume subprocess.run is reached
    unconditionally. The new resolve_provider_executable gate (PR7
    Windows-shim fix) returns None when the bare name isn't on the
    test environment's PATH, so subprocess.run never gets called. Bypass
    it for the whole file by default; tests that target the resolution
    layer specifically can monkeypatch over this fixture."""
    monkeypatch.setattr(
        "arena.providers.health.resolve_provider_executable",
        lambda name: name,
    )


def test_check_stub_codex_short_circuits_to_ok() -> None:
    h = check("stub_codex")
    assert isinstance(h, ProviderHealth)
    assert h.provider == "stub_codex"
    assert h.code == HealthCode.OK
    assert h.version == "stub_codex.v1"
    assert h.sandbox_mode == "deterministic"
    assert h.runbook is None


def test_check_stub_claude_short_circuits_to_ok() -> None:
    h = check("stub_claude")
    assert h.code == HealthCode.OK
    assert h.version == "stub_claude.v1"


def test_check_unknown_provider_returns_error() -> None:
    h = check("unknown_provider_xyz")
    assert h.code == HealthCode.ERROR
    assert "unknown" in h.detail.lower()


def test_check_real_codex_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """FileNotFoundError on subprocess.run → NOT_FOUND."""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.NOT_FOUND
    assert h.version is None
    assert h.runbook == "docs/phase0/runbooks/cli_regression.md"


def test_check_real_codex_not_found_on_help_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second-probe FileNotFoundError (binary disappeared between
    --version and --help) → NOT_FOUND uniformly. Narrow race, but the
    typed health core's contract maps missing-binary to NOT_FOUND on
    BOTH probes."""

    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        # Second probe (--help): binary is now gone
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert calls["n"] == 2  # both probes ran
    assert h.code == HealthCode.NOT_FOUND
    assert h.version == "0.4.2"  # version was parsed before --help failed
    assert h.runbook == "docs/phase0/runbooks/cli_regression.md"
    # Detail wording was generalised when the FileNotFoundError catch
    # widened to OSError (covers PermissionError too — Windows shim
    # case). Just confirm the failure was attributed to the second probe.
    assert "between --version and --help" in h.detail.lower()
    assert "filenotfounderror" in h.detail.lower()


def test_check_real_codex_ok_via_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """--version returns 0 with parseable output; --help returns 0."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        if argv[1] == "--help":
            return MagicMock(returncode=0, stdout="usage: codex [...]\n", stderr="")
        raise AssertionError(f"unexpected argv: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.OK
    assert h.version == "0.4.2"
    assert h.sandbox_mode == "workspace-write"
    # Both probes ran:
    assert len(calls) == 2


def test_check_real_codex_blocked_auth_via_exit_64(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit ≥64 on --help → BLOCKED_AUTH unconditional (regardless of stderr)."""

    def fake_run(argv, **kwargs):
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        return MagicMock(returncode=64, stdout="", stderr="generic error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.BLOCKED_AUTH
    assert h.runbook == "docs/phase0/runbooks/auth_expiry.md"


def test_check_real_codex_blocked_auth_via_exit_2_with_auth_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 2 with auth phrase in stderr → BLOCKED_AUTH (regex helps non-standard exits)."""

    def fake_run(argv, **kwargs):
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        return MagicMock(returncode=2, stdout="", stderr="session expired, please log in")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.BLOCKED_AUTH


def test_check_real_codex_blocked_capability_via_exit_2_flag_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 2 with flag/capability phrase in stderr → BLOCKED_PROVIDER_CAPABILITY."""

    def fake_run(argv, **kwargs):
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        return MagicMock(returncode=2, stdout="", stderr="error: unrecognized argument --json")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.BLOCKED_PROVIDER_CAPABILITY
    assert h.runbook == "docs/phase0/runbooks/cli_regression.md"


def test_check_real_codex_error_via_exit_1_neutral_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 1 with neutral stderr → ERROR (regex fallback didn't match)."""

    def fake_run(argv, **kwargs):
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.ERROR


def test_check_real_codex_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """TimeoutExpired → ERROR with `health check timed out` detail."""

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex", timeout_seconds=10.0)
    assert h.code == HealthCode.ERROR
    assert "timed out" in h.detail.lower()


def test_check_real_codex_version_unparseable_yields_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--version returns 0 but stdout has no semver-ish version → ERROR."""

    def fake_run(argv, **kwargs):
        return MagicMock(returncode=0, stdout="something unrecognizable\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.ERROR
    assert h.version is None


def test_check_passes_executable_env_cwd_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """DI surface: executable, env, cwd reach subprocess.run unchanged."""
    captured_kwargs: list[dict] = []

    def fake_run(argv, **kwargs):
        captured_kwargs.append(dict(kwargs))
        return MagicMock(returncode=0, stdout="codex 1.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    custom_env = {"PATH": "/custom/path", "CUSTOM_VAR": "x"}
    check(
        "codex",
        executable="/path/to/codex",
        env=custom_env,
        cwd=tmp_path,
    )
    # Both probes should have received the overrides
    assert len(captured_kwargs) == 2
    for kw in captured_kwargs:
        assert kw["cwd"] == str(tmp_path)
        assert kw["env"]["CUSTOM_VAR"] == "x"
        # env is overlaid on os.environ — PATH gets overridden, but other
        # vars (e.g., HOME on POSIX, USERPROFILE on Windows) survive.
        assert kw["env"]["PATH"] == "/custom/path"


def test_check_real_codex_resolve_not_found_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[P1] Windows-aware resolution: if shutil.which returns None for
    every PATHEXT variant (codex.cmd, codex.bat, codex.exe, codex), the
    health probe must short-circuit to NOT_FOUND BEFORE invoking
    subprocess. Override the autouse bypass fixture for this test."""
    monkeypatch.setattr(
        "arena.providers.health.resolve_provider_executable",
        lambda name: None,
    )
    # If subprocess.run is reached, the test should fail loudly:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: pytest.fail("subprocess.run must NOT be called when resolve returns None"),
    )
    h = check("codex")
    assert h.code == HealthCode.NOT_FOUND
    assert h.runbook == "docs/phase0/runbooks/cli_regression.md"
    assert "not on path" in h.detail.lower()


def test_check_real_codex_permissionerror_at_version_probe_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[P1] Defense-in-depth: shutil.which returned a path (e.g., the
    Windows extensionless npm shim slipped past resolution), but
    subprocess.run([shim, ...]) raises PermissionError [WinError 5].
    The OSError catch must surface NOT_FOUND, not crash."""

    def fake_run(*a, **kw):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.NOT_FOUND
    assert h.runbook == "docs/phase0/runbooks/cli_regression.md"
    # Detail should mention the underlying OSError type so operators
    # can distinguish PermissionError (Windows shim) from
    # FileNotFoundError (binary truly missing).
    assert "permissionerror" in h.detail.lower()


def test_check_real_codex_permissionerror_at_help_probe_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same as above but at the second probe — PermissionError on
    --help (after --version succeeded) maps to NOT_FOUND uniformly."""

    def fake_run(argv, **kwargs):
        if argv[1] == "--version":
            return MagicMock(returncode=0, stdout="codex 0.4.2\n", stderr="")
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    h = check("codex")
    assert h.code == HealthCode.NOT_FOUND
    assert h.version == "0.4.2"
    assert "permissionerror" in h.detail.lower()
