# arena/providers/claude.py
"""Real Claude adapter — subprocess wrapper for `claude -p`.

Per ADR-0004 invocation conventions. Differs from codex.py in:
  (a) argv shape: ``-p --input <prompt_file> --workspace <ws>``
  (b) stdout is a SINGLE JSON object (not NDJSON)
  (c) parser dispatches on (role, phase) to the role+phase-appropriate
      output schema (research_review, paper_digest, etc.)

DI surface mirrors codex.py: executable, env, cwd, timeout_seconds,
event_emitter. event_emitter is REQUIRED at invoke() time even though
the constructor allows None (matches ABC signature shape).

RAW PATH SECURITY BOUNDARY: stdout.raw and stderr.raw are persisted
under traces/<run_id>/<task_id>/ for forensic recovery only. They are
NEVER:
- included in ProviderResult.artifacts
- passed back into any provider's context window
- emitted to the trace event stream
- rendered in `arena report` output
- readable by sandbox-policy-enforced subprocesses
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

from jsonschema import ValidationError

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
from arena.schemas.validate import validate as validate_schema

_RUNBOOK_AUTH = "docs/phase0/runbooks/auth_expiry.md"

# Role+phase → output schema dispatch table. Claude is an advisory
# provider: each (role, phase) tuple corresponds to exactly one expected
# output shape. Unmapped tuples surface as <failure:schema_violation>
# (the wrapper has no contract to validate against).
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
        role = task_packet.get("role", "")
        phase = task_packet.get("phase", "")

        # Per-packet workspace resolution. The packet's first allowed_paths
        # entry is the per-experiment worktree per controller convention
        # since PR1. Falling back to self._cwd preserves the test-only
        # path where _packet() has empty allowed_paths. Production callers
        # always populate allowed_paths, so:
        #   - the advisory artifact lands at <packet_workspace>/<schema>.json
        #     (avoids overwriting a shared root-level file across runs;
        #     matches stub_claude's worktrees/<slug>/<exp_id>/<schema>.json
        #     convention)
        #   - subprocess cwd is the packet workspace (so claude --workspace
        #     resolves correctly)
        if task_packet.get("allowed_paths"):
            workspace = Path(task_packet["allowed_paths"][0]).resolve()
        else:
            workspace = self._cwd
        workspace.mkdir(parents=True, exist_ok=True)
        prompt_dir = workspace / ".arena_prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"prompt_{task_id}.json"
        prompt_json = json.dumps(task_packet, ensure_ascii=False)
        prompt_file.write_text(prompt_json, encoding="utf-8")

        argv = [
            self._executable,
            "-p",
            "--input",
            str(prompt_file),
            "--workspace",
            str(workspace),
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
        except FileNotFoundError as e:
            raise ProviderUnavailable(
                provider="claude",
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
        status: ProviderStatus
        if timed_out:
            status = "killed"
            artifacts.append("<killed:wall_clock_timeout>")
        elif exit_code == 0:
            parse_outcome = _parse_claude_response(scrubbed_stdout, role=role, phase=phase)
            if parse_outcome["status"] == "success":
                status = "success"
                # Materialise the validated JSON so downstream CLI
                # consumers can find it via _require_artifact(suffix=
                # "<schema>.json"). Claude is advisory: the CLI invokes
                # the subprocess but the adapter must persist the
                # advisory artifact (codex executes shell and writes
                # files itself; Claude returns content). Path mirrors
                # stub_claude: <cwd>/<schema_name>.json.
                schema_name = parse_outcome["schema_name"]
                payload = parse_outcome["payload"]
                artifact_path = workspace / f"{schema_name}.json"
                artifact_path.write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8",
                )
                artifacts.append(str(artifact_path))
            else:
                status = "failure"
                artifacts.append(f"<failure:{parse_outcome['reason']}>")
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
        # fields here are EXACTLY the schema's required set. Claude is
        # an advisory provider and does NOT execute shell commands, so
        # shell_commands / failed_commands / waste_events are always 0.
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

    On success, returns ``{"status": "success", "schema_name": <name>,
    "payload": <parsed-dict>}`` so the adapter can persist the
    validated JSON to ``<cwd>/<schema_name>.json`` (matching the
    stub_claude convention) and append the path to
    ``ProviderResult.artifacts``. Without this bridging, downstream CLI
    consumers (``arena research-proxy``, ``arena review``) that look up
    artifacts via ``_require_artifact(suffix="research_review.json")``
    would fail with "did not emit artifact" even on a valid Claude
    invocation — Claude is advisory and emits content, not files; the
    adapter is responsible for materialising the file.

    On failure, returns ``{"status": "failure", "reason":
    "json_decode_error" | "schema_violation"}``.

    Unlike codex's NDJSON parser (which counts malformed lines for a
    drift signal), Claude's parser is single-JSON: a parse failure IS
    the failure — there's no per-line drift signal to surface.
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
    return {"status": "success", "schema_name": schema_name, "payload": payload}
