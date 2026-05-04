# ADR-0004 — Provider CLI Invocation Conventions

Status: accepted (verified)
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

The exact flag spelling was verified at PR7 close-out and is pinned in the §"Resolved at PR7" section below. If a future CLI version changes the spelling, `arena provider health` returns `BLOCKED_PROVIDER_CAPABILITY` (see `docs/phase0/runbooks/cli_regression.md` for the operator update loop) and `record_provider_version` flags drift via `<PROVIDER_VERSION_CHANGED:from=...>` artifact tokens.

**Stdin contract:** the task packet JSON, written to a temp file, passed via `--prompt-file`. Inline stdin is avoided because some Windows + WSL2 + provider-CLI combinations mishandle it.

**Stdout contract:** newline-delimited JSON events. The wrapper buffers all events, applies the scrubber to each line, and persists raw + scrubbed copies to `traces/<run_id>/<task_id>/{stdout.raw, stdout.scrubbed}` via `TraceStore.write_provider_streams(...)`. The final event is expected to summarize artifacts and usage; if absent, the wrapper marks the result `status="failure"` and appends a `<failure:missing_terminal_event>` artifact token. (`ProviderResult.status` enum is closed: `success | failure | blocked | killed | interrupted` — sub-status detail flows through artifact tokens, never as a `reason` field, since `provider_result.schema.json` sets `additionalProperties: false`.)

**Stderr contract:** plain text. Captured to `stderr.raw` and `stderr.scrubbed` exactly like stdout. Stderr does not affect status by itself; status comes from exit code.

**Exit codes:**

- `0` → `ProviderResult.status = "success"`.
- `1` → `failure` (provider ran but produced no usable output).
- `2` → `blocked` (CLI rejected the request, e.g. unsafe shell).
- `>= 64` reserved for auth/session errors; wrapper translates these to `ProviderResult(status="blocked")` plus `<blocked:AuthFailureBreaker>` and `<runbook:docs/phase0/runbooks/auth_expiry.md>` artifact tokens. (`BLOCKED_AUTH` is a `HealthCode` enum value used by `arena provider health` and CLI display labels — it is NOT a `ProviderResult.status`.)
- Any signal-induced termination (SIGTERM, SIGKILL) → `status="killed"` plus `<killed:wall_clock_timeout>` artifact token when the wrapper raised `subprocess.TimeoutExpired`.
- Process not started (binary missing, permission denied, etc. — any `OSError`) → wrapper raises `ProviderUnavailable(code="not_found", ...)`; the controller catches at the CLI seam and emits no scoreboard row + no trace event for the blocked task.

