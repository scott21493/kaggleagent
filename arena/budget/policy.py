from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(var: str, default: int) -> int:
    raw = os.environ.get(var)
    return int(raw) if raw is not None and raw != "" else default


@dataclass(frozen=True)
class Phase0HardCeilings:
    """Hard operational ceilings for one Phase 0 run.

    Values mirror docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §4.1
    and .env.example. They are not billing estimates; they are deterministic
    guardrails enforced by the BudgetGovernor.
    """

    provider_calls_total: int = 12
    codex_calls_total: int = 6
    claude_calls_total: int = 6
    wall_clock_minutes_total: int = 120
    wall_clock_minutes_per_provider_call: int = 20
    shell_commands_per_task: int = 35
    failed_commands_per_task: int = 5
    repeated_same_failure_per_task: int = 2
    waste_events_per_task: int = 3
    waste_events_per_run: int = 5
    input_chars_total: int = 900_000
    output_chars_total: int = 250_000

    @classmethod
    def from_env(cls) -> Phase0HardCeilings:
        """Construct from ARENA_PHASE0_* env vars, falling back to defaults."""
        return cls(
            provider_calls_total=_env_int("ARENA_PHASE0_PROVIDER_CALL_CAP", 12),
            codex_calls_total=_env_int("ARENA_PHASE0_CODEX_CALL_CAP", 6),
            claude_calls_total=_env_int("ARENA_PHASE0_CLAUDE_CALL_CAP", 6),
            wall_clock_minutes_total=_env_int("ARENA_PHASE0_WALL_MINUTES_CAP", 120),
            wall_clock_minutes_per_provider_call=_env_int(
                "ARENA_PHASE0_PER_CALL_WALL_MINUTES_CAP", 20
            ),
            shell_commands_per_task=_env_int("ARENA_PHASE0_SHELL_COMMAND_CAP", 35),
            failed_commands_per_task=_env_int("ARENA_PHASE0_FAILED_COMMAND_CAP", 5),
            repeated_same_failure_per_task=_env_int(
                "ARENA_PHASE0_REPEATED_SAME_FAILURE_PER_TASK_CAP", 2
            ),
            waste_events_per_task=_env_int("ARENA_PHASE0_WASTE_EVENTS_PER_TASK_CAP", 3),
            waste_events_per_run=_env_int("ARENA_PHASE0_WASTE_EVENTS_PER_RUN_CAP", 5),
            input_chars_total=_env_int("ARENA_PHASE0_INPUT_CHARS_CAP", 900_000),
            output_chars_total=_env_int("ARENA_PHASE0_OUTPUT_CHARS_CAP", 250_000),
        )
