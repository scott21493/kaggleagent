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
    ProviderStatus,
    ProviderUnavailable,
    UsageProxy,
    resolve_provider_executable,
)
from arena.schemas.validate import validate as validate_schema

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
        # Validate BEFORE any side effect (mkdir, file write, subprocess).
        # Per ProviderAdapter.invoke contract: validate the incoming
        # packet. Mirrors stub_codex/stub_claude (double-validation is
        # cheap; CLI also pre-validates).
        validate_schema("task_packet", task_packet)
        task_id = task_packet["task_id"]
        # Per-packet workspace resolution. The active workspace is the
        # packet's first allowed_paths entry (the per-experiment
        # worktree per controller convention since PR1). Falling back
        # to self._cwd preserves the test-only path where _packet()
        # has empty allowed_paths and tests pin the adapter's _cwd to
        # tmp_path. Production callers (arena run-next + research-proxy)
        # always populate allowed_paths, so production never falls
        # through to self._cwd. This guarantees:
        #   - prompt files live under <packet_workspace>/.arena_prompts/
        #   - subprocess cwd == packet workspace (so codex --workspace-
        #     write resolves correctly relative to the per-experiment
        #     dir, not the repo root)
        #   - PR3's packet-scoped write boundary is honoured
        #
        # Relative allowed_paths entries (the normal case — packets
        # carry "worktrees/<slug>/<exp_id>/") are resolved against
        # self._cwd, NOT the process CWD. The DI contract says self._cwd
        # is the adapter's repo-root anchor and is overridable for
        # tests; resolving against the process CWD would silently
        # ignore that override. Absolute paths are honoured as-is.
        if task_packet.get("allowed_paths"):
            allowed = Path(task_packet["allowed_paths"][0])
            if allowed.is_absolute():
                workspace = allowed.resolve()
            else:
                workspace = (self._cwd / allowed).resolve()
        else:
            workspace = self._cwd
        workspace.mkdir(parents=True, exist_ok=True)
        prompt_dir = workspace / ".arena_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"prompt_{task_id}.json"
        prompt_json = json.dumps(task_packet, ensure_ascii=False)
        prompt_file.write_text(prompt_json, encoding="utf-8")

        # Resolve self._executable to a runnable absolute path with
        # Windows PATHEXT awareness. Without this, "codex" can match
        # an extensionless npm shim that subprocess.run cannot start
        # on Windows (PermissionError [WinError 5]). None →
        # ProviderUnavailable(code="not_found") before subprocess.run.
        resolved_exe = resolve_provider_executable(self._executable)
        if resolved_exe is None:
            raise ProviderUnavailable(
                provider="codex",
                code="not_found",
                detail=f"{self._executable} not on PATH",
                runbook="docs/phase0/runbooks/cli_regression.md",
            )

        argv = [
            resolved_exe,
            "exec",
            "--json",
            "--workspace-write",
            str(workspace),
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
                cwd=str(workspace),
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except OSError as e:
            # Defense-in-depth for the resolve_provider_executable
            # gate above: shutil.which said the path is on PATH but
            # subprocess.run still can't start the process. Covers
            # FileNotFoundError + PermissionError (Windows shim
            # variants, network drives, sandbox EPERM) + other
            # process-start failures uniformly.
            raise ProviderUnavailable(
                provider="codex",
                code="not_found",
                detail=f"{self._executable} not executable: {type(e).__name__}: {e}",
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
        status: ProviderStatus
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
            # Surface NDJSON drift even on the success path. If the
            # terminal event was valid but earlier lines were malformed,
            # the operator needs a signal to update the parser
            # (mirrors the auth-pattern maintenance loop in
            # docs/phase0/runbooks/auth_expiry.md).
            n_malformed = int(parsed.get("_n_malformed_lines", 0))
            if n_malformed > 0:
                artifacts.append(f"<warn:n_malformed_ndjson_lines:{n_malformed}>")
        elif exit_code >= 64:
            status = "blocked"
            artifacts.extend(
                [
                    "<blocked:AuthFailureBreaker>",
                    f"<runbook:{_RUNBOOK_AUTH}>",
                ]
            )
        elif exit_code == 2:
            status = "blocked"
        elif exit_code == 1 and matches_auth_expiry(scrubbed_stderr):
            status = "blocked"
            artifacts.extend(
                [
                    "<blocked:AuthFailureBreaker>",
                    f"<runbook:{_RUNBOOK_AUTH}>",
                ]
            )
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
    question #3, resolved at PR7.

    Malformed JSONL lines are skipped but COUNTED. If any are dropped,
    the count is surfaced to the caller via `_n_malformed_lines` so the
    adapter can append a `<warn:n_malformed_ndjson_lines:N>` artifact
    token. Without this, parser drift in real codex output (e.g., a
    future CLI version emitting partial JSON on stderr-mixed paths)
    would silently classify down with no operator signal."""
    events: list[dict] = []
    n_malformed = 0
    for line in scrubbed_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            n_malformed += 1
            continue
    if not events:
        return {"_missing_terminal_event": True, "_n_malformed_lines": n_malformed}
    terminal = events[-1]
    if "artifacts" not in terminal and "usage" not in terminal:
        return {"_missing_terminal_event": True, "_n_malformed_lines": n_malformed}
    return {
        "artifacts": terminal.get("artifacts", []),
        "usage": terminal.get("usage", {}),
        "_n_malformed_lines": n_malformed,
    }
