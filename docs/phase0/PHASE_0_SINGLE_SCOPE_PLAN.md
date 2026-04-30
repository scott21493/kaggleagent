# Kaggle Agent Arena — Phase 0 Single-Scope Implementation Plan

Status: **Canonical replacement for the prior Phase 0 drafts**  
Version: `phase0-v2.1`  
Date: 2026-04-30  
Decision owner: human project owner  
Scope: local fake Kaggle harness, subscription-only provider wrappers, deterministic controller, bounded research-first proxy loop, no real Kaggle submissions.

---

## 0. Why this document exists

Earlier drafts accidentally described three different Phase 0s:

1. a minimal end-to-end fixture harness,
2. a large advisory planner subsystem,
3. a full research-paper ingestion and method-fusion engine.

This document resolves that contradiction. **There is now exactly one Phase 0.**

Phase 0 is not a production Kaggle bot. It is not a full research engine. It is not multi-adapter. It is a small, measurable, security-conscious harness that proves the core operating model works before real competitions or long-running autonomy.

---

## 1. Phase 0 decision summary

### 1.1 Phase 0 objective

Build a local, repeatable, deterministic-enough agent harness that can run one fake Kaggle-like tabular fixture from task creation through implementation, review, research-fusion proxy, evaluation, memory proposal, and report generation.

### 1.2 Phase 0 must prove

The milestone is complete only when all of these are true:

1. The controller can create task packets from deterministic templates.
2. Codex can be invoked through a provider adapter, or a stub provider can simulate Codex in CI.
3. Claude can be invoked through `claude -p` for bounded advisory/review tasks, or a stub provider can simulate Claude in CI.
4. Provider stdout/stderr is captured, scrubbed, hashed, and replayable.
5. A fake tabular competition fixture can be initialized, evaluated, and scored.
6. At least one calibration baseline task completes.
7. At least one bounded research-fusion proxy task completes.
8. Claude reviews at least one implementation or research-fusion output.
9. The scoreboard records metrics, cost proxies, wall time, artifacts, and provider versions.
10. The usage governor enforces hard call, wall-clock, command-count, and proxy-token ceilings.
11. The kill switch can stop the run without asking an LLM.
12. The sandbox denies access to secrets and blocks unapproved network egress.
13. Memory updates are proposed as deltas, not auto-merged.
14. Self-improvement is blocked unless champion/challenger fixture evaluation passes.
15. CI passes using stub providers without requiring real Codex/Claude authentication.

### 1.3 Phase 0 non-goals

These are explicitly out of scope:

- Real Kaggle competitions.
- Real Kaggle submissions.
- Automatic Kaggle submission.
- Full arXiv, Semantic Scholar, Google Scholar, or web paper ingestion.
- Full paper-to-code automation.
- Vision/NLP/time-series/recommender/multimodal adapters.
- Parallel LLM agents.
- CI jobs using real Codex or Claude subscription credentials.
- API-based model orchestration.
- LangChain/LangGraph as the core harness.
- Auto-merging memory updates.
- Auto-patching protected controller files.

### 1.4 Research-first but bounded

The strategy remains **research-first**, but Phase 0 includes only a tiny proof of that strategy.

Phase 0 includes:

- one calibration baseline,
- one LLM-generated research question set,
- one small local paper bundle or seeded method note,
- one Claude paper-digest or method-digest call,
- one method-fusion proposal,
- one Codex proxy implementation,
- one ablation or mutation suggestion,
- one research memory proposal.

Phase 0 does **not** include a full research-discovery crawler, continuous literature ingestion, or a large research tree.

### 1.5 Known methods policy

Known methods are not the core strategy. In Phase 0, known methods exist only as calibration floors.

The calibration baseline answers:

- Can the harness produce a valid prediction file?
- Can the evaluator score it?
- Can the scoreboard record it?
- Can Claude review it?
- Can a research-fusion proxy beat or explain failure against it?

### 1.6 Single adapter policy

Phase 0 has one fixture and one implicit task shape: `tabular_binary_fixture`.

Do not build a nine-adapter abstraction in Phase 0. Future adapter names may appear only in architecture notes, not as implemented modules.

The first real abstraction should be extracted only after the second distinct competition type ships.

---

## 2. Concrete Phase 0 closure criteria

Phase 0 is closed only when this exact acceptance test passes on a clean clone.

