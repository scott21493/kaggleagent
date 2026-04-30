# ADR-0001 — Single Phase 0 Scope

Status: accepted  
Date: 2026-04-30

## Decision

Phase 0 is a single bounded fixture harness, not a full autonomous Kaggle lab.

It includes:

- deterministic controller;
- local fake tabular competition fixture;
- stub providers for CI;
- optional real Codex/Claude local providers;
- hard budget ceilings;
- kill switch;
- sandbox/secrets/network controls;
- calibration baseline;
- bounded local research-fusion proxy;
- review loop;
- unified memory proposal flow;
- self-improvement freeze gate.

It excludes:

- real Kaggle competitions;
- automatic submissions;
- full paper ingestion;
- multi-adapter abstraction;
- parallel agents;
- model APIs;
- LangChain/LangGraph core orchestration.

## Rationale

The prior plan mixed three scopes and could not be implemented predictably. This ADR makes the milestone measurable, secure, and CI-testable.

## Consequences

- Research-first remains strategic, but Phase 0 proves it with one small local proxy loop.
- Known methods are calibration only.
- Future adapters wait until at least two real task types exist.
- Old Phase 0 documents are superseded by `PHASE_0_SINGLE_SCOPE_PLAN.md`.
