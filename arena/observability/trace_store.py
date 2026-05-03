# arena/observability/trace_store.py
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arena.observability.events import make_event, validate_event
from arena.observability.scrubber import scrub_text


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


def _scrub_value(value: Any) -> Any:
    """Recursively scrub strings inside lists/dicts; pass through non-strings.

    Used by TraceStore._scrub_payload to ensure no secret-bearing string
    escapes into JSONL traces, regardless of nesting depth.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


class TraceStore:
    """Append-only JSONL event log per run.

    Layout under `root` (default "traces"):
        <run_id>/run.jsonl                       # run-level events (no task_id)
        <run_id>/<task_id>/events.jsonl          # per-task events

    Maintains a single monotonic `evt_NNNN` counter across the run so
    replay can globally order events. The counter RESUMES from existing
    trace files on construction — each `arena run-next` invocation builds
    a fresh TraceStore(run_id), so without resume evt_0001 would collide
    across tasks in the same run.

    Scrubs payload string fields via `scrub_text` before writing —
    providers may emit stdout that contains accidentally-captured tokens,
    and the trace is the durable record.

    Optional `set_on_event(callback)` registers a synchronous fan-out
    invoked after each successful emit. The callback receives the
    validated, scrubbed event dict. Used by Watchdog to drive WasteDetector
    from shell_command_observed events without coupling the trace store
    to budget logic. The callback is cleared after each invocation by
    convention — wrap_invoke does this in `finally`.
    """

    def __init__(self, run_id: str, root: str | Path = "traces") -> None:
        self._run_id = run_id
        self._root = Path(root) / run_id
        # Resume the monotonic evt_NNNN counter by scanning any existing
        # JSONL files. Without this, each new TraceStore(run_id) starts at
        # 0 and collides with the previous instance's evt_NNNN values
        # within the same run.
        self._counter = self._load_max_event_id()
        self._on_event: Callable[[dict[str, Any]], None] | None = None

    def _load_max_event_id(self) -> int:
        """Scan <root>/<run_id>/**/*.jsonl for the highest evt_NNNN id and
        return its integer suffix (or 0 if no traces exist yet)."""
        if not self._root.exists():
            return 0
        max_seen = 0
        for jsonl in self._root.rglob("*.jsonl"):
            try:
                for line in jsonl.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # Corrupt or partially-written line — skip and continue.
                        continue
                    eid = event.get("event_id", "")
                    if eid.startswith("evt_"):
                        try:
                            n = int(eid[len("evt_") :])
                        except ValueError:
                            continue
                        if n > max_seen:
                            max_seen = n
            except OSError:
                # Truncated or unreadable trace file — skip and continue.
                continue
        return max_seen

    def _next_event_id(self) -> str:
        self._counter += 1
        return f"evt_{self._counter:04d}"

    def _scrub_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply scrub_text to every string-valued payload field, recursively
        through lists and nested dicts.

        The event schema has `evidence: array of strings` (required by
        breaker_triggered) — without recursive scrubbing, secrets inside
        evidence arrays would land unscrubbed in the JSONL trace. Same
        risk applies to any future nested-dict payload field.
        """
        return {k: _scrub_value(v) for k, v in payload.items()}

    def _log_path(self, task_id: str | None) -> Path:
        if task_id is None:
            return self._root / "run.jsonl"
        return self._root / task_id / "events.jsonl"

    def set_on_event(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """Register a synchronous callback invoked after each successful emit.

        The callback receives the validated, scrubbed event dict. Used by
        Watchdog to drive WasteDetector from shell_command_observed events
        without coupling the trace store to budget logic. Pass None to
        clear (wrap_invoke does this in finally).
        """
        self._on_event = callback

    def emit(
        self,
        *,
        event_type: str,
        severity: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate, scrub, and append one event. Returns the event dict
        (post-scrub) so the caller can include it in error messages.

        On ValidationError the counter rolls back so subsequent emits
        produce contiguous evt_NNNN ids — no gaps in the trace.

        After a successful append, fans out to the optional on_event
        callback (registered via set_on_event). The callback is invoked
        synchronously and must not mutate the event dict.

        If the callback raises, the exception PROPAGATES out of emit().
        The JSONL line is already durable at this point — the trace
        records the event even when the callback fails. The Watchdog's
        live-waste-detector uses this contract: the BudgetExceeded
        raised by the WasteDetector callback unwinds through
        adapter.invoke and the surrounding try/except.
        """
        from jsonschema import ValidationError

        event = make_event(
            event_type=event_type,
            run_id=self._run_id,
            event_id=self._next_event_id(),
            severity=severity,
            payload=self._scrub_payload(payload),
            task_id=task_id,
        )
        try:
            validate_event(event)
        except ValidationError:
            # Roll back the counter so the failed event_id is reusable —
            # otherwise replay sees gaps and downstream tooling has to
            # accommodate non-contiguous ids.
            self._counter -= 1
            raise
        log_path = self._log_path(task_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        # Fan out AFTER the JSONL line is appended — the event is
        # durable when the callback runs.
        if self._on_event is not None:
            self._on_event(event)
        return event

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
