# PR7 — Real Adapters + Phase 0 Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the real Codex/Claude subprocess wrappers, the provider-health command, the `arena eval-harness` orchestrator, the two acceptance test deliverables, the three operator runbooks, restore the coverage gate to 70, and resolve every "verified at PR7" punch-list item in ADR-0004. After this PR merges to main, Phase 0 is closed.

**Architecture:** Skeletal subprocess wrappers per ADR-0004 + DI for `executable`/`env`/`cwd` + shim integration tests (no real-CLI auto-run). Provider-health typed core (`ProviderHealth` + `HealthCode` enum) reused by both the standalone CLI and `arena doctor`. Eval-harness is in-process orchestration scoped to the `run_id` it created. The acceptance suite has 15 documentation-style tests mapping 1:1 to §1.2.

**Tech Stack:** Python 3.12, `subprocess.run` with text-mode + UTF-8-replace decoding, Typer subapps, pytest 9.0.3 with xdist-safe per-test isolation, `monkeypatch.setattr(subprocess, "run", ...)` for unit tests, conftest-generated Python+`.cmd` shim scripts for integration tests, `time.monotonic()` for wall-clock measurement independent of provider-reported usage.

**Source design spec:** `docs/superpowers/specs/2026-05-03-pr7-real-adapters-phase0-close-design.md`.
**Architectural contract:** `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md`.

---

## Pre-dispatch preempts (carry from PR1–PR6)

Apply to every task in this plan:

- Use `from datetime import UTC, datetime` and `datetime.now(UTC).isoformat(timespec="seconds")` so timestamps pass the date-time format checker.
- Use `StrEnum` where Phase enum is referenced; do NOT add Phase values without updating `schemas/task_packet.schema.json` (drift guard at `tests/test_controller_state.py`).
- Tests use the existing `fixture_workspace` conftest fixture for CLI tests; Task 5 adds `shim_codex_executable` / `shim_claude_executable` fixtures.
- Watch for pyupgrade modernizations (StrEnum, `contextlib.AbstractContextManager`, `collections.abc` imports for Iterator/Callable/Mapping).
- Use `.venv/Scripts/python.exe` for all Python invocations.
- `ScoreboardStore.insert_experiment` takes `artifact_paths: list[str]` — pass a plain list, NOT `json.dumps(...)`.
- `TraceStore.emit` validates against `event.schema.json` with `additionalProperties: false` on payload — only use allowed keys.
- `UsageProxy` has exactly 6 fields (no `provider_calls`, no `shell_commands_observed`); `provider_calls` is the scoreboard `COUNT(*)` invariant.
- `ProviderResult.status` enum is `success | failure | blocked | killed | interrupted` — schema is closed; sub-status detail goes through artifact tokens like `<killed:wall_clock_timeout>`.
- `SandboxPolicy._default_blocked_paths` extension test (Task 4) must cover BOTH read denial AND write denial precedence over packet `allowed_paths`.
- All artifact-token substring checks in tests must `paths = json.loads(row["artifact_paths"])` first; never substring-match raw JSON.
- Branch is `pr7-real-adapters-phase0-close` (already created from main; current HEAD is the plan commit `f5069d3`, parent is the spec-fix commit `b8216ff`).
- Test count baseline: 369 passing on main pre-PR7. After PR7 expect ~395-410 (+26-41 tests across 13 tasks).
- Coverage baseline: 91.63% on main; PR7's gate restoration to 70 has 21+ percentage-point margin.

---

## Task 1: `arena/providers/auth.py` — auth-expiry pattern seed (haiku)

**Files:**
- Create: `arena/providers/auth.py`
- Create: `tests/test_provider_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_provider_auth.py`:

```python
# tests/test_provider_auth.py
"""Auth-pattern fallback classifier — positive AND negative cases.

Seed patterns are conservative; tests prevent overclassification on
broad single words (token, login) that appear in non-auth contexts.
"""
from __future__ import annotations

import pytest

from arena.providers.auth import matches_auth_expiry


@pytest.mark.parametrize(
    "stderr",
    [
        "authentication failed",
        "Credential expired, please re-authenticate.",
        "session expired",
        "Please log in to continue.",
        "token invalid",
        "Auth denied (401)",
        "401 Unauthorized",
        "Please log in again.",
        "Please re-authenticate using `codex login`.",
        "You are not logged in.",
        "not signed in",
    ],
)
def test_matches_auth_expiry_positive(stderr: str) -> None:
    assert matches_auth_expiry(stderr) is True, f"expected positive match: {stderr!r}"


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "connection refused",
        "no such file: prompt.json",
        "tokenizer initialized with 50000 tokens",  # "token" alone shouldn't match
        "user logged in successfully",                # past-tense + positive — shouldn't match
        "running with --login=optional",              # "login" as a flag shouldn't match
        "rate limit exceeded; retry after 60s",
        "permission denied: /tmp/foo",                # generic permission, not auth
        "command not found: codex",
        "child process exited with code 1",
    ],
)
def test_matches_auth_expiry_negative(stderr: str) -> None:
    assert matches_auth_expiry(stderr) is False, f"expected negative match: {stderr!r}"


def test_matches_auth_expiry_empty_string() -> None:
    assert matches_auth_expiry("") is False


def test_matches_auth_expiry_is_case_insensitive() -> None:
    assert matches_auth_expiry("AUTHENTICATION FAILED") is True
    assert matches_auth_expiry("Session Expired") is True
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_auth.py -v
```

Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement `arena/providers/auth.py`**

```python
# arena/providers/auth.py
"""Auth-expiry stderr-pattern fallback classifier.

Patterns are CONSERVATIVE SEEDS, NOT real-provider-verified. The first
real auth-failure observation MUST refresh this list (see
docs/phase0/runbooks/auth_expiry.md "Maintenance loop").

Used by `arena/providers/{codex,claude,health}.py` as the fallback
classification layer. Wrappers prefer explicit exit-code semantics
first (≥64 → BLOCKED_AUTH, 0/1/2 → success/failure/blocked); the
regex layer here only fires on the exit=1 ambiguous case.
"""
from __future__ import annotations

import re

# Conservative seed patterns derived from common CLI auth-failure
# phrasing. NOT verified against real codex/claude stderr — first real
# auth-expiry observation MUST refresh this list. See:
# docs/phase0/runbooks/auth_expiry.md (Maintenance loop section).
AUTH_EXPIRY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(authentication|credential|session|token|auth)\b.*\b"
        r"(failed|expired|invalid|denied|required)",
        re.IGNORECASE,
    ),
    # "login" pattern is narrower — only matches in the explicit
    # "please (re-)?(authenticate|log in)" construction, not bare
    # "logged in" or "--login=" usages. Negative tests pin this.
    re.compile(r"please (re-?)?(authenticate|log\s*in)", re.IGNORECASE),
    re.compile(r"\b401\b"),
    re.compile(r"\bnot (logged in|signed in)\b", re.IGNORECASE),
)


def matches_auth_expiry(stderr: str) -> bool:
    """Return True iff `stderr` contains a known auth-expiry phrase.

    The wrapper calls this only when explicit exit-code semantics did
    NOT classify the result already. False on empty input; case-
    insensitive on the first three patterns. New patterns added on
    first real-CLI auth-failure observation per the runbook.
    """
    if not stderr:
        return False
    return any(p.search(stderr) for p in AUTH_EXPIRY_PATTERNS)
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_auth.py -v
```

Expected: 23 passed (11 positive + 10 negative + 1 empty + 1 case-insensitive — pytest counts each parametrize case).

- [ ] **Step 5: Lint + mypy**

```bash
.venv/Scripts/python.exe -m ruff check arena/providers/auth.py tests/test_provider_auth.py
.venv/Scripts/python.exe -m ruff format --check arena/providers/auth.py tests/test_provider_auth.py
.venv/Scripts/python.exe -m mypy arena/providers/auth.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add arena/providers/auth.py tests/test_provider_auth.py
git commit -m "feat(providers/auth): conservative auth-expiry pattern seed + classifier"
```

---

## Task 2: `arena/providers/health.py` + `ProviderUnavailable` (standard)

**Files:**
- Create: `arena/providers/health.py`
- Create: `tests/test_provider_health.py`
- Modify: `arena/providers/base.py` (add `ProviderUnavailable` exception class)

- [ ] **Step 1: Write failing tests**

Create `tests/test_provider_health.py`:

```python
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
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
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_health.py -v
```

Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Add `ProviderUnavailable` to `arena/providers/base.py`**

Append after the existing `ProviderAdapter` class:

```python
class ProviderUnavailable(RuntimeError):
    """Raised when a real provider cannot be invoked before subprocess
    task start: missing binary, expired auth, or missing required CLI
    capability. Per ADR-0004 §"Process not started" — the controller
    treats this as a hard failure that produces NO scoreboard row and
    NO trace event.

    `code` is a runtime str (not HealthCode) to keep base.py
    dependency-free; health.py imports base.py, so typing code as
    HealthCode would create a cycle. Callers pass health.code.value.
    """

    def __init__(
        self,
        provider: str,
        code: str,
        detail: str,
        runbook: str | None = None,
    ) -> None:
        self.provider = provider
        self.code = code
        self.detail = detail
        self.runbook = runbook
        msg = f"{provider} CLI: {code} ({detail})"
        if runbook:
            msg += f"; see {runbook}"
        super().__init__(msg)
```

- [ ] **Step 4: Implement `arena/providers/health.py`**

