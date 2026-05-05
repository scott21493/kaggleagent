# Reboot: Post-Restart Recovery and State Reconstruction

**When this happens:** After the machine restarts and you return to arena work.

**Severity:** Informational — reboot is expected; state is durable and automatically reconstructed.

**Time-to-recover:** 1–2 minutes to validate health and reissue blocked tasks.

**Source:** `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §7.4.

## Canonical Post-Reboot Sequence

After logging back in, run the following sequence in the arena working directory:

```bash
arena doctor
arena provider health codex
arena provider health claude
```

- **`arena doctor`**: Readiness inventory — validates the fixture manifest and prints status lines for each provider CLI (green `OK` for OK, yellow `WARN` for NOT_FOUND, red `FAIL` for BLOCKED_AUTH / BLOCKED_PROVIDER_CAPABILITY / ERROR). **Always exits 0** (intentionally — doctor is an inventory, not a fail-fast gate). Read the printed lines to spot any red `FAIL` entries.
- **`arena provider health codex`**: Fail-fast check — runs `codex --version` + `codex --help` (cheap, non-mutating). Exits 0 on `OK`, exits 1 on any other `HealthCode`. Prints the runbook reference on failure.
- **`arena provider health claude`**: Same shape for Claude.

`arena doctor` always exits 0; `arena provider health <name>` is the surface that returns non-zero on real auth/capability problems. If either provider health check exits 1 or doctor's output shows a red `FAIL` line, refer to the `auth_expiry.md` or `cli_regression.md` runbooks to diagnose and resolve.

## State Reconstruction

The controller reconstructs durable state from the following sources on the next invocation:

1. **Scoreboard** (`scoreboard.sqlite`): Per-experiment rows — task metadata, status, usage, artifact paths, provider version.
2. **Run directories** (`runs/<run_id>/`): Per-run state. The `runs/<run_id>/queue/*.json` directory holds the file-backed task queue (each pending task is one JSON file written by `arena plan`).
3. **Event logs and traces** (`traces/<run_id>/<task_id>/events.jsonl` for per-task events; `traces/<run_id>/run.jsonl` for run-level events; `traces/<run_id>/<task_id>/{stdout,stderr}.{raw,scrubbed}` for real-provider stream artifacts).
4. **Worktrees** (`worktrees/<slug>/<exp_id>/`): Per-experiment workspace dirs that real adapters use as their cwd + `--workspace` argument.
5. **Baselines** (`runs/.baselines/<slug>/provider_versions.json` and `runs/.baselines/<slug>/fixture_hash.json`): Per-slug baseline files populated by `record_provider_version` and `record_fixture_hash` (PR4); sticky across `arena init-fixture` cycles.
6. **Freeze sentinel** (`SELF_IMPROVEMENT_FROZEN.md`): Markdown-with-fenced-JSON sentinel written by `arena self-improve scan` (PR6) when any §7.3 trigger fires. Source of truth for whether self-improvement is allowed; deletion is the unfreeze action.

All state is persisted to disk; nothing is lost during reboot. The controller reads these sources to determine what was running, what completed, and what remains to be scheduled.

## Interrupted Tasks

**Important:** Any provider task (Codex or Claude) that was running **during** the reboot is **not automatically restarted**.

When you inspect the scoreboard or trace logs, you will see:
- The last durable row or trace event before the reboot occurred.
- A clear marker or gap indicating the machine was unavailable.

**You, the operator, review the last durable state and decide how to proceed:**
- If the task was in an early phase (e.g., just sent a request to Codex), you may re-issue it with confidence that no output was lost.
- If the task had completed a meaningful intermediate step (e.g., an implementation was generated), you may continue from that artifact rather than re-running.
- If the task's state is ambiguous, you can re-issue it and compare outputs for idempotency.

The controller does **not** provide automatic task restart. This is intentional: partial or mid-flight provider work requires human judgment to validate, and automatic restart could silently duplicate work or apply stale artifacts.

## Maintenance Loop

No ongoing action is required after the reboot sequence passes. The controller will resume normal scheduling on the next `arena run-next` or similar command.

If `arena doctor` shows a red `FAIL` provider line (it still exits 0):
- Refer to the error message for the specific repair step.
- Common issues: missing fixture file, corrupted queue entry (very rare), or stale worktree (safe to delete manually).

If `arena provider health` fails, see `auth_expiry.md` to refresh credentials.

## Related

- **auth_expiry.md:** If provider health checks fail, reboot may have interrupted auth session; re-authenticate.
- **cli_regression.md:** If provider version changed during downtime (e.g., auto-update), the regression runbook covers flag or capability changes.
