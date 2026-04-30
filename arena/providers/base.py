from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Literal

ProviderStatus = Literal["success", "failure", "blocked", "killed", "interrupted"]


@dataclass(frozen=True)
class ProviderResult:
    """Structured outcome of one provider invocation.

    Mirrors provider_result.schema.json. The to_dict() method emits the
    schema-valid JSON shape (with schema_version filled in).
    """

    task_id: str
    provider: str
    provider_version: str
    status: ProviderStatus
    stdout_path: str
    stderr_path: str
    artifacts: list[str]
    usage_proxy: dict
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
