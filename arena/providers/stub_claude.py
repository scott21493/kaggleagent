from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_claude.v1"


class StubClaudeProvider(ProviderAdapter):
    """Deterministic stand-in for Claude during Phase 0 CI and local stub runs.

    PR1 lands the skeleton only — invoke() validates the packet, writes empty
    scrubbed stdout/stderr trace files into the workspace, and returns a
    schema-valid ProviderResult with no artifacts. PR5 extends invoke() to
    emit paper_digest.json / fusion_proposal.json; PR6 extends it for
    review.json.

    Optional fields exercise observability: failed_commands is a list of
    (command_str, exit_code) pairs that the stub emits as
    shell_command_observed events through `event_emitter` before producing
    its normal result. Enables PR4's live waste-detector path tests
    (security acceptance test 5: 4 identical failed commands → REPEATED_FAILURE
    because Phase0HardCeilings.repeated_same_failure_per_task = 2 with
    strict `>` check).
    """

    def __init__(
        self,
        workspace_root: str | Path = "worktrees",
        *,
        event_emitter: TraceStore | None = None,
        failed_commands: list[tuple[str, int]] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._event_emitter = event_emitter
        self._failed_commands = failed_commands or []

    @property
    def name(self) -> str:
        return "stub_claude"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
        # PR4 live waste path: emit shell_command_observed events for any
        # injected failed_commands. These are picked up by the watchdog's
        # WasteDetector observer (PR4 Task 6).
        if self._event_emitter is not None:
            for command, exit_code in self._failed_commands:
                self._event_emitter.emit(
                    event_type="shell_command_observed",
                    severity="info" if exit_code == 0 else "warning",
                    task_id=task_packet["task_id"],
                    payload={"command": command, "exit_code": exit_code},
                )
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubClaudeProvider requires task_packet.experiment_id to be set")
        task_id = task_packet["task_id"]

        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)
        stdout_path = workspace / f"{task_id}.stub_claude.stdout.scrubbed"
        stderr_path = workspace / f"{task_id}.stub_claude.stderr.scrubbed"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")

        now = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            artifacts=[],
            input_chars=0,
            output_chars=0,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=now,
            finished_at=now,
        )
