from __future__ import annotations

import os
import re

# Patterns ordered most-specific first; later patterns avoid scrubbing inside
# already-redacted text by matching against the pre-redaction haystack only
# once per category.

_SSH_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

_KAGGLE_JSON = re.compile(r'(?i)\{?\s*"username"\s*:\s*"[^"]+"\s*,\s*"key"\s*:\s*"[^"]+"\s*\}?')

# JSON-form auth tokens: `"access_token":"foo"`, `"refresh_token":"bar"`,
# `"id_token":"..."`, `"auth_token":"..."`. Matches by key name (not value
# length) so short values like `ya29.foo` are also scrubbed. Replaces the
# whole `"key":"value"` segment with the redaction marker.
_AUTH_JSON_TOKEN = re.compile(r'(?i)"(access|refresh|id|auth)_token"\s*:\s*"[^"]*"')

# Bare `access_token=...` (form-encoded / shell). Length-gated so
# arbitrary 5-char strings don't get masked. Capture opening and closing quotes
# separately so they survive the substitution.
_OAUTH_TOKEN = re.compile(r"(?i)(access_token\s*[=:]\s*)(\"?)[A-Za-z0-9._\-/+]{20,}(\"?)")

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/\-]+=*")

# Capture opening and closing quotes separately so they survive the substitution.
_API_KEY = re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)(\"?)[A-Za-z0-9._~+/\-]{12,}(\"?)")

_PASSWORD_KV = re.compile(r"(?i)(password\s*[=:]\s*)\S+")

_DB_URL = re.compile(r"(?i)([a-z0-9+]+://[^:/\s]+:)[^@/\s]+(@[^/\s]+)")

_COOKIE = re.compile(r"(?i)(cookie\s*:\s*[^=\s]+\s*=\s*)[A-Za-z0-9._\-+/]{12,}")

# A long contiguous base64-looking blob with no obvious english structure.
# 50+ chars of [A-Za-z0-9+/=] — defensive last-resort scrub for accidental
# token leakage in provider stdout. Note: the trailing \b is removed to ensure
# the padding (={0,2}) is inside the match, not dropped by the word boundary.
_BASE64ISH = re.compile(r"\b[A-Za-z0-9+/]{50,}={0,2}")


def _is_likely_base64(matched: str) -> bool:
    """Distinguish true base64 from hex digests / alphanumeric strings.

    The _BASE64ISH character class `[A-Za-z0-9+/]` accidentally subsumes
    pure hex `[0-9a-f]`, so SHA-256 digests (64 hex chars) and git commit
    SHAs (40 hex chars, but length-gated already) would otherwise be
    redacted, hurting trace auditability for the very fixture_manifest_hash
    PR4 records. True base64 almost always contains at least one of `+`,
    `/`, or `=` padding; hex never does. Require one to redact.
    """
    return any(ch in matched for ch in "+/=")


def _scrub_home_paths(text: str) -> str:
    """Optional: replace the user's home directory with `<HOME>`.

    Off by default. Enabled by setting `ARENA_SCRUB_HOME_PATHS=1` (or `true`).
    Reads `$HOME` (POSIX) or `%USERPROFILE%` (Windows) as the path to redact.

    On Windows, also replaces the forward-slash normalized form because
    pathlib.Path and many Python loggers render Windows paths with `/`
    rather than `\\`.
    """
    if os.environ.get("ARENA_SCRUB_HOME_PATHS", "").lower() not in {"1", "true", "yes"}:
        return text
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not home:
        return text
    out = text.replace(home, "<HOME>")
    home_fwd = home.replace("\\", "/")
    if home_fwd != home:
        out = out.replace(home_fwd, "<HOME>")
    return out


def scrub_text(text: str) -> str:
    """Mask all 11 categories from SECURITY_COST_REPRODUCIBILITY_SPEC.md §6.7.

    Order matters: SSH private keys (multi-line) first; then structured pairs
    (kaggle_json, oauth_token, bearer, api_key); then assignment forms
    (password, cookie); then the catch-all base64ish.
    """
    out = text
    out = _SSH_PRIVATE_KEY.sub("<REDACTED_SSH_PRIVATE_KEY>", out)
    out = _KAGGLE_JSON.sub("<REDACTED_KAGGLE_JSON>", out)
    # Auth JSON keys (access_token, refresh_token, id_token, auth_token) come
    # BEFORE the bare-form _OAUTH_TOKEN so we don't double-substitute on
    # `"access_token":"..."`.
    out = _AUTH_JSON_TOKEN.sub("<REDACTED_AUTH_JSON>", out)
    out = _OAUTH_TOKEN.sub(r"\1\2<REDACTED_OAUTH_TOKEN>\3", out)
    out = _BEARER.sub(r"\1<REDACTED_TOKEN>", out)
    out = _API_KEY.sub(r"\1\2<REDACTED_API_KEY>\3", out)
    out = _DB_URL.sub(r"\1<REDACTED_PASSWORD>\2", out)
    out = _PASSWORD_KV.sub(r"\1<REDACTED_PASSWORD>", out)
    out = _COOKIE.sub(r"\1<REDACTED_COOKIE>", out)
    out = _BASE64ISH.sub(
        lambda m: "<REDACTED_BASE64>" if _is_likely_base64(m.group(0)) else m.group(0),
        out,
    )
    out = _scrub_home_paths(out)
    return out
