from __future__ import annotations

from dataclasses import dataclass

from arena.budget.kill_switch import Breaker
from arena.budget.policy import Phase0HardCeilings
from arena.providers.base import UsageProxy


class BudgetExceeded(Exception):
    """Raised when a hard ceiling is exceeded. Carries the breaker that fired
    and (when available) the UsageProxy snapshot from the offending invoke,
    so callers can persist accurate usage on a blocked row."""

    def __init__(
        self,
        breaker: Breaker,
        message: str,
        usage_proxy: UsageProxy | None = None,
    ) -> None:
        super().__init__(message)
        self.breaker = breaker
        self.usage_proxy = usage_proxy


@dataclass
class RunAccumulators:
    """Run-level usage totals summed across all completed experiments."""

    provider_calls: int = 0
    codex_calls: int = 0
    claude_calls: int = 0
    wall_seconds: float = 0.0
    waste_events: int = 0
    input_chars: int = 0
    output_chars: int = 0


# TODO(PR8+): switch to exact-match or a provider-family registry.
# Substring matching is currently acceptable: stub_codex / stub_claude
# (PR1-PR6) and codex / claude (PR7 real adapters) all partition
# cleanly under "codex" in name / "claude" in name. A pathological
# future name like "claude_codex_hybrid" would match both helpers
# and miscount budget — registry would resolve that.
def _is_codex(provider_name: str) -> bool:
    return "codex" in provider_name


def _is_claude(provider_name: str) -> bool:
    return "claude" in provider_name


