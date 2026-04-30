from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

KILL_SWITCH_FILE = Path(".arena/KILL_SWITCH")
KILL_SWITCH_ENV = "ARENA_KILL_SWITCH"


class Breaker(StrEnum):
    """Ten named circuit breakers from
    docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §4.4.

    PR2 owns the first five (provider-call/wall-clock/shell-command/
    repeated-failure/waste-event). PR3 owns secret-access, network-egress,
    and protected-file. PR4 owns schema-violation. PR7 owns auth-failure.
    The enum is defined in full now so event payloads stay schema-stable.
    """

    PROVIDER_CALL = "ProviderCallBreaker"
    WALL_CLOCK = "WallClockBreaker"
    SHELL_COMMAND = "ShellCommandBreaker"
    REPEATED_FAILURE = "RepeatedFailureBreaker"
    WASTE_EVENT = "WasteEventBreaker"
    SECRET_ACCESS = "SecretAccessBreaker"
    NETWORK_EGRESS = "NetworkEgressBreaker"
    PROTECTED_FILE = "ProtectedFileBreaker"
    SCHEMA_VIOLATION = "SchemaViolationBreaker"
    AUTH_FAILURE = "AuthFailureBreaker"


class KillSwitch:
    """File and env-var driven kill switch.

    Active when either:
    - .arena/KILL_SWITCH file exists relative to CWD;
    - ARENA_KILL_SWITCH=1 environment variable is set.

    Precedence: ARENA_KILL_SWITCH=1 wins. deactivate() removes the file
    but cannot clear an operator-set env var; the operator must unset it
    explicitly. This is intentional — env vars are an operator override
    (e.g. set in CI to globally disable provider calls) and a manual
    `arena unkill` should not be able to flip an operator-set kill.

    Path assumption: KILL_SWITCH_FILE is CWD-relative. Phase 0 harness
    components run from repo root, so the file lives at
    <repo>/.arena/KILL_SWITCH. If a future caller invokes from a different
    CWD, it will create/check a different file silently — Phase 0
    accepts this; Phase 1's runbook should pin a canonical path.

    All methods are static — there is no per-instance state. The CLI
    `arena kill` calls activate(); `arena unkill --human-confirm` calls
    deactivate(); the watchdog polls is_active() before every provider
    invocation.
    """

    @staticmethod
    def is_active() -> bool:
        if os.environ.get(KILL_SWITCH_ENV) == "1":
            return True
        return KILL_SWITCH_FILE.exists()

    @staticmethod
    def activate() -> None:
        KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_FILE.touch()

    @staticmethod
    def deactivate() -> None:
        KILL_SWITCH_FILE.unlink(missing_ok=True)
