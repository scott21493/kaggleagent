from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena.controller.state import ALLOWED_TRANSITIONS, Phase, transition


def test_initial_phase_is_new() -> None:
    assert Phase.NEW.value == "NEW"


def test_allowed_transition_from_new_to_fixture_initialized() -> None:
    transition(Phase.NEW, Phase.FIXTURE_INITIALIZED)


def test_disallowed_transition_raises() -> None:
    with pytest.raises(ValueError):
        transition(Phase.NEW, Phase.PHASE0_COMPLETE)


def test_blocked_phases_are_terminal_dead_ends() -> None:
    """Every BLOCKED_* phase has exactly one outgoing edge: to NEEDS_HUMAN."""
    blocked = {p for p in Phase if p.name.startswith("BLOCKED_")}
    assert len(blocked) == 9
    for b in blocked:
        assert ALLOWED_TRANSITIONS[b] == {Phase.NEEDS_HUMAN}, (
            f"{b.value} should reach only NEEDS_HUMAN, got {ALLOWED_TRANSITIONS[b]}"
        )


def test_phase_enum_matches_task_packet_schema() -> None:
    """The Phase enum must mirror the phase enum in task_packet.schema.json
    exactly. Drift here silently breaks downstream packet validation
    across Tasks 4, 5, 7, and 10-13."""
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "task_packet.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_phases = set(schema["properties"]["phase"]["enum"])
    enum_values = {p.value for p in Phase}
    assert enum_values == schema_phases, (
        f"drift: in enum but not schema: {enum_values - schema_phases}; "
        f"in schema but not enum: {schema_phases - enum_values}"
    )
