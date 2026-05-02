# arena/observability/events.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from arena.schemas.validate import validate as _validate_schema


@dataclass(frozen=True)
class HarnessEvent:
    """Structured event matching schemas/event.schema.json (event.v1).

    The schema enumerates 20 event_type values and a flat payload object
    keyed by optional fields (provider, breaker, evidence, score, etc.).
    Per-event-type required fields are enforced via the schema's allOf
    conditional (e.g., breaker_triggered MUST have breaker + evidence).
    """

    event_type: str
    run_id: str
    event_id: str  # evt_NNNN pattern enforced by schema
    severity: str  # debug/info/warning/error/critical
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    timestamp: str = ""  # ISO 8601 UTC, set on construction or to_dict

    def to_dict(self) -> dict[str, Any]:
        ts = self.timestamp or datetime.now(UTC).isoformat(timespec="seconds")
        return {
            "schema_version": "event.v1",
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": ts,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "severity": self.severity,
            "payload": dict(self.payload),
        }


def make_event(
    *,
    event_type: str,
    run_id: str,
    event_id: str,
    severity: str,
    payload: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Construct a fresh event dict ready for validation + emit.

    Sets timestamp to now-UTC. Caller controls event_id (TraceStore manages
    the evt_NNNN counter).
    """
    h = HarnessEvent(
        event_type=event_type,
        run_id=run_id,
        event_id=event_id,
        severity=severity,
        payload=payload or {},
        task_id=task_id,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    return h.to_dict()


def validate_event(event: dict[str, Any]) -> None:
    """Validate `event` against schemas/event.schema.json. Raises
    jsonschema.ValidationError on any failure (unknown event_type, missing
    required fields, conditional breaker_triggered violation, etc.)."""
    _validate_schema("event", event)
