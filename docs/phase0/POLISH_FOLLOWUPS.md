# Phase-0 Polish Follow-ups

Items caught by the post-PR7 external review that do not block Phase-0 close and are not Phase-1 priorities. Each is small, in-scope, and worth landing as time permits.

The post-PR7 polish branch already addressed:
- Reviewer #2 (Claude argv drift) — wrapper updated to `-p --output-format json --add-dir <ws>` + stdin prompt.
- Reviewer #3 (Codex argv drift) — wrapper updated to `exec --json -s workspace-write -C <ws> --skip-git-repo-check` + stdin prompt.
- Reviewer #7 (packet builders hardcoded `stub_claude`) — `provider` parameter threaded through 4 builders + 12 regression tests.
- ADR-0004 status header + Resolved-at-PR7 section + workspace/environment section + drift-caught-post-close acknowledgment.

The follow-ups below remain.

---

## #4 — Make provider stdout a transport envelope, not the source of truth

**Reviewer's framing:** real Claude under prompt-template-instruction-only can emit prose; relying on stdout JSON is brittle. The reviewer suggested using `--output-format json` (Claude) and either `--output-schema` (Codex) or a wrapper-required `<workspace>/<schema>.json` file as the source of truth.

**Already partially addressed:** the post-PR7 polish landed `--output-format json` for Claude. Real Claude's output is now constrained to a single JSON object that the wrapper's parser consumes deterministically. For Codex, the polish did not yet move to file-based artifacts as the source of truth.

**What's left:** make `<workspace>/<schema_name>.json` the canonical artifact for Claude success paths (already done — the post-bridge fix at PR7 commit `84a7ae2` materialises validated JSON to disk and references it in `ProviderResult.artifacts`). For Codex, evaluate whether `--output-schema <FILE>` is supported on the installed CLI version and, if so, add a wrapper-managed schema file that constrains the model's final NDJSON event. Failing that, require Codex to emit a `provider_result.json` in the worktree that the wrapper reads instead of parsing the NDJSON terminal event.

**Effort:** 1 PR, ~200 LOC. Test surface: shim integration tests assert Codex writes the file; existing NDJSON parser becomes a fallback for older CLI versions.

---

## #9 — Add `arena doctor --strict` mode

**Reviewer's framing:** `arena doctor` is intentionally always-exits-0 (readiness inventory, not fail-fast). For automated scripts that want a single nonzero gate, expose a `--strict` mode. The security spec also calls for doctor to fail when Codex auth lives inside the repo tree or when Kaggle credentials have wrong filesystem perms — neither check exists today.

**What's left:**
- `arena doctor --strict` exits nonzero if any inventory line is red ❌ (`FAIL`).
- Add the security-spec checks: refuse to start if `~/.codex` lives inside the repo working tree (a leaked auth token); warn if `~/.kaggle/kaggle.json` has read perms wider than 0600.
- Keep the default `arena doctor` invocation behavior unchanged (always exits 0).

**Effort:** 1 PR, ~150 LOC + a few new acceptance tests.

---

## #10 — Validate task packets on `peek()` and `dequeue()`, not just `enqueue()`

**Reviewer's framing:** `TaskQueue.enqueue()` validates the packet schema, but `peek()` and `dequeue()` read JSON files unchecked. The provider adapters re-validate on `invoke()`, but `arena run-next` inspects packet fields, chooses providers, creates workspaces, and builds sandbox policy BEFORE that adapter validation fires. A malformed file on disk could cause partial side effects before being rejected.

**What's left:**
- Move the `validate("task_packet", packet)` call from `enqueue()` into a private `_load_validated()` helper.
- Have `peek()` and `dequeue()` go through the helper.
- Optionally add file locking via `fcntl` / Windows `msvcrt.locking` so concurrent `arena run-next` invocations don't race on dequeue.

**Effort:** 1 PR, ~80 LOC + 4 new tests covering the read-path validation gates.

---

## #12 — Persist prompt + provider_result + per-stream hashes for true offline replay

**Reviewer's framing:** PR4's TraceStore writes `events.jsonl` + `stdout.{raw,scrubbed}` + `stderr.{raw,scrubbed}`. The security spec's "record-and-replay without invoking real providers" mode also calls for the prompt JSON, the parsed `provider_result.json`, and per-stream content hashes so the scoreboard can be reconstructed deterministically from disk.

**What's left:**
- Extend `TraceStore.write_provider_streams(...)` to also write:
  - `prompt.json` (the task packet that was piped into the subprocess)
  - `provider_result.json` (the parsed `ProviderResult.to_dict()`)
  - `hashes.json` (sha256 of stdout.raw, stderr.raw, stdout.scrubbed, stderr.scrubbed, prompt.json, provider_result.json)
- Add `arena replay <run_id> --offline` that reconstructs the scoreboard purely from disk artifacts, never invoking a provider. Integrity-check via the hashes.
- The post-PR7 polish branch added an audit copy at `<workspace>/.arena_prompts/prompt_<task_id>.json`; that file's purpose is operator-visible audit. The trace-store-managed `prompt.json` is the replay source of truth (different audience, different lifecycle).

**Effort:** 1 PR, ~250 LOC + a new `arena replay --offline` command + acceptance tests that round-trip a known good run.

---

## Out-of-scope here

The reviewer's #1 (OS sandbox), #5 (real Git worktrees), #6 (slug-scoped runs), #8 (SI protected-file enforcement) are tracked in `docs/phase1/PHASE_1_PRIORITIES.md` — they are Phase-1 work, not Phase-0 polish.

The reviewer's #11 (provider-family accounting) is intentionally deferred per Q4 brainstorming and the existing `TODO(PR8+)` comment in `arena/budget/governor.py`.

The reviewer's #2, #3, #7 were caught at the wire and fixed in the post-PR7 polish branch.
