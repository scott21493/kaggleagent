# Phase 0 Implementation DAG — Design

Status: approved (user-approved 2026-04-30)
Version: `phase0-impl-dag-v1.0`
Owner: Scott
Implementer: Claude Code (sequential now; subagent-driven parallel runs as a future option)

---

## 1. Purpose

Decompose the remaining Phase 0 work (Issues 4–12 of [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §8 plus a missing Issue 0 controller skeleton) into a directed acyclic graph of vertically-sliced PRs that:

- run end-to-end on the local fake fixture by the end of PR1;
- accumulate observable behavior every PR;
- expose explicit fan-out points for future subagent-driven parallel runs without rework;
- close Phase 0 by hitting all 15 closure conditions in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §1.2.

This document is the authoritative DAG. Each PR will get its own implementation plan via the `superpowers:writing-plans` skill, beginning with PR1 immediately after this spec is approved.

---

## 2. Decisions baked in

These were resolved during brainstorming and are not revisited here:

1. **Workforce:** Claude Code performs all implementation. Sequential by default. Subagent-driven parallel runs are reserved for the two designated fan-out layers (PR3+PR4, PR5+PR6).
2. **Strategy:** Vertical slices, not horizontal layers. Every PR ends with a demonstrably better end-to-end harness.
3. **Phase 0 close scope:** Stub-only is what CI gates. Real Codex/Claude adapters are skeletal — enough to attempt the happy path locally — but full auth/health/runbook polish defers to Phase 1.
4. **Granularity:** ~7 functional vertical slices (Approach B). ~1–3 hour Claude Code session per slice. PR0 preflight is separate.
5. **TDD:** Tests land in the same commit as the code under test. Coverage gate temporarily relaxed (50%) during PR1–6, restored to 70% in PR7.

---

## 3. The DAG

```
PR0 (preflight)
  │
  ▼
PR1 (the spine)               ← stub end-to-end loop runs here
  │
  ▼
PR2 (the cap)                 ← budget + kill switch + waste
  │
  ├──────────┐
  ▼          ▼
PR3 (moat)   PR4 (eyes)       ← parallel-safe (disjoint module trees)
  │          │
  └─────┬────┘
        ▼
        ├──────────┐
        ▼          ▼
       PR5         PR6        ← parallel-safe (disjoint module trees)
        │          │
        └─────┬────┘
              ▼
            PR7                ← real adapters + Phase 0 close
```

Sequential: 8 PRs (including PR0). Wall-clock with subagent fan-out at the two parallel layers: ~5 sequential session-equivalents.

---

## 4. PR0 — Preflight (~30 min)

PR0 is a real implementation PR, not "initial commit of current state." The first commit is this spec doc plus existing project files plus `.gitignore`. PR0 follows that as the first behavioral preflight PR.

**What lands:**

- `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` — one-page ADR documenting:
  - `codex exec --json` invocation conventions, stdin shape, stdout/stderr capture, exit-code semantics
  - `claude -p` (or `claude --print`) invocation conventions, stdin shape, stdout/stderr capture
  - Where the scrubber attaches (between subprocess stdout and ProviderResult)
  - How auth-expiry surfaces (non-zero exit + diagnostic stderr → `BLOCKED_AUTH`)
- `fixtures/tabular_binary_v1/paper_bundle/method_note_001.md` and `method_note_002.md` — short trusted method notes referenced by [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §2.1 and §6.1; needed by PR5
- Updated `fixtures/tabular_binary_v1/fixture_manifest.yaml` — adds sha256 entries for the two new method notes
- Issue 0 added to [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §8: "Controller skeleton" with acceptance referencing PR1's contents
- `pyproject.toml` `[tool.coverage.report] fail_under` lowered from 70 → 50 with a TODO comment to restore in PR7

**Already implemented in preflight CI (no PR0 work needed):**

- Security acceptance test 7 from [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §9 (prompt template contains untrusted variable outside delimiter → CI fails) — implemented by [scripts/validate_prompt_delimiters.py](../../../scripts/validate_prompt_delimiters.py) and runs on every push.

**Acceptance:**

- ADR-0004 exists and is referenced by PR1's provider modules.
- Paper bundle files exist and are hashed in the manifest.
- `python scripts/fixture_smoke.py` passes after manifest update.
- Issue 0 appears in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §8 backlog.
- CI passes with the lowered coverage gate.

---

## 5. PR1 — The Spine (~3–4 hours)

The first vertical slice. After PR1, `arena run-next tabular_binary_v1 --provider stub_codex` produces a valid calibration submission, evaluated against `hidden_labels.csv`, and the scoreboard records the experiment.

**What lands:**

- `arena/controller/state.py` — state machine enum matching [task_packet.schema.json](../../../schemas/task_packet.schema.json) `phase` enum; transitions table; `transition()` function
- `arena/controller/task_queue.py` — in-memory FIFO queue with task-packet validation hook
- `arena/controller/planner.py` — creates calibration task packets from a fixed template
- `arena/controller/worktree.py` — minimal: creates a per-experiment workspace directory (no git worktree yet — that's a Phase 1 thing for real competitions)
- `arena/providers/base.py` — `ProviderAdapter` ABC with `invoke(task_packet) -> ProviderResult`
- `arena/providers/stub_codex.py` — returns a deterministic submission CSV (e.g., constant 0.5 predictions, or a tiny logistic regression if scikit-learn is already a dep)
- `arena/providers/stub_claude.py` — returns a deterministic review-shaped result (used in later PRs; in PR1 it's just the skeleton)
- `arena/providers/parser.py` — minimal stdout-to-`ProviderResult` parser
- `arena/scoreboard/store.py` — SQLite store; applies migrations on first connect; CRUD for runs and experiments
- `arena/scoreboard/migrations/0002_extend_experiments_for_design_v2.sql` — adds the 18 fields listed in [KAGGLE_AGENT_ARENA_DESIGN_V2.md](../../architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md) §7
- `arena/schemas/loader.py` and `arena/schemas/validate.py` — load JSON schemas from disk, validate dicts; cached `Draft202012Validator` per schema
- CLI: `arena init-fixture`, `arena plan`, `arena run-next`, `arena evaluate`
- Tests: state-machine transitions; task-queue validation; stub-codex roundtrip; scoreboard persistence; CLI smoke

**Acceptance:**

- `arena init-fixture tabular_binary_v1` initializes the workspace.
- `arena plan tabular_binary_v1` creates a calibration task packet that passes [task_packet.schema.json](../../../schemas/task_packet.schema.json) validation.
- `arena run-next tabular_binary_v1 --provider stub_codex` produces a valid submission.
- `arena evaluate tabular_binary_v1 --latest` reports a deterministic ROC-AUC against hidden labels.
- Scoreboard persists `runs` and `experiments` rows with all design-v2 §7 fields present (most can be `NULL` until later PRs populate them).
- `pytest --cov=arena` passes the (relaxed) coverage gate.

---

## 6. PR2 — The Cap (~1.5–2 hours)

Adds hard ceilings and the kill switch to PR1's loop. Same end-to-end run, now bounded.

**What lands:**

- `arena/budget/policy.py` — load `phase0_hard_ceilings` from environment / `.env.example` defaults
- `arena/budget/governor.py` — per-task and per-run cap accumulators; raises `BudgetExceeded` to the controller
- `arena/budget/kill_switch.py` — file-trigger (`.arena/KILL_SWITCH`) and env-var-trigger (`ARENA_KILL_SWITCH=1`) checks; the 10-breaker enum from [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §4.4
- `arena/budget/waste.py` — repeated-failure detector
- `arena/controller/watchdog.py` — wires governor + kill switch into the controller's per-task loop
- CLI: `arena kill`, `arena unkill --human-confirm`, `arena budget status`
- Tests: each breaker can trip; kill switch halts a running task; `unkill` requires human-confirm

**Acceptance:**

- A misbehaving stub provider that emits 100 shell command events trips `ShellCommandBreaker` and the run halts with a structured event.
- Touching `.arena/KILL_SWITCH` halts in-flight tasks within one polling interval.
- `arena budget status` reports current accumulators against the configured ceilings.

---

## 7. PR3 — The Moat (~2–3 hours, parallel-safe with PR4)

Sandbox + secrets + network deny. Defends the loop from PR2.

**What lands:**

- `arena/sandbox/policy.py` — load policy: blocked path globs, allowed network domains (Phase 0: empty), worktree root
- `arena/sandbox/secrets.py` — runtime check: did this provider event reference a blocked path?
- `arena/sandbox/network.py` — deterministic egress monitor (in Phase 0, providers run as subprocesses; the monitor watches their reported events and rejects unapproved domains)
- `arena/sandbox/runner.py` — subprocess wrapper that confines the working directory, sets read/write whitelists, scrubs environment of credentials
- Replace [scripts/static_sandbox_policy_check.py](../../../scripts/static_sandbox_policy_check.py) with a real driver: it runs the sandbox runner against a stub that attempts each forbidden action and asserts the breaker fires
- Implement security acceptance tests 1–4 and 6 from [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §9

**Acceptance:**

- Stub provider simulating read of `~/.kaggle/kaggle.json` triggers `SecretAccessBreaker`.
- Stub provider simulating `curl https://example.com` triggers `NetworkEgressBreaker`.
- Stub provider attempting to write outside its worktree triggers `ProtectedFileBreaker`.
- `static_sandbox_policy_check.py` SCAFFOLDING comment is removed; the script is now a real driver.

**Disjoint from PR4:** PR3 touches only `arena/sandbox/` plus `scripts/static_sandbox_policy_check.py` and tests under `tests/sandbox/`. No overlap with PR4.

---

## 8. PR4 — The Eyes (~2–3 hours, parallel-safe with PR3)

Observability + replay + scrubber expansion + provider-version baselining.

**What lands:**

- `arena/observability/events.py` — structured event emitter; validates against [event.schema.json](../../../schemas/event.schema.json); per-event-type payload helpers
- `arena/observability/trace_store.py` — append-only JSONL per run/task at `traces/<run_id>/<task_id>/`
- `arena/observability/scrubber.py` — extends [arena/security/scrubber.py](../../../arena/security/scrubber.py) to all 11 categories from [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §6.7. Move scrubber from `arena/security/` to `arena/observability/scrubber.py`; update [tests/test_scrubber.py](../../../tests/test_scrubber.py) import in the same PR. The `arena/security/` package is dropped (it had only the scrubber).
- `arena/observability/replay.py` — given a `<run_id>`, reconstructs scoreboard view from traces; verifies hashes
- `arena/observability/report.py` — markdown run report renderer
- `arena/observability/version_baseline.py` — record stub provider versions on first run; flag drift via `provider_version_recorded` event and `PROVIDER_VERSION_CHANGED` status (real-provider extension lands in PR7)
- CLI: `arena replay <run_id>`, `arena report <competition_slug>`
- Replace [scripts/check_migrations.py](../../../scripts/check_migrations.py) with a real driver that applies migrations to a temp SQLite DB and verifies idempotency on empty + populated DBs (per [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §6.6)
- Implement security acceptance tests 5, 8, 9, 10 from §9 (repeated failure breaker, scrubber masks fake token, fixture hash drift detected, provider version drift flagged)

**Acceptance:**

- `arena replay <run_id>` reconstructs scoreboard from `traces/` deterministically.
- Provider stdout containing a fake bearer token is scrubbed in the recorded trace.
- Stub provider version baseline is captured on first run; subsequent run with a different version flags `PROVIDER_VERSION_CHANGED`.
- `check_migrations.py` SCAFFOLDING comment is removed.

**Disjoint from PR3:** PR4 touches only `arena/observability/` plus `scripts/check_migrations.py` and tests under `tests/observability/`. No overlap with PR3.

---

## 9. PR5 — Research-Fusion Proxy (steps 1–8) (~2 hours, parallel-safe with PR6)

Bounded research-fusion proxy implementing the first 8 of the 10 steps in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §6.2. Steps 9–10 (review + memory proposal) are owned by PR6. The complete 10-step loop is proven by PR7.

**What lands:**

- `arena/research_proxy/question_generator.py` — emits research-question task packets; validates against [research_question.schema.json](../../../schemas/research_question.schema.json)
- `arena/research_proxy/method_digest.py` — invokes stub_claude on a method note; validates output against [paper_digest.schema.json](../../../schemas/paper_digest.schema.json)
- `arena/research_proxy/fusion_proposal.py` — invokes stub_claude to produce a fusion proposal; validates against [fusion_proposal.schema.json](../../../schemas/fusion_proposal.schema.json)
- `arena/research_proxy/fusion_scorer.py` — deterministic risk/cost/fit score; gate on minimum score before queueing the proxy implementation
- Stub Claude extended in `arena/providers/stub_claude.py` to emit valid `paper_digest.json` and `fusion_proposal.json` payloads when given the matching task role
- Stub Codex extended in `arena/providers/stub_codex.py` to implement the proxy test from a fusion proposal
- CLI: `arena research-proxy`
- Tests covering each of steps 1–8 individually

**Acceptance:**

- `arena research-proxy tabular_binary_v1 --provider stub_claude` runs steps 1 → 8 of §6.2 against `paper_bundle/method_note_001.md`, producing valid digest, fusion proposal, and proxy implementation artifacts.
- All fusion proposals satisfy the eligibility checklist in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §6.3.
- Scoreboard records the proxy result linked to the fusion_id.
- PR5 explicitly does NOT close the loop with review or memory proposal — that's PR6.

**Disjoint from PR6:** PR5 touches `arena/research_proxy/`, extends both stub providers, adds the `research-proxy` CLI command. PR6 owns `arena/memory/`, `arena/self_improvement/`, and the `review`/`memory propose`/`self-improve` CLI commands. No overlap.

---

## 10. PR6 — Reviews + Memory + Self-Improvement Freeze (~2–3 hours, parallel-safe with PR5)

Review flow + memory proposal flow + self-improvement scan with freeze gate.

**What lands:**

- `arena/memory/proposal.py` — propose, validate (against [memory_update.schema.json](../../../schemas/memory_update.schema.json) including the `prior_claim` conditional), persist
- `arena/memory/validator.py` — evidence/delta/contradiction checks
- `arena/memory/diff.py` — render proposed deltas against the unified memory wiki
- `arena/self_improvement/scan.py` — scans recent runs for self-improvement triggers
- `arena/self_improvement/proposal.py` — emits `self_improvement_proposal.json` validated against schema
- `arena/self_improvement/freeze.py` — applies freeze rules from [SECURITY_COST_REPRODUCIBILITY_SPEC.md](../../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §7.3
- `arena/self_improvement/champion_challenger.py` — fixture-based comparison harness (placeholder champion = PR1's calibration)
- CLI: `arena review`, `arena memory propose`, `arena self-improve scan`
- Replace [scripts/validate_memory_examples.py](../../../scripts/validate_memory_examples.py) with a proper test suite (covering all four `operation` paths and contradiction detection)

**Acceptance:**

- Stub Claude can produce a `review.json` (validated against [review.schema.json](../../../schemas/review.schema.json)).
- Memory proposals require evidence + delta; contradictory proposals are flagged not auto-merged.
- Self-improvement scan reports findings and writes `self_improvement_proposal.json` artifacts but never auto-applies.
- Freeze triggers when a synthesized challenger regresses score / cost / safety beyond thresholds.

**Disjoint from PR5:** PR6 touches `arena/memory/`, `arena/self_improvement/`, plus `arena/review/` if a review module is needed (otherwise review is a thin CLI wrapper around stub_claude). No overlap with PR5.

---

## 11. PR7 — Real adapters + Phase 0 close (~2–3 hours)

Skeletal real Codex/Claude adapters + the complete 10-step research-proxy loop + Phase 0 acceptance suite.

**What lands:**

- `arena/providers/codex.py` — subprocess wrapper for `codex exec --json` per ADR-0004; captures stdout/stderr; applies scrubber; parses to `ProviderResult`
- `arena/providers/claude.py` — subprocess wrapper for `claude -p` per ADR-0004
- `arena/providers/health.py` — `arena provider health <name>` runs CLI version check + tiny no-op + reports configured sandbox mode
- Provider-version drift detection extended from PR4's `version_baseline.py` to cover real Codex/Claude versions
- CLI: `arena provider health`, `arena eval-harness`, extend `arena doctor` to check provider CLIs
- `pyproject.toml` `fail_under` raised back to 70
- `tests/test_phase0_acceptance.py` — runs the full 15-condition closure suite from [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §1.2 under stub providers
- `tests/test_research_proxy_full_loop.py` — proves the complete 10-step §6.2 loop end-to-end (steps 9–10 use the PR6 review + memory machinery)
- Stub runbook docs at `docs/phase0/runbooks/` for §7.3–7.5 (auth-expiry, reboot, CLI capability regression) — narrative form, not automation

**Acceptance:**

- All 15 closure conditions in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §1.2 pass under stub providers.
- The complete 10-step §6.2 research proxy loop runs end-to-end (stubs).
- `arena provider health codex` and `arena provider health claude` work on a configured local machine; they fail cleanly with `BLOCKED_AUTH` / `BLOCKED_PROVIDER_CAPABILITY` when they should.
- Coverage gate at 70% holds on the merged trunk.

---

## 12. Discipline notes

**TDD:** Tests land in the same commit as the code under test. Aim for 70%+ coverage on new code in every PR; the *gate* runs at 50% during PR1–PR6 to allow for thin spots in plumbing code that's hard to test in isolation. PR7 restores the gate to 70%.

**Schema/spec hygiene:** A PR that updates a schema must update any prompt template, ADR, or doc that references it in the same PR. No schema drift across PRs.

**Scaffolding cleanup discipline:**
- [scripts/static_sandbox_policy_check.py](../../../scripts/static_sandbox_policy_check.py) → real driver in PR3.
- [scripts/check_migrations.py](../../../scripts/check_migrations.py) → real driver in PR4.
- [scripts/validate_memory_examples.py](../../../scripts/validate_memory_examples.py) → proper test suite in PR6.
- The SCAFFOLDING headers come off in those PRs.

**Subagent fan-out:** PR3 and PR4 are designed to be safe to run as parallel subagents after PR2 lands. PR5 and PR6 are designed to be safe to run as parallel subagents after PR4 lands. The boundaries are file-tree disjoint and the schemas they touch are also disjoint. The spine PRs (PR0, PR1, PR2, PR7) must run sequentially.

**Module ownership table:**

| Module tree                | PR  |
|----------------------------|-----|
| `arena/controller/`        | PR1 (state, queue, planner, worktree) + PR2 (watchdog) |
| `arena/providers/`         | PR1 (base, stubs, parser) + PR5 (stub extensions) + PR7 (real adapters, health) |
| `arena/scoreboard/`        | PR1 |
| `arena/schemas/`           | PR1 |
| `arena/budget/`            | PR2 |
| `arena/sandbox/`           | PR3 |
| `arena/observability/`     | PR4 |
| `arena/research_proxy/`    | PR5 |
| `arena/memory/`            | PR6 |
| `arena/self_improvement/`  | PR6 |
| Top-level CLI in `arena/cli.py` | grows in every PR; conflict-likely; merge order matters |

`arena/cli.py` is the one file every PR touches. Subagents working on parallel PRs must coordinate their CLI command additions or rebase carefully.

---

## 13. Estimates

| PR  | Topic                                | Sequential effort | Parallel-eligible |
|-----|--------------------------------------|-------------------|-------------------|
| PR0 | Preflight                             | ~30 min           | no                |
| PR1 | The spine                             | 3–4 hours         | no                |
| PR2 | Budget + kill switch                  | 1.5–2 hours       | no                |
| PR3 | Sandbox + secrets + network           | 2–3 hours         | yes (with PR4)    |
| PR4 | Observability + replay + scrubber     | 2–3 hours         | yes (with PR3)    |
| PR5 | Research proxy steps 1–8              | 2 hours           | yes (with PR6)    |
| PR6 | Review + memory + SI freeze           | 2–3 hours         | yes (with PR5)    |
| PR7 | Real adapters + Phase 0 close         | 2–3 hours         | no                |

**Totals:**

- Sequential (one Claude Code agent at a time): 15.5–21 hours
- With subagent fan-out at the two parallel layers: ~10–13 wall-clock hours
- End-to-end stub-only fixture run: working by end of PR1 (~3.5–4.5 hours into the project)

---

## 14. Out of scope (explicitly deferred)

- Real Kaggle competitions and submissions.
- Full paper ingestion / arXiv crawling / web fetch.
- Multiple adapter abstraction (vision/NLP/etc).
- Auth-expiry / reboot / CLI-regression *automation* (only narrative runbooks in PR7).
- LangChain/LangGraph or any model-API fallback.
- GitHub remote setup. Local git only for Phase 0.
- A `.github/CODEOWNERS`, `CONTRIBUTING.md`, or any release process.

These are listed in [ADR-0001-PHASE0-SCOPE.md](../../architecture/ADR-0001-PHASE0-SCOPE.md) and remain deferred.

---

## 15. Acceptance for this DAG

The DAG is "done" when:

- Every PR listed above has been merged to trunk.
- All 15 closure conditions in [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §1.2 pass on a clean clone via `pytest && python scripts/fixture_smoke.py`.
- The three SCAFFOLDING-marked scripts have been replaced.
- Coverage gate is at 70%.
- A run report from `arena report tabular_binary_v1` shows: stub-only fixture loop completed, calibration recorded, research-proxy completed full 10-step loop, no safety violations, no breaker trips beyond expected test cases.

---

## 16. Next step

Invoke the `superpowers:writing-plans` skill on this spec to produce a detailed implementation plan for **PR1 (The Spine)** — that is the first behavioral PR after the spec commit and PR0 preflight.

Future PR plans will be produced lazily from this same spec as each predecessor lands.
