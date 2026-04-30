# ADR-0004 — Provider CLI Invocation Conventions

Status: accepted (forward-looking; verified-on-implement at PR7)
Date: 2026-04-30
Supersedes: none
Related: [ADR-0002](ADR-0002-CONTROLLER-AND-PROVIDERS.md), [ADR-0003](ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md), [SECURITY_COST_REPRODUCIBILITY_SPEC](../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md)

---

## Context

The deterministic controller dispatches every task to a `ProviderAdapter`. Stub providers in PR1 do not subprocess. Real providers in PR7 wrap subscription-authenticated CLIs (`codex` and `claude`) and must capture stdout/stderr, scrub them, parse them into a `ProviderResult` matching `provider_result.schema.json`, and surface auth/version failures as deterministic blocked-states.

Without a single agreed-upon invocation contract, every PR that touches providers will re-litigate stdin shape, exit-code semantics, and auth-failure detection. This ADR fixes the contract before PR7 ships.

## Decision

Real Codex and Claude providers are invoked as subprocesses with the conventions below. Stub providers in PR1–PR6 do **not** subprocess; they synthesize equivalent return shapes.

### Codex (ChatGPT subscription, `codex` CLI)

**Invocation form:**

```text
codex exec --json --workspace-write <workspace> [--prompt-file <path>]
```

The exact flag spelling is verified at PR7 against the installed Codex CLI version recorded by `arena provider health codex`. If the spelling differs, the wrapper updates this ADR and bumps `provider_version` baseline; the controller flags `PROVIDER_VERSION_CHANGED` until a human accepts.

**Stdin contract:** the task packet JSON, written to a temp file, passed via `--prompt-file`. Inline stdin is avoided because some Windows + WSL2 + provider-CLI combinations mishandle it.

**Stdout contract:** newline-delimited JSON events. The wrapper buffers all events, applies the scrubber to each line, and persists raw + scrubbed copies to `traces/<run_id>/<task_id>/{stdout.raw, stdout.scrubbed}`. The final event is expected to summarize artifacts and usage; if absent, the wrapper marks the result `failure` with reason `missing_terminal_event`.

**Stderr contract:** plain text. Captured to `stderr.raw` and `stderr.scrubbed` exactly like stdout. Stderr does not affect status by itself; status comes from exit code.

**Exit codes:**

- `0` → `ProviderResult.status = "success"`.
- `1` → `failure` (provider ran but produced no usable output).
- `2` → `blocked` (CLI rejected the request, e.g. unsafe shell).
- `>= 64` reserved for auth/session errors; wrapper translates these to `BLOCKED_AUTH` and writes a runbook reference. The exact code is verified at PR7.
- Any signal-induced termination (SIGTERM, SIGKILL) → `killed`.
- Process not started (binary missing, permission denied) → controller raises before the wrapper logs anything.