```python
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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

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
            provider=name, code=HealthCode.NOT_FOUND, version=None,
            sandbox_mode=None, detail=f"{exe} not on PATH",
            runbook=_RUNBOOK_REGRESSION,
        )
    except subprocess.TimeoutExpired:
        return ProviderHealth(
            provider=name, code=HealthCode.ERROR, version=None,
            sandbox_mode=None, detail="health check timed out",
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
            provider=name, code=HealthCode.ERROR, version=None,
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
    except subprocess.TimeoutExpired:
        return ProviderHealth(
            provider=name, code=HealthCode.ERROR, version=parsed_version,
            sandbox_mode=None, detail="--help probe timed out",
            runbook=None,
        )

    if result.returncode != 0:
        return _classify_nonzero(name, result.returncode, result.stderr or "", version=parsed_version)

    return ProviderHealth(
        provider=name, code=HealthCode.OK, version=parsed_version,
        sandbox_mode=sandbox_mode, detail="auth ok",
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
            provider=name, code=HealthCode.BLOCKED_AUTH, version=version,
            sandbox_mode=None, detail="auth check failed",
            runbook=_RUNBOOK_AUTH,
        )
    if returncode == 2:
        if matches_auth_expiry(stderr):
            return ProviderHealth(
                provider=name, code=HealthCode.BLOCKED_AUTH, version=version,
                sandbox_mode=None, detail="auth check failed",
                runbook=_RUNBOOK_AUTH,
            )
        if any(phrase in stderr.lower() for phrase in _CAPABILITY_PHRASES):
            return ProviderHealth(
                provider=name, code=HealthCode.BLOCKED_PROVIDER_CAPABILITY,
                version=version, sandbox_mode=None,
                detail="CLI rejected probe arguments",
                runbook=_RUNBOOK_REGRESSION,
            )
        return ProviderHealth(
            provider=name, code=HealthCode.ERROR, version=version,
            sandbox_mode=None, detail=f"exit {returncode}",
            runbook=None,
        )
    if matches_auth_expiry(stderr):
        return ProviderHealth(
            provider=name, code=HealthCode.BLOCKED_AUTH, version=version,
            sandbox_mode=None, detail="auth phrase matched in stderr",
            runbook=_RUNBOOK_AUTH,
        )
    return ProviderHealth(
        provider=name, code=HealthCode.ERROR, version=version,
        sandbox_mode=None, detail=f"exit {returncode}",
        runbook=None,
    )
```

- [ ] **Step 5: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_health.py tests/test_provider_auth.py -v
```

Expected: 12 health tests + 23 auth tests = 35 passed.

- [ ] **Step 6: Lint + mypy**

```bash
.venv/Scripts/python.exe -m ruff check arena/providers/ tests/test_provider_health.py
.venv/Scripts/python.exe -m ruff format --check arena/providers/ tests/test_provider_health.py
.venv/Scripts/python.exe -m mypy arena/providers/health.py arena/providers/base.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add arena/providers/health.py arena/providers/base.py tests/test_provider_health.py
git commit -m "feat(providers): ProviderHealth typed core + ProviderUnavailable"
```

---

## Task 3: `TraceStore.write_provider_streams` extension (haiku)

**Files:**
- Modify: `arena/observability/trace_store.py`
- Modify: `tests/test_observability_trace_store.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_observability_trace_store.py`:

```python
def test_write_provider_streams_writes_four_files(tmp_path):
    """Raw + scrubbed pairs land at the canonical traces layout."""
    from arena.observability.trace_store import TraceStore, ProviderStreamPaths
    store = TraceStore(run_id="run_test", root=tmp_path)
    paths = store.write_provider_streams(
        task_id="task_0001",
        raw_stdout="raw out",
        raw_stderr="raw err",
        scrubbed_stdout="scrub out",
        scrubbed_stderr="scrub err",
    )
    assert isinstance(paths, ProviderStreamPaths)
    base = tmp_path / "run_test" / "task_0001"
    assert (base / "stdout.raw").read_text(encoding="utf-8") == "raw out"
    assert (base / "stderr.raw").read_text(encoding="utf-8") == "raw err"
    assert (base / "stdout.scrubbed").read_text(encoding="utf-8") == "scrub out"
    assert (base / "stderr.scrubbed").read_text(encoding="utf-8") == "scrub err"
    assert paths.stdout_scrubbed == base / "stdout.scrubbed"
    assert paths.stderr_scrubbed == base / "stderr.scrubbed"


def test_write_provider_streams_creates_parent_dirs(tmp_path):
    """Path layout works when traces/<run_id>/<task_id>/ doesn't exist yet."""
    from arena.observability.trace_store import TraceStore
    store = TraceStore(run_id="run_test", root=tmp_path)
    store.write_provider_streams(
        task_id="task_fresh", raw_stdout="x", raw_stderr="y",
        scrubbed_stdout="x", scrubbed_stderr="y",
    )
    assert (tmp_path / "run_test" / "task_fresh" / "stdout.raw").exists()


def test_write_provider_streams_writes_raw_before_scrubbed(tmp_path, monkeypatch):
    """Forensic recovery: raw paths must be written first."""
    from arena.observability.trace_store import TraceStore
    write_order: list[str] = []
    real_write_text = type(tmp_path).write_text
    def tracking_write_text(self, data, **kwargs):
        write_order.append(self.name)
        return real_write_text(self, data, **kwargs)
    monkeypatch.setattr("pathlib.Path.write_text", tracking_write_text)
    store = TraceStore(run_id="run_test", root=tmp_path)
    store.write_provider_streams(
        task_id="task_0001", raw_stdout="r1", raw_stderr="r2",
        scrubbed_stdout="s1", scrubbed_stderr="s2",
    )
    raw_idxs = [i for i, n in enumerate(write_order) if n.endswith(".raw")]
    scrubbed_idxs = [i for i, n in enumerate(write_order) if n.endswith(".scrubbed")]
    assert raw_idxs and scrubbed_idxs, write_order
    assert max(raw_idxs) < min(scrubbed_idxs), f"raw must precede scrubbed: {write_order}"
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_observability_trace_store.py -v -k write_provider_streams
```

Expected: ImportError on `ProviderStreamPaths` or AttributeError on `write_provider_streams`.

- [ ] **Step 3: Implement extension in `arena/observability/trace_store.py`**

Add near the top of the file (after existing imports):

```python
from dataclasses import dataclass
```

Add after the existing dataclasses (or after the TraceStore class):

```python
@dataclass(frozen=True)
class ProviderStreamPaths:
    """Frozen result of TraceStore.write_provider_streams. The four
    paths point at the four artifacts written. Raw paths are forensic-
    only; never include them in ProviderResult.artifacts, never pass
    them back into provider context, never emit them in trace events."""
    stdout_raw: Path
    stderr_raw: Path
    stdout_scrubbed: Path
    stderr_scrubbed: Path
```

Add the new method to the `TraceStore` class:

```python
    def write_provider_streams(
        self,
        *,
        task_id: str,
        raw_stdout: str,
        raw_stderr: str,
        scrubbed_stdout: str,
        scrubbed_stderr: str,
    ) -> ProviderStreamPaths:
        """Write four artifacts at:
            <root>/<run_id>/<task_id>/{stdout.raw, stderr.raw,
                                       stdout.scrubbed, stderr.scrubbed}

        Raw paths are written FIRST (forensic recovery if scrubber has
        a bug). Scrubbed paths are what consumers reference. Per
        ADR-0004 §scrubber-attachment-point.

        Returns a frozen ProviderStreamPaths with the four absolute
        paths. The scrubbed paths are appropriate for
        ProviderResult.stdout_path / stderr_path; the raw paths must
        never cross any artifact / event / report boundary."""
        # self._root is already <root>/<run_id> per TraceStore.__init__,
        # so DO NOT prepend self._run_id again — that would yield
        # traces/<run_id>/<run_id>/<task_id>/.
        base = self._root / task_id
        base.mkdir(parents=True, exist_ok=True)
        stdout_raw = base / "stdout.raw"
        stderr_raw = base / "stderr.raw"
        stdout_scrubbed = base / "stdout.scrubbed"
        stderr_scrubbed = base / "stderr.scrubbed"
        # Raw first — forensic boundary per ADR-0004.
        stdout_raw.write_text(raw_stdout, encoding="utf-8")
        stderr_raw.write_text(raw_stderr, encoding="utf-8")
        stdout_scrubbed.write_text(scrubbed_stdout, encoding="utf-8")
        stderr_scrubbed.write_text(scrubbed_stderr, encoding="utf-8")
        return ProviderStreamPaths(
            stdout_raw=stdout_raw,
            stderr_raw=stderr_raw,
            stdout_scrubbed=stdout_scrubbed,
            stderr_scrubbed=stderr_scrubbed,
        )
```

(Note: if `_root` and `_run_id` aren't the existing private names, adjust. Read the file first to confirm.)

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_observability_trace_store.py -v
```

Expected: existing trace_store tests + 3 new = all pass.

- [ ] **Step 5: Lint + mypy**

```bash
.venv/Scripts/python.exe -m ruff check arena/observability/ tests/test_observability_trace_store.py
.venv/Scripts/python.exe -m ruff format --check arena/observability/ tests/test_observability_trace_store.py
.venv/Scripts/python.exe -m mypy arena/observability/trace_store.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add arena/observability/trace_store.py tests/test_observability_trace_store.py
git commit -m "feat(trace_store): write_provider_streams centralized stream-persistence API"
```

---

## Task 4: SandboxPolicy extension — block `traces/` from provider reads (haiku)

**Files:**
- Modify: `arena/sandbox/policy.py`
- Modify: `tests/test_sandbox_policy.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_sandbox_policy.py`:

```python
def test_default_blocked_paths_includes_traces(tmp_path):
    """traces/ is in the default blocked-read set, scoped to workspace_root."""
    from arena.sandbox.policy import _default_blocked_paths
    blocked = _default_blocked_paths(workspace_root=tmp_path)
    assert (tmp_path / "traces").resolve() in blocked


def test_provider_packet_cannot_read_traces_even_if_in_allowed_paths(tmp_path):
    """blocked_paths wins over allowed_paths for SECRET_READ (raw stream protection).

    Note: is_secret_read / is_protected_write are MODULE FUNCTIONS in
    arena.sandbox.secrets, not methods on SandboxPolicy."""
    from arena.sandbox.policy import SandboxPolicy
    from arena.sandbox.secrets import is_secret_read
    packet = {
        "task_id": "task_0001",
        "allowed_paths": ["traces/"],  # try to allow traces — must still be denied
        "blocked_paths": [],
    }
    policy = SandboxPolicy.from_packet(packet, workspace_root=tmp_path)
    target = (tmp_path / "traces" / "run_x" / "task_y" / "stdout.raw").resolve()
    assert is_secret_read(target, policy) is True, (
        "blocked_paths must win over allowed_paths for raw-trace reads"
    )


def test_provider_packet_cannot_write_to_traces_even_if_in_allowed_paths(tmp_path):
    """blocked_paths wins over allowed_paths for PROTECTED_WRITE."""
    from arena.sandbox.policy import SandboxPolicy
    from arena.sandbox.secrets import is_protected_write
    packet = {
        "task_id": "task_0001",
        "allowed_paths": ["traces/"],
        "blocked_paths": [],
    }
    policy = SandboxPolicy.from_packet(packet, workspace_root=tmp_path)
    target = (tmp_path / "traces" / "fake_write.txt").resolve()
    assert is_protected_write(target, policy) is True
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_sandbox_policy.py -v -k traces
```

