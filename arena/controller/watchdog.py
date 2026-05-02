# arena/controller/watchdog.py
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext

from arena.budget.governor import BudgetExceeded, BudgetGovernor
from arena.budget.kill_switch import KillSwitch
from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.sandbox.runner import SandboxRunner, SandboxViolation


class KillSwitchActive(Exception):
    """Raised by Watchdog.check_can_invoke when the kill switch is active.

    Distinct from BudgetExceeded because the kill switch is a manual stop,
    not a cap violation. Run-next catches this before queue.dequeue() so
    the task remains retryable.
    """


class Watchdog:
    """Wraps a provider invocation with kill-switch, budget, sandbox, and
    event-emission concerns.

    Phases:

    - check_can_invoke(provider_name): kill switch + pre-invoke budget.
      Call BEFORE TaskQueue.dequeue.
    - wrap_invoke(adapter, packet, *, sandbox=None, event_emitter=None):
      activate sandbox → emit provider_invoked → adapter.invoke →
      emit task_finished (or breaker_triggered) → post-invoke budget.

    The sandbox and event_emitter are passed PER-CALL because both have
    packet-scoped lifetime (sandbox policy, run-level trace store). The
    watchdog itself stays packet-agnostic. When either is None, the
    relevant behavior is a no-op (PR2/PR3 backward compat).
    """

    def __init__(
        self,
        governor: BudgetGovernor,
        kill_switch: type[KillSwitch] = KillSwitch,
    ) -> None:
        self._governor = governor
        self._kill_switch = kill_switch

    def check_can_invoke(self, provider_name: str) -> None:
        if self._kill_switch.is_active():
            raise KillSwitchActive(f"kill switch active; refusing to invoke {provider_name!r}")
        self._governor.check_pre_invoke(provider_name)

    def wrap_invoke(
        self,
        adapter: ProviderAdapter,
        packet: dict,
        *,
        sandbox: SandboxRunner | None = None,
        event_emitter: TraceStore | None = None,
    ) -> ProviderResult:
        sandbox_ctx: AbstractContextManager[object] = (
            sandbox.context() if sandbox is not None else nullcontext()
        )
        if event_emitter is not None:
            event_emitter.emit(
                event_type="provider_invoked",
                severity="info",
                task_id=packet["task_id"],
                payload={"provider": adapter.name, "provider_version": adapter.version},
            )
        try:
            with sandbox_ctx:
                result = adapter.invoke(packet)
        except SandboxViolation as exc:
            if event_emitter is not None:
                event_emitter.emit(
                    event_type="breaker_triggered",
                    severity="error",
                    task_id=packet["task_id"],
                    payload={"breaker": exc.breaker.value, "evidence": [exc.attempt.target]},
                )
            raise
        try:
            self._governor.record_post_invoke(
                adapter.name,
                result.usage_proxy,
                task_id=packet["task_id"],
            )
        except BudgetExceeded as exc:
            if event_emitter is not None:
                event_emitter.emit(
                    event_type="breaker_triggered",
                    severity="error",
                    task_id=packet["task_id"],
                    payload={"breaker": exc.breaker.value, "evidence": [str(exc)]},
                )
            raise
        if event_emitter is not None:
            event_emitter.emit(
                event_type="task_finished",
                severity="info",
                task_id=packet["task_id"],
                payload={"status": result.status, "provider": adapter.name},
            )
        return result
