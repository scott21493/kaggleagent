# Auth Expiry and Recovery

**When this fires:** Real provider (Codex or Claude) returns `BLOCKED_AUTH` status—indicated by exit code ≥64, exit code 1 combined with auth-related stderr pattern, or `arena provider health` health-check failure.

**Severity:** Block — the controller stops launching new real-provider tasks until auth is restored.

**Time-to-recover (typical):** 2–5 minutes for re-authentication if credentials are valid; longer if credentials have expired on the auth service side or require account recovery.

**Source:** `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §7.3.

## When This Fires

The provider wrapper detects authentication failure through three fallback detection layers:

1. **Exit code ≥64:** Wrapper treats this as an explicit auth signal (e.g., `BLOCKED_AUTH` sentinel).
2. **Stderr pattern fallback:** If exit code is 1 (ambiguous), the wrapper consults the regex pattern list in `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS`. Conservative seed patterns match phrases like "authentication failed," "credential expired," "session expired," "please log in," "not signed in," etc.
3. **Health-check failure:** The `arena provider health <name>` command fails when the provider is unreachable or auth is invalid.

When any of these layers detect auth expiry, the controller marks the task `BLOCKED_AUTH` and emits a trace event. The scoreboard records the blocked status. Controller stops issuing new real-provider work.

## Symptoms

- Log output or trace event shows `status="blocked"` with `<blocked:auth>` artifact token.
- `arena provider health codex` or `arena provider health claude` exits with status code indicating auth failure (e.g., stderr contains auth-related text).
- Console output may show "authentication failed," "credential expired," "Please re-authenticate," or similar phrasing in stderr scrubbed logs.

## Diagnose

1. Run the health check for each provider:
   ```bash
   arena provider health codex
   arena provider health claude
   ```
   Exit status and stderr will indicate which provider(s) have auth issues.

2. Check the scrubbed trace (`traces/<run_id>/` directory) for the failing task's stderr. Look for the exact auth-failure phrase. If it is not in `AUTH_EXPIRY_PATTERNS`, capture it verbatim for the maintenance loop below.

3. If only one provider is blocked, the other may continue (e.g., Claude tasks can proceed if only Codex auth expired).

## Recover

1. Re-authenticate using your provider's documented auth command:
   ```bash
   codex login
   # or
   claude login
   # (or the CLI's current documented auth command)
   ```
   Follow the on-screen prompts. If the CLI prompts you for a web browser, complete the login flow and return to the terminal.

2. Verify auth is restored:
   ```bash
   arena provider health codex
   arena provider health claude
   ```
   Both should exit with status 0 and output health details (no auth error).

3. If your subscription or credentials are truly expired (not just a local session):
   - Visit your Codex or Claude account portal (typically web-based).
   - Verify subscription is active and credentials are valid.
   - Log in via the web portal first, then retry the CLI auth flow above.

4. Once auth is restored and `arena provider health` passes, the controller automatically resumes issuing new real-provider tasks on the next invocation.

## Maintenance Loop

When the health-check or wrapper first encounters an **unknown** auth-failure phrase (not yet in `AUTH_EXPIRY_PATTERNS`):

1. **Capture:** Extract the exact stderr phrase from the scrubbed trace (`traces/<run_id>/...`). Example: "Authorization token not found in $HOME/.config/my_cli/auth"
2. **Add pattern:** Add a new regex pattern to `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS` that matches the phrase but does not over-match unrelated failures. Keep patterns conservative (e.g., anchor on "token" + ("invalid" | "expired") rather than "token" alone).
3. **Add regression test:** Add a parametrized test case to `tests/test_provider_auth.py::test_matches_auth_expiry_positive` verifying the new pattern. Also add a negative-case test to prevent false positives.
4. **Update runbook:** Optionally note the new pattern here and link to the PR commit.

Health probes (`arena provider health <name>`) are intended to be **non-mutating** and **token-free** — they run a simple version check or health query without consuming rate-limit quota or requiring stored auth. If a provider CLI changes its health behavior to require interactive login or consume tokens, treat that as a `BLOCKED_PROVIDER_CAPABILITY` regression (see `docs/phase0/runbooks/cli_regression.md`), not an auth expiry.

## Related

- **cli_regression.md:** If the provider CLI changed its flags or no longer supports non-interactive health checks, see the capability-regression runbook.
- **reboot.md:** After a machine reboot, run the post-reboot sequence including `arena provider health` to refresh auth state.
- **ADR-0004 §"Auth-expiry surface":** Design and integration details for the three-layer detection system.