### 2.1 Test fixture

Fixture name: `tabular_binary_v1`

Fixture files:

```text
fixtures/tabular_binary_v1/
  competition.yaml
  rules.md
  train.csv
  test.csv
  sample_submission.csv
  hidden_labels.csv
  paper_bundle/
    method_note_001.md
    method_note_002.md
```

The fixture is intentionally small enough to run without GPU.

### 2.2 Required commands

A clean clone must support:

```bash
arena doctor
arena init-fixture tabular_binary_v1
arena plan tabular_binary_v1
arena run-next tabular_binary_v1 --provider stub_codex
arena review tabular_binary_v1 --provider stub_claude --experiment exp_0001
arena research-proxy tabular_binary_v1 --provider stub_claude
arena run-next tabular_binary_v1 --provider stub_codex
arena evaluate tabular_binary_v1 --latest
arena memory propose tabular_binary_v1
arena self-improve scan tabular_binary_v1
arena report tabular_binary_v1
arena eval-harness tabular_binary_v1 --providers stub
```

The real-provider version is optional for Phase 0 acceptance but should work on a configured local machine:

```bash
arena provider health codex
arena provider health claude
arena run-next tabular_binary_v1 --provider codex
arena review tabular_binary_v1 --provider claude --experiment exp_0001
arena research-proxy tabular_binary_v1 --provider claude
```

### 2.3 Quality target

The fixture must define a deterministic calibration baseline. Phase 0 passes only if:

- at least one valid submission is produced;
- calibration baseline score is recorded;
- research-fusion proxy either improves the fixture score by at least `0.01 AUC` or writes a valid failure analysis explaining why it did not;
- no secret access events occur;
- no blocked-path writes occur;
- no unapproved network egress occurs;
- no panic breaker triggers;
- the run stays below the Phase 0 hard ceilings below.

A score improvement is desirable but not the only pass criterion because the main Phase 0 goal is harness correctness. However, a failure to improve must produce evidence: hypothesis, smallest proxy test, observed result, and next mutation.

### 2.4 Phase 0 hard ceilings

For one full fixture run using real providers:

```yaml
phase0_hard_ceilings:
  provider_calls_total: 12
  codex_calls_total: 6
  claude_calls_total: 6
  wall_clock_minutes_total: 120
  wall_clock_minutes_per_provider_call: 20
  shell_commands_per_task: 35
  failed_shell_commands_per_task: 5
  repeated_same_failure_per_task: 2
  input_context_chars_total: 900000
  output_chars_total: 250000
  network_domains_allowed: []
  kaggle_submissions_allowed: 0
  gpu_jobs_allowed: 0
```

If provider-reported token usage is available, record it. If it is not available, use the local character counters as conservative proxy metrics. The proxy is not a billing estimate; it is a deterministic guardrail.

### 2.5 Panic kill switch

The run must stop immediately if any of these is true:

- `.arena/KILL_SWITCH` exists.
- Environment variable `ARENA_KILL_SWITCH=1` is set.
- A provider attempts to read a blocked secret path.
- A provider attempts unapproved network egress.
- A provider task exceeds wall-clock ceiling.
- A provider task exceeds shell command ceiling.
- The same failed command repeats more than the configured ceiling without file/config changes.
- A task modifies a protected file without an approved self-improvement proposal.
- More than three waste events occur in a single task.
- More than five waste events occur in a full fixture run.

The kill switch is enforced by the deterministic controller and watchdog, not by an LLM.

---

## 3. Phase 0 architecture

### 3.1 Controller rule

The controller is deterministic Python. It does not call an LLM to decide state transitions.

It may invoke LLM-powered provider workers for:

- implementation,
- review,
- advisory planning,
- bounded research-fusion proxy work,
- failure analysis.

The provider output is advisory or artifact-producing. It becomes actionable only after schema validation, budget checks, sandbox checks, and controller policy checks.

### 3.2 Communication model

Models do not talk directly. They communicate through files.

```text
Controller -> task_packet.json -> provider
Provider -> files/reports/stdout/stderr -> controller
Controller -> validation/evaluation/scoreboard
Controller -> compact review packet -> Claude
Claude -> review.json -> controller
Controller -> fix task -> Codex
```

### 3.3 Phase 0 providers

Implemented adapters:

