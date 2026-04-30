# ADR-0003 — Subscription-Only Provider Limits

Status: accepted  
Date: 2026-04-30

## Decision

Phase 0 remains subscription-first and does not use OpenAI or Anthropic model APIs. Real provider automation is local-only and optional for acceptance. CI uses stub providers.

## Rationale

The project goal is to use Codex and Claude Code subscriptions. However, subscription CLIs can have auth expiry, interactive login requirements, version drift, capability changes, and weekly/session caps. Therefore they cannot be the only basis for unattended CI or long-running proof of correctness.

## Consequences

- Stubs are mandatory.
- Provider health checks are mandatory.
- Auth-expiry runbooks are mandatory.
- No unsafe browser automation.
- No hidden credential scraping.
- No fallback to API billing unless this ADR is replaced.
