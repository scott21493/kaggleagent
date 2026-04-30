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


def test_frozen_dataclass(tmp_path: Path) -> None:
    p = SandboxPolicy.for_writes(frozenset({tmp_path}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.allowed_writes = frozenset()  # type: ignore[misc]