```text
StubCodexProvider
StubClaudeProvider
CodexProvider        # local real provider, optional for CI
ClaudeProvider       # local real provider, optional for CI
```

Future provider placeholders may exist as interface stubs only:

```text
GeminiProvider
KimiProvider
```

They must not be part of Phase 0 acceptance.

### 3.4 Provider roles

```text
IMPLEMENTATION      Codex or stub Codex
REVIEW              Claude or stub Claude
ADVISORY_PLANNING   Claude -p or stub Claude
RESEARCH_PROXY      Claude/Codex or stubs
DETERMINISTIC       controller-only task
FIX                 Codex or stub Codex, created only from a failed validation or review
```

### 3.5 Controller state machine

```text
NEW
  -> FIXTURE_INITIALIZED
  -> PLAN_CREATED
  -> CALIBRATION_TASK_CREATED
  -> CALIBRATION_IMPLEMENTED
  -> CALIBRATION_EVALUATED
  -> CALIBRATION_REVIEWED
  -> RESEARCH_QUESTION_CREATED
  -> METHOD_DIGEST_CREATED
  -> FUSION_PROPOSAL_CREATED
  -> FUSION_PROXY_IMPLEMENTED
  -> FUSION_PROXY_EVALUATED
  -> FUSION_PROXY_REVIEWED
  -> MEMORY_PROPOSAL_CREATED
  -> SELF_IMPROVEMENT_SCAN_COMPLETED
  -> HARNESS_EVAL_COMPLETED
  -> PHASE0_COMPLETE
```

Failure states:

```text
BLOCKED_AUTH
BLOCKED_BUDGET
BLOCKED_SANDBOX
BLOCKED_SCHEMA
BLOCKED_SECRET_ACCESS
BLOCKED_NETWORK
BLOCKED_PROTECTED_FILE
BLOCKED_KILL_SWITCH
BLOCKED_REPRODUCIBILITY
NEEDS_HUMAN
```

### 3.6 Task creation sources

The controller creates tasks from:

1. fixed Phase 0 templates;
2. fixture state;
3. scoreboard state;
4. event triggers;
5. Claude review outputs;
6. bounded advisory recommendations that pass deterministic validation.

The controller does not invent tasks creatively. Creative task ideas come from LLM workers but remain proposals until accepted by policy.

---

## 4. Repository structure for Phase 0

Only this subset is needed for Phase 0.

```text
kaggle-agent-arena/
  README.md
  pyproject.toml
  .pre-commit-config.yaml
  .env.example
  AGENTS.md
  CLAUDE.md
  REVIEW.md

  .github/
    workflows/
      ci.yml

  arena/
    __init__.py
    cli.py

    controller/
      state.py
      task_queue.py
      planner.py
      watchdog.py
      worktree.py
      protected_files.py

    providers/
      base.py
      stub_codex.py
      stub_claude.py
      codex.py
      claude.py
      parser.py
      health.py

    fixture/
      init.py
      evaluator.py
      validator.py
      hashing.py

    research_proxy/
      question_generator.py
      method_digest.py
      fusion_proposal.py
      fusion_scorer.py

    budget/
      policy.py
      governor.py
      kill_switch.py
      waste.py

    sandbox/
      policy.py
      runner.py
      network.py
      secrets.py

    observability/
      events.py
      trace_store.py
      scrubber.py
      replay.py
      report.py

    memory/
      proposal.py
      validator.py
      diff.py

    self_improvement/
      scan.py
      proposal.py
      freeze.py
      champion_challenger.py

    scoreboard/
      store.py
      migrations.py

    schemas/
      loader.py
      validate.py

  schemas/
    task_packet.schema.json
    provider_result.schema.json
    experiment.schema.json
    review.schema.json
    strategist_recommendation.schema.json
    research_question.schema.json
    paper_digest.schema.json
    fusion_proposal.schema.json
    research_node.schema.json
    research_review.schema.json
    memory_update.schema.json
    self_improvement_proposal.schema.json
    event.schema.json
    usage_snapshot.schema.json

  prompts/
    claude_strategy_prompt.md.j2
    claude_paper_digest_prompt.md.j2
    claude_research_fusion_prompt.md.j2

  fixtures/
    tabular_binary_v1/
      competition.yaml
      rules.md
      train.csv
      test.csv
      sample_submission.csv
      hidden_labels.csv
      paper_bundle/
        method_note_001.md
        method_note_002.md

  docs/
    phase0/
      PHASE_0_SINGLE_SCOPE_PLAN.md
    security/
      SECURITY_COST_REPRODUCIBILITY_SPEC.md
    memory/
      UNIFIED_MEMORY_WIKI.md
    architecture/
      ADR-0001-PHASE0-SCOPE.md
      ADR-0002-CONTROLLER-AND-PROVIDERS.md
```