class BudgetGovernor:
    """Tracks run-level usage and validates per-invoke against hard ceilings.

    `check_pre_invoke(provider_name)` raises before a provider call would
    exceed run-level call counts. `record_post_invoke(provider_name, usage,
    task_id)` updates accumulators with the returned UsageProxy and raises
    if any per-task or per-run cap is now exceeded.

    Ordering semantics in record_post_invoke:
      - Per-task caps raise BEFORE accumulators update. The failing
        invoke is excluded from run totals — the run can recover or be
        diagnosed without polluted numbers.
      - Per-run caps raise AFTER accumulators update. The failing invoke
        IS included in the cumulative totals — the error message reflects
        accurate run-level usage.

    The governor is per-process. The CLI passes in starting accumulators
    (typically summed from the scoreboard) so persistent counts survive
    across `arena run-next` invocations within one run.
    """

    def __init__(
        self,
        ceilings: Phase0HardCeilings,
        accumulators: RunAccumulators | None = None,
    ) -> None:
        self._ceilings = ceilings
        self._accum = accumulators if accumulators is not None else RunAccumulators()

    @property
    def ceilings(self) -> Phase0HardCeilings:
        """Read-only access to the configured hard ceilings.

        Added in PR4 so the Watchdog can construct a WasteDetector with the
        same ceilings without reaching into private state. Treat as
        read-only; cap mutation requires re-instantiating the governor.
        """
        return self._ceilings

    @property
    def accumulators(self) -> RunAccumulators:
        """Return the live RunAccumulators instance (not a copy).

        Treat as read-only. Mutating the returned object directly will
        corrupt governor state and bypass cap checks. PR2 callers (the
        Watchdog and the budget-status CLI) only read this value; PR4
        may need a snapshot/copy mechanism if event observers want to
        peek without affecting state.
        """
        return self._accum

    def check_pre_invoke(self, provider_name: str) -> None:
        if self._accum.provider_calls + 1 > self._ceilings.provider_calls_total:
            raise BudgetExceeded(
                Breaker.PROVIDER_CALL,
                f"would exceed provider_calls_total ({self._ceilings.provider_calls_total}) "
                f"with {provider_name!r} (current {self._accum.provider_calls})",
            )
        if _is_codex(provider_name) and (
            self._accum.codex_calls + 1 > self._ceilings.codex_calls_total
        ):
            raise BudgetExceeded(
                Breaker.PROVIDER_CALL,
                f"would exceed codex_calls_total ({self._ceilings.codex_calls_total}) "
                f"with {provider_name!r} (current {self._accum.codex_calls})",
            )
        if _is_claude(provider_name) and (
            self._accum.claude_calls + 1 > self._ceilings.claude_calls_total
        ):
            raise BudgetExceeded(
                Breaker.PROVIDER_CALL,
                f"would exceed claude_calls_total ({self._ceilings.claude_calls_total}) "
                f"with {provider_name!r} (current {self._accum.claude_calls})",
            )

    def record_post_invoke(
        self,
        provider_name: str,
        usage: UsageProxy,
        *,
        task_id: str,
    ) -> None:
        # Per-task caps first (tightest scope).
        per_call_seconds = self._ceilings.wall_clock_minutes_per_provider_call * 60
        if usage["wall_seconds"] > per_call_seconds:
            raise BudgetExceeded(
                Breaker.WALL_CLOCK,
                f"task {task_id} ran {usage['wall_seconds']:.1f}s on {provider_name!r} "
                f"(per-call ceiling {per_call_seconds}s)",
                usage_proxy=usage,
            )
        if usage["shell_commands"] > self._ceilings.shell_commands_per_task:
            raise BudgetExceeded(
                Breaker.SHELL_COMMAND,
                f"task {task_id} emitted {usage['shell_commands']} shell commands "
                f"(ceiling {self._ceilings.shell_commands_per_task})",
                usage_proxy=usage,
            )
        if usage["failed_commands"] > self._ceilings.failed_commands_per_task:
            raise BudgetExceeded(
                Breaker.REPEATED_FAILURE,
                f"task {task_id} accumulated {usage['failed_commands']} failed "
                f"commands (ceiling {self._ceilings.failed_commands_per_task}); "
                f"breaker matches WasteDetector.check_task_caps",
                usage_proxy=usage,
            )
        if usage["waste_events"] > self._ceilings.waste_events_per_task:
            raise BudgetExceeded(
                Breaker.WASTE_EVENT,
                f"task {task_id} emitted {usage['waste_events']} waste events "
                f"(ceiling {self._ceilings.waste_events_per_task})",
                usage_proxy=usage,
            )

        # Update accumulators.
        self._accum.provider_calls += 1
        if _is_codex(provider_name):
            self._accum.codex_calls += 1
        if _is_claude(provider_name):
            self._accum.claude_calls += 1
        self._accum.wall_seconds += usage["wall_seconds"]
        self._accum.waste_events += usage["waste_events"]
        self._accum.input_chars += usage["input_chars"]
        self._accum.output_chars += usage["output_chars"]

        # Per-run caps after update.
        run_wall_seconds_cap = self._ceilings.wall_clock_minutes_total * 60
        if self._accum.wall_seconds > run_wall_seconds_cap:
            raise BudgetExceeded(
                Breaker.WALL_CLOCK,
                f"run wall clock {self._accum.wall_seconds:.1f}s exceeded ceiling "
                f"{run_wall_seconds_cap}s after task {task_id}",
                usage_proxy=usage,
            )
        if self._accum.waste_events > self._ceilings.waste_events_per_run:
            raise BudgetExceeded(
                Breaker.WASTE_EVENT,
                f"run waste events {self._accum.waste_events} exceeded ceiling "
                f"{self._ceilings.waste_events_per_run} after task {task_id}",
                usage_proxy=usage,
            )
        if self._accum.input_chars > self._ceilings.input_chars_total:
            # No dedicated input-chars breaker; map to PROVIDER_CALL as coarse proxy.
            raise BudgetExceeded(
                Breaker.PROVIDER_CALL,
                f"run input chars {self._accum.input_chars} exceeded ceiling "
                f"{self._ceilings.input_chars_total} after task {task_id}",
                usage_proxy=usage,
            )
        if self._accum.output_chars > self._ceilings.output_chars_total:
            raise BudgetExceeded(
                Breaker.PROVIDER_CALL,
                f"run output chars {self._accum.output_chars} exceeded ceiling "
                f"{self._ceilings.output_chars_total} after task {task_id}",
                usage_proxy=usage,
            )

    def status(self) -> dict:
        """Snapshot for `arena budget status` and humans."""
        return {
            "provider_calls": {
                "used": self._accum.provider_calls,
                "ceiling": self._ceilings.provider_calls_total,
            },
            "codex_calls": {
                "used": self._accum.codex_calls,
                "ceiling": self._ceilings.codex_calls_total,
            },
            "claude_calls": {
                "used": self._accum.claude_calls,
                "ceiling": self._ceilings.claude_calls_total,
            },
            "wall_seconds": {
                "used": self._accum.wall_seconds,
                "ceiling": self._ceilings.wall_clock_minutes_total * 60,
            },
            "input_chars": {
                "used": self._accum.input_chars,
                "ceiling": self._ceilings.input_chars_total,
            },
            "output_chars": {
                "used": self._accum.output_chars,
                "ceiling": self._ceilings.output_chars_total,
            },
            "waste_events": {
                "used": self._accum.waste_events,
                "ceiling": self._ceilings.waste_events_per_run,
            },
        }
