from __future__ import annotations

from pathlib import Path

from arena.sandbox.policy import SandboxPolicy
from arena.sandbox.secrets import is_protected_write, is_secret_read


def _policy(tmp_path: Path, exp_id: str = "exp_0001") -> SandboxPolicy:
    """Build a packet-scoped policy with the active worktree as the only writable
    directory. Sibling experiments and fixtures are NOT writable."""
    return SandboxPolicy.from_packet(
        {"allowed_paths": [f"worktrees/tabular_binary_v1/{exp_id}/"]},
        workspace_root=tmp_path,
    )


def test_is_secret_read_flags_kaggle_creds(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    target = Path("~/.kaggle/kaggle.json").expanduser()
    assert is_secret_read(target, p) is True


def test_is_secret_read_flags_dotenv(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    # The .env at workspace_root is blocked
    target = (tmp_path / ".env").resolve()
    assert is_secret_read(target, p) is True


def test_is_secret_read_flags_codex_and_claude(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    assert is_secret_read(Path("~/.codex/auth.json").expanduser(), p) is True
    assert is_secret_read(Path("~/.claude/state.json").expanduser(), p) is True


def test_is_secret_read_does_not_flag_worktree_files(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    target = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    assert is_secret_read(target, p) is False


def test_is_protected_write_allows_worktree_path(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    target = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    assert is_protected_write(target, p) is False


def test_is_protected_write_rejects_outside_worktree(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    target = (
        Path("/etc/passwd")
        if Path("/etc").exists()
        else Path("C:/Windows/System32/drivers/etc/hosts")
    )
    assert is_protected_write(target, p) is True


def test_is_protected_write_rejects_dotenv_write(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    # The .env at workspace_root is protected
    assert is_protected_write((tmp_path / ".env").resolve(), p) is True


# --- Regression tests for the P1 plan-review fix -----------------------------
# These exercise the "outside its worktree" half of the PR3 acceptance and
# would have passed silently under the old run-level allowed_paths design
# (which made the whole worktrees/ tree and fixtures/ writable).


def test_is_protected_write_rejects_sibling_worktree(tmp_path: Path) -> None:
    """Provider for exp_0001 must NOT be able to write into exp_9999's worktree."""
    p = _policy(tmp_path, exp_id="exp_0001")
    sibling = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_9999" / "submission.csv"
    assert is_protected_write(sibling, p) is True


def test_is_protected_write_rejects_sibling_competition(tmp_path: Path) -> None:
    """Provider for tabular_binary_v1 must NOT be able to write to a different slug."""
    p = _policy(tmp_path, exp_id="exp_0001")
    other = tmp_path / "worktrees" / "image_classification_v1" / "exp_0001" / "submission.csv"
    assert is_protected_write(other, p) is True


def test_is_protected_write_rejects_fixture_write(tmp_path: Path) -> None:
    """Fixtures are read-only inputs; writes against them must trip ProtectedFile."""
    p = _policy(tmp_path)
    fixture_target = tmp_path / "fixtures" / "tabular_binary_v1" / "train.csv"
    assert is_protected_write(fixture_target, p) is True


def test_is_protected_write_rejects_workspace_root_write(tmp_path: Path) -> None:
    """Writes to the workspace root (not under any worktree) trip ProtectedFile."""
    p = _policy(tmp_path)
    target = tmp_path / "stray_output.txt"
    assert is_protected_write(target, p) is True
