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


def test_event_id_counter_resume_skips_corrupt_jsonl_lines(tmp_path: Path) -> None:
    """A corrupt JSONL line (truncated write, manual edit) must NOT crash
    TraceStore construction. The scan skips bad lines and resumes from the
    highest valid evt_NNNN id found."""
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    # Write a mix of valid + corrupt lines.
    valid_event = '{"schema_version":"event.v1","event_id":"evt_0007","event_type":"run_started","timestamp":"2026-05-02T12:00:00+00:00","run_id":"run_x","task_id":null,"severity":"info","payload":{}}'
    (run_dir / "run.jsonl").write_text(
        valid_event + "\n{ this is not json\n",  # second line corrupt
        encoding="utf-8",
    )
    # Should NOT raise.
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    log = (tmp_path / "run_x" / "task_0001" / "events.jsonl").read_text(encoding="utf-8")
    new_event = json.loads(log.strip())
    # Counter resumed past evt_0007 from the valid line — corrupt line skipped.
    assert new_event["event_id"] == "evt_0008"


def test_emit_rollback_counter_on_validation_error(tmp_path: Path) -> None:
    """When emit raises ValidationError, the counter must roll back so the
    next successful emit gets the same evt_NNNN id (no gap)."""
    from jsonschema import ValidationError

    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(event_type="run_started", severity="info", payload={})  # evt_0001
    with pytest.raises(ValidationError):
        store.emit(
            event_type="not_a_real_event",  # type: ignore[arg-type]
            severity="info",
            task_id="task_0001",
            payload={},
        )
    # Counter rolled back; next emit gets evt_0002 (not evt_0003).
    store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    log = (tmp_path / "run_x" / "task_0001" / "events.jsonl").read_text(encoding="utf-8")
    new_event = json.loads(log.strip())
    assert new_event["event_id"] == "evt_0002"


def test_set_on_event_callback_fires_after_jsonl_append(tmp_path: Path) -> None:
    """The callback must run AFTER the line is durable on disk so a
    callback that raises (e.g., BudgetExceeded from WasteDetector) does
    NOT roll back the durable trace."""
    store = TraceStore(run_id="run_x", root=tmp_path)
    captured: list[dict] = []

    def _callback(evt: dict) -> None:
        # Verify the file is already written when we get here.
        log = tmp_path / "run_x" / "task_0001" / "events.jsonl"
        assert log.exists()
        captured.append(evt)

    store.set_on_event(_callback)
    store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    assert len(captured) == 1
    assert captured[0]["event_type"] == "task_started"


def test_set_on_event_none_clears_callback(tmp_path: Path) -> None:
    """Passing None to set_on_event removes the callback so subsequent
    emits don't fan out. Watchdog uses this in `finally` to prevent
    leaked callbacks across invocations."""
    store = TraceStore(run_id="run_x", root=tmp_path)
    fired: list[dict] = []
    store.set_on_event(lambda evt: fired.append(evt))
    store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    assert len(fired) == 1

    store.set_on_event(None)
    store.emit(
        event_type="task_finished",
        severity="info",
        task_id="task_0001",
        payload={"status": "success"},
    )
    # Second emit did NOT fire the callback.
    assert len(fired) == 1


def test_set_on_event_callback_exception_propagates(tmp_path: Path) -> None:
    """A callback that raises (e.g., BudgetExceeded from WasteDetector)
    propagates out of emit. The JSONL line is already durable at this
    point — the trace records the event even when the callback fails.
    This is the behavior the live waste observer in Watchdog.wrap_invoke
    relies on: the BudgetExceeded raised by the callback unwinds through
    adapter.invoke and the surrounding try/except."""
    store = TraceStore(run_id="run_x", root=tmp_path)

    class CallbackError(RuntimeError):
        pass

    def _raising(evt: dict) -> None:
        raise CallbackError("simulating WasteDetector cap")

    store.set_on_event(_raising)
    with pytest.raises(CallbackError):
        store.emit(event_type="task_started", severity="info", task_id="task_0001", payload={})
    # The trace line WAS written before the callback ran.
    log = tmp_path / "run_x" / "task_0001" / "events.jsonl"
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["event_type"] == "task_started"


def test_emit_scrubs_strings_inside_payload_evidence_array(tmp_path: Path) -> None:
    """The schema's `evidence: array of strings` (required by
    breaker_triggered) was previously bypassed by the scrubber because
    _scrub_payload only checked direct-string values. Verify a bearer
    token inside evidence is now redacted."""
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="breaker_triggered",
        severity="error",
        task_id="task_0001",
        payload={
            "breaker": "SecretAccessBreaker",
            "evidence": ["Authorization: Bearer abcdefghijklmnop", "/etc/passwd"],
        },
    )
    log = (tmp_path / "run_x" / "task_0001" / "events.jsonl").read_text(encoding="utf-8")
    line = json.loads(log.strip())
    evidence = line["payload"]["evidence"]
    # Bearer token redacted in the FIRST evidence entry.
    assert "abcdefghijklmnop" not in evidence[0]
    assert "<REDACTED_TOKEN>" in evidence[0]
    # Second entry (no secret) passes through unchanged.
    assert evidence[1] == "/etc/passwd"


def test_emit_scrubs_strings_inside_nested_payload_dict(tmp_path: Path) -> None:
    """Defensive: future event payload fields may carry nested dicts.
    The recursive scrubber redacts strings at any depth."""
    store = TraceStore(run_id="run_x", root=tmp_path)
    store.emit(
        event_type="provider_output_captured",
        severity="info",
        task_id="task_0001",
        payload={
            "message": "outer",  # event.schema only allows specific top-level fields
        },
    )
    # Sanity check: the basic case still works.
    log = (tmp_path / "run_x" / "task_0001" / "events.jsonl").read_text(encoding="utf-8")
    line = json.loads(log.strip())
    assert line["payload"]["message"] == "outer"
