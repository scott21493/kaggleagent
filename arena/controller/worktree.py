from __future__ import annotations

from pathlib import Path


def create_workspace(
    worktree_root: str | Path,
    competition_slug: str,
    experiment_id: str,
) -> Path:
    """Create and return the per-experiment workspace directory.

    Layout: <worktree_root>/<competition_slug>/<experiment_id>/.
    Idempotent: if the directory already exists, it is returned unchanged.
    """
    workspace = Path(worktree_root) / competition_slug / experiment_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
