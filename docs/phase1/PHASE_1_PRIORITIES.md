# Phase 1 Priorities

Phase 0 closed at commit `17152f7` (2026-05-04) with the deterministic stub harness in place: budget governor, kill switch, sandbox policy, scrubber, scoreboard, replay, freeze sentinel, version baselines, and stub providers all working under CI.

A post-close external review and a follow-up internal cleanup pass surfaced four findings that are deliberately deferred to Phase 1. They are documented as Phase-0 design decisions (the Phase-0 single-scope plan and DAG spec explicitly defer them), but each will become a real Phase-1 acceptance criterion as soon as we move toward real Kaggle work.

This document is the seed for Phase 1 planning. Each priority should land as one or more PRs with its own design spec following the same brainstorming → design → plan → execute pattern PR1–PR7 used.

---

## Priority 1 — OS-level provider sandbox (was reviewer #1)

**Phase-0 status:** Not implemented. `arena/sandbox/policy.py` defines a packet-scoped policy object (`allowed_writes`, `blocked_paths`, `allowed_network_domains`); `arena/sandbox/runner.py` provides voluntary `assert_sandbox_allowed`; tests prove the in-process policy denies declared paths. None of this contains a real subprocess: `RealCodexProvider.invoke` and `RealClaudeProvider.invoke` call `subprocess.run` with `effective_env = {**os.environ, **env_overlay}`, no `HOME` redirection, no chroot, no namespace.

**Threat model that requires this:** §1.3 of `PHASE_0_SINGLE_SCOPE_PLAN.md` rules out real Kaggle competitions for Phase 0, but the §1.2 condition #12 ("sandbox denies access to secrets and blocks unapproved network egress") makes no stub-only qualifier. Once we run real Codex / Claude on real competition data, generated code or shell commands the agent emits could read `~/.kaggle`, `~/.codex`, `~/.claude`, `.env`, hidden labels, or hit arbitrary network endpoints. The Python policy cannot intercept normal OS reads or TCP from a child process.

**What "good" looks like for Phase 1:**

- Real providers run inside a containment layer. Pick one that fits the operator's host:
  - **Linux dev box:** bubblewrap or firejail with read-only repo mount + writable per-experiment worktree mount + network namespace that defaults to off.
  - **Cross-platform / production-shaped:** Docker with a minimal runtime image, `--read-only` rootfs, `--tmpfs` for `/tmp`, `--network none` by default, mounted writable worktree.
  - **Cheapest first step:** dedicated unprivileged OS user that can only read the repo + write the worktree; `kill_switch_user.gid` strips network access via firewall rule.
- `HOME` redirected to a controller-managed temp dir (per `SECURITY_COST_REPRODUCIBILITY_SPEC.md` §6.1). `CODEX_HOME` and `CLAUDE_CONFIG_DIR` resolve from the operator's `.env` (per ADR-0003) into paths inside that redirected `HOME`. The auth caches that the subscription CLIs need stay reachable; everything outside the worktree is blocked.
- Clean environment derived from a `.env` allowlist hash recorded in the run manifest. The hash drift signal mirrors the existing `<PROVIDER_VERSION_CHANGED:from=...>` token convention.
- Inputs to the agent come from a copy bundle assembled by the controller: `<worktree>/inputs/` is populated with the public competition data only; `hidden_labels.csv` and other sensitive paths never enter the bundle.
- Outbound network defaults to deny. The packet's `allowed_network_domains` field already exists in `SandboxPolicy`; Phase 1 wires it to firewall rules / docker `--add-host` policy / namespace rules so it has actual effect.
- A new acceptance test: `tests/test_phase1_real_sandbox_smoke.py` runs the provider against a deliberately misbehaving generated script (tries to read `~/.kaggle`, hit `8.8.8.8`, write to `/etc`) and asserts each attempt fails at the OS layer.

**Estimated PRs:** 2-3.
- **PR-A:** containment layer abstraction + Linux/Docker driver (the operator picks one for their host).
- **PR-B:** input-bundle assembler + worktree-only output policy.
- **PR-C:** network deny-all + per-packet allowlist enforcement.

**Dependencies:** Priority 2 (Git worktrees) and Priority 3 (slug-scoped runs) make PR-B's input-bundle assembly meaningfully different — they should land before PR-B, ideally as part of PR-A's setup work.

---

## Priority 2 — Real Git worktrees (was reviewer #5)

**Phase-0 status:** `arena/controller/worktree.py` is a 16-line `mkdir -p` helper that creates `worktrees/<slug>/<exp_id>/`. No Git branch, no Git index, no `.git/worktree`, no diff base, no PR boundary. The Phase-0 scope explicitly notes this in the `_latest_run_id` docstring ("single fixture per branch") and in the DAG spec's Phase-1 deferrals.

**Why this matters for Phase 1:** As soon as we run multiple experiments in parallel — or even sequentially on real competition data — the directory-only worktree leaks state across experiments. Two concurrent Codex invocations would share the same `submission.csv`. Review/rollback workflows expect a Git diff base to compare against. `arena memory propose` consumes review artifacts that should be tied to a specific commit.

**What "good" looks like for Phase 1:**