**Auth-expiry surface:** any of (a) exit code in the auth range, (b) stderr containing a known auth-expiry phrase pinned at PR7, (c) the `arena provider health codex` precheck failing — all map to `BLOCKED_AUTH`. The runbook in [PHASE_0_SINGLE_SCOPE_PLAN](../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §7.3 governs recovery.

### Claude Code (Anthropic subscription, `claude` CLI)

**Invocation form:**

```text
claude -p [--input <prompt-file>] [--workspace <workspace>]
```

The exact flag spelling is verified at PR7. Older docs reference `claude --print`; both are equivalent in current versions.

**Stdin contract:** the task packet JSON or the rendered prompt (depending on role), written to a temp file, passed via `--input`. The wrapper does not pipe via stdin for the same Windows/WSL2 reason.

**Stdout contract:** plain text or JSON depending on the task role. For `role=review`, the prompt template instructs Claude to return a JSON object validating against `review.schema.json`. For `role=advisory_planning`, the schema is `strategist_recommendation.schema.json`. The wrapper:

1. captures all stdout to `stdout.raw`,
2. applies the scrubber → `stdout.scrubbed`,
3. attempts to parse the scrubbed output as JSON,
4. if parse succeeds, validates against the role-appropriate schema,
5. on parse failure or schema violation, marks the result `failure` and logs the schema error.

**Stderr contract:** captured to `stderr.raw` and `stderr.scrubbed`. Treated like Codex's stderr.

**Exit codes:** mirror Codex semantics (`0` success, `1` failure, `2` blocked, auth-range → `BLOCKED_AUTH`). Verified at PR7.

**Auth-expiry surface:** same triple as Codex. The runbook in §7.3 also applies.

### Scrubber attachment point

The scrubber is the line right after subprocess capture, before any persistence or parsing. Concretely:

```
subprocess.run(...) -> raw_stdout, raw_stderr
  -> trace_store.write_raw(raw_stdout, raw_stderr)
  -> scrubber.scrub(raw_stdout) -> scrubbed_stdout
  -> scrubber.scrub(raw_stderr) -> scrubbed_stderr
  -> trace_store.write_scrubbed(scrubbed_stdout, scrubbed_stderr)
  -> parser.parse(scrubbed_stdout) -> ProviderResult
```

Raw traces are written first (for forensic recovery if scrubbing has a bug) but live under a path that is never included in any provider context, never sent to any LLM, and is treated as sensitive by the sandbox. The path layout matches [SECURITY_COST_REPRODUCIBILITY_SPEC](../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §6.4.

PR4 lands the scrubber and trace store; until then, PR7's wrappers depend on PR4 being merged first.

### Workspace and environment

Real providers run with:

- working directory set to the per-experiment worktree (`worktrees/<slug>/<exp_id>/`),
- `PATH` inherited but `HOME` redirected to a controller-managed temp dir so subscription auth caches outside the repo are not shadowed (the actual `CODEX_HOME` and `CLAUDE_CONFIG_DIR` paths resolve from the .env, per [ADR-0003](ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md)),
- a clean environment derived from the `.env` allowlist hash recorded in the run manifest (per §6.1 of the security spec),
- a wall-clock timeout from `task_packet.budgets.max_wall_minutes` enforced by the controller's watchdog (PR2), with a graceful-then-forceful kill sequence per §4.3 of the security spec.

Stub providers ignore all of the above and synthesize results in pure Python.

## Open questions to verify at PR7

These are deliberate uncertainties. The wrapper PR is responsible for resolving each before merge.

1. **Exact flag spelling** for both CLIs at the version installed on the dev machine. Pin the version in `.env.example` and bump on drift.
2. **Auth-expiry stderr fingerprint** — the canonical strings the wrapper greps for. May change across CLI versions; treat as a regex-pinnable list, not a single string.
3. **Whether `codex exec --json` emits a structured "done" event** or terminates silently after the last content event. Affects the `missing_terminal_event` failure path.
4. **Streaming vs. buffering.** PR7 buffers; if streaming becomes useful for cost-tracking later (e.g. emit trace events as they arrive), this ADR will be superseded.

These open questions are *not* PR0 work and *not* PR1 work. They are an explicit punch-list for the engineer who lands PR7.

## Consequences

- The `ProviderAdapter` ABC in PR1 carries no subprocess code; PR7 implementers can subclass it without rewriting the interface.
- Stubs in PR1 (and through PR6) emit `provider_version` strings (`stub_codex.v1`, `stub_claude.v1`) and use `started_at`/`finished_at` UTC ISO timestamps so the scoreboard schema works identically for stub and real runs.
- The scrubber dependency is explicit: PR7 cannot land before PR4.
- Auth-expiry handling is a single code path the wrapper produces, not scattered detection logic across the controller.
- Real-provider behavior in CI is intentionally untested; the stub-only gate from [ADR-0003](ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md) holds.

## Status

Accepted — forward-looking. The wrappers in PR7 are responsible for verifying every "verified at PR7" point in this document and updating it in the same commit if reality differs.
