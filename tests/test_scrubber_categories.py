# tests/test_scrubber_categories.py
from __future__ import annotations

import pytest

from arena.observability.scrubber import scrub_text


def test_scrubs_bearer_token() -> None:
    out = scrub_text("Authorization: Bearer abc123XYZdef456")
    assert "abc123XYZdef456" not in out
    assert "<REDACTED_TOKEN>" in out


def test_scrubs_oauth_access_token() -> None:
    out = scrub_text('access_token="ya29.a0AfH6SMBxxxxxxxxxxxxxxxxx"')
    assert "ya29.a0AfH6SMBxxxxxxxxxxxxxxxxx" not in out
    assert "<REDACTED_OAUTH_TOKEN>" in out


def test_scrubs_api_key_assignment() -> None:
    out = scrub_text("api_key = sk-1234567890abcdefghij")
    assert "sk-1234567890abcdefghij" not in out
    assert "<REDACTED_API_KEY>" in out


def test_scrubs_kaggle_username_key_pair() -> None:
    out = scrub_text('{"username":"kaggler123","key":"abc1234567890def"}')
    assert "kaggler123" not in out
    assert "abc1234567890def" not in out
    assert "<REDACTED_KAGGLE_JSON>" in out


def test_scrubs_ssh_private_key_block() -> None:
    out = scrub_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE=\n-----END OPENSSH PRIVATE KEY-----"
    )
    assert "b3BlbnNzaC1rZXktdjE=" not in out
    assert "<REDACTED_SSH_PRIVATE_KEY>" in out


def test_scrubs_home_path_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARENA_SCRUB_HOME_PATHS", "1")
    monkeypatch.setenv("HOME", "/home/scott")
    out = scrub_text("loaded /home/scott/.kaggle/kaggle.json")
    assert "/home/scott" not in out
    assert "<HOME>" in out


def test_does_not_scrub_home_path_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARENA_SCRUB_HOME_PATHS", raising=False)
    out = scrub_text("loaded /home/scott/.kaggle/kaggle.json")
    # Default: leave unchanged
    assert "/home/scott" in out


def test_scrubs_dotenv_contents() -> None:
    out = scrub_text("DATABASE_URL=postgres://user:supersecret@host/db")
    assert "supersecret" not in out
    assert "<REDACTED_PASSWORD>" in out


def test_scrubs_cookie_header() -> None:
    out = scrub_text("Cookie: session_id=abc123XYZdefSESSION456789")
    assert "abc123XYZdefSESSION456789" not in out
    assert "<REDACTED_COOKIE>" in out


def test_scrubs_auth_json_blob() -> None:
    """JSON-form auth tokens use quoted keys; the value can be short
    (e.g., `ya29.foo`) so length-gated patterns won't catch them.
    The auth-JSON pattern matches by key name, not by value length."""
    out = scrub_text('{"access_token":"ya29.foo","refresh_token":"1//bar"}')
    assert "ya29.foo" not in out
    assert "1//bar" not in out
    assert "<REDACTED_AUTH_JSON>" in out


def test_scrubs_auth_json_with_id_token_and_auth_token_keys() -> None:
    out = scrub_text('{"id_token":"eyJ.foo","auth_token":"abc"}')
    assert "eyJ.foo" not in out
    assert '"abc"' not in out
    assert "<REDACTED_AUTH_JSON>" in out


def test_scrubs_password_assignment() -> None:
    out = scrub_text("password=hunter2_supersecret")
    assert "hunter2_supersecret" not in out
    assert "<REDACTED_PASSWORD>" in out


def test_scrubs_long_base64_blob() -> None:
    # 60+ char base64-looking string is suspicious — mask defensively.
    out = scrub_text("token=YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU2Nzg5QUJDREVGRw==")
    assert "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU2Nzg5QUJDREVGRw==" not in out
    assert "<REDACTED_BASE64>" in out


def test_preserves_non_secret_text() -> None:
    sample = "Hello, this is a regular log line with no secrets. score=0.5"
    assert scrub_text(sample) == sample


def test_case_insensitive_matching() -> None:
    out_lower = scrub_text("authorization: bearer abc123")
    out_upper = scrub_text("AUTHORIZATION: BEARER abc123")
    assert "abc123" not in out_lower
    assert "abc123" not in out_upper


def test_does_not_scrub_pure_hex_digests() -> None:
    """Hex digests (SHA-256, git commit SHAs, etc.) share the [A-Za-z0-9+/]
    alphabet but are NOT base64 — they must survive scrubbing so the
    fixture_manifest_hash and similar audit values stay readable in traces."""
    sha256_hex = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    out = scrub_text(f"fixture_manifest_hash: {sha256_hex}")
    assert sha256_hex in out  # NOT redacted
    assert "<REDACTED_BASE64>" not in out


def test_scrubs_home_path_windows_forward_slash_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, USERPROFILE is C:\\Users\\name but logs often render
    paths as C:/Users/name (pathlib.Path str form). Both must scrub."""
    monkeypatch.setenv("ARENA_SCRUB_HOME_PATHS", "1")
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\scott")
    out = scrub_text("loaded C:/Users/scott/.kaggle/kaggle.json")
    assert "C:/Users/scott" not in out
    assert "<HOME>" in out


def test_quoted_api_key_preserves_closing_quote() -> None:
    """Cosmetic but matters for log parsers: the closing quote on
    `api_key="sk-..."` must survive scrubbing."""
    out = scrub_text('api_key="sk-1234567890abcdefghij"')
    assert out.startswith('api_key="')
    assert out.endswith('"')
    assert "<REDACTED_API_KEY>" in out
    assert "sk-1234567890abcdefghij" not in out


def test_scrubs_long_base64_blob_includes_padding() -> None:
    """The {0,2} padding must be inside the match; previously the trailing
    \\b dropped it."""
    out = scrub_text("token=YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU2Nzg5QUJDREVGRw==")
    assert "==" not in out  # padding swallowed too
    assert "<REDACTED_BASE64>" in out
