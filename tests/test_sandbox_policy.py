from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from arena.sandbox.policy import SandboxPolicy


def _packet(*allowed: str) -> dict:
    return {"allowed_paths": list(allowed)}


def test_defaults_have_empty_allowed_domains_and_known_blocked_paths(tmp_path: Path) -> None:
    p = SandboxPolicy.from_packet(
        _packet("worktrees/tabular_binary_v1/exp_0001/"),
        workspace_root=tmp_path,
    )
    assert p.allowed_network_domains == frozenset()
    # The four canonical secret paths must be in blocked_paths after expansion.
    expanded = {str(b) for b in p.blocked_paths}
    home = str(Path("~").expanduser())
    assert any(home + "/.kaggle" in s or home + "\.kaggle" in s for s in expanded)
    assert any(home + "/.codex" in s or home + "\.codex" in s for s in expanded)
    assert any(home + "/.claude" in s or home + "\.claude" in s for s in expanded)
    # allowed_writes is the packet's allowed_paths resolved against workspace_root.
    expected_write = (tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001").resolve()
    assert expected_write in p.allowed_writes


def test_from_packet_resolves_multiple_allowed_paths(tmp_path: Path) -> None:
    p = SandboxPolicy.from_packet(
        _packet("worktrees/a/exp_0001/", "worktrees/a/exp_0001/scratch/"),
        workspace_root=tmp_path,
    )
    resolved = {str(w) for w in p.allowed_writes}
    assert str((tmp_path / "worktrees" / "a" / "exp_0001").resolve()) in resolved
    assert str((tmp_path / "worktrees" / "a" / "exp_0001" / "scratch").resolve()) in resolved


def test_for_writes_reads_allowed_network_domains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARENA_NETWORK_DOMAINS_ALLOWED", "example.com,api.example.org")
    p = SandboxPolicy.for_writes(frozenset({tmp_path}))
    assert p.allowed_network_domains == frozenset({"example.com", "api.example.org"})


def test_for_writes_treats_empty_or_missing_as_deny_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    p = SandboxPolicy.for_writes(frozenset({tmp_path}))
    assert p.allowed_network_domains == frozenset()
    monkeypatch.setenv("ARENA_NETWORK_DOMAINS_ALLOWED", "")
    p2 = SandboxPolicy.for_writes(frozenset({tmp_path}))
    assert p2.allowed_network_domains == frozenset()


def test_from_packet_resolves_dotenv_against_workspace_root(tmp_path: Path) -> None:
    """The .env entry in blocked_paths must be workspace_root/.env, not
    CWD-relative — so a sandbox built from a packet still blocks .env reads
    when the CLI runs from a different directory."""
    p = SandboxPolicy.from_packet(
        _packet("worktrees/tabular_binary_v1/exp_0001/"),
        workspace_root=tmp_path,
    )
    expected_env = (tmp_path / ".env").resolve()
    assert expected_env in p.blocked_paths


def test_frozen_dataclass(tmp_path: Path) -> None:
    p = SandboxPolicy.for_writes(frozenset({tmp_path}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.allowed_writes = frozenset()  # type: ignore[misc]


def test_from_packet_rejects_traversal_in_allowed_paths(tmp_path: Path) -> None:
    """Adversarial packet with `..` traversal in allowed_paths must raise
    rather than silently grant write access outside workspace_root."""
    with pytest.raises(ValueError, match=r"possible `\.\.`-traversal"):
        SandboxPolicy.from_packet(
            {"allowed_paths": ["worktrees/../../../etc/"], "blocked_paths": []},
            workspace_root=tmp_path,
        )


def test_from_packet_rejects_traversal_in_blocked_paths(tmp_path: Path) -> None:
    """Workspace-relative blocked_paths entries are also subject to the
    traversal guard. Tilde-prefixed entries are exempt (covered separately)."""
    with pytest.raises(ValueError, match=r"possible `\.\.`-traversal"):
        SandboxPolicy.from_packet(
            {
                "allowed_paths": ["worktrees/a/exp_0001/"],
                "blocked_paths": ["fixtures/../../../bad.txt"],
            },
            workspace_root=tmp_path,
        )


def test_from_packet_admits_tilde_blocked_paths_outside_workspace(
    tmp_path: Path,
) -> None:
    """`~`-prefixed blocked entries are intentionally outside workspace_root
    (they target user-home secret stores). The traversal guard must not
    reject them."""
    p = SandboxPolicy.from_packet(
        {
            "allowed_paths": ["worktrees/a/exp_0001/"],
            "blocked_paths": ["~/.kaggle/", "~/.codex/"],
        },
        workspace_root=tmp_path,
    )
    # Should construct without raising; the tilde entries land in blocked_paths.
    target = Path("~/.kaggle/kaggle.json").expanduser()
    from arena.sandbox.secrets import is_secret_read

    assert is_secret_read(target, p) is True


def test_default_blocked_paths_includes_traces(tmp_path):
    """traces/ is in the default blocked-read set, scoped to workspace_root."""
    from arena.sandbox.policy import _default_blocked_paths

    blocked = _default_blocked_paths(workspace_root=tmp_path)
    assert (tmp_path / "traces").resolve() in blocked


def test_provider_packet_cannot_read_traces_even_if_in_allowed_paths(tmp_path):
    """blocked_paths wins over allowed_paths for SECRET_READ (raw stream protection).

    Note: is_secret_read / is_protected_write are MODULE FUNCTIONS in
    arena.sandbox.secrets, not methods on SandboxPolicy."""
    from arena.sandbox.policy import SandboxPolicy
    from arena.sandbox.secrets import is_secret_read

    packet = {
        "task_id": "task_0001",
        "allowed_paths": ["traces/"],  # try to allow traces — must still be denied
        "blocked_paths": [],
    }
    policy = SandboxPolicy.from_packet(packet, workspace_root=tmp_path)
    target = (tmp_path / "traces" / "run_x" / "task_y" / "stdout.raw").resolve()
    assert is_secret_read(target, policy) is True, (
        "blocked_paths must win over allowed_paths for raw-trace reads"
    )


def test_provider_packet_cannot_write_to_traces_even_if_in_allowed_paths(tmp_path):
    """blocked_paths wins over allowed_paths for PROTECTED_WRITE."""
    from arena.sandbox.policy import SandboxPolicy
    from arena.sandbox.secrets import is_protected_write

    packet = {
        "task_id": "task_0001",
        "allowed_paths": ["traces/"],
        "blocked_paths": [],
    }
    policy = SandboxPolicy.from_packet(packet, workspace_root=tmp_path)
    target = (tmp_path / "traces" / "fake_write.txt").resolve()
    assert is_protected_write(target, policy) is True
