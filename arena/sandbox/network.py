from __future__ import annotations

from urllib.parse import urlparse

from arena.sandbox.policy import SandboxPolicy


def _hostname(url: str) -> str:
    """Extract the hostname (without port) from a URL.

    Returns an empty string for malformed/no-scheme URLs — callers treat
    that as a deny because there is no way to verify intent.
    """
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def is_unapproved_egress(url: str, policy: SandboxPolicy) -> bool:
    """True if egressing to `url` would breach the policy's allowed_network_domains.

    Phase 0 default is empty allowlist → every URL is unapproved.

    Hostname matching is EXACT — an allowlist entry of `example.com` does NOT
    cover `api.example.com`. Wildcard / suffix matching is intentionally not
    supported in Phase 0; callers must enumerate every allowed subdomain.
    """
    host = _hostname(url)
    if not host:
        # No discoverable hostname — fail closed.
        return True
    return host not in policy.allowed_network_domains
