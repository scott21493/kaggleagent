# arena/providers/health.py
"""Provider health typed core.

Public surface: HealthCode enum, ProviderHealth dataclass, check().

Probes for real providers are CHEAP and NON-MUTATING: --version and
--help only. No LLM invocation, no token consumption, no workspace
artifacts. If a provider CLI changes that, treat as
BLOCKED_PROVIDER_CAPABILITY.

Stubs short-circuit to OK with their declared provider_version
strings; no subprocess.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from arena.providers.auth import matches_auth_expiry


class HealthCode(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    BLOCKED_AUTH = "blocked_auth"
    BLOCKED_PROVIDER_CAPABILITY = "blocked_provider_capability"
    ERROR = "error"


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    code: HealthCode
    version: str | None
    sandbox_mode: str | None
    detail: str
    runbook: str | None


_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")
_CAPABILITY_PHRASES = (
    "unrecognized argument",
    "unrecognized option",
    "unknown flag",
    "unknown option",
    "no such option",
    "invalid argument",
)

_RUNBOOK_AUTH = "docs/phase0/runbooks/auth_expiry.md"
_RUNBOOK_REGRESSION = "docs/phase0/runbooks/cli_regression.md"


def check(
    name: str,
    *,
    executable: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    timeout_seconds: float = 10.0,
) -> ProviderHealth:
    """Run a cheap, non-mutating health check for `name`."""
    if name == "stub_codex":
        return ProviderHealth(
            provider="stub_codex",
            code=HealthCode.OK,
            version="stub_codex.v1",
            sandbox_mode="deterministic",
            detail="no subprocess; deterministic",
            runbook=None,
        )
    if name == "stub_claude":
        return ProviderHealth(
            provider="stub_claude",
            code=HealthCode.OK,
            version="stub_claude.v1",
            sandbox_mode="deterministic",
            detail="no subprocess; deterministic",
            runbook=None,
        )
    if name not in ("codex", "claude"):
        return ProviderHealth(
            provider=name,
            code=HealthCode.ERROR,
            version=None,
            sandbox_mode=None,
            detail=f"unknown provider: {name!r}",
            runbook=None,
        )

    exe = executable or name
    effective_env = {**os.environ, **(env or {})}
    cwd_str = str(cwd) if cwd is not None else None
    sandbox_mode = "workspace-write" if name == "codex" else "workspace"

    # Probe 1: --version
    try:
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=effective_env,
            cwd=cwd_str,
        )
    except FileNotFoundError:
        return ProviderHealth(
            provider=name,
            code=HealthCode.NOT_FOUND,
            version=None,
            sandbox_mode=None,
            detail=f"{exe} not on PATH",
            runbook=_RUNBOOK_REGRESSION,
        )
    except subprocess.TimeoutExpired:
        return ProviderHealth(
            provider=name,
            code=HealthCode.ERROR,
            version=None,
            sandbox_mode=None,
            detail="health check timed out",
            runbook=None,
        )

    if result.returncode != 0:
        return _classify_nonzero(name, result.returncode, result.stderr or "")

    parsed_version: str | None = None
    m = _VERSION_RE.search(result.stdout or "")
    if m:
        parsed_version = m.group(1)
    if parsed_version is None:
        return ProviderHealth(
            provider=name,
            code=HealthCode.ERROR,
            version=None,
            sandbox_mode=None,
            detail="--version output had no parseable version",
            runbook=None,
        )

    # Probe 2: --help (validates auth/session is available)
    try:
        result = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=effective_env,
            cwd=cwd_str,
        )
    except FileNotFoundError:
        # Narrow race: the executable that succeeded for --version is
        # gone before --help. The contract is cleaner if both probes
        # map missing-binary to NOT_FOUND uniformly.
        return ProviderHealth(
            provider=name,
            code=HealthCode.NOT_FOUND,
            version=parsed_version,
            sandbox_mode=None,
            detail=f"{exe} disappeared between --version and --help",
            runbook=_RUNBOOK_REGRESSION,
        )
    except subprocess.TimeoutExpired:
        return ProviderHealth(
            provider=name,
            code=HealthCode.ERROR,
            version=parsed_version,
            sandbox_mode=None,
            detail="--help probe timed out",
            runbook=None,
        )

    if result.returncode != 0:
        return _classify_nonzero(
            name, result.returncode, result.stderr or "", version=parsed_version
        )

    return ProviderHealth(
        provider=name,
        code=HealthCode.OK,
        version=parsed_version,
        sandbox_mode=sandbox_mode,
        detail="auth ok",
        runbook=None,
    )


def _classify_nonzero(
    name: str,
    returncode: int,
    stderr: str,
    *,
    version: str | None = None,
) -> ProviderHealth:
    """Map non-zero exit to a HealthCode. Precedence: ≥64 → BLOCKED_AUTH
    unconditional; exit 2 → stderr inspection; exit 1 + auth phrase →
    BLOCKED_AUTH (regex fallback); otherwise ERROR."""
    if returncode >= 64:
        return ProviderHealth(
            provider=name,
            code=HealthCode.BLOCKED_AUTH,
            version=version,
            sandbox_mode=None,
            detail="auth check failed",
            runbook=_RUNBOOK_AUTH,
        )
    if returncode == 2:
        if matches_auth_expiry(stderr):
            return ProviderHealth(
                provider=name,
                code=HealthCode.BLOCKED_AUTH,
                version=version,
                sandbox_mode=None,
                detail="auth check failed",
                runbook=_RUNBOOK_AUTH,
            )
        if any(phrase in stderr.lower() for phrase in _CAPABILITY_PHRASES):
            return ProviderHealth(
                provider=name,
                code=HealthCode.BLOCKED_PROVIDER_CAPABILITY,
                version=version,
                sandbox_mode=None,
                detail="CLI rejected probe arguments",
                runbook=_RUNBOOK_REGRESSION,
            )
        return ProviderHealth(
            provider=name,
            code=HealthCode.ERROR,
            version=version,
            sandbox_mode=None,
            detail=f"exit {returncode}",
            runbook=None,
        )
    if matches_auth_expiry(stderr):
        return ProviderHealth(
            provider=name,
            code=HealthCode.BLOCKED_AUTH,
            version=version,
            sandbox_mode=None,
            detail="auth phrase matched in stderr",
            runbook=_RUNBOOK_AUTH,
        )
    return ProviderHealth(
        provider=name,
        code=HealthCode.ERROR,
        version=version,
        sandbox_mode=None,
        detail=f"exit {returncode}",
        runbook=None,
    )
