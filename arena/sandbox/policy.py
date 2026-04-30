from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _resolve(p: Path) -> Path:
    """Expand ~ and resolve to absolute. Tolerates non-existent paths."""
    return Path(p).expanduser().resolve()


def _default_blocked_paths(workspace_root: Path | None = None) -> frozenset[Path]:
    """Canonical secret/credential paths that providers must never read."""
    home = Path("~").expanduser().resolve()
    env_path = (
        _resolve(workspace_root / ".env") if workspace_root is not None else _resolve(Path(".env"))
    )
    return frozenset(
        {
            home / ".kaggle",
            home / ".codex",
            home / ".claude",
            env_path,
        }
    )


def _split_csv(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


@dataclass(frozen=True)
class SandboxPolicy:
    """Packet-scoped sandbox policy.

    `allowed_writes`: directories the active provider MAY write under. Built
        from the dequeued packet's `allowed_paths` (e.g.,
        `worktrees/tabular_binary_v1/exp_0001/`). Anything outside — including
        sibling worktrees and fixtures — trips ProtectedFileBreaker.
    `blocked_paths`: directories the provider MUST NOT read (secret stores,
        credential caches, .env). Reads against these trip SecretAccessBreaker.
        Reads outside this set are unrestricted (providers may load OS libs,
        read fixtures, etc.).
    `allowed_network_domains`: hostnames the provider MAY egress to. Matching
        is EXACT (no wildcards): an entry of `example.com` does NOT cover
        `api.example.com`. Empty means deny-all (Phase 0 default per
        docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §5).

    Built per-packet by `from_packet` and held by a per-call `SandboxRunner`;
    stable for the duration of one `wrap_invoke`.
    """

    allowed_writes: frozenset[Path] = field(default_factory=frozenset)
    blocked_paths: frozenset[Path] = field(default_factory=_default_blocked_paths)
    allowed_network_domains: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def for_writes(cls, allowed_writes: frozenset[Path]) -> SandboxPolicy:
        """Build with explicit writable roots, default blocked paths,
        and ARENA_NETWORK_DOMAINS_ALLOWED from env.

        Used by the static-check driver and any caller that already has
        absolute, resolved paths. `from_packet` is the conventional path
        from a CLI run.
        """
        normalized = frozenset(_resolve(p) for p in allowed_writes)
        domains = _split_csv(os.environ.get("ARENA_NETWORK_DOMAINS_ALLOWED"))
        return cls(
            allowed_writes=normalized,
            blocked_paths=_default_blocked_paths(),
            allowed_network_domains=domains,
        )

    @classmethod
    def from_packet(
        cls,
        packet: dict,
        *,
        workspace_root: Path,
    ) -> SandboxPolicy:
        """Build a packet-scoped policy.

        `packet["allowed_paths"]` is a list of relative directory strings
        (e.g., `["worktrees/tabular_binary_v1/exp_0001/"]`). They are
        resolved against `workspace_root` (typically `Path.cwd()` for the
        CLI; `tmp_path` for tests).

        The blocked paths come from `_default_blocked_paths(workspace_root)`
        (so .env is workspace_root/.env, not CWD-relative); the network
        allowlist comes from `ARENA_NETWORK_DOMAINS_ALLOWED`.
        """
        allowed = frozenset(_resolve(workspace_root / p) for p in packet["allowed_paths"])
        domains = _split_csv(os.environ.get("ARENA_NETWORK_DOMAINS_ALLOWED"))
        return cls(
            allowed_writes=allowed,
            blocked_paths=_default_blocked_paths(workspace_root=workspace_root),
            allowed_network_domains=domains,
        )
