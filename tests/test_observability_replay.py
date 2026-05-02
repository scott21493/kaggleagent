# tests/test_observability_replay.py
from __future__ import annotations

from pathlib import Path

import pytest

from arena.observability.replay import replay_run
from arena.observability.trace_store import TraceStore


def _seed(tmp_path: Path, run_id: str = "run_x") -> TraceStore:
    """Emit a known sequence of events for `run_id`."""
    store = TraceStore(run_id=run_id, root=tmp_path)
    store.emit(
        event_type="run_started",
        severity="info",
        payload={"sha256": "abcdef", "phase": "NEW"},
    )
    store.emit(
        event_type="task_started",
        severity="info",
        task_id="task_0001",
        payload={"phase": "CALIBRATION_TASK_CREATED"},
    )
    store.emit(
        event_type="provider_invoked",
        severity="info",
        task_id="task_0001",
        payload={"provider": "stub_codex", "provider_version": "stub_codex.v1"},
    )
    store.emit(
        event_type="task_finished",
        severity="info",
        task_id="task_0001",
        payload={"status": "success", "provider": "stub_codex"},
    )
    store.emit(
        event_type="score_recorded",
        severity="info",
        task_id="task_0001",
        payload={"score": 0.5, "metric_name": "accuracy"},
    )
    return store


def test_replay_returns_one_task_summary_with_score(tmp_path: Path) -> None:
    _seed(tmp_path, run_id="run_x")
    view = replay_run(run_id="run_x", root=tmp_path)
    assert view.run_id == "run_x"
    assert len(view.tasks) == 1
    task = view.tasks[0]
    assert task.task_id == "task_0001"
    assert task.status == "success"
    assert task.score == 0.5
    assert task.metric_name == "accuracy"
    assert task.provider == "stub_codex"
    assert task.provider_version == "stub_codex.v1"


def test_replay_orders_tasks_by_first_event_id(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="run_started",
        severity="info",
        payload={},
    )
    store.emit(
        event_type="task_started",
        severity="info",
        task_id="task_0002",
        payload={},
    )
    store.emit(
        event_type="task_started",
        severity="info",
        task_id="task_0001",
        payload={},
    )
    view = replay_run(run_id="run_x", root=tmp_path)
    assert [t.task_id for t in view.tasks] == ["task_0002", "task_0001"]


def test_replay_counts_breaker_events(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(event_type="run_started", severity="info", payload={})
    store.emit(
        event_type="breaker_triggered",
        severity="error",
        task_id="task_0001",
        payload={"breaker": "SecretAccessBreaker", "evidence": ["~/.kaggle/kaggle.json"]},
    )
    store.emit(
        event_type="breaker_triggered",
        severity="error",
        task_id="task_0002",
        payload={"breaker": "ProtectedFileBreaker", "evidence": ["/etc/passwd"]},
    )
    view = replay_run(run_id="run_x", root=tmp_path)
    assert view.breaker_counts == {"SecretAccessBreaker": 1, "ProtectedFileBreaker": 1}


def test_replay_run_started_carries_fixture_hash(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(event_type="run_started", severity="info", payload={"sha256": "deadbeef"})
    view = replay_run(run_id="run_x", root=tmp_path)
    assert view.fixture_manifest_hash == "deadbeef"


def test_replay_raises_on_missing_run(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        replay_run(run_id="nonexistent", root=tmp_path)
