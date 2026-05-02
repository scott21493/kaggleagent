# arena/controller/watchdog.py
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

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
      activate sandbox → emit provider_invoked → adapter.invoke (with
      live waste observer driven from shell_command_observed events) →
      emit task_finished (or breaker_triggered) → post-invoke budget.

    The sandbox and event_emitter are passed PER-CALL because both have
    packet-scoped lifetime (sandbox policy, run-level trace store). The
    watchdog itself stays packet-agnostic. When either is None, the
    relevant behavior is a no-op (PR2/PR3 backward compat).

    PR4 Task 6 adds the live waste observer: when event_emitter is set,
    wrap_invoke binds a TaskWasteCounters for the task and registers an
    on_event callback on the trace store. The callback filters for
    shell_command_observed events with exit_code != 0 and calls
    WasteDetector.observe_failed_command + check_task_caps. The latter
    raises BudgetExceeded(REPEATED_FAILURE) if Phase0HardCeilings.repeated_same_failure_per_task
    is exceeded — this propagates through the sandbox context manager,
    out of adapter.invoke, and through wrap_invoke as a normal
    BudgetExceeded.
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
        event_emitter: TraceStore | None = None,
    ) -> ProviderResult:
        """Invoke the provider with optional sandbox and trace emission.

        Caller should have already called check_can_invoke(adapter.name);
        this method skips the kill-switch and pre-invoke checks (no
        re-check during invoke per PR2 plan §8; PR7 will add per-event
        polling for long-running subprocess providers).

        When `sandbox` is set, activates it via runner.context() for the
        duration of adapter.invoke; SandboxViolation propagates and
        record_post_invoke is correctly skipped.

        When `event_emitter` is set:
        - Emits provider_invoked before invoke
        - Binds a TaskWasteCounters and registers an on_event callback that
          drives WasteDetector from shell_command_observed events with
          exit_code != 0. Repeated-same-failure over the cap raises
          BudgetExceeded(REPEATED_FAILURE) BEFORE adapter.invoke returns.
        - Emits task_finished after a successful record_post_invoke.
        - SandboxViolation and BudgetExceeded both emit breaker_triggered
          events (with breaker + evidence) before re-raising.

        The trace always shows a complete causal chain: provider_invoked →
        either task_finished OR breaker_triggered, never both.
        """
        # Local imports to avoid circular dep (waste.py imports governor).
        from arena.budget.waste import TaskWasteCounters, WasteDetector

        sandbox_ctx: AbstractContextManager[object] = (
            sandbox.context() if sandbox is not None else nullcontext()
        )
        if event_emitter is not None:
            event_emitter.emit(
                event_type="provider_invoked",
                severity="info",
                task_id=packet["task_id"],
                payload={
                    "provider": adapter.name,
                    "provider_version": adapter.version,
                },
            )

        # Live waste observer: bind a counter for this task and drive
        # WasteDetector.observe_failed_command from shell_command_observed
        # events the provider emits during invoke. Repeated-same-failure
        # over the cap raises BudgetExceeded(REPEATED_FAILURE) BEFORE
        # adapter.invoke returns.
        waste_state = TaskWasteCounters()
        waste = WasteDetector(self._governor.ceilings)
        task_id_for_waste = packet["task_id"]

        def _on_event(evt: dict[str, Any]) -> None:
            if evt.get("event_type") != "shell_command_observed":
                return
            payload = evt.get("payload", {})
            if payload.get("exit_code", 0) == 0:
                return
            command = payload.get("command", "")
            waste.observe_failed_command(waste_state, command)
            waste.check_task_caps(waste_state, task_id=task_id_for_waste)

        if event_emitter is not None:
            event_emitter.set_on_event(_on_event)

        try:
            with sandbox_ctx:
                result = adapter.invoke(packet)
        except SandboxViolation as exc:
            if event_emitter is not None:
                event_emitter.emit(
                    event_type="breaker_triggered",
                    severity="error",
                    task_id=packet["task_id"],
                    payload={
                        "breaker": exc.breaker.value,
                        "evidence": [exc.attempt.target],
                    },
                )
            raise
        except BudgetExceeded as exc:
            # Live waste observer raised mid-invoke. Emit breaker_triggered
            # and propagate so run_next persists a status=blocked row via
            # the existing except BudgetExceeded arm.
            if event_emitter is not None:
                event_emitter.emit(
                    event_type="breaker_triggered",
                    severity="error",
                    task_id=packet["task_id"],
                    payload={"breaker": exc.breaker.value, "evidence": [str(exc)]},
                )
            raise
        finally:
            if event_emitter is not None:
                event_emitter.set_on_event(None)

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
