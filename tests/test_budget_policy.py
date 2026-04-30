from __future__ import annotations

import dataclasses

import pytest

from arena.budget.policy import Phase0HardCeilings


def test_defaults_match_phase_0_spec() -> None:
    c = Phase0HardCeilings()
    assert c.provider_calls_total == 12
    assert c.codex_calls_total == 6
    assert c.claude_calls_total == 6
    assert c.wall_clock_minutes_total == 120
    assert c.wall_clock_minutes_per_provider_call == 20
    assert c.shell_commands_per_task == 35
    assert c.failed_commands_per_task == 5
    assert c.repeated_same_failure_per_task == 2
    assert c.waste_events_per_task == 3
    assert c.waste_events_per_run == 5
    assert c.input_chars_total == 900_000
    assert c.output_chars_total == 250_000


def test_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_PHASE0_PROVIDER_CALL_CAP", "5")
    monkeypatch.setenv("ARENA_PHASE0_SHELL_COMMAND_CAP", "10")
    c = Phase0HardCeilings.from_env()
    assert c.provider_calls_total == 5
    assert c.shell_commands_per_task == 10
    # Untouched vars keep defaults.
    assert c.codex_calls_total == 6


def test_from_env_uses_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wipe relevant vars and confirm defaults survive.
    for var in ["ARENA_PHASE0_PROVIDER_CALL_CAP", "ARENA_PHASE0_SHELL_COMMAND_CAP"]:
        monkeypatch.delenv(var, raising=False)
    c = Phase0HardCeilings.from_env()
    assert c.provider_calls_total == 12
    assert c.shell_commands_per_task == 35


def test_frozen_dataclass() -> None:
    c = Phase0HardCeilings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.provider_calls_total = 999  # type: ignore[misc]
