from __future__ import annotations

from arena.providers.base import ProviderResult, ProviderStatus, UsageProxy


def build_result(
    *,
    task_id: str,
    provider: str,
    provider_version: str,
    status: ProviderStatus,
    stdout_path: str,
    stderr_path: str,
    artifacts: list[str],
    input_chars: int,
    output_chars: int,
    wall_seconds: float,
    shell_commands: int,
    failed_commands: int,
    waste_events: int,
    started_at: str,
    finished_at: str,
) -> ProviderResult:
    """Assemble a ProviderResult from raw pieces.

    Phase 0 stub providers don't subprocess, so this is just a dataclass
    builder; real providers will use it to package subprocess output after
    scrubbing.
    """
    return ProviderResult(
        task_id=task_id,
        provider=provider,
        provider_version=provider_version,
        status=status,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        artifacts=artifacts,
        usage_proxy=UsageProxy(
            input_chars=input_chars,
            output_chars=output_chars,
            wall_seconds=wall_seconds,
            shell_commands=shell_commands,
            failed_commands=failed_commands,
            waste_events=waste_events,
        ),
        started_at=started_at,
        finished_at=finished_at,
    )