---

## 5. CLI contract

### 5.1 Required Phase 0 commands

```bash
arena doctor
arena provider health <provider>
arena init-fixture <fixture_name>
arena plan <competition_slug>
arena run-next <competition_slug> [--provider <provider>]
arena review <competition_slug> --experiment <experiment_id> [--provider <provider>]
arena research-proxy <competition_slug> [--provider <provider>]
arena evaluate <competition_slug> --latest
arena memory propose <competition_slug>
arena self-improve scan <competition_slug>
arena report <competition_slug>
arena eval-harness <competition_slug> --providers stub|real
arena budget status
arena kill
arena unkill --human-confirm
```

### 5.2 Commands explicitly not in Phase 0

```bash
arena kaggle submit
arena research discover-web
arena research crawl-arxiv
arena research crawl-kaggle-discussions
arena adapter add vision
arena adapter add nlp
arena run-queue --parallel
```

---

## 6. Research-first proxy loop in Phase 0

### 6.1 Inputs

The research proxy uses local, trusted, pre-seeded method notes or paper abstracts under:

```text
fixtures/tabular_binary_v1/paper_bundle/
```

No live paper crawling happens in Phase 0.

### 6.2 Research proxy steps

```text
1. Controller writes a research question task.
2. Claude -p or stub Claude proposes 3-5 research questions.
3. Controller validates schema and filters unsafe or irrelevant suggestions.
4. Claude -p digests one local method note.
5. Claude -p proposes one method fusion.
6. Controller scores the fusion deterministically for risk/cost/fit.
7. Codex implements the smallest proxy test.
8. Controller evaluates the proxy.
9. Claude reviews the result and proposes one mutation or stop condition.
10. Controller writes a memory update proposal, not an auto-merge.
```

### 6.3 Research proxy acceptance

A fusion proposal is eligible only if it has:

- two or more mechanisms being combined;
- a task-fit explanation;
- smallest proxy test;
- ablation plan;
- resource estimate;
- risk list;
- stop condition;
- schema-valid output;
- no external data dependency;
- no forbidden network dependency;
- no untrusted code import.

### 6.4 Research budget ratchet

The prior 55% to 70% research-budget ratchet is **not Phase 0**.

Post-Phase 0, research allocation must be bidirectional:

Increase research allocation only if, over the last 10 completed experiments:

- at least two research-fusion tasks beat calibration or current best by the configured threshold, or
- one research-fusion task materially improves validation reliability or resource efficiency, and
- waste events remain below threshold.

Decrease research allocation if, over the last 10 completed experiments:

- no research-fusion task improves score or reliability,
- repeated failure count exceeds threshold,
- cost per useful artifact exceeds threshold,
- or self-improvement freeze is active.

---

## 7. Auth-expiry and subscription runbook

### 7.1 Core principle

Subscription-authenticated CLI tools are treated as local interactive tools with cached login. They are not assumed to be reliable for unattended CI.

CI must use stub providers only.

### 7.2 Provider health check

Before every real-provider task:

```text
1. Run provider health command.
2. Verify CLI exists.
3. Verify version is allowed.
4. Verify login/session is available.
5. Verify configured sandbox mode is available.
6. Verify provider can perform a tiny no-op or status command.
7. If any check fails, enter BLOCKED_AUTH or BLOCKED_PROVIDER.
```

### 7.3 Auth expiry at 03:00 runbook

If Codex or Claude auth expires during a run:

1. Provider wrapper captures the failing exit code/stdout/stderr.
2. Trace scrubber removes tokens, paths, and sensitive material.
3. Controller marks the task `BLOCKED_AUTH`.
4. Controller stops launching new real-provider tasks.
5. Controller continues only deterministic local work already safe to run: scoreboard summaries, report rendering, fixture hash verification.
6. Controller writes `reports/auth_blocked_<timestamp>.md`.
7. If notifications are configured, controller sends a local desktop notification or logs a clear console message.
8. Human runs:

