# ADR-0002 — Deterministic Controller with Advisory LLM Providers

Status: accepted  
Date: 2026-04-30

## Decision

The controller is deterministic Python. LLMs are invoked only through provider adapters as external workers.

Provider roles:

- implementation;
- review;
- advisory planning;
- research proxy;
- failure analysis.

The controller owns:

- task creation;
- state transitions;
- budgets;
- sandbox checks;
- schema validation;
- kill switch;
- merge/reject policy;
- memory proposal validation;
- self-improvement freeze logic.

## Rationale

Letting an LLM become the controller would make routing, budget control, safety enforcement, and reproducibility harder. LLM creativity is valuable, but only as bounded recommendations and artifacts.

## Consequences

- Claude `-p` advisory planning is allowed but advisory-only.
- Recommendations become tasks only after deterministic validation.
- Codex and Claude do not chat directly; they communicate through task packets, reports, reviews, traces, and memory proposals.
