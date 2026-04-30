# Response to Consolidated Feedback

Status: resolved in V2 pack  
Date: 2026-04-30

---

## 1. Pick one Phase 0

Decision: **one Phase 0 only**.

Phase 0 is now: local fake tabular fixture + deterministic controller + stub providers + optional real Codex/Claude providers + hard guardrails + one calibration baseline + one bounded research-fusion proxy.

Full research-paper ingestion is not Phase 0.

Resolved in:

- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md`
- `docs/architecture/ADR-0001-PHASE0-SCOPE.md`
- `docs/architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md`

---

## 2. Subscription-only automation story

Decision: real providers are local-only and optional for acceptance; CI uses stubs.

Added:

- provider health checks;
- auth-expiry runbook;
- reboot runbook;
- CLI capability regression runbook;
- no unsafe browser automation;
- no hidden credential scraping;
- no fallback to model APIs without ADR change.

Resolved in:

- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §7
- `docs/architecture/ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md`
- `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md`

---

## 3. Hard cost cap, kill switch, and circuit breakers

Decision: soft throttles are insufficient. Phase 0 now has hard ceilings and breaker-triggered stops.

Added:

- provider-call cap;
- wall-clock cap;
- shell-command cap;
- failed-command cap;
- waste-event cap;
- input/output character caps;
- `.arena/KILL_SWITCH`;
- `ARENA_KILL_SWITCH=1`;
- breaker event schema;
- panic kill behavior.

Resolved in:

- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §2.4-2.5
- `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md` §4

---

## 4. Threat model for agents

Decision: agents are untrusted code-writing workers processing untrusted text.

Added:

- sandbox boundary;
- dedicated OS user guidance;
- secret path deny list;
- network deny by default;
- Kaggle/Codex/Claude credential handling;
- prompt-injection delimiters;
- prompt-template CI checks;
- security acceptance tests.

Resolved in:

- `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md`
- `prompts/claude_paper_digest_prompt.md.j2`
- `prompts/claude_research_fusion_prompt.md.j2`

---

## 5. Determinism engineered, not asserted

Decision: determinism is implemented through replay, hashes, version capture, schema migrations, and scrubbed event logs.

Added:

- provider version recording;
- fixture hash manifest;
- provider stdout/stderr capture;
- scrubbed replay;
- event log types;
- schema migration policy;
- log scrubber requirements;
- provider version drift flags.

Resolved in:

- `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md` §6

---

## 6. Schema and prompt fixes

Decision: on-disk JSON schemas are canonical.

Fixed:

- `schema_version` added to all schemas;
- `additionalProperties: false` applied consistently;
- `fusion_proposal.implementation_plan` has concrete inner shape;
- `smallest_proxy_test` encoded in schema;
- `research_review.schema.json` created;
- prompt injection delimiters added;
- inline YAML is illustrative only.

Resolved in:

- `schemas/*.schema.json`
- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §9

---

## 7. Nature citation issue

Decision: remove the questionable 2026 Nature citation from canonical docs.

It is not used in V2 docs. Future citation entries must be verified and entered in the source ledger.

---

## 8. Adapter sprawl

Decision: no nine-adapter implementation in Phase 0.

Only one fixture shape exists in Phase 0: `tabular_binary_v1`.

Resolved in:

- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §1.6
- `docs/architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md` §10

---

## 9. Memory wiki overlap

Decision: one unified memory wiki with namespaces.

Resolved in:

- `docs/memory/UNIFIED_MEMORY_WIKI.md`
- `schemas/memory_update.schema.json`

---

## 10. Auto-merge memory corruption

Decision: no auto-merge on schema validity.

Memory updates require evidence, delta, contradiction check, and review.

Resolved in:

- `docs/memory/UNIFIED_MEMORY_WIKI.md`

---

## 11. Self-improvement getting worse

Decision: self-improvement freezes on score, cost, safety, or replay regression.

Resolved in:

- `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md` §7
- `schemas/self_improvement_proposal.schema.json`

---

## 12. Falsifiable evaluation

Decision: Phase 0 is evaluated by fixture metrics and harness efficiency, not merely process completion.

Metrics:

- valid submission;
- calibration score;
- research-proxy improvement or failure analysis;
- provider calls;
- wall time;
- waste events;
- security violations;
- replay success.

Resolved in:

- `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §2
- `docs/architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md` §11

---

## 13. CI and pre-commit

Added:

- `.github/workflows/ci.yml`
- `.pre-commit-config.yaml`
- `.env.example`

---

## 14. Answers to the ten open questions

1. Phase 0 closes on a local fixture run under hard ceilings, with valid calibration, bounded research proxy, review, memory proposal, no safety violations, and replayable traces.
2. Research-first is in Phase 0 only as a bounded local proxy loop.
3. Auth expiry moves the controller to `BLOCKED_AUTH`; no retry loops; human reauth required.
4. Hard cap is provider calls + wall time + command count + char proxies + kill switch; no USD cap is possible under subscriptions without provider billing telemetry.
5. Sandbox boundary is dedicated local user + provider sandbox/permissions + denied secrets + denied network + worktree-only writes.
6. Self-improvement freezes on challenger regressions in success, score, cost, waste, safety, or replay.
7. One memory wiki with namespaces replaces two overlapping wikis.
8. Nine adapters are future notes only; Phase 0 implements no adapter hierarchy.
9. Research allocation ratchet is post-Phase-0 and bidirectional; escalation requires score/reliability/cost evidence.
10. On-disk JSON schemas are canonical; inline YAML is illustrative only.


---

## V2.1 mechanical audit patch

This patch addresses the follow-up audit items:

- CI heredocs removed; validation now uses script files.
- JSON schemas are validated with `jsonschema.Draft202012Validator.check_schema`.
- Prompt delimiter validation now checks all six untrusted variables and verifies each variable occurrence is inside an `UNTRUSTED_SOURCE` block.
- CI now includes fixture smoke, migration check, sandbox static check, memory proposal validation, coverage-gated pytest, strict mypy, and `pip-audit`.
- `event_type` is now an enum; breaker example fields now live inside `payload`.
- `memory_update` requires `prior_claim` for modify/deprecate/remove operations.
- `task_packet.phase` is now an enum and provider role docs include `FIX`.
- `.env.example` includes all Phase 0 hard ceilings from the security spec.
- Pre-commit adds `detect-secrets` and pins default Python to 3.11.
- README includes a five-line quick start.
- `fixtures/tabular_binary_v1` now exists with toy CSVs and a hash manifest.
- Phase 0 explicitly states that harness value over a bare Codex run is unmeasured until the first post-Phase-0 Codex-alone comparison.
