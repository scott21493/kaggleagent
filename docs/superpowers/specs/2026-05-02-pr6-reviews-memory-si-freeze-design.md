# PR6 — Reviews + Memory + Self-Improvement Freeze

**Date:** 2026-05-02
**Status:** approved (brainstorming complete; ready for implementation plan)
**Branch:** `pr6-reviews-memory-si-freeze` (from fresh post-PR5 main, baseline 294 tests)
**Spec ref:** `docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md` §10
**Phase 0 ref:** `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §6.2 steps 9–10
**Security ref:** `docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md` §7.3

---

## 1. Goal

Land the §6.2 steps 9–10 machinery (review + memory proposal) plus the self-improvement scan + freeze gate, as **three composable standalone CLI commands**. PR7 will compose them into the full 10-step §6.2 loop acceptance test; PR6 ships the building blocks.

## 2. Architecture

PR6 adds three independent control-plane subsystems. Two are deterministic-controller actions with no provider invocation; one is a single stub_claude invocation. PR5's accounting invariant — `experiments` row ⇔ provider invocation, `COUNT(*) == provider_calls` — is preserved exactly.

```text
arena review <competition_slug> --provider stub_claude --experiment <impl_exp_id>
   → resolves impl row from scoreboard; reads <fusion_id:...> token + submission.csv path
   → invokes stub_claude with new dispatch (role="review", phase="FUSION_PROXY_REVIEWED")
   → emits worktrees/<slug>/<rev_exp>/research_review.json (subject_id = impl_exp_id)
   → persists 1 scoreboard row (experiment_type="research_proxy", <step:review> token)
   → provider_calls +1

arena memory propose <competition_slug> --review <review_exp_id>
   → reads the review row's research_review.json from its artifact_paths
   → synthesizes memory_update.json (no-op observation if review has no actionable findings)
   → writes memory/proposals/mem_NNNN.json (repo-root, monotonic ID across all slugs)
   → emits trace event memory_proposal_created (payload: message, phase=
     MEMORY_PROPOSAL_CREATED, proposal_id, memory_update_id, experiment_id=
     <review_exp_id>, review_id=<rr_NNNN>, path=memory/proposals/mem_NNNN.json,
     paths=[<source_review_artifact>])
   → NO scoreboard row, NO provider invocation, provider_calls unchanged

arena self-improve scan <competition_slug>
   → reads scoreboard rows + traces + baselines for <slug>; scans ALL rows (Phase 0)
   → checks §7.3 freeze triggers against thresholds
   → emits 0..N self_improvement/proposals/sip_NNNN.json (repo-root)
   → emits trace event self_improvement_scan_completed (payload: message,
     phase=SELF_IMPROVEMENT_SCAN_COMPLETED, status=clean|findings|frozen,
     reason="findings_count=N; freeze_triggered=true|false",
     paths=[self_improvement/proposals/sip_NNNN.json, ...], evidence=[...],
     and path=SELF_IMPROVEMENT_FROZEN.md when frozen)
   → if any §7.3 trigger fires, writes SELF_IMPROVEMENT_FROZEN.md (Markdown
     body + fenced JSON metadata block)
   → NO scoreboard row, NO provider invocation, provider_calls unchanged
