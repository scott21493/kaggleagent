# tests/test_observability_trace_store.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena.observability.trace_store import TraceStore


def test_emit_writes_one_jsonl_line_per_event_for_a_task(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="task_started",
        severity="info",
        task_id="task_0001",
        payload={"message": "go"},
    )
    store.emit(
        event_type="task_finished",
        severity="info",
        task_id="task_0001",
        payload={"status": "success"},
    )

    log = tmp_path / "run_x" / "task_0001" / "events.jsonl"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["event_type"] == "task_started"
    assert second["event_type"] == "task_finished"
    # event_id is monotonically increasing
    assert first["event_id"] == "evt_0001"
    assert second["event_id"] == "evt_0002"


def test_emit_run_level_events_go_to_run_jsonl(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="run_started",
        severity="info",
        payload={"message": "go"},
    )

    log = tmp_path / "run_x" / "run.jsonl"
    assert log.exists()
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["event_type"] == "run_started"
    assert line["task_id"] is None


def test_emit_scrubs_payload_string_fields(tmp_path: Path) -> None:
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="provider_output_captured",
        severity="info",
        task_id="task_0001",
        payload={"message": "Authorization: Bearer abc123XYZ secret_value"},
    )
    log = tmp_path / "run_x" / "task_0001" / "events.jsonl"
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert "abc123XYZ" not in line["payload"]["message"]
    assert "<REDACTED_TOKEN>" in line["payload"]["message"]


def test_emit_validates_event_and_raises_on_bad_event_type(tmp_path: Path) -> None:
    from jsonschema import ValidationError

    store = TraceStore(run_id="run_x", root=tmp_path)
    with pytest.raises(ValidationError):
        store.emit(
            event_type="not_a_real_event",  # type: ignore[arg-type]
            severity="info",
            task_id="task_0001",
            payload={},
        )


def test_emit_id_counter_is_per_store_not_per_task(tmp_path: Path) -> None:
    """A single run's events share one monotonic counter even across tasks
    so replay can globally order them by id."""
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    store.emit(event_type="task_started", severity="info", task_id="task_0002", payload={})
    store.emit(
        event_type="task_finished",
        severity="info",
        task_id="task_0001",
        payload={"status": "success"},
    )

    t1 = (
        (tmp_path / "run_x" / "task_0001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    )
    t2 = (
        (tmp_path / "run_x" / "task_0002" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    )
    ids = sorted(json.loads(line)["event_id"] for line in t1 + t2)
    assert ids == ["evt_0001", "evt_0002", "evt_0003"]


def test_event_id_counter_resumes_from_existing_traces(tmp_path: Path) -> None:
    """Each run-next invocation constructs a fresh TraceStore(run_id=...).
    Without resume, evt_0001 collides across invocations and replay
    ordering breaks. Verify a second store picks up where the first left off."""
    first = TraceStore(run_id="run_x", root=tmp_path)
    first.emit(event_type="run_started", severity="info", payload={})
    first.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    first.emit(
        event_type="task_finished",
        severity="info",
        task_id="task_0001",
        payload={"status": "success"},
    )

    # Simulate a second run-next invocation: brand new TraceStore, same run_id.
    second = TraceStore(run_id="run_x", root=tmp_path)
    second.emit(event_type="task_started", severity="info", task_id="task_0002", payload={})

    log_t2 = (tmp_path / "run_x" / "task_0002" / "events.jsonl").read_text(encoding="utf-8")
    new_event = json.loads(log_t2.strip())
    # Counter resumed past evt_0003 (the highest from the first store).
    assert new_event["event_id"] == "evt_0004"


def test_event_id_counter_resume_handles_empty_run_dir(tmp_path: Path) -> None:
    """A run_id with no existing traces starts the counter at 0."""
    store = TraceStore(run_id="fresh_run", root=tmp_path)
    store.emit(event_type="run_started", severity="info", payload={})
    log = (tmp_path / "fresh_run" / "run.jsonl").read_text(encoding="utf-8")
    first = json.loads(log.strip())
    assert first["event_id"] == "evt_0001"