- `create_workspace` is renamed to `create_git_worktree` and uses `git worktree add <path> -b <branch_name>` from the repo root.
- Per-experiment branch naming convention: `arena/<slug>/<exp_id>` (so `git branch -a` shows all in-flight experiments at a glance).
- Per-experiment commits accumulate inside the worktree; `arena report` can `git diff <base>...HEAD` to surface what the agent actually changed.
- `arena worktree cleanup` command for explicit teardown after operator review.
- The `worktrees/` directory becomes ephemeral state; the source of truth is the Git branch.

**Estimated PRs:** 1.

**Dependencies:** none — independent of Priorities 1 and 3.

---

## Priority 3 — Slug-scoped run selection (was reviewer #6)

**Phase-0 status:** `arena/cli.py:_latest_run_id()` returns the lex-greatest directory under `runs/`. The docstring acknowledges this is a "single-fixture-per-branch" Phase-0 simplification. `arena plan` and `arena run-next` both call it, so they implicitly assume there's only one fixture in flight.

**Why this matters for Phase 1:** Run two competitions concurrently and `_latest_run_id` returns whichever was initialized last. Run a fixture today and a real practice competition tomorrow on the same branch and the lex sort gives the wrong answer. Even a single `arena init-fixture` / `arena init-fixture` / `arena run-next` sequence in different slugs trips this.

**What "good" looks like for Phase 1:**

- Active run is recorded per slug at `runs/.active/<slug>` (a small JSON file mapping slug → run_id).
- `_latest_run_id(slug)` becomes `_active_run_id(slug)`, reads from that file.
- Every CLI command that touches a run takes a `--run-id <id>` option for unambiguous targeting; the option defaults to the slug's active run.
- `arena init-fixture` writes the new run_id to `runs/.active/<slug>` atomically (write-then-rename).
- `arena run-set-active <slug> <run_id>` for explicit operator override.
- `arena report --all-runs <slug>` shows every run for a slug, ordered by start time.

**Estimated PRs:** 1, possibly bundled with Priority 2.

**Dependencies:** none. Should land before any multi-fixture work.

---

## Priority 4 — Self-improvement protected-file enforcement (was reviewer #8)

**Phase-0 status:** `arena/self_improvement/scan.py` detects the §7.3 triggers and writes a freeze sentinel; `arena/self_improvement/proposal.py` synthesizes proposals; `arena/self_improvement/freeze.py` evaluates and writes the sentinel. The scanner explicitly says "Protected-file mutation and schema drift are out of scope; an auto-apply flow remains future work" — Phase 0 ships observe-only.

**Why this matters for Phase 1:** The freeze sentinel is the gate, but there's no auto-apply flow that needs the gate to mean anything. Once we want the agent to actually modify controller / provider / sandbox / schema / prompt / memory files (the "self-improvement" half of the loop), the gate becomes load-bearing.

**What "good" looks like for Phase 1:**

- A new `arena self-improve apply <sip_id>` command that:
  - Refuses if `SELF_IMPROVEMENT_FROZEN.md` is present.
  - Refuses if the proposal's `protected_files_touched` list is non-empty (Phase 1 keeps human-approval-required as the default; Phase 2+ may add a curated whitelist of safe-to-auto-apply patterns).
  - Refuses if the proposal's `requires_human_approval` flag is true (Phase 0 default; Phase 1 may relax for narrowly-scoped recommendations).
  - Otherwise creates a Git branch, applies the proposed change, runs the test suite + acceptance scripts in the new branch, and exits with the diff for human review (does NOT auto-merge).
- A protected-files registry at `arena/self_improvement/protected.py` listing the path patterns that always require human approval (controller/, schemas/, providers/base.py, providers/auth.py, sandbox/, budget/, scoreboard/, observability/scrubber.py).
- `arena self-improve apply` always uses `git diff` as the review surface; never edits files in place.

**Estimated PRs:** 2.
- **PR-A:** protected-files registry + the `apply` command's gate logic + tests against the gate.
- **PR-B:** Git-branch-and-test-and-diff workflow + integration with `arena report`.

**Dependencies:** Priority 2 (Git worktrees) is a soft dependency — `arena self-improve apply` makes more sense once Git is the source of truth.

---

## Cross-cutting Phase-1 framing

These four priorities reflect the boundary between the deterministic skeleton (Phase 0) and a system that can safely run real agents on real data (Phase 1+). The order is roughly:

1. **Priority 3 (slug-scoped runs)** is small and removes a foot-gun.
2. **Priority 2 (Git worktrees)** is small and provides the diff base everything else needs.
3. **Priority 1 (OS-level sandbox)** is the big one — multiple PRs, multiple containment-layer driver options, the actual security work that lets us point a real CLI at a real competition.
4. **Priority 4 (SI auto-apply)** rounds out the self-improvement loop once Git diff is the review surface.

After all four, Phase 1 acceptance becomes: a real Codex CLI runs on a real practice Kaggle competition (e.g., Titanic), produces a submission, and every secret stays inaccessible across the entire run.

The reviewer's #2, #3, and #7 findings (real-CLI argv drift, packet-builder hardcoding) were caught and fixed in the post-PR7 polish branch; they do not roll forward to Phase 1.
