# Kaggle Agent Arena — Design V2

Status: canonical architecture direction after Phase 0 scope correction  
Version: `design-v2.0`  
Date: 2026-04-30

---

## 1. Project goal

Build a reusable local agent harness that uses subscription-authenticated coding agents to compete in Kaggle-style machine learning competitions.

The long-term strategy is research-first:

- use LLMs heavily for research question generation;
- digest papers and solution writeups;
- extract mechanisms;
- combine methods from related papers;
- run smallest proxy tests;
- mutate and ablate ideas until they improve;
- use known methods primarily as calibration baselines, not the main innovation engine.

The Phase 0 implementation is intentionally smaller: it proves one bounded local research-fusion loop on a fake tabular fixture.

---

## 2. Non-negotiable invariants

1. Real Kaggle submissions require human approval.
2. Unknown rule status means blocked.
3. The controller is deterministic.
4. LLMs are provider workers, not the controller.
5. CI uses stub providers, not real subscription auth.
6. Provider output is untrusted until validated.
7. Memory updates are proposals, not truth.
8. Secret paths are denied.
9. Network is denied by default in Phase 0.
10. Self-improvement is subject to champion/challenger evaluation.

---

## 3. Architecture overview

```text
Human / CLI
  -> arena deterministic controller
      -> task planner
      -> budget governor / kill switch
      -> sandbox runner
      -> provider adapters
          -> Codex CLI local provider
          -> Claude Code local provider
          -> stub providers for CI
      -> fixture evaluator
      -> scoreboard
      -> trace store
      -> memory proposal system
      -> self-improvement scanner
```

Models communicate through artifacts:

```text
task_packet.json
provider_result.json
experiment_report.md
metrics.json
review.json
fusion_proposal.json
memory_update.json
trace.jsonl
scoreboard.sqlite
```

No direct model-to-model chat is required.

---

## 4. Subscription-only posture

Codex and Claude Code are used as local CLI tools under subscription authentication when running on the local machine.

Because subscription CLIs may require cached login, local sessions, or reauthentication, the system is not designed to require real providers in CI. CI runs stub providers and verifies the harness logic.

Real-provider runs are opt-in local runs after:

```bash
arena provider health codex
arena provider health claude
```

If auth expires, the controller enters a blocked state and waits for human reauthentication.

---

## 5. Phase 0 design

Canonical Phase 0 document:

```text
docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md
```

Phase 0 includes:

- local tabular fixture;
- deterministic controller;
- stub providers;
- optional real Codex/Claude providers;
- calibration baseline;
- bounded research-fusion proxy loop;
- Claude review/advisory planning with `claude -p` where configured;
- hard ceilings and kill switch;
- sandbox and secret blocking;
- structured traces;
- unified memory proposal flow;
- self-improvement freeze gate.

Phase 0 excludes:

- real Kaggle competitions;
- full paper ingestion;
- multiple adapters;
- parallel agents;
- automatic submission;
- model APIs.

---

## 6. Repo structure

See `PHASE_0_SINGLE_SCOPE_PLAN.md` for the exact Phase 0 tree. Future expansion should preserve:

```text
arena/controller/       deterministic orchestration
arena/providers/        provider wrappers and stubs
arena/budget/           hard ceilings, kill switch, waste detectors
arena/sandbox/          sandbox/secrets/network policy
arena/observability/    event log, trace store, replay, scrubbers
arena/fixture/          fake competition harness
arena/research_proxy/   bounded local research-fusion proof
arena/memory/           memory proposals and validation
arena/self_improvement/ freeze and champion/challenger gates
schemas/                canonical JSON schemas
prompts/                bounded provider prompts with injection delimiters
```

---

## 7. Scoreboard schema direction

Phase 0 scoreboard must record at least:

```text
experiment_id
competition_slug
task_id
experiment_type
provider
provider_version
status
metric_name
score
valid_submission
wall_seconds
input_chars
output_chars
shell_commands
failed_commands
waste_events
artifact_paths
trace_path
created_at
```

Future fields may include:

```text
cv_score
public_lb_score
private_lb_score
prediction_correlation
gpu_minutes
ram_peak
vram_peak
research_node_id
fusion_id
ablation_id
```

Do not add future fields until needed.

---

## 8. CLI contract direction

Phase 0 commands are listed in `PHASE_0_SINGLE_SCOPE_PLAN.md`.

Future production commands may include:

```bash
arena kaggle ingest <url>
arena kaggle stage-submission <slug> --experiment <id>
arena kaggle submit <slug> --candidate <id> --human-confirm <slug>
arena research discover-web <slug>
arena run-queue <slug> --parallel <n>
```

These are not Phase 0.

---

## 9. Research-first future direction

After Phase 0, the research engine can expand from local method notes to:

- paper search plans;
- trusted paper indexes;
- downloaded/quarantined PDFs;
- paper digests;
- mechanism graphs;
- fusion trees;
- ablation planning;
- score/cost-aware research allocation.

Escalation from bounded research to heavier research must be evidence-based and bidirectional. If research-fusion experiments stop improving score, robustness, or cost, allocation decreases.

---

## 10. Adapter policy

Do not build a generic nine-adapter hierarchy in Phase 0.

Recommended extraction rule:

- implement tabular fixture first;
- implement one real tabular competition after Phase 0;
- implement a second distinct modality;
- then extract shared adapter interfaces.

Future adapter names may be documented, but not implemented prematurely.

---

## 11. Evaluation of the harness

The harness must be evaluated against baselines:

1. stub-only deterministic fixture run;
2. Codex-only local run without Arena orchestration;
3. Codex+Arena run;
4. Codex+Arena+Claude review run;
5. Codex+Arena+Claude research-fusion run.

Metrics:

```text
valid submissions produced
score improvement over calibration
provider calls per useful artifact
wall time per experiment
waste events
secret/sandbox violations
schema violations
replay success
memory proposal quality
```

Phase 0 requires only the stub fixture evaluation and optional real-provider smoke comparison.

---

## 12. Final architecture principle

The harness should use LLMs aggressively for research and code, but conservatively for authority.

Creativity belongs to provider workers. Control belongs to deterministic code.


## Appendix A. Phase 0 value caveat

Phase 0 does not prove value over Codex-alone. It proves that the harness can safely and reproducibly run a bounded local fixture loop. The first post-Phase-0 milestone must run a controlled comparison of Codex-alone versus Codex+Arena under the same fixture, wall-clock, and provider-call budgets.