```

### 2.1 Why standalone commands (not extending `arena research-proxy`)

PR5 owns steps 1–8; PR6 owns steps 9–10 + self-improvement; PR7 proves the full 10-step loop. Standalone commands give the operator clean retry/replay points (a failed step 9 retries with `arena review` rather than re-running the whole proxy chain) and keep PR5/PR6 ownership disjoint. PR7 may add a `--with-review` convenience flag to `arena research-proxy` once the standalone commands are proven; that's out of PR6 scope.

### 2.2 Why memory + self-improve are controller-only (no scoreboard row)

PR5 established `experiments` row ⇔ provider invocation. Memory proposals (§6.2 step 10) and self-improvement findings are deterministic-controller work; persisting rows for them would inflate `provider_calls` (since `get_run_usage_totals` derives it from `COUNT(*)`) and corrupt the budget-governor's seeded accumulators on the next run.

The auditability requirement is satisfied by **durable artifacts on disk + trace events**:

- Memory proposals at `memory/proposals/<proposal_id>.json` + `memory_proposal_created` trace event.
- Self-improvement proposals at `self_improvement/proposals/<sip_id>.json` + `self_improvement_scan_completed` trace event.

This pattern matches `arena report` — a control-plane command that reads scoreboard + traces but doesn't mutate them.

### 2.3 Note for PR7+

> PR7's "10-step §6.2 loop" acceptance MUST be proven across **scoreboard rows + controller artifacts + trace events**, NOT scoreboard rows alone. Memory proposals (step 10) and self-improvement findings live in `memory/proposals/` and `self_improvement/proposals/` plus their corresponding trace events; they are durable but intentionally do NOT appear as `experiments` rows. This preserves the `COUNT(*) == provider_calls` invariant established in PR5 (`arena/cli.py:185-377` + `arena/scoreboard/store.py:get_run_usage_totals`).

## 3. CLI commands

### 3.1 `arena review <slug> --provider stub_claude --experiment <impl_exp_id>`

| Behavior | Detail |
|---|---|
| Provider invocation | yes — stub_claude (only supported provider in PR6) |
| Scoreboard row | yes — 1 row, `experiment_type="research_proxy"`, `<step:review>` token |
| `provider_calls` | +1 |
| PR4 reproducibility | yes — fixture digest + provider-version baseline checks fire BEFORE invoke (mirrors `arena research-proxy` after the post-merge fix at `773c5a9`) |
| Sandbox | per-step packet-scoped, same as research-proxy |
| Watchdog | full pre-invoke + post-invoke discipline (`_guarded_invoke` + `_persist_inflight_blocked` patterns) |

**Resolution path:**
1. Verify the implementation row exists in the scoreboard for `<slug>`.
2. Read its `artifact_paths`, extract the submission.csv path and the `<fusion_id:fusion_NNNN>` token.
3. From the fusion_id, locate the originating fusion_proposal.json (find the row whose artifact_paths contains the same fusion token AND has `<step:fusion>`).
4. Build the review packet with `inputs=[submission_path, fusion_proposal_path]`, `objective="Review proxy implementation <impl_exp_id> against fusion <fusion_id>"`.
5. Mint a new experiment_id for the review row (via `get_next_experiment_id`).

**Failure modes:**
- `--experiment <impl_exp_id>` doesn't exist → `typer.BadParameter` + clean exit
- impl row's artifact_paths is missing the fusion_id token → `typer.BadParameter` ("not a research-proxy implementation row")
- can't locate the originating fusion_proposal.json → `typer.BadParameter` (corrupt scoreboard)
- post-dequeue / post-invoke failures: same disciplines as `arena research-proxy` (KillSwitchActive pre-invoke = no row; BudgetExceeded post-invoke = blocked row WITH `usage_proxy`; SandboxViolation = blocked row WITHOUT `usage_proxy`)

### 3.2 `arena memory propose <slug> --review <review_exp_id>`

| Behavior | Detail |
|---|---|
| Provider invocation | NO — controller action only |
| Scoreboard row | NO |
| `provider_calls` | unchanged |
| Output artifact | `memory/proposals/mem_NNNN.json` (repo-root, monotonic ID across all slugs via filesystem scan) |
| Trace event | `memory_proposal_created`. Payload uses ONLY `event.schema.json`-permitted keys (the schema sets `additionalProperties: false`): `message`, `phase=MEMORY_PROPOSAL_CREATED`, `proposal_id=mem_NNNN`, `memory_update_id=mem_NNNN` (same value; both keys are exposed for replay tooling), `experiment_id=<review_exp_id>`, `review_id=<rr_NNNN>`, `path=memory/proposals/mem_NNNN.json`, `paths=[<source_review_artifact>]`. |

**Resolution path:**
1. Verify the review row exists in the scoreboard for `<slug>` and is a research-proxy row with `<step:review>` token.
2. Read the row's artifact_paths to find the `research_review.json` path; load + validate against `research_review.schema.json`.
3. Synthesize `memory_update.json`:
   - **If review has actionable content** (`required_fixes` non-empty OR `follow_up_recommendations` non-empty): build an `add` operation in the `research` namespace with `claim` = first actionable item, `delta` = "Add this constraint to the research namespace based on review {review_id}", `evidence` = [{type: "trace", ref: review_id, quote_or_summary: review.summary}], `confidence` based on `risk_level`, `expiry_or_revisit` = "After Phase 0 close", `risk` = review.risk_level, `review_status` = "proposed".
   - **If review has no actionable content**: emit a no-op observation as an `add` to the `research` namespace with `claim` = "No actionable findings from review {review_id}", `delta` = "No-op observation; review accepted with no required changes", `evidence` = [{type: "trace", ref: review_id, quote_or_summary: review.summary}], `confidence` = "low", `risk` = "low", `review_status` = "proposed". Schema-valid; intentionally captures audit trail.
4. Mint `proposal_id` via `get_next_proposal_id` (filesystem scan of `memory/proposals/`).
5. Write `memory/proposals/mem_NNNN.json` (atomic write).
6. Emit `memory_proposal_created` trace event.

**Failure modes:**
- review row doesn't exist or wrong type → `typer.BadParameter`
- review's artifact_paths missing the research_review.json reference → `typer.BadParameter`
- corrupt research_review.json (schema-invalid) → `typer.BadParameter`

**Memory namespace:** Always `research` in PR6. All PR6 reviews are research-proxy outputs and the unified wiki has a `research/` namespace for this lane. Future review types (calibration, harness) can derive namespace; deferred to PR7+.

### 3.3 `arena self-improve scan <slug>`

| Behavior | Detail |
|---|---|
| Provider invocation | NO — controller action only |
| Scoreboard row | NO |
| `provider_calls` | unchanged |
| Scan window | ALL rows for `<slug>` (Phase 0 — small scoreboard); `--since` / `--limit` flags deferred to PR7+ |
| Output artifacts | 0..N `self_improvement/proposals/sip_NNNN.json` (repo-root, monotonic across all slugs) |
| Trace event | `self_improvement_scan_completed`. Payload uses ONLY `event.schema.json`-permitted keys (the schema sets `additionalProperties: false`): `message`, `phase=SELF_IMPROVEMENT_SCAN_COMPLETED`, `status` ∈ {`clean`, `findings`, `frozen`}, `reason="findings_count=N; freeze_triggered=true\|false"`, `paths=[self_improvement/proposals/sip_NNNN.json, ...]`, `evidence=[...]` (string array of human-readable evidence refs), and `path=SELF_IMPROVEMENT_FROZEN.md` ONLY when `status=frozen`. |
| Freeze sentinel | `SELF_IMPROVEMENT_FROZEN.md` at repo root if any §7.3 trigger fires |

**Scan procedure:**
1. Load all scoreboard rows for `<slug>`.
2. Load fixture-digest + provider-version baselines from `runs/.baselines/<slug>/`.
3. Walk recent traces under `traces/<run_id>/<task_id>/events.jsonl` for breaker_triggered + repeated shell_command_observed events.
4. For each §7.3 trigger condition, check if it fires:
   - **Lower fixture success rate**: any rows with `valid_submission=False` ratio above threshold
   - **Higher safety violations**: count rows with status=blocked + sandbox/budget breakers
   - **More waste events**: SUM(`waste_events`) above threshold
   - **Wall-clock +20% without score/safety improvement**: SUM(`wall_seconds`) / champion baseline
   - **Provider-call +20% without improvement**: COUNT(*) / champion baseline
   - **Score regression**: max(score) < calibration baseline (PR1's 0.5)
   - **Failed replay**: any task_id has missing/corrupt events.jsonl
   - **Protected-file mutation, schema drift**: out of scope for PR6 stub (no auto-patch flow exists yet)
5. Each fired trigger becomes a `Finding` dataclass with kind/evidence/severity.
6. For each Finding, synthesize a `self_improvement_proposal.json` (schema-valid; `requires_human_approval=True` always for Phase 0).
7. If any Finding fires, call `apply_freeze(decision)` → write `SELF_IMPROVEMENT_FROZEN.md`.
8. Emit `self_improvement_scan_completed` trace event.

**Idempotency:** Re-running the scan against unchanged scoreboard state should not duplicate proposals. Implementation uses content-hash deduplication: a proposal whose `(problem, evidence_refs)` hash matches an existing proposal in `self_improvement/proposals/` is not re-emitted. New scans only emit proposals for findings that don't already have an open proposal.

**Freeze sentinel format** (`SELF_IMPROVEMENT_FROZEN.md` at repo root):

```markdown
# Self-Improvement Frozen

