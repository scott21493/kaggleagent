from __future__ import annotations

from pathlib import Path

from arena.sandbox.policy import SandboxPolicy


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _is_under(candidate: Path, root: Path) -> bool:
    """True if `candidate` equals or is contained under `root` after resolution."""
    candidate = _resolve(candidate)
    root = _resolve(root)
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def is_secret_read(path: Path | str, policy: SandboxPolicy) -> bool:
    """True if reading `path` would breach the policy's blocked_paths."""
    candidate = _resolve(path)
    return any(_is_under(candidate, blocked) for blocked in policy.blocked_paths)


def is_protected_write(path: Path | str, policy: SandboxPolicy) -> bool:
    """True if writing `path` would land outside any allowed_writes root.

    Writes ALSO must not land on a blocked_paths secret target (e.g.,
    overwriting .env). Both conditions are violations.

    `allowed_writes` is packet-scoped (built from the dequeued packet's
    `allowed_paths`), so a sibling worktree, a different competition's
    workspace, or any path under fixtures/ trips this check.
    """
    candidate = _resolve(path)
    if any(_is_under(candidate, blocked) for blocked in policy.blocked_paths):
        return True
    return not any(_is_under(candidate, allowed) for allowed in policy.allowed_writes)