```bash
arena provider login codex
arena provider login claude
arena provider health codex
arena provider health claude
arena resume --from-blocked
```

9. The blocked provider task is not automatically retried more than once.
10. If the retry fails, the controller remains blocked until human intervention.

### 7.4 Machine reboot runbook

On startup:

```bash
arena doctor
arena provider health codex
arena provider health claude
arena resume --dry-run
```

The controller must reconstruct state from:

- event log;
- task queue;
- scoreboard;
- artifact manifests;
- git status;
- fixture hashes.

Any provider task that was running during reboot is marked `INTERRUPTED_REQUIRES_REVIEW`, not automatically resumed.

### 7.5 CLI capability regression runbook

If a provider removes or tightens a flag, such as non-interactive execution or full-auto style operation:

1. Provider health fails.
2. Controller enters `BLOCKED_PROVIDER_CAPABILITY`.
3. CI remains green using stub providers.
4. Human updates provider adapter or chooses manual mode.
5. No task falls back to unsafe browser automation.
6. No task falls back to model APIs unless the subscription-only policy is explicitly revised by ADR.

---

## 8. Implementation backlog with testable acceptance criteria

### Issue 0 — Controller skeleton

Definition of done:

- `arena/controller/` package exists with `state.py`, `task_queue.py`, `planner.py`, `worktree.py`.
- `Phase` enum mirrors `task_packet.schema.json` `phase` enum exactly; `transition(src, dst)` raises on disallowed edges.
- `TaskQueue` is a file-backed FIFO that schema-validates packets on enqueue.
- `create_calibration_task_packet(...)` returns a deterministic, schema-valid task packet whose `role=implementation` and `phase=CALIBRATION_TASK_CREATED`.
- `create_workspace(worktree_root, slug, exp_id)` is idempotent.
- Tests cover state transitions, queue FIFO behavior + persistence, planner schema validity, and worktree idempotency.
- Implemented as part of PR1 ("The Spine") of the [implementation DAG](../superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md) §5; see [PR1 plan](../superpowers/plans/2026-04-30-pr1-the-spine.md) Tasks 3-6.

### Issue 1 — Repo scaffold and CI

Definition of done:

- `.github/workflows/ci.yml` exists.
- CI runs on push and PR.
- CI uses stub providers only.
- CI runs `ruff`, `mypy`, coverage-gated `pytest`, Draft 2020-12 schema validation, prompt delimiter validation, fixture smoke test, sandbox static check, migration check, memory proposal validation, and dependency audit.
- CI fails if schemas are invalid JSON Schema.
- CI fails if docs reference missing schema files.

### Issue 2 — Pre-commit

Definition of done:

- `.pre-commit-config.yaml` exists.
- Hooks include ruff format/check, check-yaml, check-json, check-toml, end-of-file, trailing-whitespace, mixed-line-ending, detect-private-key, and detect-secrets.
- `pre-commit run --all-files` passes.

### Issue 3 — Fixture init and evaluator

Definition of done:

- `arena init-fixture tabular_binary_v1` creates a competition directory.
- File hashes are written to `fixture_hashes.json`.
- `hidden_labels.csv` is not exposed in task packets.
- A valid sample submission scores successfully.
- An invalid submission fails with a deterministic error.

### Issue 4 — Provider adapters and stubs

Definition of done:

- `ProviderAdapter` interface exists.
- Stub Codex produces deterministic fixture output.
- Stub Claude produces deterministic review output.
- Real provider adapters are implemented but skipped in CI unless explicitly enabled.
- Provider version is recorded in every task result.

### Issue 5 — Task packets and schema validation

Definition of done:

- `task_packet.schema.json` is canonical.
- Controller writes task packets as JSON.
- Task packets include allowed paths, blocked paths, budgets, required outputs, and success criteria.
- Invalid packets are rejected before provider invocation.

### Issue 6 — Budget governor and kill switch

Definition of done:

- Hard ceilings are configurable.
- `.arena/KILL_SWITCH` stops all new tasks.
- Repeated failure breaker is tested.
- Wall-clock timeout is tested.
- Shell-command-count ceiling is tested via stub provider event stream.

### Issue 7 — Sandbox and secrets boundary

Definition of done:

