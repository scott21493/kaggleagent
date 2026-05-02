from __future__ import annotations

from contextlib import nullcontext

from arena.budget.governor import BudgetGovernor
from arena.budget.kill_switch import KillSwitch
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.sandbox.runner import SandboxRunner


class KillSwitchActive(Exception):
    """Raised by Watchdog.check_can_invoke when the kill switch is active.

    Distinct from BudgetExceeded because the kill switch is a manual stop,
    not a cap violation. Run-next catches this before queue.dequeue() so
    the task remains retryable.
    """


class Watchdog:
    """Wraps a provider invocation with kill-switch, budget, and sandbox checks.

    The API is split so callers can check before they dequeue (so a blocked
    invoke leaves the queued task retryable):

    - check_can_invoke(provider_name): kill switch + pre-invoke budget.
      Call BEFORE TaskQueue.dequeue.
    - wrap_invoke(adapter, packet, *, sandbox=None): activate sandbox →
      provider.invoke → post-invoke budget. Call AFTER dequeue.

    The sandbox is passed PER-CALL because its policy is packet-scoped (built
    from the dequeued packet's allowed_paths). The watchdog itself stays
    packet-agnostic. When `sandbox` is None, `wrap_invoke` runs without
    sandbox enforcement (PR2 backward compat + tests that don't exercise the
    sandbox path).

    Providers register filesystem/network intent via assert_sandbox_allowed
    inside their invoke; the active sandbox raises SandboxViolation on the
    first policy violation, which propagates through wrap_invoke for the
    CLI to handle.

    The waste detector's repeated-same-failure tracking is event-level
    (PR4); the watchdog does not call it in PR2/PR3. Per-task waste cap
    enforcement happens inside governor.record_post_invoke from
    usage_proxy['waste_events'] and usage_proxy['failed_commands'].
    """

    def __init__(
        self,
        governor: BudgetGovernor,
        kill_switch: type[KillSwitch] = KillSwitch,
    ) -> None:
        self._governor = governor
        self._kill_switch = kill_switch

    def check_can_invoke(self, provider_name: str) -> None:
        """Pre-dequeue check. Raises KillSwitchActive if the kill switch is
        active, or BudgetExceeded if the next provider call would exceed
        run-level call counts. Does not touch the queue."""
        if self._kill_switch.is_active():
            raise KillSwitchActive(f"kill switch active; refusing to invoke {provider_name!r}")
        self._governor.check_pre_invoke(provider_name)

    def wrap_invoke(
        self,
        adapter: ProviderAdapter,
        packet: dict,
        *,
        sandbox: SandboxRunner | None = None,
    ) -> ProviderResult:
        """Invoke the provider with the (optional) sandbox active and
        validate the returned UsageProxy against per-task and per-run
        ceilings.

        Caller should have already called check_can_invoke(adapter.name);
        this method skips the kill-switch and pre-invoke checks.

        `sandbox`, when provided, is activated via runner.context() for the
        duration of adapter.invoke(packet) and deactivated on both success
        and exception (the context manager guarantees this). SandboxViolation
        propagates back to the caller, who translates it into a status=blocked
        row.
        """
        sandbox_ctx = sandbox.context() if sandbox is not None else nullcontext()
        # No re-check of the kill switch here: see PR2 plan §8. PR7 will
        # add per-event polling for long-running subprocess providers.
        with sandbox_ctx:
            result = adapter.invoke(packet)
        self._governor.record_post_invoke(
            adapter.name,
            result.usage_proxy,
            task_id=packet["task_id"],
        )
        return result