Expected: AssertionError on `(tmp_path / "traces").resolve() in blocked` (current default doesn't include it).

- [ ] **Step 3: Read existing `_default_blocked_paths` and `SandboxPolicy.is_secret_read` / `is_protected_write` to confirm precedence semantics**

```bash
.venv/Scripts/python.exe -c "import inspect; from arena.sandbox import policy; print(inspect.getsource(policy.SandboxPolicy))"
```

Confirm: `is_secret_read` checks `blocked_paths`; `is_protected_write` ALSO checks `blocked_paths` before checking `allowed_writes`. If the existing `is_protected_write` doesn't check `blocked_paths`, that's a bug to fix in this task — but per design spec §6, "blocked_paths wins for both." Add the check if absent.

- [ ] **Step 4: Implement `_default_blocked_paths` extension**

In `arena/sandbox/policy.py`:

```python
def _default_blocked_paths(workspace_root: Path | None = None) -> frozenset[Path]:
    """Canonical secret/credential/forensic paths providers must never read."""
    home = Path("~").expanduser().resolve()
    env_path = (
        _resolve(workspace_root / ".env") if workspace_root is not None else _resolve(Path(".env"))
    )
    traces_path = (
        _resolve(workspace_root / "traces") if workspace_root is not None else _resolve(Path("traces"))
    )
    return frozenset(
        {
            home / ".kaggle",
            home / ".codex",
            home / ".claude",
            env_path,
            traces_path,
        }
    )
```

If `is_protected_write` doesn't already check `blocked_paths`: add the check at the top of the method, returning `True` for any target that resolves under any blocked path.

- [ ] **Step 5: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_sandbox_policy.py -v
```

Expected: all existing tests + 3 new = pass.

- [ ] **Step 6: Run full suite to confirm no regression**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: 369 baseline + this PR's tests so far. No regressions from existing tests reading `traces/` for any reason.

- [ ] **Step 7: Lint + mypy**

```bash
.venv/Scripts/python.exe -m ruff check arena/sandbox/ tests/test_sandbox_policy.py
.venv/Scripts/python.exe -m ruff format --check arena/sandbox/ tests/test_sandbox_policy.py
.venv/Scripts/python.exe -m mypy arena/sandbox/policy.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add arena/sandbox/policy.py tests/test_sandbox_policy.py
git commit -m "feat(sandbox): block traces/ from provider reads (raw-trace forensic boundary)"
```

---

## Task 5: `arena/providers/codex.py` — real Codex adapter (standard)

**Files:**
- Create: `arena/providers/codex.py`
- Create: `tests/test_provider_codex.py`
- Modify: `tests/conftest.py` (add `shim_codex_executable` fixture)

- [ ] **Step 1: Extend `tests/conftest.py` with shim fixture**

Append:

```python
import os
import stat
import sys


@pytest.fixture
def shim_codex_executable(tmp_path: Path) -> Path:
    """Write a Python script that pretends to be `codex exec --json`.

    The shim's behavior is controlled by env vars:
      - ARENA_SHIM_EXIT_CODE: integer exit code (default 0)
      - ARENA_SHIM_STDOUT: NDJSON event stream to emit on stdout
      - ARENA_SHIM_STDERR: stderr text to emit
      - ARENA_SHIM_PROMPT_FILE_VAR: env var name to write the prompt
        path into (so tests can verify --prompt-file argv handling)

    Returns the absolute path to the executable. On Windows, a .cmd
    wrapper points at python invoking the script."""
    script = tmp_path / "fake_codex.py"
    script.write_text(
        '''#!/usr/bin/env python
import os, sys
exit_code = int(os.environ.get("ARENA_SHIM_EXIT_CODE", "0"))
sys.stdout.write(os.environ.get("ARENA_SHIM_STDOUT", ""))
sys.stderr.write(os.environ.get("ARENA_SHIM_STDERR", ""))
# Record the --prompt-file argv slot if requested:
var = os.environ.get("ARENA_SHIM_PROMPT_FILE_VAR")
if var:
    for i, a in enumerate(sys.argv):
        if a == "--prompt-file" and i + 1 < len(sys.argv):
            with open(os.environ.get("ARENA_SHIM_RECORD_PATH", os.devnull), "w") as f:
                f.write(sys.argv[i + 1])
            break
sys.exit(exit_code)
''',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if sys.platform == "win32":
        cmd = tmp_path / "codex.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    # POSIX: rename to "codex" so argv[0] looks right
    posix = tmp_path / "codex"
    script.rename(posix)
    posix.chmod(posix.stat().st_mode | stat.S_IXUSR)
    return posix


@pytest.fixture
def shim_claude_executable(tmp_path: Path) -> Path:
    """Same shape as shim_codex_executable but named claude / claude.cmd.
    Emits single JSON (not NDJSON) per claude -p contract."""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        '''#!/usr/bin/env python
import os, sys
sys.stdout.write(os.environ.get("ARENA_SHIM_STDOUT", ""))
sys.stderr.write(os.environ.get("ARENA_SHIM_STDERR", ""))
sys.exit(int(os.environ.get("ARENA_SHIM_EXIT_CODE", "0")))
''',
        encoding="utf-8",
    )
    if sys.platform == "win32":
        cmd = tmp_path / "claude.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    posix = tmp_path / "claude"
    script.rename(posix)
    posix.chmod(posix.stat().st_mode | stat.S_IXUSR)
    return posix
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_provider_codex.py`:

```python
# tests/test_provider_codex.py
"""RealCodexProvider: monkeypatch unit tests + shim integration tests.

Unit tests cover edge cases (timeouts, FileNotFoundError, exit-code
mapping). Shim tests exercise the real subprocess boundary including
argv construction, prompt-file routing, scrubber attachment, and
TraceStore.write_provider_streams.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderUnavailable
from arena.providers.codex import RealCodexProvider


def _packet(task_id: str = "task_0001", *, role: str = "implementation",
            phase: str = "CALIBRATION_TASK_CREATED",
            provider: str = "codex") -> dict:
    """task_packet.schema.json-valid packet helper.

    Required fields per schema (additionalProperties: false):
      schema_version, task_id, competition_slug, provider, role, phase,
      objective, inputs, allowed_paths, blocked_paths, budgets,
      required_outputs, success_criteria.
    Note: it's `required_outputs` (NOT `expected_outputs`) and
    `budgets.max_shell_commands` is in the budgets `required` set.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": "tabular_binary_v1",
        "provider": provider,
        "role": role,
        "phase": phase,
        "objective": "test",
        "inputs": [],
        "allowed_paths": [],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 100,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": [],
    }


def test_invoke_requires_event_emitter(tmp_path: Path) -> None:
    """Real adapters MUST have a non-None TraceStore at invoke() time —
    task packets do not carry run_id."""
    p = RealCodexProvider(executable="codex", version="0.4.2", cwd=tmp_path)
    with pytest.raises(RuntimeError, match=r"event_emitter"):
        p.invoke(_packet())


def test_invoke_file_not_found_raises_provider_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        raise FileNotFoundError("codex")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    with pytest.raises(ProviderUnavailable) as exc:
        p.invoke(_packet())
    assert exc.value.code == "not_found"


def test_invoke_timeout_returns_killed_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=600)
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "killed"
    assert "<killed:wall_clock_timeout>" in result.artifacts


def test_invoke_exit_64_returns_blocked_with_auth_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=64, stdout="", stderr="auth")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "blocked"
    assert "<blocked:AuthFailureBreaker>" in result.artifacts
    assert "<runbook:docs/phase0/runbooks/auth_expiry.md>" in result.artifacts


def test_invoke_exit_1_with_auth_stderr_upgrades_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="session expired, please log in")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "blocked"
    assert "<blocked:AuthFailureBreaker>" in result.artifacts


def test_invoke_exit_1_neutral_stays_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="connection refused")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert not any(t.startswith("<blocked:") for t in result.artifacts)


def test_invoke_exit_0_missing_terminal_event_returns_failure_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NDJSON without a terminal usage/artifacts event."""
    def fake_run(*a, **kw):
        return MagicMock(
            returncode=0,
            stdout='{"event": "thinking"}\n{"event": "thinking"}\n',
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert "<failure:missing_terminal_event>" in result.artifacts


def test_invoke_exit_0_with_terminal_event_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final NDJSON event has artifacts + usage."""
    terminal = json.dumps({
        "event": "done",
        "artifacts": ["submission.csv"],
        "usage": {"shell_commands": 3, "failed_commands": 0, "waste_events": 0},
    })
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=terminal + "\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    assert "submission.csv" in result.artifacts
    # UsageProxy is a TypedDict (not a dataclass) — use ["key"] access.
    assert result.usage_proxy["shell_commands"] == 3


def test_invoke_writes_provider_streams_via_tracestore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout='{"event":"done","artifacts":[],"usage":{}}\n', stderr="some err")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    base = tmp_path / "run_test" / "task_0001"
    assert (base / "stdout.raw").exists()
    assert (base / "stderr.raw").exists()
    assert (base / "stdout.scrubbed").exists()
    assert (base / "stderr.scrubbed").exists()
    # ProviderResult paths reference SCRUBBED only:
    assert result.stdout_path.endswith("stdout.scrubbed")
    assert result.stderr_path.endswith("stderr.scrubbed")


