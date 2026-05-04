# CLI Capability Regression

**When this fires:** Provider CLI (Codex or Claude) has a version change that removes or renames flags, making the arena wrapper's argv construction invalid. `arena provider health <name>` returns `HealthCode.BLOCKED_PROVIDER_CAPABILITY`, and `_get_provider("codex"/"claude")` raises `ProviderUnavailable(code="blocked_provider_capability")` BEFORE any subprocess invocation. **No scoreboard row is written, no trace event is emitted** for the blocked task — per ADR-0004 §"Process not started." The operator-visible signal is the failing health-check exit code + runbook reference.

Separately, **provider version drift** (the version changed but `--help` still works) is purely informational — it surfaces as a `<PROVIDER_VERSION_CHANGED:from=...>` artifact-path token on the next successful invocation, not as a blocked task. That drift signal is recorded by PR4's baseline-recording machinery and is intended to flag "you should run a smoke test against the new version" without blocking work.

**Severity:** Block — affected provider tasks cannot run until the wrapper is patched and redeployed.

**Time-to-recover:** 5–15 minutes to update the wrapper and run a smoke test; longer if flag semantics have changed significantly.

**Source:** `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §7.5.

## When This Fires

The provider CLI updates or is manually upgraded, and:
- A flag that the wrapper relied on is renamed or removed.
- A flag that worked in isolation no longer works in combination with others.
- The flag spelling or behavior subtly changed (e.g., `--input-file` becomes `--input`, or requires a different delimiter).

The health-check probe (`<exe> --version` or `<exe> --help`) returns exit code 2 with stderr matching a capability-related phrase ("unrecognized argument," "unknown flag," "no such option"). `arena/providers/health.py::_classify_nonzero` maps this to `HealthCode.BLOCKED_PROVIDER_CAPABILITY` with `runbook="docs/phase0/runbooks/cli_regression.md"`.

## Symptoms

- `arena provider health codex` or `arena provider health claude` exits with status 1 and prints a red `❌ codex: BLOCKED PROVIDER CAPABILITY (...)` line plus a `Runbook: docs/phase0/runbooks/cli_regression.md` line.
- Stderr or stdout shows "flag not recognized," "unexpected argument," "unrecognized option," or similar.
- New `arena run-next` / `arena research-proxy` / `arena review` invocations against this provider exit with `typer.BadParameter` from `_get_provider`.
- Older successful invocations may have emitted `<PROVIDER_VERSION_CHANGED:from=...>` artifact tokens on prior scoreboard rows — this is the drift signal, not a capability-blocked marker.

## Diagnose

1. **Check version drift:**
   ```bash
   codex --version
   claude --version
   ```
   Compare the output to `runs/.baselines/<slug>/provider_versions.json` (the per-slug baseline file populated by `record_provider_version` from PR4). If the version has advanced, a flag change may have occurred.

2. **Inspect flag list:**
   ```bash
   codex --help
   claude --help
   ```
   Look for the flags the wrapper is trying to use. Arena's wrappers are hardcoded to use specific flags:
   - **Codex:** `exec`, `--json`, `--workspace-write`, `--prompt-file` (plus workspace path and file path arguments).
   - **Claude:** `-p`, `--input`, `--workspace` (plus path arguments).
   
   If any of these flags are missing or have changed spelling, proceed to the Recover section.

3. **Consult the ADR:**
   Read `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` §"Codex" and §"Claude" for the canonical verified argv sets. The document lists the exact flags and argument order that are known to work.

## Recover

### Step 1: Update the wrapper's argv construction

1. Locate the affected wrapper. The argv list is constructed inline inside `invoke()` — there is no `_build_argv()` helper:
   - Codex: `arena/providers/codex.py`, look for `argv = [self._executable, "exec", "--json", "--workspace-write", str(workspace), "--prompt-file", str(prompt_file)]`.
   - Claude: `arena/providers/claude.py`, look for `argv = [self._executable, "-p", "--input", str(prompt_file), "--workspace", str(workspace)]`.

2. Update the inline argv list to construct argv using the new flag spelling:
   ```python
   # Example: if --prompt-file was renamed to --input-file
   argv = [
       self._executable,
       "exec",
       "--json",
       "--workspace-write",
       str(workspace),
       "--input-file",  # renamed from --prompt-file
       str(prompt_file),
   ]
   ```

3. Run the wrapper's unit tests locally to verify the new argv is accepted by the (stubbed or real) provider:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/test_provider_codex.py -v
   .venv/Scripts/python.exe -m pytest tests/test_provider_claude.py -v
   ```

### Step 2: Update ADR-0004

Update `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` to record the verified flag set:

- Under §"Codex," update the argv list to reflect the new flags.
- Under §"Claude," update similarly.
- Add a comment noting the date and version in which this change was made.

Example:
```
#### Codex

Verified at PR7 (and updated in PR-N after version X.Y.Z released).

Argv: `[exec, --json, --workspace-write, <ws>, --input-file, <path>]`
```

### Step 3: Add or update regression test on the shim

The shim script (`conftest.py` fixture or similar integration test harness) must accept the new argv and validate it:

1. Locate `tests/conftest.py` and find the `fixture_codex_shim` or `fixture_claude_shim` function (or similar).
2. Update the shim's argv validation to accept the new flags.
3. Optionally, add an integration test that exercises the wrapper with the real (or stubbed) CLI to ensure the new argv works end-to-end.
4. Run the full test suite:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/test_provider_codex.py tests/test_provider_claude.py -v
   ```

### Step 4: Optional — pin or downgrade

If the new version is incompatible and you cannot update the wrapper immediately:
- Document the known-good version in a comment (e.g., "Verified with Codex >= 1.2.3 only").
- Consider pinning the provider version in your local environment or CI (e.g., `brew install codex@1.2.3`).
- Document the pin in the baseline or a separate version-lock file.

## No Fallbacks

The controller does **not** have fallback behaviors when provider capability is blocked:

- **No browser fallback:** The wrapper does not fall back to terminal UIs or web-based consoles. Non-interactive execution is a hard requirement.
- **No model API fallback:** The controller does not attempt to call Codex or Claude APIs directly (e.g., via REST or SDK). The CLI wrapper is the sole path.
- **No auto-resume:** Blocked tasks are not automatically retried after the wrapper update. You must re-issue them explicitly once the wrapper is patched and verified.

These policies are enforced per `docs/architecture/ADR-0003-SUBSCRIPTION-ONLY-POLICY.md`. CLI wrappers are the subscription-honoring surface; API calls would require separate auth and policy negotiation.

## Maintenance Loop

After a successful wrapper update and test pass:
1. Commit the changes to the wrapper and ADR.
2. Tag or note the commit in the PR/release notes.
3. Re-run any blocked tasks using the operator CLI (`arena run-next ...`).

Future provider CLI updates will be caught by the same diagnostic steps above. Monitor provider release notes for breaking changes to non-interactive flags.

## Related

- **auth_expiry.md:** If the health-check failure is due to auth (not capability), see the auth runbook.
- **reboot.md:** Machine reboot is unrelated to CLI capability, but may trigger a provider auto-update.
- **ADR-0004 §"Codex" and §"Claude":** Canonical verified argv sets and version history.
- **ADR-0003 §"Subscription-only policy":** Why fallbacks are disallowed and how to escalate CLI incompatibility issues.
