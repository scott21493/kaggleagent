# tests/test_provider_auth.py
"""Auth-pattern fallback classifier — positive AND negative cases.

Seed patterns are conservative; tests prevent overclassification on
broad single words (token, login) that appear in non-auth contexts.
"""

from __future__ import annotations

import pytest

from arena.providers.auth import matches_auth_expiry


@pytest.mark.parametrize(
    "stderr",
    [
        "authentication failed",
        "Credential expired, please re-authenticate.",
        "session expired",
        "Please log in to continue.",
        "token invalid",
        "Auth denied (401)",
        "401 Unauthorized",
        "Please log in again.",
        "Please re-authenticate using `codex login`.",
        "You are not logged in.",
        "not signed in",
    ],
)
def test_matches_auth_expiry_positive(stderr: str) -> None:
    assert matches_auth_expiry(stderr) is True, f"expected positive match: {stderr!r}"


@pytest.mark.parametrize(
    "stderr",
    [
        "connection refused",
        "no such file: prompt.json",
        "tokenizer initialized with 50000 tokens",  # "token" alone shouldn't match
        "user logged in successfully",  # past-tense + positive — shouldn't match
        "running with --login=optional",  # "login" as a flag shouldn't match
        "rate limit exceeded; retry after 60s",
        "permission denied: /tmp/foo",  # generic permission, not auth
        "command not found: codex",
        "child process exited with code 1",
    ],
)
def test_matches_auth_expiry_negative(stderr: str) -> None:
    assert matches_auth_expiry(stderr) is False, f"expected negative match: {stderr!r}"


def test_matches_auth_expiry_empty_string() -> None:
    assert matches_auth_expiry("") is False


def test_matches_auth_expiry_is_case_insensitive() -> None:
    assert matches_auth_expiry("AUTHENTICATION FAILED") is True
    assert matches_auth_expiry("Session Expired") is True