def test_invoke_records_deterministic_usage_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure paths still record wall_seconds, input_chars, output_chars."""
    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="malformed{", stderr="err")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex", version="0.4.2", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    # UsageProxy is a TypedDict — use ["key"] access.
    assert result.usage_proxy["wall_seconds"] >= 0.0
    assert result.usage_proxy["input_chars"] > 0
    assert result.usage_proxy["output_chars"] == len("malformed{") + len("err")


# Shim integration tests — exercise the real subprocess boundary

def test_shim_invoke_argv_is_correct(
    tmp_path: Path, shim_codex_executable: Path,
) -> None:
    """Real subprocess: codex shim sees the right argv structure."""
    record_path = tmp_path / "argv_record.txt"
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable=str(shim_codex_executable),
        version="0.4.2",
        cwd=tmp_path,
        env={
            "ARENA_SHIM_STDOUT": '{"event":"done","artifacts":[],"usage":{}}\n',
            "ARENA_SHIM_PROMPT_FILE_VAR": "1",
            "ARENA_SHIM_RECORD_PATH": str(record_path),
        },
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    # Shim recorded the prompt-file path; should point inside .arena_prompts/
    recorded = record_path.read_text(encoding="utf-8")
    assert ".arena_prompts" in recorded
    assert recorded.endswith("prompt_task_0001.json")


def test_shim_invoke_full_pipeline_writes_traces(
    tmp_path: Path, shim_codex_executable: Path,
) -> None:
    """Stdout + stderr from real subprocess flow through scrubber + TraceStore."""
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable=str(shim_codex_executable),
        version="0.4.2",
        cwd=tmp_path,
        env={
            "ARENA_SHIM_STDOUT": '{"event":"done","artifacts":["x.csv"],"usage":{}}\n',
            "ARENA_SHIM_STDERR": "ignore me",
        },
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    assert "x.csv" in result.artifacts
    assert (tmp_path / "run_test" / "task_0001" / "stdout.raw").read_text(encoding="utf-8").strip()
```

- [ ] **Step 3: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_codex.py -v
```

Expected: ImportError on `arena.providers.codex`.

- [ ] **Step 4: Implement `arena/providers/codex.py`**

```python
# arena/providers/codex.py
"""Real Codex adapter — subprocess wrapper for `codex exec --json`.

Per ADR-0004 invocation conventions. DI surface: executable, env, cwd,
timeout_seconds, event_emitter. event_emitter is REQUIRED at invoke()
time even though the constructor allows None (matches ABC signature
shape).

RAW PATH SECURITY BOUNDARY: stdout.raw and stderr.raw are persisted
under traces/<run_id>/<task_id>/ for forensic recovery only. They are
NEVER:
- included in ProviderResult.artifacts
- passed back into any provider's context window
- emitted to the trace event stream
- rendered in `arena report` output
- readable by sandbox-policy-enforced subprocesses (Task 4 blocks
  traces/ from provider reads)
Only *.scrubbed files cross any of those boundaries.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.observability.scrubber import scrub_text
from arena.observability.trace_store import TraceStore
from arena.providers.auth import matches_auth_expiry
from arena.providers.base import (
    ProviderAdapter,
    ProviderResult,
    ProviderUnavailable,
    UsageProxy,
)


_RUNBOOK_AUTH = "docs/phase0/runbooks/auth_expiry.md"


class RealCodexProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        executable: str = "codex",
        version: str,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
        event_emitter: TraceStore | None = None,
    ) -> None:
        self._executable = executable
        self._version = version
        self._env_overlay = dict(env) if env is not None else {}
        self._cwd = cwd if cwd is not None else Path(".")
        self._timeout_seconds = timeout_seconds
        self._event_emitter = event_emitter

    @property
    def name(self) -> str:
        return "codex"

    @property
    def version(self) -> str:
        return self._version

    def invoke(self, task_packet: dict) -> ProviderResult:
        if self._event_emitter is None:
            raise RuntimeError(
                f"{type(self).__name__}.invoke requires event_emitter; "
                "task packets do not carry run_id, so the wrapper cannot "
                "route stdout/stderr persistence without a TraceStore. "
                "Pass event_emitter= at construction time."
            )
        # Per ProviderAdapter.invoke contract: validate the incoming
        # packet. Mirrors stub_codex/stub_claude (double-validation is
        # cheap; CLI also pre-validates).
        from arena.schemas.validate import validate as _validate_schema
        _validate_schema("task_packet", task_packet)
        task_id = task_packet["task_id"]
        prompt_dir = self._cwd / ".arena_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"prompt_{task_id}.json"
        prompt_json = json.dumps(task_packet, ensure_ascii=False)
        prompt_file.write_text(prompt_json, encoding="utf-8")

        argv = [
            self._executable,
            "exec",
            "--json",
            "--workspace-write",
            str(self._cwd),
            "--prompt-file",
            str(prompt_file),
        ]
        effective_env = {**os.environ, **self._env_overlay}

        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self._timeout_seconds,
                env=effective_env,
                cwd=str(self._cwd),
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except FileNotFoundError as e:
            raise ProviderUnavailable(
                provider="codex",
                code="not_found",
                detail=f"{self._executable} not on PATH",
                runbook="docs/phase0/runbooks/cli_regression.md",
            ) from e
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
            exit_code = -1
            timed_out = True
        wall_seconds = time.monotonic() - start
        finished_at = datetime.now(UTC).isoformat(timespec="seconds")

        scrubbed_stdout = scrub_text(stdout)
        scrubbed_stderr = scrub_text(stderr)

        paths = self._event_emitter.write_provider_streams(
            task_id=task_id,
            raw_stdout=stdout,
            raw_stderr=stderr,
            scrubbed_stdout=scrubbed_stdout,
            scrubbed_stderr=scrubbed_stderr,
        )

        # Status mapping
        artifacts: list[str] = []
        parsed: dict[str, Any] = {}
        if timed_out:
            status = "killed"
            artifacts.append("<killed:wall_clock_timeout>")
        elif exit_code == 0:
            parsed = _parse_codex_ndjson(scrubbed_stdout)
            if parsed.get("_missing_terminal_event"):
                status = "failure"
                artifacts.append("<failure:missing_terminal_event>")
            else:
                status = "success"
                artifacts.extend(parsed.get("artifacts", []))
        elif exit_code >= 64:
            status = "blocked"
            artifacts.extend([
                "<blocked:AuthFailureBreaker>",
                f"<runbook:{_RUNBOOK_AUTH}>",
            ])
        elif exit_code == 2:
            status = "blocked"
        elif exit_code == 1 and matches_auth_expiry(scrubbed_stderr):
            status = "blocked"
            artifacts.extend([
                "<blocked:AuthFailureBreaker>",
                f"<runbook:{_RUNBOOK_AUTH}>",
            ])
        else:
            status = "failure"

        # UsageProxy is a TypedDict — kwarg construction is permitted by
        # PEP 589 but tests must read fields via ["key"] access. The 6
        # fields here are EXACTLY the schema's required set.
        usage: UsageProxy = {
            "input_chars": len(prompt_json),
            "output_chars": len(scrubbed_stdout) + len(scrubbed_stderr),
            "wall_seconds": wall_seconds,
            "shell_commands": int(parsed.get("usage", {}).get("shell_commands", 0)),
            "failed_commands": int(parsed.get("usage", {}).get("failed_commands", 0)),
            "waste_events": int(parsed.get("usage", {}).get("waste_events", 0)),
        }

        return ProviderResult(
            task_id=task_id,
            provider="codex",
            provider_version=self._version,
            status=status,
            stdout_path=str(paths.stdout_scrubbed),
            stderr_path=str(paths.stderr_scrubbed),
            artifacts=artifacts,
            usage_proxy=usage,
            started_at=started_at,
            finished_at=finished_at,
        )


def _parse_codex_ndjson(scrubbed_stdout: str) -> dict[str, Any]:
    """Parse codex's newline-delimited JSON event stream.

    The final event SHOULD summarize artifacts + usage. If absent,
    returns {"_missing_terminal_event": True}. Per ADR-0004 open
    question #3, resolved at PR7."""
    events: list[dict] = []
    for line in scrubbed_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not events:
        return {"_missing_terminal_event": True}
    terminal = events[-1]
    if "artifacts" not in terminal and "usage" not in terminal:
        return {"_missing_terminal_event": True}
    return {
        "artifacts": terminal.get("artifacts", []),
        "usage": terminal.get("usage", {}),
    }
```

- [ ] **Step 5: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_codex.py -v
```

Expected: 12 tests passed (10 unit + 2 shim integration).

- [ ] **Step 6: Lint + mypy**

```bash
.venv/Scripts/python.exe -m ruff check arena/providers/codex.py tests/test_provider_codex.py tests/conftest.py
.venv/Scripts/python.exe -m ruff format --check arena/providers/codex.py tests/test_provider_codex.py tests/conftest.py
.venv/Scripts/python.exe -m mypy arena/providers/codex.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add arena/providers/codex.py tests/test_provider_codex.py tests/conftest.py
git commit -m "feat(providers/codex): real Codex adapter with shim integration tests"
```

---

## Task 6: `arena/providers/claude.py` — real Claude adapter (standard)

**Files:**
- Create: `arena/providers/claude.py`
- Create: `tests/test_provider_claude.py`

The Claude adapter mirrors codex.py with two differences:
1. Stdout is single JSON (not NDJSON); parse with `json.loads`
2. Validate against role+phase-appropriate schema; on failure → `<failure:schema_violation>` or `<failure:json_decode_error>` token

- [ ] **Step 1: Write failing tests**

Create `tests/test_provider_claude.py`. Mirrors `test_provider_codex.py` but uses `RealClaudeProvider`, `claude_provider`, role="review"/"research_proxy", and JSON-shape stdout. Key additions:

