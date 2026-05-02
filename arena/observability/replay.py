# arena/observability/replay.py
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskSummary:
    task_id: str
    first_event_id: str
    status: str | None = None
    score: float | None = None
    metric_name: str | None = None
    provider: str | None = None
    provider_version: str | None = None
    breakers: list[str] = field(default_factory=list)


@dataclass
class RunReplayView:
    run_id: str
    fixture_manifest_hash: str | None
    tasks: list[TaskSummary]
    breaker_counts: dict[str, int]


def _event_id_numeric(evt: dict) -> int:
    """Extract the integer suffix from an evt_NNNN id for numeric sort.
    Lexicographic sort would put evt_10000 before evt_9999."""
    eid = evt.get("event_id", "")
    if eid.startswith("evt_"):
        try:
            return int(eid[len("evt_") :])
        except ValueError:
            return 0
    return 0


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def replay_run(*, run_id: str, root: str | Path = "traces") -> RunReplayView:
    """Reconstruct a deterministic RunReplayView from `<root>/<run_id>/**/*.jsonl`.

    Reads the run-level run.jsonl plus every per-task events.jsonl, replays
    them globally ordered by event_id, and accumulates per-task summaries
    (status, score, breakers) plus run-level fixture_manifest_hash.

    Tasks appear in order of their first event_id (so a task that started
    first is listed first).

    Phase 0 assumptions (relax in PR7+ if either becomes a real constraint):
    - In-memory accumulation: all events are loaded into a single list
      before replay. Fine for fixture runs (~10s of events); may need
      streaming for long real-provider runs.
    - Single-writer: this function assumes one TraceStore lineage wrote
      the events. Concurrent TraceStore(run_id=same_id) instances would
      produce overlapping evt_NNNN values that this replay would silently
      reorder by id (correct deterministically, but not what the writers
      intended).
    """
    run_root = Path(root) / run_id
    if not run_root.exists():
        raise FileNotFoundError(f"no traces for run {run_id} under {root}")

    all_events: list[dict] = []
    all_events.extend(_read_jsonl(run_root / "run.jsonl"))
    for task_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
        all_events.extend(_read_jsonl(task_dir / "events.jsonl"))

    all_events.sort(key=_event_id_numeric)

    fixture_hash: str | None = None
    summaries: dict[str, TaskSummary] = {}
    breaker_counter: Counter[str] = Counter()

    for evt in all_events:
        et = evt["event_type"]
        payload = evt.get("payload", {})
        task_id = evt.get("task_id")

        if et == "run_started":
            # Last-non-empty-wins semantics: if multiple run_started events
            # appear (unlikely in Phase 0; one per run), the later non-empty
            # sha256 takes precedence. Empty payloads don't overwrite.
            fixture_hash = payload.get("sha256") or fixture_hash
            continue

        if task_id is None:
            continue

        summary = summaries.setdefault(
            task_id, TaskSummary(task_id=task_id, first_event_id=evt["event_id"])
        )
        if et == "provider_invoked":
            summary.provider = payload.get("provider")
            summary.provider_version = payload.get("provider_version")
        elif et == "task_finished":
            summary.status = payload.get("status")
        elif et == "score_recorded":
            summary.score = payload.get("score")
            summary.metric_name = payload.get("metric_name")
        elif et == "breaker_triggered":
            breaker = payload.get("breaker")
            if breaker:
                summary.breakers.append(breaker)
                breaker_counter[breaker] += 1

    tasks = sorted(
        summaries.values(),
        key=lambda s: _event_id_numeric({"event_id": s.first_event_id}),
    )
    return RunReplayView(
        run_id=run_id,
        fixture_manifest_hash=fixture_hash,
        tasks=tasks,
        breaker_counts=dict(breaker_counter),
    )
