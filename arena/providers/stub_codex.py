from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_codex.v1"


class StubCodexProvider(ProviderAdapter):
    """Deterministic stand-in for Codex during Phase 0 CI and local stub runs.

    For role=implementation calibration tasks, emits a submission.csv with
    constant 0.5 target predictions for every row in test.csv. The score
    against hidden_labels will be ~0.5 (random); the goal is to prove the
    pipeline, not to win the fixture.

    Path assumption: invoke() reads `fixtures/<slug>/test.csv` relative to
    the current working directory. The Phase 0 CLI invokes from repo root
    (see Task 10's `arena init-fixture`), so this is consistent with the
    rest of the harness. If a future caller needs to invoke from elsewhere,
    add a `fixture_root` constructor argument and thread it through the
    `inputs` resolution.

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
        return "stub_codex"

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
        task_id = task_packet["task_id"]
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubCodexProvider requires task_packet.experiment_id to be set")

        started = datetime.now(UTC).isoformat(timespec="seconds")
        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)

        test_path = Path("fixtures") / slug / "test.csv"
        test_df = pd.read_csv(test_path)
        submission = pd.DataFrame({"id": test_df["id"], "target": 0.5})
        submission_path = workspace / "submission.csv"
        submission.to_csv(submission_path, index=False)

        finished = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(workspace / "stdout.scrubbed"),
            stderr_path=str(workspace / "stderr.scrubbed"),
            artifacts=[str(submission_path)],
            input_chars=0,
            output_chars=submission_path.stat().st_size,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=started,
            finished_at=finished,
        )