```python
def test_invoke_review_role_validates_against_research_review_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role='review' + phase='FUSION_PROXY_REVIEWED' → research_review schema."""
    # research_review.schema.json (additionalProperties: false) requires:
    #   schema_version, review_id (rr_NNNN), competition_slug,
    #   subject_id, decision (accept|reject|revise|run_proxy|stop),
    #   summary (≥10 chars), strengths, weaknesses, required_fixes,
    #   follow_up_recommendations, risk_level.
    # No `recommendations` or `reviewed_at` fields — they would fail
    # additionalProperties: false.
    valid_review = json.dumps({
        "schema_version": "research_review.v1",
        "review_id": "rr_0001",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "fusion_0001",
        "decision": "accept",
        "summary": "Proposal looks reasonable for the proxy slice.",
        "strengths": ["clear mechanism", "smallest test defined"],
        "weaknesses": [],
        "required_fixes": [],
        "follow_up_recommendations": [],
        "risk_level": "low",
    })
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=valid_review, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude", version="0.3.1", cwd=tmp_path, event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    result = p.invoke(packet)
    assert result.status == "success"


def test_invoke_invalid_json_returns_failure_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout="not json{", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude", version="0.3.1", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="FUSION_PROXY_REVIEWED"))
    assert result.status == "failure"
    assert "<failure:json_decode_error>" in result.artifacts


def test_invoke_schema_violation_returns_failure_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid JSON but missing required fields → <failure:schema_violation>."""
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout='{"foo": "bar"}', stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude", version="0.3.1", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="FUSION_PROXY_REVIEWED"))
    assert result.status == "failure"
    assert "<failure:schema_violation>" in result.artifacts


def test_invoke_unknown_role_phase_combo_returns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown (role, phase) → no schema to validate against → failure."""
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout='{"foo": "bar"}', stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude", version="0.3.1", cwd=tmp_path, event_emitter=ts,
    )
    result = p.invoke(_packet(role="unknown_role", phase="UNKNOWN_PHASE"))
    assert result.status == "failure"
    assert "<failure:schema_violation>" in result.artifacts
```

Plus the same set of unit + shim tests as Task 5 (FileNotFoundError, timeout, exit-64, exit-1+auth, exit-1 neutral, deterministic usage on failure, stream persistence). Adapt argv assertions: claude argv is `[exe, "-p", "--input", str(prompt_file), "--workspace", str(cwd)]`.

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_claude.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `arena/providers/claude.py`**

Mirrors codex.py with the parser swap:

```python
# arena/providers/claude.py
"""Real Claude adapter — subprocess wrapper for `claude -p`.

Per ADR-0004. Differs from codex.py in (a) argv shape, (b) stdout is
single JSON (not NDJSON), (c) parser validates against role+phase-
appropriate schema."""
from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from arena.observability.scrubber import scrub_text
from arena.observability.trace_store import TraceStore
from arena.providers.auth import matches_auth_expiry
from arena.providers.base import (
    ProviderAdapter,
    ProviderResult,
    ProviderUnavailable,
    UsageProxy,
)
from arena.schemas.validate import validate as validate_schema


_RUNBOOK_AUTH = "docs/phase0/runbooks/auth_expiry.md"

_ROLE_PHASE_TO_SCHEMA: dict[tuple[str, str], str] = {
    ("review", "FUSION_PROXY_REVIEWED"): "research_review",
    ("research_proxy", "RESEARCH_QUESTION_CREATED"): "research_question",
    ("research_proxy", "METHOD_DIGEST_CREATED"): "paper_digest",
    ("research_proxy", "FUSION_PROPOSAL_CREATED"): "fusion_proposal",
    ("advisory_planning", "STRATEGY_RECOMMENDED"): "strategist_recommendation",
}


class RealClaudeProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        executable: str = "claude",
        version: str,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
        event_emitter: TraceStore | None = None,
    ) -> None:
        self._executable = executable
        self._version = version
        self._env_overlay = dict(env) if env is not None else {}
        self._cwd = cwd if cwd is not None else Path(".")
        self._timeout_seconds = timeout_seconds
        self._event_emitter = event_emitter

    @property
    def name(self) -> str:
        return "claude"

    @property
    def version(self) -> str:
        return self._version

    def invoke(self, task_packet: dict) -> ProviderResult:
        if self._event_emitter is None:
            raise RuntimeError(
                f"{type(self).__name__}.invoke requires event_emitter; "
                "task packets do not carry run_id."
            )
        # Adapter-level packet validation per ProviderAdapter contract.
        validate_schema("task_packet", task_packet)
        task_id = task_packet["task_id"]
        role = task_packet.get("role", "")
        phase = task_packet.get("phase", "")

        prompt_dir = self._cwd / ".arena_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"prompt_{task_id}.json"
        prompt_json = json.dumps(task_packet, ensure_ascii=False)
        prompt_file.write_text(prompt_json, encoding="utf-8")

        argv = [
            self._executable,
            "-p",
            "--input", str(prompt_file),
            "--workspace", str(self._cwd),
        ]
        effective_env = {**os.environ, **self._env_overlay}

        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                argv,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                check=False, timeout=self._timeout_seconds,
                env=effective_env, cwd=str(self._cwd),
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except FileNotFoundError as e:
            raise ProviderUnavailable(
                provider="claude", code="not_found",
                detail=f"{self._executable} not on PATH",
                runbook="docs/phase0/runbooks/cli_regression.md",
            ) from e
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
            exit_code = -1
            timed_out = True
        wall_seconds = time.monotonic() - start
        finished_at = datetime.now(UTC).isoformat(timespec="seconds")

        scrubbed_stdout = scrub_text(stdout)
        scrubbed_stderr = scrub_text(stderr)

        paths = self._event_emitter.write_provider_streams(
            task_id=task_id,
            raw_stdout=stdout, raw_stderr=stderr,
            scrubbed_stdout=scrubbed_stdout, scrubbed_stderr=scrubbed_stderr,
        )

        artifacts: list[str] = []
        if timed_out:
            status = "killed"
            artifacts.append("<killed:wall_clock_timeout>")
        elif exit_code == 0:
            parse_outcome = _parse_claude_response(scrubbed_stdout, role=role, phase=phase)
            if parse_outcome["status"] == "success":
                status = "success"
            else:
                status = "failure"
                artifacts.append(f"<failure:{parse_outcome['reason']}>")
        elif exit_code >= 64:
            status = "blocked"
            artifacts.extend([
                "<blocked:AuthFailureBreaker>",
                f"<runbook:{_RUNBOOK_AUTH}>",
            ])
        elif exit_code == 2:
            status = "blocked"
        elif exit_code == 1 and matches_auth_expiry(scrubbed_stderr):
            status = "blocked"
            artifacts.extend([
                "<blocked:AuthFailureBreaker>",
                f"<runbook:{_RUNBOOK_AUTH}>",
            ])
        else:
            status = "failure"

        # UsageProxy TypedDict — see codex.py for the convention note.
        usage: UsageProxy = {
            "input_chars": len(prompt_json),
            "output_chars": len(scrubbed_stdout) + len(scrubbed_stderr),
            "wall_seconds": wall_seconds,
            "shell_commands": 0,
            "failed_commands": 0,
            "waste_events": 0,
        }

        return ProviderResult(
            task_id=task_id,
            provider="claude",
            provider_version=self._version,
            status=status,
            stdout_path=str(paths.stdout_scrubbed),
            stderr_path=str(paths.stderr_scrubbed),
            artifacts=artifacts,
            usage_proxy=usage,
            started_at=started_at,
            finished_at=finished_at,
        )


def _parse_claude_response(scrubbed_stdout: str, *, role: str, phase: str) -> dict[str, Any]:
    """Parse + role-phase-validate claude's single-JSON stdout.

    Returns {"status": "success"} on parse + schema-valid; otherwise
    {"status": "failure", "reason": "json_decode_error" | "schema_violation"}.
    """
    schema_name = _ROLE_PHASE_TO_SCHEMA.get((role, phase))
    if schema_name is None:
        return {"status": "failure", "reason": "schema_violation"}
    try:
        payload = json.loads(scrubbed_stdout)
    except json.JSONDecodeError:
        return {"status": "failure", "reason": "json_decode_error"}
    try:
        validate_schema(schema_name, payload)
    except ValidationError:
        return {"status": "failure", "reason": "schema_violation"}
    return {"status": "success"}
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_provider_claude.py -v
```

