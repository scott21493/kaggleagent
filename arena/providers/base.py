from __future__ import annotations

import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypedDict


def resolve_provider_executable(name: str) -> str | None:
    """Find a runnable provider executable on PATH, Windows-aware.

    On Windows, ``shutil.which("codex")`` can return an extensionless
    npm shim that ``subprocess.run([resolved, ...])`` cannot directly
    execute — it raises ``PermissionError: [WinError 5] Access is
    denied``. This helper tries ``.cmd``, ``.bat``, and ``.exe``
    extensions first on Windows, falling back to the bare name only
    if none match. POSIX is unaffected (extensionless executables are
    runnable directly).

    Absolute paths (e.g., ``str(shim_codex_executable)`` from the
    conftest test fixture) bypass the extension search and are
    returned as-is if the file exists and is executable; this keeps
    test overrides idempotent.

    Returns the resolved absolute path on success, or ``None`` if no
    runnable variant exists on PATH. Callers should also catch
    ``OSError`` from ``subprocess.run`` as defense-in-depth: a path
    that satisfies ``shutil.which`` may still fail at process start
    on tight permission setups, network drives, etc.
    """
    if Path(name).is_absolute():
        # Operator/test override — use as-is (shutil.which handles the
        # exists+executable check and returns None otherwise).
        return shutil.which(name)
    if sys.platform == "win32":
        for candidate in (f"{name}.cmd", f"{name}.bat", f"{name}.exe", name):
            resolved = shutil.which(candidate)
            if resolved is not None:
                return resolved
        return None
    return shutil.which(name)


ProviderStatus = Literal["success", "failure", "blocked", "killed", "interrupted"]


class UsageProxy(TypedDict):
    """Six required deterministic usage counters per provider invocation.

    Values are not billing estimates; they are operational guardrail
    metrics the budget governor (PR2) compares against task ceilings.
    """

    input_chars: int
    output_chars: int
    wall_seconds: float
    shell_commands: int
    failed_commands: int
    waste_events: int


@dataclass(frozen=True)
class ProviderResult:
    """Structured outcome of one provider invocation.

    Mirrors provider_result.schema.json. The to_dict() method emits the
    schema-valid JSON shape (with schema_version filled in).

    Mutability note: the dataclass is frozen, but `usage_proxy` (TypedDict
    underneath = dict) and `artifacts` (list) are mutable in place. Callers
    must treat ProviderResult instances as immutable once constructed and
    never mutate the contained collections; downstream consumers cache and
    serialize them, and in-place mutation can cause spooky-action-at-a-
    distance bugs across consumers.
    """

    task_id: str
    provider: str
    provider_version: str
    status: ProviderStatus
    stdout_path: str
    stderr_path: str
    artifacts: list[str]
    usage_proxy: UsageProxy
    started_at: str
    finished_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        return {"schema_version": "provider_result.v1", **payload}


class ProviderAdapter(ABC):
    """Abstract base class for provider workers (stub or real).

    See docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md for the
    real-provider subprocess conventions; stubs do not subprocess.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def invoke(self, task_packet: dict) -> ProviderResult:
        """Run the task and return a ProviderResult.

        Implementations must:
        - validate the incoming packet against task_packet.schema.json
          (callers may also pre-validate; double-validation is cheap)
        - write any required outputs into the workspace
        - return a ProviderResult whose to_dict() satisfies provider_result.schema.json
        """


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
