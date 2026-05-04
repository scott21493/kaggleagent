# CLI Capability Regression

**When this fires:** Provider CLI (Codex or Claude) has a version change that removes or renames flags, making the arena wrapper's argv construction invalid. The controller marks tasks `BLOCKED_PROVIDER_CAPABILITY` until the wrapper is updated.

**Severity:** Block — affected provider tasks cannot run until the wrapper is patched and redeployed.

**Time-to-recover:** 5–15 minutes to update the wrapper and run a smoke test; longer if flag semantics have changed significantly.

**Source:** `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §7.5.

## When This Fires

The provider CLI updates or is manually upgraded, and:
- A flag that the wrapper relied on is renamed or removed.
- A flag that worked in isolation no longer works in combination with others.
- The flag spelling or behavior subtly changed (e.g., `--input-file` becomes `--input`, or requires a different delimiter).

The health-check command fails because it cannot construct valid argv. The wrapper logs `BLOCKED_PROVIDER_CAPABILITY` status with an artifact token like `<blocked:provider_version_changed>`.

## Symptoms

- `arena provider health codex` or `arena provider health claude` exits with a non-zero status.
- Stderr or stdout shows "flag not recognized," "unexpected argument," "unrecognized option," or similar.
- Trace events show `status="blocked"` with `<blocked:provider_version_changed>` artifact token.
- New task launches fail with the same provider.

## Diagnose

1. **Check version drift:**
   ```bash
   codex --version
   claude --version
   ```
   Compare the output to `baselines/provider_versions.json` (or similar file tracked during fixture baseline creation). If the version has advanced, a flag change may have occurred.

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

1. Locate the affected wrapper:
   - Codex: `arena/providers/codex.py` (function `_build_argv()` or equivalent).
   - Claude: `arena/providers/claude.py` (function `_build_argv()` or equivalent).

2. Update the `_build_argv()` function to construct argv using the new flag spelling:
   ```python
   # Example: if --prompt-file was renamed to --input-file
   argv = [
       "codex",
       "exec",
       "--json",
       "--workspace-write",
       workspace_path,
       "--input-file",  # renamed from --prompt-file
       prompt_file_path,
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