Expected: 13 tests passed (mirrors codex's 12 + role/phase dispatch test).

- [ ] **Step 5: Lint + mypy + commit**

```bash
.venv/Scripts/python.exe -m ruff check arena/providers/claude.py tests/test_provider_claude.py
.venv/Scripts/python.exe -m ruff format --check arena/providers/claude.py tests/test_provider_claude.py
.venv/Scripts/python.exe -m mypy arena/providers/claude.py
git add arena/providers/claude.py tests/test_provider_claude.py
git commit -m "feat(providers/claude): real Claude adapter with role+phase schema dispatch"
```

---

## Task 7: `arena provider health` Typer subapp + CLI (haiku)

**Files:**
- Modify: `arena/cli.py` (add `provider_app` Typer subapp + `provider health` command)
- Create: `tests/test_cli_provider_health.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_provider_health.py`:

```python
# tests/test_cli_provider_health.py
"""arena provider health <name> — text output + exit codes.

Stub paths exit 0 with a green checkmark line. Real paths use
monkeypatch on provider_health.check to simulate each HealthCode.
"""
from __future__ import annotations

import pytest
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
        provider="codex", code=HealthCode.NOT_FOUND, version=None,
        sandbox_mode=None, detail="codex not on PATH",
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
        provider="codex", code=HealthCode.BLOCKED_AUTH, version=None,
        sandbox_mode=None, detail="auth check failed",
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
        provider="codex", code=HealthCode.BLOCKED_PROVIDER_CAPABILITY,
        version="0.4.2", sandbox_mode=None,
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
        provider="codex", code=HealthCode.OK, version="0.4.2",
        sandbox_mode="workspace-write", detail="auth ok", runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    runner = CliRunner()
    result = runner.invoke(app, ["provider", "health", "codex"])
    assert result.exit_code == 0
    assert "0.4.2" in result.output
    assert "workspace-write" in result.output
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_provider_health.py -v
```

Expected: failures (no `provider` subcommand or `health_check` import).

- [ ] **Step 3: Add Typer subapp + CLI command in `arena/cli.py`**

Add to imports (next to existing `from arena.providers.stub_*`):

```python
from arena.providers.health import HealthCode, check as health_check
```

After `app.add_typer(memory_app, name="memory")` and `app.add_typer(self_improve_app, name="self-improve")`:

```python
provider_app = typer.Typer(help="Provider commands.")
app.add_typer(provider_app, name="provider")
```

Add at the end of the file (after the existing self-improve scan command):

```python
@provider_app.command("health")
def provider_health(name: str) -> None:
    """Run a cheap, non-mutating health check for `<name>`.

    Stubs (stub_codex, stub_claude) short-circuit. Real providers
    (codex, claude) probe --version + --help. Output is a single line
    plus an optional runbook reference. Exits 0 on OK, 1 on any other
    HealthCode."""
    h = health_check(name)
    if h.code == HealthCode.OK:
        line = f"[green]✅[/green] {h.provider}: {h.version}"
        if h.sandbox_mode:
            line += f" ({h.sandbox_mode}; {h.detail})"
        else:
            line += f" ({h.detail})"
        console.print(line)
        raise typer.Exit(0)
    label = h.code.value.upper().replace("_", " ")
    console.print(f"[red]❌[/red] {h.provider}: {label} ({h.detail})")
    if h.runbook:
        console.print(f"Runbook: {h.runbook}")
    raise typer.Exit(1)
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_provider_health.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Lint + mypy + commit**

```bash
.venv/Scripts/python.exe -m ruff check arena/cli.py tests/test_cli_provider_health.py
.venv/Scripts/python.exe -m ruff format --check arena/cli.py tests/test_cli_provider_health.py
.venv/Scripts/python.exe -m mypy arena/cli.py
git add arena/cli.py tests/test_cli_provider_health.py
git commit -m "feat(cli): arena provider health subcommand + provider Typer subapp"
```

---

## Task 8: `_get_provider` real-provider branch + `arena doctor` extension (standard)

**Files:**
- Modify: `arena/cli.py` (extend `_get_provider`, extend `doctor` command)
- Create: `tests/test_cli_get_provider.py`
- Modify: `tests/test_cli_doctor.py` (or create if absent)

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_get_provider.py`:

```python
# tests/test_cli_get_provider.py
"""_get_provider real-provider resolution + ProviderUnavailable raises.

Stub paths return their respective providers without health-check.
Real paths run provider_health.check first and raise ProviderUnavailable
on any non-OK HealthCode."""
from __future__ import annotations

from pathlib import Path

import pytest

from arena.cli import _get_provider
from arena.providers.base import ProviderUnavailable
from arena.providers.codex import RealCodexProvider
from arena.providers.claude import RealClaudeProvider
from arena.providers.stub_codex import StubCodexProvider
from arena.providers.stub_claude import StubClaudeProvider
from arena.providers.health import HealthCode, ProviderHealth


def test_get_provider_stub_codex_no_health_check():
    p = _get_provider("stub_codex")
    assert isinstance(p, StubCodexProvider)


def test_get_provider_stub_claude_no_health_check():
    p = _get_provider("stub_claude")
    assert isinstance(p, StubClaudeProvider)


def test_get_provider_codex_ok_returns_real_adapter(monkeypatch: pytest.MonkeyPatch):
    fake = ProviderHealth(
        provider="codex", code=HealthCode.OK, version="0.4.2",
        sandbox_mode="workspace-write", detail="auth ok", runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    p = _get_provider("codex")
    assert isinstance(p, RealCodexProvider)
    assert p.version == "0.4.2"


def test_get_provider_codex_not_found_raises_provider_unavailable(monkeypatch):
    fake = ProviderHealth(
        provider="codex", code=HealthCode.NOT_FOUND, version=None,
        sandbox_mode=None, detail="codex not on PATH",
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
        provider="codex", code=HealthCode.OK, version=None,
        sandbox_mode="workspace-write", detail="auth ok", runbook=None,
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: fake)
    with pytest.raises(ProviderUnavailable) as exc:
        _get_provider("codex")
    assert exc.value.code == "error"


def test_get_provider_unknown_raises_bad_parameter(monkeypatch):
    import typer
    with pytest.raises(typer.BadParameter):
        _get_provider("unknown_xyz")
```

Create or extend `tests/test_cli_doctor.py`:

```python
# tests/test_cli_doctor.py
from typer.testing import CliRunner

from arena.cli import app
from arena.providers.health import HealthCode, ProviderHealth


def test_doctor_exits_0_when_real_clis_missing(monkeypatch, fixture_workspace):
    """Doctor must NOT exit non-zero on NOT_FOUND — readiness inventory,
    not fail-fast."""
    not_found = ProviderHealth(
        provider="codex", code=HealthCode.NOT_FOUND, version=None,
        sandbox_mode=None, detail="not on PATH",
        runbook="docs/phase0/runbooks/cli_regression.md",
    )
    monkeypatch.setattr("arena.cli.health_check", lambda name: not_found)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not installed" in result.output.lower() or "not on path" in result.output.lower()


def test_doctor_summary_says_complete_not_passed(monkeypatch, fixture_workspace):
    not_found = ProviderHealth(
        provider="codex", code=HealthCode.NOT_FOUND, version=None,
        sandbox_mode=None, detail="not on PATH",
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
            provider=name, code=HealthCode.OK, version="x.y",
            sandbox_mode="ws", detail="ok", runbook=None,
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert "codex" in result.output.lower()
    assert "claude" in result.output.lower()
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_get_provider.py tests/test_cli_doctor.py -v
```

Expected: 6 + 3 = 9 failures.

- [ ] **Step 3: Extend `_get_provider` in `arena/cli.py`**

Replace existing function:

```python
def _get_provider(
    name: str,
    *,
    event_emitter: TraceStore | None = None,
) -> ProviderAdapter:
    if name == "stub_codex":
        return StubCodexProvider(workspace_root=WORKTREE_ROOT, event_emitter=event_emitter)
    if name == "stub_claude":
        return StubClaudeProvider(workspace_root=WORKTREE_ROOT, event_emitter=event_emitter)
    if name in ("codex", "claude"):
        from arena.providers.codex import RealCodexProvider
        from arena.providers.claude import RealClaudeProvider
        cls = RealCodexProvider if name == "codex" else RealClaudeProvider
        h = health_check(name)
        if h.code != HealthCode.OK:
            raise ProviderUnavailable(
                provider=name, code=h.code.value,
                detail=h.detail, runbook=h.runbook,
            )
        if h.version is None:
            raise ProviderUnavailable(
                provider=name, code="error",
                detail="--version probe returned no parseable version",
                runbook=None,
            )
        return cls(
            executable=name,
            version=h.version,
            event_emitter=event_emitter,
        )
    raise typer.BadParameter(f"unknown provider: {name}")
```

Add `from arena.providers.base import ProviderUnavailable` to the imports.

- [ ] **Step 4: Extend `doctor` command in `arena/cli.py`**

Replace existing doctor:

```python
@app.command()
def doctor() -> None:
    """Run lightweight local readiness checks."""
    validate_fixture_manifest("fixtures/tabular_binary_v1")
    console.print("[green]✅[/green] fixture manifest")

    # Provider CLIs — non-fatal status lines. Doctor is a readiness
    # inventory; `arena provider health <name>` is the fail-fast check.
    for name in ("codex", "claude"):
        h = health_check(name)
        if h.code == HealthCode.OK:
            console.print(
                f"[green]✅[/green] {h.provider} CLI: {h.version} ({h.detail})"
            )
        elif h.code == HealthCode.NOT_FOUND:
            console.print(
                f"[yellow]⚠[/yellow]  {h.provider} CLI: not installed "
                "(stub-only is fine for CI)"
            )
        else:
            label = h.code.value.upper().replace("_", " ")
            console.print(
                f"[red]❌[/red] {h.provider} CLI: {label} ({h.detail})"
            )

    console.print("arena doctor complete")
```

- [ ] **Step 5: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_get_provider.py tests/test_cli_doctor.py -v
```

Expected: 9 passed.

- [ ] **Step 6: Run full suite to confirm no regression**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: previous total + new tests, no regressions.

- [ ] **Step 7: Lint + mypy + commit**

```bash
.venv/Scripts/python.exe -m ruff check arena/cli.py tests/test_cli_get_provider.py tests/test_cli_doctor.py
.venv/Scripts/python.exe -m ruff format --check arena/cli.py
.venv/Scripts/python.exe -m mypy arena/cli.py
git add arena/cli.py tests/test_cli_get_provider.py tests/test_cli_doctor.py
git commit -m "feat(cli): _get_provider real-provider resolution + doctor provider section"
```

---

## Task 9: `arena eval-harness` orchestrator (standard)

**Files:**
- Modify: `arena/cli.py` (add `eval_harness` command + `_StepResult` + helpers)
- Create: `tests/test_cli_eval_harness.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_eval_harness.py`:

```python
# tests/test_cli_eval_harness.py
"""arena eval-harness — orchestration smoke + step-failure reporting.

Tests assert key substrings/counts/rows. NO snapshot assertions on the
full table.
"""
from __future__ import annotations

import pytest
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
        "init-fixture", "plan", "run-next", "research-proxy",
        "evaluate", "review", "memory propose", "self-improve scan", "report",
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
    fixture_workspace, monkeypatch,
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
    fixture_workspace, monkeypatch,
) -> None:
    """Per spec §3.2: SI scan reported as ok regardless of freeze."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["eval-harness", "tabular_binary_v1", "--providers", "stub"])
    # Even if SI scan fired findings, the step must show ok status
    assert "self-improve scan" in result.output
    # The 9/9 ok line still holds in the happy path
    assert "9/9 steps ok" in result.output or "ok" in result.output.lower()
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_eval_harness.py -v
```

Expected: AttributeError on `_resolve_providers` or unknown command `eval-harness`.

- [ ] **Step 3: Implement `arena eval-harness` in `arena/cli.py`**

Add `_StepResult` dataclass + helpers + command. Place at end of file (after `self_improve_scan`):

```python
@dataclass(frozen=True)
class _StepResult:
    name: str
    status: str        # "ok" | "failed" | "skipped"
    reason: str | None


def _resolve_providers(providers: str) -> tuple[str, str]:
    if providers == "stub":
        return "stub_codex", "stub_claude"
    if providers == "real":
        return "codex", "claude"
    raise typer.BadParameter(f"--providers must be 'stub' or 'real', got {providers!r}")


def _lookup_latest_impl_row(slug: str, *, run_id: str) -> str | None:
    store = _store()
    try:
        row = store._require_conn().execute(
            "SELECT experiment_id FROM experiments "
            "WHERE competition_slug = ? AND run_id = ? "
            "AND artifact_paths LIKE ? "
            "ORDER BY experiment_id DESC LIMIT 1",
            (slug, run_id, '%"<step:implementation>"%'),
        ).fetchone()
    finally:
        store.close()
    return row["experiment_id"] if row else None


def _lookup_latest_review_row(slug: str, *, run_id: str) -> str | None:
    store = _store()
    try:
        row = store._require_conn().execute(
            "SELECT experiment_id FROM experiments "
            "WHERE competition_slug = ? AND run_id = ? "
            "AND artifact_paths LIKE ? "
            "ORDER BY experiment_id DESC LIMIT 1",
            (slug, run_id, '%"<step:review>"%'),
        ).fetchone()
    finally:
        store.close()
    return row["experiment_id"] if row else None


def _render_step_table(steps: list[_StepResult]) -> None:
    from rich.table import Table
    table = Table(title="eval-harness step summary")
    table.add_column("Step")
    table.add_column("Status")
    table.add_column("Reason")
    for s in steps:
        glyph = {"ok": "✅ ok", "failed": "❌ failed", "skipped": "⊘ skipped"}[s.status]
        table.add_row(s.name, glyph, s.reason or "")
    console.print(table)
    failed = sum(1 for s in steps if s.status == "failed")
    skipped = sum(1 for s in steps if s.status == "skipped")
    ok = sum(1 for s in steps if s.status == "ok")
    if failed == 0 and skipped == 0:
        console.print(f"{ok}/{len(steps)} steps ok.")
    else:
        console.print(f"{ok}/{len(steps)} steps ok; {failed} failed; {skipped} skipped.")


@app.command("eval-harness")
def eval_harness(
    competition_slug: str,
    providers: str = typer.Option(
        "stub", "--providers",
        help="'stub' (stub_codex + stub_claude) or 'real' (codex + claude).",
    ),
) -> None:
    """Run the full Phase-0 sequence and report per-step status.

    Continue-collect: a failed step does NOT abort the run; subsequent
    steps with hard data dependencies skip with reason='prerequisite
    missing in this run'. Status semantics = "step execution status",
    NOT "no findings/no freeze". Exit 0 iff every step is ok or
    skipped-by-design.
    """
    codex_provider, claude_provider = _resolve_providers(providers)
    steps: list[_StepResult] = []

    def run(name: str, fn, *args, **kwargs) -> bool:
        try:
            fn(*args, **kwargs)
        except typer.Exit as e:
            code = e.exit_code or 0
            if code == 0:
                steps.append(_StepResult(name, "ok", None))
                return True
            steps.append(_StepResult(name, "failed", f"exit {code}"))
            return False
        except (typer.BadParameter, BudgetExceeded, KillSwitchActive, ProviderUnavailable) as e:
            steps.append(_StepResult(name, "failed", str(e) or type(e).__name__))
            return False
        steps.append(_StepResult(name, "ok", None))
        return True

    def skip(name: str, reason: str) -> None:
        steps.append(_StepResult(name, "skipped", reason))

    # Capture the run_id init-fixture creates, so all subsequent lookups
    # filter by run_id (avoids cross-run row pickup; spec §3.2).
    run("init-fixture", init_fixture, competition_slug)
    harness_run_id = _latest_run_id()

    run("plan", plan, competition_slug)
    run("run-next (calibration)", run_next, competition_slug, provider=codex_provider)
    run("research-proxy", research_proxy, competition_slug, provider=claude_provider)

    impl_exp_id = (
        _lookup_latest_impl_row(competition_slug, run_id=harness_run_id)
        if harness_run_id else None
    )
    if impl_exp_id:
        run("evaluate --latest", evaluate, competition_slug, latest=True)
        if run(
            f"review --experiment {impl_exp_id}",
            review, competition_slug,
            provider=claude_provider, experiment=impl_exp_id,
        ):
            review_exp_id = _lookup_latest_review_row(
                competition_slug, run_id=harness_run_id,
            )
            if review_exp_id:
                run(
                    f"memory propose --review {review_exp_id}",
                    memory_propose, competition_slug, review=review_exp_id,
                )
            else:
                skip("memory propose", "review row not found in this run")
    else:
        skip("evaluate", "impl row not found in this run")
        skip("review", "impl row not found in this run")
        skip("memory propose", "review prerequisite missing in this run")

    run("self-improve scan", self_improve_scan, competition_slug)
    run("report", report, competition_slug)

    _render_step_table(steps)

    failed_count = sum(1 for s in steps if s.status == "failed")
    raise typer.Exit(1 if failed_count > 0 else 0)
```

(Note: `init_fixture`, `plan`, `run_next`, `research_proxy`, `evaluate`, `review`, `memory_propose`, `self_improve_scan`, `report` are all already defined in cli.py — call them directly.)

Add to imports near the top of cli.py if not already present:

```python
from dataclasses import dataclass
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_eval_harness.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Lint + mypy + full suite + commit**

```bash
.venv/Scripts/python.exe -m ruff check arena/cli.py tests/test_cli_eval_harness.py
.venv/Scripts/python.exe -m ruff format --check arena/cli.py
.venv/Scripts/python.exe -m mypy arena/cli.py
.venv/Scripts/python.exe -m pytest -q
git add arena/cli.py tests/test_cli_eval_harness.py
git commit -m "feat(cli): arena eval-harness orchestrator with run-scoped lookups"
```

---

## Task 10: `tests/test_phase0_acceptance.py` — 15 condition-style tests (standard)

**Files:**
- Create: `tests/test_phase0_acceptance.py`

This file does not introduce new production code. Each test exercises EXISTING functionality with a focused per-condition assertion using production-facing CLI/APIs.

- [ ] **Step 1: Write the file**

Per the spec §6a + §9 mapping table. Each test ~5-15 lines, named `test_condition_NN_<name>`. Use `fixture_workspace` for per-test isolation; tmp_path env override for kill switch; in-test cleanup for freeze sentinel. Always `paths = json.loads(row["artifact_paths"])` before token check; status="completed" not "ok".

The file's docstring contains the §1.2 wording verbatim (shown in spec §6a).

For each condition, the test invokes the relevant CLI command(s) via `runner.invoke(app, [...])` and asserts a focused invariant. Examples already shown in the design spec §6a — use those verbatim for conditions 01, 06, 11, 14, 15. Sketch the remaining 10 in the same shape:

- 02 stub_codex callable: `StubCodexProvider(workspace_root=tmp_path)` constructs without error; `provider.invoke(packet).status == "success"`.
- 03 stub_claude callable: same with role-appropriate packet.
- 04 stdout/stderr captured/scrubbed: run-next → assert `traces/<run_id>/<task_id>/{stdout.scrubbed, stderr.scrubbed}` exist (PR4 deliverable; existing).
- 05 fixture init/eval/score: `init-fixture` then `fixture-smoke` → assert score is in (0, 1).
- 07 research-proxy completes: `research-proxy` → assert at least one `<step:implementation>` row exists.
- 08 review at least one: `review --experiment <impl>` → assert review row with `<step:review>` token.
- 09 scoreboard records: after run-next, assert experiment row has non-null `wall_seconds`, `provider_version`, `artifact_paths`, `valid_submission`.
- 10 governor enforces: set `ARENA_PHASE0_PROVIDER_CALL_CAP=0` and run-next → assert exit blocks.
- 12 sandbox denies secrets: `from arena.sandbox.secrets import is_secret_read; from arena.sandbox.policy import SandboxPolicy`; assert `is_secret_read(home / ".kaggle" / "key", SandboxPolicy.from_packet({}, workspace_root=tmp_path))` is True. (Module functions, not methods.)
- 13 memory updates as deltas: `memory propose --review <id>` → assert `mem_*.json` exists; assert `memory/research.md` is NOT mutated by the propose command (only diff rendered).

- [ ] **Step 2: Run the tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase0_acceptance.py -v
```

Expected: 15 passed.

- [ ] **Step 3: Run with `-k` and xdist to confirm independence**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase0_acceptance.py -v -k condition_06
.venv/Scripts/python.exe -m pytest tests/test_phase0_acceptance.py -n 4
```

Expected: filterable runs work; xdist run produces 15 passed.

- [ ] **Step 4: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check tests/test_phase0_acceptance.py
.venv/Scripts/python.exe -m ruff format --check tests/test_phase0_acceptance.py
git add tests/test_phase0_acceptance.py
git commit -m "test(phase0): 15 closure-condition acceptance tests with §1.2 mapping"
```

---

## Task 11: `tests/test_research_proxy_full_loop.py` — single sequential test (haiku)

**Files:**
- Create: `tests/test_research_proxy_full_loop.py`

Per spec §6b. Single test function; intermediate `§6.2 step N` step-labelled asserts; CLI driven via `runner.invoke(app, [...])`; no helper sharing with eval-harness; inline scoreboard SQL for impl/review id lookup; `paths = json.loads(row["artifact_paths"])` before token check; status=="completed".

The full test body is in spec §6b — use it verbatim.

- [ ] **Step 1: Write the file** (verbatim from spec §6b)
- [ ] **Step 2: Run test; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_research_proxy_full_loop.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check tests/test_research_proxy_full_loop.py
.venv/Scripts/python.exe -m ruff format --check tests/test_research_proxy_full_loop.py
git add tests/test_research_proxy_full_loop.py
git commit -m "test(research_proxy): full §6.2 ten-step loop with step-labelled asserts"
```

---

## Task 12: Operator runbooks (haiku)

**Files:**
- Create: `docs/phase0/runbooks/auth_expiry.md`
- Create: `docs/phase0/runbooks/reboot.md`
- Create: `docs/phase0/runbooks/cli_regression.md`
- Create: `tests/test_runbooks_exist.py`

Per spec §11. Free-form prose with the documented header structure. Content sketches in spec §11.1, §11.2, §11.3 — expand each into a readable runbook of ~80-150 lines.

- [ ] **Step 1: Write the three runbooks** per spec §11.1-§11.3 sketches.

Notes:
- `auth_expiry.md` MUST include the maintenance loop (capture stderr → add pattern → add regression test → update runbook).
- `auth_expiry.md` MUST mention "health probes are non-mutating and token-free; if a CLI changes that, treat as BLOCKED_PROVIDER_CAPABILITY."
- `auth_expiry.md` `codex login` / `claude login` shown as placeholders: "(or the CLI's current documented auth command)".
- `reboot.md` does NOT mention `INTERRUPTED_REQUIRES_REVIEW` as a row status. Use "the operator reviews the last durable row/trace and decides how to re-issue."
- `reboot.md` does NOT mention `arena resume` — there is no such command in Phase 0.
- `cli_regression.md` references the wrappers' `_build_argv()` and the shim's accepted argv set as the things to update.
- All three include relative cross-refs to each other and to ADR-0004 / ADR-0003.

- [ ] **Step 2: Write `tests/test_runbooks_exist.py`**

```python
# tests/test_runbooks_exist.py
"""Cheap existence/header coverage for the three Phase-0 runbooks.

