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

- **`arena doctor`**: Validates workspace structure, fixture files, and scoreboard schema. Exits 0 if healthy, exits 1 if problems are found. Repair instructions will be printed to the console.
- **`arena provider health codex`**: Validates that the Codex CLI is installed, accessible, and auth is valid. No actual computation is run.
- **`arena provider health claude`**: Validates that the Claude CLI is installed, accessible, and auth is valid. No actual computation is run.

All three should return status 0. If any fails, refer to the `auth_expiry.md` or `cli_regression.md` runbooks to diagnose and resolve.

## State Reconstruction

The controller reconstructs durable state from the following sources on the next invocation:

1. **Scoreboard** (`scoreboard.db`): Task metadata, status, usage, and artifacts.
2. **Run directories** (`runs/<run_id>/`): Per-run artifacts and worktree state.
3. **Event logs and traces** (`traces/<run_id>/`): Detailed task execution history and provider communication.
4. **Task queue** (`queue.sqlite`): Pending and completed tasks.
5. **Worktrees** (`.claude/worktrees/`): Isolated git worktrees for independent experiments.
6. **Baselines** (`baselines/`): Fixture and provider version baselines used for regression detection.
7. **Freeze sentinel** (`freeze_self_improvement`): Self-improvement pause flag, if set.

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

If `arena doctor` reports schema or structure errors:
- Refer to the error message for the specific repair step.
- Common issues: missing fixture file, corrupted queue entry (very rare), or stale worktree (safe to delete manually).

If `arena provider health` fails, see `auth_expiry.md` to refresh credentials.

## Related

- **auth_expiry.md:** If provider health checks fail, reboot may have interrupted auth session; re-authenticate.
- **cli_regression.md:** If provider version changed during downtime (e.g., auto-update), the regression runbook covers flag or capability changes.
