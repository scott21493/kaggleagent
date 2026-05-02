# tests/test_observability_events.py
from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from jsonschema import ValidationError

from arena.observability.events import HarnessEvent, make_event, validate_event


def test_make_event_returns_valid_dict_for_run_started() -> None:
    evt = make_event(
        event_type="run_started",
        run_id="run_2026_05_02_001",
        event_id="evt_0001",
        severity="info",
        payload={"message": "run started", "phase": "NEW"},
    )
    # No raise = valid.
    validate_event(evt)
    assert evt["event_id"] == "evt_0001"
    assert evt["schema_version"] == "event.v1"
    assert evt["event_type"] == "run_started"


def test_make_event_iso_timestamp_in_utc() -> None:
    """Verify the timestamp is ISO-8601 UTC at seconds precision and that
    the schema validates it. The bare tzinfo-aware check would pass for
    `str(datetime.now(UTC))` (space separator, microseconds), which is
    NOT ISO-8601 — schema format validation catches that."""
    evt = make_event(
        event_type="task_started",
        run_id="run_x",
        event_id="evt_0001",
        severity="info",
        payload={},
        task_id="task_0001",
    )
    # Format check: YYYY-MM-DDTHH:MM:SS+00:00 (seconds precision, T separator,
    # explicit UTC offset).
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", evt["timestamp"]), (
        f"unexpected timestamp format: {evt['timestamp']!r}"
    )
    # tzinfo is set
    parsed = datetime.fromisoformat(evt["timestamp"])
    assert parsed.tzinfo is not None
    # Schema also accepts (the format: date-time constraint passes).
    validate_event(evt)


def test_validate_event_rejects_unknown_event_type() -> None:
    bogus = {
        "schema_version": "event.v1",
        "event_id": "evt_0001",
        "event_type": "fictional_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": "run_x",
        "severity": "info",
        "payload": {},
    }
    with pytest.raises(ValidationError):
        validate_event(bogus)


def test_validate_event_rejects_breaker_triggered_without_breaker() -> None:
    """The schema's allOf clause requires 'breaker' + 'evidence' for breaker_triggered."""
    incomplete = make_event(
        event_type="breaker_triggered",
        run_id="run_x",
        event_id="evt_0001",
        severity="error",
        payload={"message": "missing breaker field"},  # no 'breaker', no 'evidence'
    )
    with pytest.raises(ValidationError):
        validate_event(incomplete)


def test_validate_event_accepts_breaker_triggered_with_breaker_and_evidence() -> None:
    valid = make_event(
        event_type="breaker_triggered",
        run_id="run_x",
        event_id="evt_0001",
        severity="error",
        payload={
            "breaker": "SecretAccessBreaker",
            "evidence": ["~/.kaggle/kaggle.json"],
        },
    )
    validate_event(valid)


def test_validate_event_rejects_bad_event_id_pattern() -> None:
    bogus = make_event(
        event_type="run_started",
        run_id="run_x",
        event_id="not_an_evt_id",  # missing evt_NNNN pattern
        severity="info",
        payload={},
    )
    with pytest.raises(ValidationError):
        validate_event(bogus)


def test_make_event_optional_task_id_is_null_when_omitted() -> None:
    evt = make_event(
        event_type="run_started",
        run_id="run_x",
        event_id="evt_0001",
        severity="info",
        payload={},
    )
    assert evt["task_id"] is None


def test_harness_event_dataclass_to_dict_roundtrip() -> None:
    h = HarnessEvent(
        event_type="task_finished",
        run_id="run_x",
        event_id="evt_0042",
        severity="info",
        payload={"status": "completed"},
        task_id="task_0001",
    )
    as_dict = h.to_dict()
    validate_event(as_dict)
    assert as_dict["event_id"] == "evt_0042"
    assert as_dict["task_id"] == "task_0001"
