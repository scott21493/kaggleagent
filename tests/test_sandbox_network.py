from __future__ import annotations

from pathlib import Path

import pytest

from arena.sandbox.network import is_unapproved_egress
from arena.sandbox.policy import SandboxPolicy


def _policy(tmp_path: Path) -> SandboxPolicy:
    return SandboxPolicy.for_writes(frozenset({tmp_path}))


def test_deny_all_when_allowlist_empty(tmp_path: Path) -> None:
    p = _policy(tmp_path)
    assert is_unapproved_egress("https://example.com", p) is True
    assert is_unapproved_egress("http://localhost", p) is True
    assert is_unapproved_egress("https://api.example.org/path", p) is True


def test_admits_explicit_allowlist_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_NETWORK_DOMAINS_ALLOWED", "example.com")
    p = _policy(tmp_path)
    assert is_unapproved_egress("https://example.com", p) is False
    assert is_unapproved_egress("https://example.com/path?q=1", p) is False
    # Subdomain not on the list — denied.
    assert is_unapproved_egress("https://api.example.com", p) is True
    # Different domain — denied.
    assert is_unapproved_egress("https://example.org", p) is True


def test_handles_no_scheme_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_NETWORK_DOMAINS_ALLOWED", "example.com")
    p = _policy(tmp_path)
    # urlparse without scheme treats first segment as path — we want to deny
    # rather than accidentally admit. Empty hostname → deny.
    assert is_unapproved_egress("example.com/path", p) is True


def test_handles_url_with_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_NETWORK_DOMAINS_ALLOWED", "example.com")
    p = _policy(tmp_path)
    assert is_unapproved_egress("https://example.com:8443/path", p) is False