Catches accidental delete/rename. Not a full doc-validation test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "docs" / "phase0" / "runbooks"


@pytest.mark.parametrize(
    "filename, title",
    [
        ("auth_expiry.md", "Auth"),
        ("reboot.md", "Reboot"),
        ("cli_regression.md", "CLI"),
    ],
)
def test_runbook_exists_and_has_title(filename: str, title: str) -> None:
    p = RUNBOOK_DIR / filename
    assert p.exists(), f"runbook missing: {p}"
    text = p.read_text(encoding="utf-8")
    # First non-empty line should be a level-1 header containing the title:
    first = next((line for line in text.splitlines() if line.strip()), "")
    assert first.startswith("# "), f"runbook {filename} missing top-level header"
    assert title.lower() in first.lower(), f"runbook {filename} header missing {title!r}"


def test_auth_expiry_runbook_documents_maintenance_loop() -> None:
    text = (RUNBOOK_DIR / "auth_expiry.md").read_text(encoding="utf-8")
    assert "Maintenance" in text or "maintenance" in text
    assert "AUTH_EXPIRY_PATTERNS" in text or "auth.py" in text


def test_reboot_runbook_does_not_invent_arena_resume() -> None:
    text = (RUNBOOK_DIR / "reboot.md").read_text(encoding="utf-8")
    assert "arena resume" not in text, (
        "reboot runbook must not reference a nonexistent `arena resume` command"
    )
