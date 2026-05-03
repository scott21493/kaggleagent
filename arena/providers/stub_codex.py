# arena/providers/stub_codex.py
from __future__ import annotations

import json
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

    For role=implementation + phase=FUSION_PROXY_IMPLEMENTED (PR5), reads
    the fusion_id from the inputs[0] (a path ending in fusion_proposal.json)
    and appends a <fusion_id:{fusion_id}> token to the ProviderResult.artifacts
    list.

    The token is the link between the scoreboard row and the originating
    fusion proposal. The CLI in Task 5 surfaces it through artifact_paths
    so `arena replay` can reconstruct the chain.

    The submission.csv shape is identical to calibration (constant 0.5);
    PR7 with real Codex will produce non-trivial implementations grounded
    in the fusion proposal. Backward compat: calibration packets (phase=
    CALIBRATION_TASK_CREATED) continue to emit only submission.csv.

    Path assumption: invoke() reads `fixtures/<slug>/test.csv` relative to
    the current working directory. The Phase 0 CLI invokes from repo root,
    so this is consistent with the rest of the harness.

    Optional fields exercise observability: failed_commands is a list of
    (command_str, exit_code) pairs that the stub emits as
    shell_command_observed events through `event_emitter` before producing
    its normal result.
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

        artifacts: list[str] = [str(submission_path)]
        # PR5: link the proxy submission back to its fusion_id so the
        # scoreboard row carries the connection.
        if task_packet["phase"] == "FUSION_PROXY_IMPLEMENTED":
            fusion_id = self._read_fusion_id_from_inputs(task_packet["inputs"])
            if fusion_id is not None:
                artifacts.append(f"<fusion_id:{fusion_id}>")

        finished = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(workspace / "stdout.scrubbed"),
            stderr_path=str(workspace / "stderr.scrubbed"),
            artifacts=artifacts,
            input_chars=0,
            output_chars=submission_path.stat().st_size,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=started,
            finished_at=finished,
        )

    def _read_fusion_id_from_inputs(self, inputs: list[str]) -> str | None:
        """Find the first input ending in `fusion_proposal.json` and read
        its `fusion_id` field. Returns None if no such input exists or
        the file is missing/malformed (the caller treats absence as
        skipping the token; missing fusion_id on a FUSION_PROXY_IMPLEMENTED
        packet is a programming error caught upstream by the CLI)."""
        for input_path in inputs:
            if not input_path.endswith("fusion_proposal.json"):
                continue
            p = Path(input_path)
            if not p.exists():
                return None
            try:
                payload: dict[str, object] = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            # json.loads returns Any; narrow explicitly so mypy's
            # warn_return_any does not fire, and so a non-string fusion_id
            # (malformed JSON, schema regression) cannot leak into
            # artifact_paths as a token like "<fusion_id:None>" or
            # "<fusion_id:42>".
            fusion_id = payload.get("fusion_id")
            return fusion_id if isinstance(fusion_id, str) else None
        return None