**Auth-expiry surface:** any of (a) exit code in the auth range (`>= 64`, dispositive), (b) exit code 1 + stderr matching a pattern in `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS` (regex fallback), (c) the `arena provider health codex` precheck failing pre-invoke — all surface auth failure. Within-invoke detection (a, b) → `ProviderResult(status="blocked")` + `<blocked:AuthFailureBreaker>` + `<runbook:...>` tokens. Pre-invoke detection (c) → `ProviderUnavailable(code="blocked_auth", ...)` raised before subprocess; no row, no event. The runbook in [PHASE_0_SINGLE_SCOPE_PLAN](../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §7.3 + `docs/phase0/runbooks/auth_expiry.md` governs recovery.

### Claude Code (Anthropic subscription, `claude` CLI)

**Invocation form:**

```text
claude -p [--input <prompt-file>] [--workspace <workspace>]
```

The exact flag spelling was verified at PR7 close-out (see §"Resolved at PR7" below). Older docs reference `claude --print`; both are equivalent in current versions.

**Stdin contract:** the task packet JSON or the rendered prompt (depending on role), written to a temp file, passed via `--input`. The wrapper does not pipe via stdin for the same Windows/WSL2 reason.

**Stdout contract:** single JSON object whose shape depends on the task `(role, phase)`. The wrapper dispatches via the `_ROLE_PHASE_TO_SCHEMA` table in `arena/providers/claude.py`:

| `(role, phase)` | Schema |
|---|---|
| `("review", "FUSION_PROXY_REVIEWED")` | `research_review.schema.json` |
| `("research_proxy", "RESEARCH_QUESTION_CREATED")` | `research_question.schema.json` |
| `("research_proxy", "METHOD_DIGEST_CREATED")` | `paper_digest.schema.json` |
| `("research_proxy", "FUSION_PROPOSAL_CREATED")` | `fusion_proposal.schema.json` |
| `("advisory_planning", "STRATEGY_RECOMMENDED")` | `strategist_recommendation.schema.json` |

(Note: `review.schema.json` is a different shape used elsewhere in the codebase for codex-impl reviews; PR7's Claude wrapper consumes `research_review.schema.json` for `role=review`. PR6's `arena review` and the stub provider follow the same dispatch.) The wrapper:

1. captures all stdout to `stdout.raw` via `TraceStore.write_provider_streams(...)`,
2. applies the scrubber → `stdout.scrubbed` (also via `write_provider_streams`),
3. attempts to parse the scrubbed output as JSON,
4. if parse succeeds, validates against the `(role, phase)`-appropriate schema,
5. on parse failure → `status="failure"` + `<failure:json_decode_error>` artifact token; on schema violation OR unmapped `(role, phase)` → `status="failure"` + `<failure:schema_violation>` artifact token,
6. on success → materialises the validated JSON to `<workspace>/<schema_name>.json` and appends the path to `ProviderResult.artifacts` (real Claude is advisory; the wrapper persists the advisory artifact for downstream `arena review` / `arena research-proxy` consumers via `_require_artifact(suffix=...)`).

**Stderr contract:** captured to `stderr.raw` and `stderr.scrubbed`. Treated like Codex's stderr.

**Exit codes:** mirror Codex semantics (`0` success, `1` failure, `2` blocked, auth-range → `status="blocked"` + AuthFailureBreaker tokens). Verified at PR7.

**Auth-expiry surface:** same triple as Codex. The runbook in §7.3 also applies.

### Scrubber attachment point

The scrubber is the line right after subprocess capture, before any persistence or parsing. Concretely:

```
subprocess.run(...) -> raw_stdout, raw_stderr
  -> scrub_text(raw_stdout) -> scrubbed_stdout
  -> scrub_text(raw_stderr) -> scrubbed_stderr
  -> trace_store.write_provider_streams(
         task_id=...,
         raw_stdout=raw_stdout, raw_stderr=raw_stderr,
         scrubbed_stdout=scrubbed_stdout, scrubbed_stderr=scrubbed_stderr,
     ) -> ProviderStreamPaths{stdout_raw, stderr_raw, stdout_scrubbed, stderr_scrubbed}
  -> parser.parse(scrubbed_stdout) -> ProviderResult
```

`TraceStore.write_provider_streams(...)` writes the four artifacts to `<root>/<run_id>/<task_id>/{stdout.raw, stderr.raw, stdout.scrubbed, stderr.scrubbed}` in that order — **raw paths are written first** for forensic recovery if the scrubber has a bug. `ProviderResult.stdout_path` / `stderr_path` reference the **scrubbed** paths only; the raw paths NEVER appear in artifacts, are NEVER passed back into provider context, are NEVER emitted to the trace event stream, and are NEVER rendered in `arena report`. PR3's `SandboxPolicy._default_blocked_paths` includes the workspace-root-relative `traces/` directory so providers cannot read these forensic streams. The path layout matches [SECURITY_COST_REPRODUCIBILITY_SPEC](../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §6.4.

PR4 lands the scrubber and trace store; PR7's wrappers depend on PR4 (already merged).

### Workspace and environment

Real providers run with:

- **Working directory** set to the per-experiment worktree resolved from `task_packet["allowed_paths"][0]` (relative paths resolve against the adapter's `cwd` constructor argument; absolute paths are honoured as-is). Falls back to the constructor `cwd` when `allowed_paths` is empty (test-only path; production callers always populate `allowed_paths`).
- **Environment** built as `effective_env = {**os.environ, **env_overlay}` — an overlay on the inherited process environment, where `env_overlay` is the `env=` kwarg supplied at adapter construction. This is what shipped at PR7. The CLI's `_get_provider("codex"/"claude")` resolution path constructs adapters WITHOUT an env overlay, so production inherits the operator's full process environment (including `HOME`, subscription auth caches, `PATH`, etc.). Tests pass `env={...}` to override specific keys (e.g., shim `PATH` redirection) while keeping the rest of `os.environ` intact.

  > **Future hardening (out of PR7 scope):** the original [SECURITY_COST_REPRODUCIBILITY_SPEC](../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §6.1 design called for a clean environment derived from a `.env` allowlist hash (recorded in the run manifest) plus `HOME` redirection to a controller-managed temp dir so the per-CLI auth cache (`CODEX_HOME`, `CLAUDE_CONFIG_DIR` per [ADR-0003](ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md)) lives inside the run directory rather than being inherited. PR7 deliberately did not implement that level of isolation — the env-overlay-on-os.environ semantics were locked at brainstorming Q1 ("env overlay" with a refinement note "so PATH and provider auth env survive unless tests explicitly override them"). Adding `HOME` redirection + .env-allowlist-hash filtering is appropriate when an adversarial-prompt or shared-machine threat model becomes Phase-1 scope.

- **Wall-clock timeout** via `subprocess.run(..., timeout=...)` derived from the adapter's `timeout_seconds` constructor argument (default 600.0). The controller's watchdog (PR2) provides the outer process-level enforcement per [SECURITY_COST_REPRODUCIBILITY_SPEC](../security/SECURITY_COST_REPRODUCIBILITY_SPEC.md) §4.3; the in-wrapper timeout exists as the inner ring of a graceful-then-forceful kill sequence. `subprocess.TimeoutExpired` produces `ProviderResult(status="killed")` + `<killed:wall_clock_timeout>` artifact token.

Stub providers ignore all of the above and synthesize results in pure Python.

## Resolved at PR7

The wrapper implementations verified the following points and confirmed them inline:

1. **Exact flag spelling:** verified against the installed CLI versions.
   - Codex: `[exec, --json, --workspace-write, <ws>, --prompt-file, <path>]`
   - Claude: `[-p, --input, <path>, --workspace, <ws>]`
   - Note: real CLI version drift is detected via `BLOCKED_PROVIDER_CAPABILITY` (see `docs/phase0/runbooks/cli_regression.md`).

2. **Auth-expiry stderr fingerprint:** conservative seed list pinned at `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS`. Marked "not real-provider-verified yet"; first real auth-failure observation refreshes the list per the maintenance loop in `docs/phase0/runbooks/auth_expiry.md`.

3. **Codex terminal-event behaviour:** if absent, `_parse_codex_ndjson` returns a sentinel that the wrapper maps to `ProviderResult(status="failure")` with a `<failure:missing_terminal_event>` artifact token (no `reason` field — `ProviderResult` schema is closed).

4. **Streaming vs. buffering:** PR7 buffers, per the ADR's stated default. (No behavior change.)

## Consequences

- The `ProviderAdapter` ABC in PR1 carries no subprocess code; PR7 implementers can subclass it without rewriting the interface.
- Stubs in PR1 (and through PR6) emit `provider_version` strings (`stub_codex.v1`, `stub_claude.v1`) and use `started_at`/`finished_at` UTC ISO timestamps so the scoreboard schema works identically for stub and real runs.
- The scrubber dependency is explicit: PR7 cannot land before PR4.
- Auth-expiry handling is a single code path the wrapper produces, not scattered detection logic across the controller.
- Real-provider behavior in CI is intentionally untested; the stub-only gate from [ADR-0003](ADR-0003-SUBSCRIPTION-ONLY-LIMITS.md) holds.

## Status

Accepted — verified at PR7 close-out. The four "verified at PR7" punch-list items are resolved inline in §"Resolved at PR7" above; the body of the ADR no longer carries forward-looking markers. Subsequent CLI version drift is detected via `BLOCKED_PROVIDER_CAPABILITY` and the maintenance loops in `docs/phase0/runbooks/{auth_expiry,cli_regression}.md`, NOT by re-running this ADR.
