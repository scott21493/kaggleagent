from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Literal, TypedDict

ProviderStatus = Literal["success", "failure", "blocked", "killed", "interrupted"]


class UsageProxy(TypedDict):
    """Six required deterministic usage counters per provider invocation.

    Values are not billing estimates; they are operational guardrail
    metrics the budget governor (PR2) compares against task ceilings.
    """

    input_chars: int
    output_chars: int
    wall_seconds: float
    shell_commands: int
    failed_commands: int
    waste_events: int


@dataclass(frozen=True)
class ProviderResult:
    """Structured outcome of one provider invocation.

    Mirrors provider_result.schema.json. The to_dict() method emits the
    schema-valid JSON shape (with schema_version filled in).

    Mutability note: the dataclass is frozen, but `usage_proxy` (TypedDict
    underneath = dict) and `artifacts` (list) are mutable in place. Callers
    must treat ProviderResult instances as immutable once constructed and
    never mutate the contained collections; downstream consumers cache and
    serialize them, and in-place mutation can cause spooky-action-at-a-
    distance bugs across consumers.
    """

    task_id: str
    provider: str
    provider_version: str
    status: ProviderStatus
    stdout_path: str
    stderr_path: str
    artifacts: list[str]
    usage_proxy: UsageProxy
    started_at: str
    finished_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        return {"schema_version": "provider_result.v1", **payload}


class ProviderAdapter(ABC):
    """Abstract base class for provider workers (stub or real).

    See docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md for the
    real-provider subprocess conventions; stubs do not subprocess.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def invoke(self, task_packet: dict) -> ProviderResult:
        """Run the task and return a ProviderResult.

        Implementations must:
        - validate the incoming packet against task_packet.schema.json
          (callers may also pre-validate; double-validation is cheap)
        - write any required outputs into the workspace
        - return a ProviderResult whose to_dict() satisfies provider_result.schema.json
        """
