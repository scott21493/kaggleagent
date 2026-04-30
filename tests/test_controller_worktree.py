from __future__ import annotations

from pathlib import Path

from arena.controller.worktree import create_workspace


def test_creates_per_experiment_directory(tmp_path: Path) -> None:
    workspace = create_workspace(
        worktree_root=tmp_path,
        competition_slug="tabular_binary_v1",
        experiment_id="exp_0001",
    )
    assert workspace.exists()
    assert workspace.is_dir()
    assert workspace == tmp_path / "tabular_binary_v1" / "exp_0001"


def test_idempotent(tmp_path: Path) -> None:
    a = create_workspace(tmp_path, "tabular_binary_v1", "exp_0001")
    b = create_workspace(tmp_path, "tabular_binary_v1", "exp_0001")
    assert a == b
