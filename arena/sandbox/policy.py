from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _resolve(p: Path) -> Path:
    """Expand ~ and resolve to absolute. Tolerates non-existent paths."""
    return Path(p).expanduser().resolve()


def _default_blocked_paths(workspace_root: Path | None = None) -> frozenset[Path]:
    """Canonical secret/credential/forensic paths providers must never read."""
    home = Path("~").expanduser().resolve()
    env_path = (
        _resolve(workspace_root / ".env") if workspace_root is not None else _resolve(Path(".env"))
    )
    traces_path = (
        _resolve(workspace_root / "traces")
        if workspace_root is not None
        else _resolve(Path("traces"))
    )
    return frozenset(
        {
            home / ".kaggle",
            home / ".codex",
            home / ".claude",
            env_path,
            traces_path,
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
    `blocked_paths`: directories the provider MUST NOT read. Two sources:
        the four canonical secret stores (~/.kaggle, ~/.codex, ~/.claude,
        .env — installed by every policy via _default_blocked_paths) PLUS
        any per-packet `blocked_paths` entries (e.g.,
        fixtures/<slug>/hidden_labels.csv — held-out evaluation labels).
        Reads against any entry trip SecretAccessBreaker. Reads outside this
        set are unrestricted (providers may load OS libs, read the rest of
        fixtures/, etc.).
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

        `packet["blocked_paths"]` is a list of paths the provider must NOT
        read. Entries starting with `~` are user-home-relative (e.g.,
        `~/.kaggle/`); other entries are workspace-relative (e.g.,
        `fixtures/<slug>/hidden_labels.csv`). They are merged with the
        canonical four-secret-store defaults.

        Path-traversal guard: workspace-relative entries (in either
        allowed_paths or blocked_paths) MUST resolve to a location under
        workspace_root after normalization. A packet that tries to escape
        via `..`-traversal raises ValueError immediately. ~-prefixed
        blocked_paths entries are exempt — they are deliberately outside
        the workspace and must remain so to block actual secret stores.

        The network allowlist comes from `ARENA_NETWORK_DOMAINS_ALLOWED`.
        """
        resolved_root = _resolve(workspace_root)

        def _resolve_workspace_relative(p: str, *, field: str) -> Path:
            """Resolve a workspace-relative entry and reject `..`-traversal."""
            resolved = _resolve(workspace_root / p)
            try:
                resolved.relative_to(resolved_root)
            except ValueError:
                raise ValueError(
                    f"packet {field} entry {p!r} resolves to {resolved} "
                    f"which is outside workspace_root {resolved_root}; "
                    "possible `..`-traversal attempt"
                ) from None
            return resolved

        allowed = frozenset(
            _resolve_workspace_relative(p, field="allowed_paths") for p in packet["allowed_paths"]
        )

        # blocked_paths splits into two camps: ~-prefixed (user-home,
        # exempt from the workspace check) and workspace-relative (subject
        # to the same traversal guard as allowed_paths).
        packet_blocked: set[Path] = set()
        for p in packet.get("blocked_paths", []):
            if str(p).startswith("~"):
                packet_blocked.add(_resolve(p))
            else:
                packet_blocked.add(_resolve_workspace_relative(p, field="blocked_paths"))

        blocked = _default_blocked_paths(workspace_root=workspace_root) | frozenset(packet_blocked)
        domains = _split_csv(os.environ.get("ARENA_NETWORK_DOMAINS_ALLOWED"))
        return cls(
            allowed_writes=allowed,
            blocked_paths=blocked,
            allowed_network_domains=domains,
        )