```json
{
  "frozen": true,
  "triggered_at": "2026-05-02T18:30:00+00:00",
  "competition_slug": "tabular_binary_v1",
  "triggers": [
    {"kind": "score_regression", "champion": 0.5, "challenger": 0.42, "evidence": [...]}
  ]
}
```

## Evidence

- Run `run_2026_05_02_001` task `task_0004`: blocked by `BUDGET_EXCEEDED`
- ...

## Unfreeze

Human review required. Delete this file after addressing the triggers above.
```

Markdown-first (human-readable); fenced JSON block for machine parsing. No separate JSON sidecar.

## 4. File structure

### 4.1 New modules (11)

| Path | Responsibility |
|---|---|
| `arena/review/__init__.py` | bare marker |
| `arena/review/packet.py` | `make_review_packet(*, competition_slug, run_id, experiment_id, task_id, review_id, subject_experiment_id, fusion_proposal_path, submission_path)` returns task_packet with role="review", phase="FUSION_PROXY_REVIEWED". `validate_research_review(payload)` thin wrapper over `validate("research_review", payload)`. |
| `arena/memory/__init__.py` | bare marker |
| `arena/memory/proposal.py` | `synthesize_memory_proposal(review_payload, *, proposal_id, namespace="research") -> dict`. `get_next_proposal_id(proposals_dir=Path("memory/proposals")) -> str`. `validate_memory_update(payload)` thin wrapper. |
| `arena/memory/validator.py` | `check_evidence(proposal) -> list[str]` — semantic checks beyond schema (e.g., `operation="modify"` must have `prior_claim != claim`; `operation="remove"` must have non-empty `prior_claim`; contradiction detection: `claim` and `prior_claim` must differ on `modify`/`deprecate`/`remove`). Returns list of issue strings; empty = valid. |
| `arena/memory/diff.py` | `render_diff(proposal, wiki_path=Path("docs/memory/UNIFIED_MEMORY_WIKI.md")) -> str` — produces a unified-diff-style string showing what the proposal would change. Pure function, no file mutation. Scoped by namespace. |
| `arena/self_improvement/__init__.py` | bare marker |
| `arena/self_improvement/scan.py` | `scan_runs(slug, *, store, runs_root, baselines_root) -> list[Finding]`. `Finding` is a frozen dataclass with `kind: str`, `severity: str`, `evidence_refs: list[str]`, `problem: str`. |
| `arena/self_improvement/proposal.py` | `make_self_improvement_proposal(finding, *, proposal_id) -> dict`. `get_next_sip_id(proposals_dir=Path("self_improvement/proposals")) -> str`. `validate_self_improvement_proposal(payload)` thin wrapper. |
| `arena/self_improvement/freeze.py` | `evaluate_freeze(findings) -> FreezeDecision`. `apply_freeze(decision, sentinel_path=Path("SELF_IMPROVEMENT_FROZEN.md")) -> None` — writes the sentinel atomically. `is_frozen(sentinel_path=Path("SELF_IMPROVEMENT_FROZEN.md")) -> bool`. |
| `arena/self_improvement/champion_challenger.py` | `compare_metrics(champion: Metrics, challenger: Metrics) -> ComparisonResult` — pure helper, no I/O, narrow contract. Library-only; used by freeze evaluator and PR7's future `arena self-improve apply` flow. |

### 4.2 Modified modules (2)

| Path | Change |
|---|---|
| `arena/providers/stub_claude.py` | Extend `_research_proxy_payload` dispatch to also handle `(role="review", phase="FUSION_PROXY_REVIEWED")` → emit `research_review.json`. New `_research_review_payload(slug, subject_id) -> dict` method. |
| `arena/cli.py` | Add `review`, `memory propose`, `self-improve scan` subcommands. Reuse existing helpers (`_store`, `_latest_run_id`, `_get_provider`, `_persist_row`, `_guarded_invoke`, `_persist_inflight_blocked`). |

### 4.3 New tests (~10 files)

| Path | Coverage |
|---|---|
| `tests/test_stub_claude_review.py` | 5 tests: emits valid research_review.json on FUSION_PROXY_REVIEWED phase; subject_id extracted from inputs[0] path; default decision=accept + risk=low; calibration backward-compat unchanged; monkeypatch can override default decision for rejection-path tests |
| `tests/test_research_review_packet.py` | 3 tests: packet builder shape; schema validation; review_id pattern |
| `tests/test_cli_review.py` | 6 tests: happy path; missing impl experiment; impl row missing fusion_id token; PR4 reproducibility (fixture digest, provider drift); pre-invoke kill switch; post-invoke BudgetExceeded persists row WITH usage_proxy |
| `tests/test_memory_proposal.py` | 6 tests: synthesizes valid memory_update for actionable review; no-op observation for empty review; deterministic proposal_id minting; namespace="research"; review_status="proposed"; evidence array points to review_id |
| `tests/test_memory_validator.py` | 4 tests: contradiction detection (claim==prior_claim on modify); operation-specific prior_claim requirements; valid proposals pass; rejects empty evidence |
| `tests/test_memory_diff.py` | 3 tests: renders unified-diff against wiki; namespace-scoped; pure function (no file mutation) |
| `tests/test_cli_memory_propose.py` | 5 tests: happy path; no-op fallback; missing review experiment; corrupt research_review.json; deterministic proposal_id |
| `tests/test_self_improvement_scan.py` | 6 tests: detects blocked rows; detects score regression; detects waste threshold; idempotent (no duplicate proposals); proposal IDs monotonic; all-clean run produces zero findings |
| `tests/test_self_improvement_freeze.py` | 5 tests: evaluate fires on each §7.3 trigger; apply_freeze writes sentinel atomically; sentinel format validates (YAML/JSON metadata block parses); is_frozen reads sentinel; unfreeze deletes sentinel |
| `tests/test_self_improvement_champion_challenger.py` | 3 tests: compare_metrics returns ComparisonResult; regressions flagged; pure function |
| `tests/test_cli_self_improve_scan.py` | 5 tests: happy path (no findings); finding triggers freeze; sentinel written; trace event emitted; backward-compat (no scoreboard row) |
| `tests/test_memory_proposal_examples.py` | **Replaces `scripts/validate_memory_examples.py`.** 6 tests: each of 4 `operation` enum paths (add/modify/deprecate/remove) + the `prior_claim` conditional (required on modify/deprecate/remove, optional on add) + contradiction detection round-trip. |

### 4.4 Removed files

| Path | Reason |
|---|---|
| `scripts/validate_memory_examples.py` | Replaced by `tests/test_memory_proposal_examples.py`. |

### 4.5 Documentation updates

The "6 CI scripts" references in CLAUDE.md, README, and the PR5 plan must be updated. **Phrasing:** rename to "**5 external acceptance scripts + memory-examples test suite**" or "the acceptance script set" with an explicit list. This avoids the count-drift footgun every time we add or remove a script.

Affected docs:
- `CLAUDE.md` — search for "6 CI scripts" / "all 6 CI scripts"
- Any plan headers that reference "6 CI scripts" — update inline references
- The new PR6 plan will use the updated phrasing from the start

## 5. Schemas (already in place)

| Schema | Used by |
|---|---|
| `schemas/review.schema.json` | (general review — not used in PR6; reserved for future calibration reviews) |
| `schemas/research_review.schema.json` | `arena review` output, validated by `validate_research_review` |
| `schemas/memory_update.schema.json` | `arena memory propose` output, validated by `validate_memory_update` |
| `schemas/self_improvement_proposal.schema.json` | `arena self-improve scan` outputs, validated by `validate_self_improvement_proposal` |

No schema changes needed for PR6.

## 6. Phase enum values (already in place)

| Phase value | Used by |
|---|---|
| `FUSION_PROXY_REVIEWED` | review row's trace events |
| `MEMORY_PROPOSAL_CREATED` | memory_proposal_created trace event payload |
| `SELF_IMPROVEMENT_SCAN_COMPLETED` | self_improvement_scan_completed trace event payload |

No Phase enum changes needed for PR6.

## 7. Task decomposition (7 tasks)

| # | Task | Model | Estimated test additions |
|---|---|---|---|
| 1 | Stub Claude review dispatch + tests | haiku | +5 |
| 2 | `arena/review/` packet builder + `arena review` CLI + tests | standard | +9 |
| 3 | `arena/memory/{proposal,validator,diff}.py` + tests | standard | +13 |
| 4 | `arena memory propose` CLI + tests | standard | +5 |
| 5 | `arena/self_improvement/{scan,proposal,champion_challenger}.py` + tests | standard | +14 |
| 6 | `arena/self_improvement/freeze.py` + `arena self-improve scan` CLI + tests | standard | +10 |
| 7 | Replace `scripts/validate_memory_examples.py` with `tests/test_memory_proposal_examples.py` + update docs | haiku | +6 (net 0 since we delete the script) |

**Test count target:** 294 → **~340–350** (+46–56). Final count locked in the implementation plan.

**Coverage gate:** 50% during PR6 (per pyproject.toml; restored to 70% in PR7).

**Per-task model selection rationale:**
- haiku for Task 1 (mechanical extension of an existing dispatch table) and Task 7 (mechanical script→tests transcription)
- standard for Tasks 2–6 (CLI integration with multiple side effects: scoreboard writes, file writes, trace events, schema validation)
- No task needs opus — none are doing architectural design

## 8. Test strategy + invariants

### 8.1 PR5 invariants preserved

| Invariant | How PR6 preserves it |
|---|---|
| `experiments` row ⇔ provider invocation | only `arena review` creates a row (it's a real provider invocation) |
| `COUNT(*) == provider_calls` | memory + self-improve don't create rows |
| Pre-invoke failures don't insert rows | `arena review` mirrors `arena research-proxy` precheck pattern (`in_flight["invocation_started"]` flag, `_persist_inflight_blocked` helper) |
| Post-invoke `BudgetExceeded` persists row WITH `exc.usage_proxy` | same handler shape as `arena research-proxy` |
| PR4 reproducibility (fixture digest + provider versions) | `arena review` runs the precheck before its `_guarded_invoke`; memory + self-improve don't need it (no provider call) |

### 8.2 New invariants introduced by PR6

| Invariant | How tested |
|---|---|
| Memory proposals are durable artifacts, not scoreboard rows | `tests/test_cli_memory_propose.py::test_no_scoreboard_row_inserted` asserts `len(rows) == prior_count` |
| Self-improve scan is idempotent | `tests/test_self_improvement_scan.py::test_rescan_does_not_duplicate_proposals` runs scan twice, asserts proposal directory unchanged |
| Freeze sentinel is the source of truth | `tests/test_self_improvement_freeze.py::test_is_frozen_after_apply` writes via `apply_freeze`, reads via `is_frozen`, asserts True |
| Memory namespace is `research` in PR6 | `tests/test_cli_memory_propose.py::test_namespace_is_research` asserts the synthesized proposal's namespace |
| Memory diff is read-only | `tests/test_memory_diff.py::test_render_diff_does_not_mutate_wiki` checks file mtime + content hash before/after |

### 8.3 Acceptance gates

PR6 lands when:

1. `arena review tabular_binary_v1 --experiment <impl_exp> --provider stub_claude` succeeds against a research-proxy implementation row, persists a row with `<step:review>` token, emits valid research_review.json.
2. `arena memory propose tabular_binary_v1 --review <rev_exp>` succeeds against a review row, writes valid memory_update.json to `memory/proposals/`, emits `memory_proposal_created` trace event, does NOT create a scoreboard row.
3. `arena self-improve scan tabular_binary_v1` runs against a clean scoreboard with zero findings (no proposals, no sentinel). Same command runs against a scoreboard with a blocked row produces ≥1 finding + sentinel.
4. `tests/test_memory_proposal_examples.py` covers all 4 `operation` paths + contradiction detection. `scripts/validate_memory_examples.py` is removed and the acceptance-scripts list is updated in CLAUDE.md.
5. Full suite green: ruff, ruff format, mypy, pytest, plus the (now 5) external acceptance scripts. Total tests ≥ 340.
6. PR5 invariants still hold: re-running `arena research-proxy` against a clean fixture still produces 4 rows; `provider_calls == COUNT(*)`; PR4 reproducibility checks fire.

## 9. Out of scope (deferred to PR7+)

- `arena self-improve apply <sip_id>` — actually applying a self-improvement proposal (PR6 ships scan + freeze; apply is PR7+).
- Memory wiki auto-merge — even after a proposal is `accepted`, no PR6 command auto-merges into `docs/memory/UNIFIED_MEMORY_WIKI.md`.
- Multi-namespace memory routing — PR6 always uses `namespace="research"`. PR7+ may derive from review subject type.
- `--with-review` convenience flag on `arena research-proxy` — PR7+ once standalone commands are proven.
- `--since <run_id>` / `--limit <N>` flags on `arena self-improve scan` — PR7+ when the scoreboard is large enough to need windowing.
- Real review-driven memory drafting (Claude-authored memory proposals via stub_claude dispatch) — PR7+ may add `(role="advisory_planning", phase="MEMORY_PROPOSAL_CREATED")` if needed.
- Champion-challenger comparison wired into a real apply gate — PR6 ships the library helper; PR7+ wires it into apply.

## 10. Risk register (pre-implementation)

| Risk | Mitigation |
|---|---|
| Memory proposal "no-op observation" pattern — schema-valid but informationally empty | Docs emphasize this is intentional audit-trail; tests pin the no-op shape; review reviewer explicitly checks |
| Self-improve scan deduplication via content-hash — collisions produce silently-dropped findings | Test pins idempotency exactly; hash includes problem + sorted evidence_refs |
| Freeze sentinel format drift — JSON metadata block parsing fragility | Test parses the JSON block directly; sentinel writer uses a single canonical template |
| Champion baseline = PR1's calibration (0.5 stub) | Phase 0 stub-only behavior; PR7's real Codex changes the comparison; documented in champion_challenger.py docstring |
| `arena review`'s subject_id resolution — assumes path format `worktrees/<slug>/<exp>/submission.csv` | Stub extracts subject_id from inputs[0] same way stub_codex extracts fusion_id; failure mode is `typer.BadParameter` |
| Replace `scripts/validate_memory_examples.py` — CI references the script | Acceptance gate #4 explicitly checks docs are updated; reviewer verifies CLAUDE.md + README |
| Trace event payload drift — `event.schema.json` sets `additionalProperties: false`, so any payload key not in the allowed set rejects at `TraceStore.emit()` validation | §3.2 and §3.3 specify the EXACT key set per event; spec brainstorming round caught this before code was written; plan reviewer must check that no PR6 code uses keys outside `{message, phase, proposal_id, memory_update_id, experiment_id, review_id, path, paths, status, reason, evidence}` |

## 11. Plan-review preempts (carry from PR1+PR2+PR3+PR4+PR5)

These are now boilerplate; documented here so the implementation plan doesn't re-derive them:

- Use `from datetime import UTC, datetime` and `datetime.now(UTC).isoformat(timespec="seconds")`.
- Use StrEnum where Phase is referenced; do NOT add new Phase values without updating `schemas/task_packet.schema.json`.
- Use `.venv/Scripts/python.exe` for Python invocations.
- Use `git -C "C:/Users/scott/Documents/kaggle agent"` prefix for git ops in subagent prompts.
- `ScoreboardStore.insert_experiment` takes `artifact_paths: list[str]` (don't double-encode).
- Tests use the existing `fixture_workspace` conftest fixture.
- Schema validation goes through `arena.schemas.validate.validate(name, payload)` — module-level import (no function-local imports per Task 5 polish in PR5).
- `_persist_row` callers ensure `experiment_type="research_proxy"` (not step-typed) and the step name is the FIRST element of `artifact_paths` (per PR5 round-4 schema-enum fix).
- Pre-invoke failures (KillSwitchActive, ProviderCallBreaker in `check_can_invoke`, fixture digest drift) do NOT persist rows. Post-invoke failures (BudgetExceeded with `exc.usage_proxy`, SandboxViolation in `wrap_invoke`) DO persist a blocked row with the consumed usage threaded through.
- All ruff / ruff-format / mypy / pytest / external-acceptance-scripts must be green at every commit.

---

## Self-review

Spec scope: `arena/review/`, `arena/memory/`, `arena/self_improvement/` plus extensions to `arena/cli.py` and `arena/providers/stub_claude.py`. Disjoint from PR5's research-proxy lane. No schema changes, no Phase enum changes. 11 new modules, 2 modified, ~10 new test files, 1 deleted script.

Placeholder scan: no TBD/TODO/incomplete sections.

Internal consistency: §3.2's no-op fallback matches §10.1's risk register entry; §4's file structure matches §7's task decomposition; §8's invariants match §2's accounting design.

Scope check: focused for one implementation plan, ~7 tasks, ~2-3 hours per spec §10's estimate.

Ambiguity check:
- "All rows" for self-improve scan → §3.3 says "all rows for `<slug>`", explicit.
- "Default decision" for review stub → §3.1 says `decision="accept"`, `risk_level="low"`, `required_fixes=[]`, explicit.
- "First actionable item" in memory synthesis → §3.2 says "`required_fixes[0]` if non-empty, else `follow_up_recommendations[0]` if non-empty, else no-op", to be made explicit in the plan code.
