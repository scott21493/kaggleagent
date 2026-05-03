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