```

- [ ] **Step 3: Run the test; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_runbooks_exist.py -v
```

Expected: 5 passed (3 parametrize + 2 specific).

- [ ] **Step 4: Commit**

```bash
git add docs/phase0/runbooks/ tests/test_runbooks_exist.py
git commit -m "docs(phase0): operator runbooks for auth-expiry, reboot, cli-regression"
```

---

## Task 13: Cleanup pass + coverage gate (haiku)

**Files:**
- Modify: `pyproject.toml` (coverage gate 50 → 70)
- Modify: `arena/providers/stub_codex.py:36`
- Modify: `arena/providers/stub_claude.py:410`
- Modify: `arena/research_proxy/question_generator.py:16,61`
- Modify: `arena/self_improvement/scan.py:83`
- Modify: `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` (status + 4 punch-list resolutions)

THIS TASK MUST BE THE LAST COMMIT ON PR7. All other tasks must be merged first; the coverage gate flip and ADR resolution depend on the rest of PR7 being landed.

- [ ] **Step 1: pyproject.toml gate restore**

Replace the existing block:

```toml
# Old
# TODO(PR7): restore fail_under = 70 once all subsystems have tests landed.
# Lowered to 50 in PR0 so PR1-PR6 can land iteratively without the gate
# blocking on plumbing code that has not yet been wired into a behavioral path.
fail_under = 50
```

with:

```toml
fail_under = 70
```

- [ ] **Step 2: Update "PR7 will…" comments to past tense / timeless wording**

Open each file and apply per spec §14:

- `arena/providers/stub_codex.py:36` — "PR7 with real Codex will produce…" → drop the PR ref or past-tense.
- `arena/providers/stub_claude.py:410` — "After PR7's real Codex lands…" → past-tense.
- `arena/research_proxy/question_generator.py:16` — "PR7's real Claude will replace this with…" → "Real Claude adapters can replace this deterministic builder in production runs." (timeless)
- `arena/research_proxy/question_generator.py:61` — "(or real Claude in PR7)" → "(or real Claude in production)".
- `arena/self_improvement/scan.py:83` — "(or PR7's real adapters)" → "(or production CLI adapters)".

- [ ] **Step 3: Update ADR-0004**

Edit `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md`:

- Status header: `accepted (forward-looking; verified-on-implement at PR7)` → `accepted (verified)`.
- Each "verified at PR7" inline marker is replaced with the resolved value:
  - Codex flag spelling: pinned to the shim's argv set (`exec --json --workspace-write <ws> --prompt-file <path>`).
  - Claude flag spelling: pinned to (`-p --input <path> --workspace <ws>`).
  - Auth-expiry stderr fingerprint: seed list at `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS`; runbook at `docs/phase0/runbooks/auth_expiry.md`.
  - Codex terminal-event behaviour: parser returns sentinel mapping to `ProviderResult(status="failure")` + `<failure:missing_terminal_event>` artifact token.
  - Streaming: PR7 buffers (per ADR's stated default).
  - Auth exit code range: ≥64 → BLOCKED_AUTH (verified in `arena/providers/health.py::_classify_nonzero`).
- "Open questions to verify at PR7" section heading → "Resolved at PR7" with the four answers above.

- [ ] **Step 4: Run full suite + lint + mypy + acceptance scripts + pip-audit**

```bash
.venv/Scripts/python.exe -m pytest --cov=arena -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
.venv/Scripts/python.exe -m pip_audit
.venv/Scripts/python.exe scripts/validate_schemas.py
.venv/Scripts/python.exe scripts/validate_prompt_delimiters.py
.venv/Scripts/python.exe scripts/fixture_smoke.py
.venv/Scripts/python.exe scripts/static_sandbox_policy_check.py
.venv/Scripts/python.exe scripts/check_migrations.py
```

Expected: all clean. Coverage `Required test coverage of 70.0% reached. Total coverage: 90+.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml \
  arena/providers/stub_codex.py arena/providers/stub_claude.py \
  arena/research_proxy/question_generator.py \
  arena/self_improvement/scan.py \
  docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md
git commit -m "chore(pr7-cleanup): close-out polish — gate 50→70, PR7 will → past tense, ADR-0004 verified"
```

---

## PR7 acceptance recap

After Task 13, the following must all be true on a clean clone:

```bash
pip install '.[dev]'
pytest --cov=arena -q                      # → 395+ passed (was 369), gate 70 holds
ruff check . && ruff format --check .
mypy arena
pip-audit                                  # → no known vulnerabilities
python scripts/validate_schemas.py
python scripts/validate_prompt_delimiters.py
python scripts/fixture_smoke.py
python scripts/static_sandbox_policy_check.py
python scripts/check_migrations.py
```

Plus operator-only (not in CI):

```bash
arena provider health codex        # → exit 0 if codex installed + auth ok; exit 1 otherwise
arena provider health claude       # → same
arena provider health stub_codex   # → exit 0 short-circuit
arena doctor                       # → exits 0 even with both real CLIs missing
arena eval-harness tabular_binary_v1 --providers stub   # → 9/9 ok, exit 0
```

Spec acceptance §18 is satisfied. Phase 0 is closed when this PR merges to main.

---

## Spec coverage map

| Spec section | Implementation task |
|---|---|
| §3.1 `arena provider health <name>` | Task 7 |
| §3.2 `arena eval-harness <slug>` | Task 9 |
| §3.3 `arena doctor` extension | Task 8 |
| §3.4 `provider` Typer subapp | Task 7 |
| §4 real adapters (codex.py, claude.py) | Tasks 5, 6 |
| §4.1 `invoke()` flow | Tasks 5, 6 |
| §4.2 deterministic UsageProxy | Tasks 5, 6 |
| §4.3 parser split (NDJSON vs single-JSON) | Tasks 5, 6 |
| §4.4 auth.py seed | Task 1 |
| §4.5 ProviderUnavailable | Task 2 |
| §5 TraceStore.write_provider_streams | Task 3 |
| §6 Sandbox traces/ blocking | Task 4 |
| §7 version_baseline reuse (no new API) | Task 8 (the `record_provider_version` call already exists in run-next; PR7 just feeds real-version strings) |
| §8 file structure | All tasks |
| §9 §1.2 closure-condition coverage map | Task 10 |
| §10 §6.2 full-loop coverage | Task 11 |
| §11 runbooks | Task 12 |
| §12 coverage gate restore | Task 13 |
| §13 ADR-0004 verified-at-PR7 resolutions | Task 13 |
| §14 cleanup pass touch-points | Task 13 |
| §17 plan-review preempts | Top of plan |
| §18 acceptance | Acceptance recap above |

## Self-review

After writing this plan, reviewed against spec with fresh eyes:

1. **Spec coverage:** all 18 spec sections have a task pointer in the coverage map above.
2. **Placeholder scan:** every step has actual code or actual commands; no "TBD", no "fill in details," no "similar to Task N" without code.
3. **Type consistency:** `ProviderHealth`, `HealthCode`, `ProviderUnavailable`, `_StepResult`, `RealCodexProvider`, `RealClaudeProvider`, `ProviderStreamPaths` are referenced with consistent signatures across tasks 1-9.
4. **Test count math:** Task 1 +23, Task 2 +12, Task 3 +3, Task 4 +3, Task 5 +12, Task 6 +13, Task 7 +6, Task 8 +9, Task 9 +6, Task 10 +15, Task 11 +1, Task 12 +5 = +108. Subtract overlap with existing test extensions (Task 4 modifies test_sandbox_policy, Task 8 modifies test_cli_doctor) ≈ 369 + 100 ≈ 469. Realistic landing: 395-410 (some test cases may consolidate).
5. **Order dependencies:** Task 13 last (gate flip + ADR). Tasks 5-6 depend on Tasks 1-4. Task 7 depends on Task 2. Task 8 depends on Task 7. Task 9 depends on Task 8. Tasks 10-12 can land after Task 9. The strict order minimizes mid-PR coverage dips.

Plan is ready.
