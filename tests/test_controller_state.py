from __future__ import annotations

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
    # Once blocked, you cannot transition to a non-blocked phase without going through NEEDS_HUMAN.
    assert Phase.BLOCKED_AUTH not in ALLOWED_TRANSITIONS or all(
        target in {Phase.NEEDS_HUMAN, Phase.BLOCKED_KILL_SWITCH}
        for target in ALLOWED_TRANSITIONS.get(Phase.BLOCKED_AUTH, set())
    )