- Sandbox policy file exists.
- Secret paths are denied.
- Network is denied by default.
- Test attempts to read `.env`, Kaggle credentials, Codex auth, and Claude state are blocked or detected.
- Test attempts unapproved network egress and is blocked or flagged.

### Issue 8 — Observability and replay

Definition of done:

- Structured event log exists.
- Event schema validates.
- Provider stdout/stderr are captured and scrubbed.
- Replay can reconstruct task status and scoreboard state for the fixture.
- Fixture run report includes calls, wall time, command count, waste events, and artifact hashes.

### Issue 9 — Calibration baseline

Definition of done:

- `arena run-next` creates and executes a calibration baseline task.
- Submission is valid.
- Scoreboard records score and artifacts.
- Claude/stub Claude can review the baseline.

### Issue 10 — Bounded research proxy

Definition of done:

- `arena research-proxy` runs over local method notes only.
- Research question, digest, fusion proposal, and research review schemas validate.
- Fusion proposal includes smallest proxy test and ablation plan.
- Codex/stub Codex implements proxy or writes valid failure analysis.
- Scoreboard links proxy result to fusion proposal.

### Issue 11 — Unified memory proposal system

Definition of done:

- Single `MEMORY_WIKI.md` exists.
- Memory updates are proposed as structured deltas.
- Every factual claim requires source/evidence/confidence.
- No memory update auto-merges solely on schema validity.
- Contradictory claims are flagged for human review.

### Issue 12 — Self-improvement freeze

Definition of done:

- Self-improvement proposals are generated only after evidence.
- Protected files require human approval.
- Champion/challenger fixture evaluation runs before accepting harness changes.
- Freeze triggers when challenger regresses success, cost, score, or safety metrics.

---

## 9. Canonical schemas policy

The on-disk JSON schemas under `schemas/` are canonical.

Inline YAML snippets in docs are illustrative only and must say so. They must not define alternative required fields or naming.

All schemas must include:

```json
{
  "schema_version": "..."
}
```

All object schemas must set:

```json
{
  "additionalProperties": false
}
```

unless the schema includes an explicit `extensions` object designed for future expansion.

---

## 10. Memory policy

There is one memory wiki:

```text
docs/memory/UNIFIED_MEMORY_WIKI.md
```

It uses namespaces:

```text
invariants/
codebase/
research/
experiments/
sources/
self_improvement/
```

Memory updates are never auto-merged because schema validity is not truth.

A memory proposal must include:

- claim being added/changed/removed;
- exact prior claim if any;
- delta from prior claim;
- evidence path or citation;
- confidence;
- expiration/revisit condition;
- reviewer decision.

---

## 11. Threat model summary

Agents are treated as untrusted code-writing workers operating on untrusted text.

The harness assumes:

- papers can contain prompt injection;
- Kaggle discussions can contain prompt injection;
- README files can contain prompt injection;
- generated code can try to read secrets accidentally or maliciously;
- provider CLIs can drift versions or capabilities;
- subscription auth can expire;
- model output can violate schemas;
- model output can be confidently wrong.

The defense stack is:

1. minimal task packets;
2. untrusted text delimiters;
3. sandboxed worktree;
4. denied secrets;
5. denied network by default;
6. event logging;
7. deterministic validation;
8. schema validation;
9. protected-file policy;
10. kill switch;
11. memory review;
12. champion/challenger gate.

---

## 12. Hardware and environment prerequisites

Target local machine:

- Windows 11 + WSL2 Ubuntu or native Linux;
- RTX 5080 16 GB VRAM or comparable;
- 32 GB RAM minimum, 64 GB recommended;
- 2 TB NVMe minimum;
- Docker or a comparable Linux sandbox mechanism recommended;
- Python 3.11 or 3.12;
- Git and GitHub CLI;
- Kaggle CLI installed but not used for real submissions in Phase 0;
- Codex CLI installed for real-provider local runs;
- Claude Code CLI installed for real-provider local runs.

Phase 0 acceptance must also pass on a CPU-only CI runner with stub providers.

---

## 13. Final Phase 0 answer to the scope question

**Is research-first in Phase 0?**

Yes, but only as a bounded local proxy loop. The full research-paper ingestion engine is not Phase 0.

**What closes Phase 0?**

A fixture run that proves the controller, provider wrappers, budget governor, sandbox, tracing, calibration baseline, bounded research-fusion proxy, review, memory proposal, and self-improvement freeze all work under hard ceilings.

