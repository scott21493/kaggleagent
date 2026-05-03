# PR7 — Real Adapters + Phase 0 Close

> **Source:** [docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md](2026-04-30-phase-0-implementation-dag-design.md) §11.
> **Source:** [docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §1.2 (closure conditions), §6.2 (research-fusion proxy loop), §7.3–7.5 (auth/reboot/CLI-regression runbooks).
> **Architectural contract:** [ADR-0004](../../architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md) — provider CLI invocation conventions.

---

## 1. Goal

Land the real-adapter subprocess wrappers, the provider-health command, the `arena eval-harness` orchestrator, the two acceptance test deliverables, the three operator runbooks, restore the coverage gate, and resolve every "verified at PR7" punch-list item in ADR-0004. After this PR merges to main, Phase 0 is closed: every §1.2 condition is provable under stub providers and every §6.2 step has a real CLI seam ready for an authenticated operator.

## 2. Architecture

PR7 has four design axes, each settled by Q&A before this spec was written:

1. **Real adapters depth (Q1):** *skeletal subprocess wrappers + shim integration tests*. Real adapter modules implement the ADR-0004 contract; unit tests `monkeypatch` `subprocess.run` for edge cases (missing CLI, non-zero exit, malformed JSON, timeout); shim integration tests put a fake `codex` / `claude` Python script on PATH (via DI override `executable=`) and exercise the real subprocess boundary. No auto-run real-CLI smoke; that lives in the runbooks as an operator checklist.
2. **DI surface (Q1 refinement):** adapters take `executable: str`, `env: Mapping[str, str] | None`, `cwd: Path | None` constructor args, plus `timeout_seconds` and `event_emitter`. `env` is an overlay on `os.environ` by default so PATH and provider auth env survive unless tests explicitly override them.
3. **Acceptance suite shape (Q2):** *one test per §1.2 condition*, each an independent focused assertion using production-facing CLI/APIs, with mutable state isolated via `tmp_path` or in-test cleanup.
4. **Eval-harness scope (Q3):** *thin orchestration wrapper*. Runs the full Phase-0 sequence under the chosen provider set; CLI failures surface as step failures (no special-casing); continue-collect when isolable; pure orchestration, no closure assertions, no hidden pytest invocation.

Cross-cutting decisions:

- **Cleanup pass scope (Q4):** spec §11 deliverables + coverage gate restore (50 → 70) + cosmetic "PR7 will…" → past tense / timeless wording. Genuine "PR7+" annotations stay. Governor's `_is_codex` / `_is_claude` substring TODO stays.
- **Full-loop test shape (Q5):** single sequential test with intermediate `§6.2 step N` step-labelled asserts. Drives the public CLI via `runner.invoke(app, [...])` independently of `arena eval-harness` (no shared orchestration helpers; small read-only helpers OK).
- **Operator diagnostic surface (Q6):** layered + terse text output. `provider_health.check(name) -> ProviderHealth` typed core; CLI rendering separate. `arena doctor` reuses the typed core directly. Stubs participate in `arena provider health`.
- **Auth-stderr fingerprints (Q7):** conservative seed-pattern list at `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS`. Tests cover positive AND negative cases. Wrappers prefer explicit exit-code semantics first; regex is fallback.

## 3. CLI commands

Three new subcommands plus an extension to `arena doctor`. All accept the existing `fixture_workspace` conftest fixture for test isolation.

### 3.1 `arena provider health <name>`

Exits 0 with `✅ <provider>: <version> (<sandbox_mode>; <detail>)` on `HealthCode.OK`. Exits 1 with `❌ <provider>: <CODE> (<detail>)` and a runbook reference line on any other code.

Health-check probe sequence (`provider_health.check(name)`):

| `name` | Probe | Result mapping |
|---|---|---|
| `stub_codex`, `stub_claude` | none — short-circuit | `OK`, version=`stub_codex.v1` / `stub_claude.v1`, sandbox_mode=`"deterministic"` |
| `codex`, `claude` | (1) `<executable> --version`; (2) `<executable> --help` | exit 0 → parse version → `OK`. `FileNotFoundError` → `NOT_FOUND`. exit ≥64 → `BLOCKED_AUTH` (unconditional). exit 2 → inspect stderr: auth phrase → `BLOCKED_AUTH`; flag/capability phrase → `BLOCKED_PROVIDER_CAPABILITY`; else `ERROR`. Other non-zero → `ERROR` (regex fallback applies). `TimeoutExpired` → `ERROR`. |

Health-check is **non-mutating and token-free** (just `--version` + `--help`, no LLM invocation, no workspace artifacts). If a provider CLI changes that, the runbook records it as `BLOCKED_PROVIDER_CAPABILITY`.

Health-check is **on the hot path of `_get_provider`** for real adapters (`arena run-next`, `arena research-proxy`, `arena review`). ~50–100 ms per call; eval-harness incurs ~400 ms cumulative. Caching deferred to PR8+.

### 3.2 `arena eval-harness <competition_slug> --providers stub|real`

Thin orchestration wrapper. Runs the full Phase-0 sequence per `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §2.2. In-process function calls (NOT subprocess fanout — preserves trace continuity and `_latest_run_id()` semantics). Lives inline in `arena/cli.py` next to the command; no new `arena/orchestrator.py` module.

**Provider mapping:**
- `--providers stub` → (`stub_codex`, `stub_claude`)
- `--providers real` → (`codex`, `claude`); `ProviderUnavailable` from any step is recorded as a step failure, not special-cased.

**Step ordering (continue-collect with prerequisite skips):**

```
init-fixture
plan
run-next (calibration)         # uses codex_provider
research-proxy                 # uses claude_provider
if impl_exp_id (looked up from scoreboard):
    evaluate --latest
    review --experiment <impl>  # uses claude_provider
    if review_exp_id:
        memory propose --review <review>
    else:
        skip ("review row not found")
else:
    skip evaluate, review, memory propose ("impl row not found")
self-improve scan
report
```

Skipping `evaluate --latest` when no impl row exists is a semantic correction: evaluate's purpose in the loop is the proxy implementation, not the calibration row.

**Exception → step status mapping:**
- `typer.Exit(0)` → `ok`
- `typer.Exit(N≠0)` → `failed`, `reason=f"exit {N}"`
- `typer.BadParameter`, `BudgetExceeded`, `KillSwitchActive`, `ProviderUnavailable` → `failed`, `reason=str(e) or type(e).__name__`

**Output:** rich-table-rendered step summary; columns `Step | Status | Reason`. Trailing line: `<n>/<total> steps ok; <m> failed; <k> skipped.` Exits 0 iff `failed_count == 0`.

**SI freeze handling:** `arena self-improve scan` is reported as ✅ ok regardless of whether the freeze sentinel was written. Detecting freeze post-hoc is the closure-suite's job (`test_phase0_acceptance.py::test_condition_14`), not the orchestrator's. Step status header/docstring clarifies "step execution status," not "no findings/no freeze."

### 3.3 `arena doctor` extension

Reuses `provider_health.check(name)` directly (no shelling out, no text parsing). Adds two lines to the existing doctor output, one per real provider. Final summary line is `arena doctor complete` (neutral phrasing — doctor exits 0 even when red provider lines printed; readiness inventory, not fail-fast).

```text
✅ fixture manifest
✅ codex CLI: 0.4.2 (workspace-write; auth ok)
⚠  claude CLI: not installed (stub-only is fine for CI)
arena doctor complete
```

Exit semantics:
- Doctor: 0 always (unless a hard error like missing fixture manifest)
- `arena provider health <name>`: 0 on `OK`, 1 on any other code (fail-fast)

### 3.4 `provider` Typer subapp as forward seam

`arena provider` becomes a Typer subapp (mirrors `memory_app` and `self_improve_app` from PR6). Reserved for future commands (§7.3 runbook references `arena provider login codex` — out of scope for PR7, runbook-only).

## 4. Real adapter contract

Both `RealCodexProvider` and `RealClaudeProvider` follow the same shape:

```python
class RealCodexProvider(ProviderAdapter):
    def __init__(
        self,
        *,
        executable: str = "codex",
        version: str,                      # required; from provider_health.check()
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
        event_emitter: TraceStore | None = None,
    ) -> None: ...
```

`version=` is required (not defaulted); the CLI's `_get_provider` resolution path runs `provider_health.check(name)` first and passes the parsed version forward. If `health.code == OK` but `health.version is None`, treat as `ProviderUnavailable(code=ERROR)` before adapter construction (protects baseline file from null version writes).

### 4.1 `invoke()` flow (both adapters)

1. Write `task_packet` JSON to `cwd / ".arena_prompts" / f"prompt_{task_id}.json"` (per ADR-0004 stdin contract; avoids inline stdin which mishandles on Windows + WSL2).
2. Build argv:
   - Codex: `[executable, "exec", "--json", "--workspace-write", str(cwd), "--prompt-file", str(tmp_file)]`
   - Claude: `[executable, "-p", "--input", str(tmp_file), "--workspace", str(cwd)]`
3. `subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=timeout_seconds, env=effective_env, cwd=str(cwd))` where `effective_env = {**os.environ, **(env or {})}`.
   - `FileNotFoundError` → `ProviderUnavailable(code=NOT_FOUND, ...)` raised — controller-level error, NO scoreboard row, NO trace event.
   - `subprocess.TimeoutExpired` → `ProviderResult(status="killed", reason="wall_clock_timeout")` plus partial scrubbed output if any.
4. `wall_seconds = time.monotonic() - start` (independent of any provider-reported usage).
5. Persist raw `stdout` / `stderr` via `TraceStore.write_provider_streams(...)` BEFORE any parsing (forensic recovery if scrubber has a bug). Apply scrubber; persist scrubbed via the same API. Raw paths NEVER appear in `ProviderResult.artifacts`, are NEVER passed back into provider context, are NEVER emitted to the trace event stream, are NEVER rendered in `arena report`.
6. Map exit code:

| Exit code | Result | Auth-regex check? |
|---|---|---|
| `0` | `success` (parse stdout → artifacts, usage) | no |
| `1` | `failure` → upgrade to `blocked` + `<blocked:AuthFailureBreaker>` + `<runbook:docs/phase0/runbooks/auth_expiry.md>` artifact tokens if `matches_auth_expiry(stderr)` matches | yes (fallback) |
| `2` | `blocked` (reason from packet hint or generic) | no |
| `≥ 64` | `blocked` + AuthFailureBreaker + runbook tokens (exit code dispositive) | no |
| signal-induced (`TimeoutExpired`) | `killed`, `reason="wall_clock_timeout"` | no |
| `FileNotFoundError` | `ProviderUnavailable` raised; no row, no event | n/a |

`ProviderResult.status` stays inside the schema enum (`success | failure | blocked | killed | interrupted`). `BLOCKED_AUTH` lives only in `HealthCode` and CLI display labels — never as a result status.

### 4.2 Deterministic usage on every result status

`UsageProxy` records the schema's exact six fields (no `provider_calls`, no `shell_commands_observed` — `provider_calls` is the scoreboard `COUNT(*)` invariant maintained at the scoreboard boundary):

```python
UsageProxy(
    input_chars=len(prompt_json),
    output_chars=len(scrubbed_stdout) + len(scrubbed_stderr),
    wall_seconds=measured_wall_seconds,
    shell_commands=parsed.get("shell_commands", 0),
    failed_commands=parsed.get("failed_commands", 0),
    waste_events=parsed.get("waste_events", 0),
)
```

On parser failure, the four `parsed.*` fields default to 0; wall_seconds and char counters still record real cost. Failure modes still consume budget governor accumulators correctly.

### 4.3 Parser split

`arena/providers/codex.py::_parse_codex_ndjson(scrubbed_stdout) -> dict`: NDJSON event stream. The final event SHOULD summarize artifacts + usage; if absent, returns sentinel mapping to `ProviderResult(status="failure", reason="missing_terminal_event")` (resolves ADR-0004 open question #3).

`arena/providers/claude.py::_parse_claude_response(scrubbed_stdout, *, role, phase) -> dict`: single-JSON stdout. Validates against the role+phase-appropriate schema. Dispatch table (mirrors stub_claude 1:1):

| `(role, phase)` | Schema |
|---|---|
| `("review", "FUSION_PROXY_REVIEWED")` | `research_review` |
| `("research_proxy", "RESEARCH_QUESTION_CREATED")` | `research_question` |
| `("research_proxy", "METHOD_DIGEST_CREATED")` | `paper_digest` |
| `("research_proxy", "FUSION_PROPOSAL_CREATED")` | `fusion_proposal` |
| `("advisory_planning", "STRATEGY_RECOMMENDED")` | `strategist_recommendation` |

`schemas/review.schema.json` (different shape, `rev_…` ids, reviewer/findings) is NOT consumed by Claude in PR7. Out of scope.

### 4.4 Auth-pattern seed (`arena/providers/auth.py`)

```python
"""Auth-expiry stderr-pattern fallback classifier.

Patterns are CONSERVATIVE SEEDS, NOT real-provider-verified. The first
real auth-failure observation MUST refresh this list (see
docs/phase0/runbooks/auth_expiry.md "Maintenance loop")."""

AUTH_EXPIRY_PATTERNS = [
    r"(?i)(authentication|credential|session|login|token|auth).*(failed|expired|invalid|denied|required)",
    r"(?i)please (re-?)?(authenticate|log\s*in)",
    r"(?i)\b401\b",
    r"(?i)not (logged in|signed in)",
]


def matches_auth_expiry(stderr: str) -> bool: ...
```

Tests cover positive AND negative cases — broad words like `token` or `login` alone don't overclassify.

### 4.5 `ProviderUnavailable` exception

```python
class ProviderUnavailable(RuntimeError):
    """Raised when a real provider cannot be invoked before subprocess
    task start: missing binary, expired auth, or missing required CLI
    capability. Per ADR-0004 §"Process not started" — the controller
    treats this as a hard failure that produces NO scoreboard row and
    NO trace event."""

    def __init__(
        self,
        provider: str,
        code: HealthCode | str,
        detail: str,
        runbook: str | None = None,
    ) -> None: ...
```

Caught explicitly at:
- `arena run-next` / `arena research-proxy` / `arena review` → `typer.BadParameter` with operator-friendly message including the runbook ref
- `arena eval-harness` → `_StepResult(status="failed", reason="ProviderUnavailable: ...")`

## 5. TraceStore extension

Add a single new method to `arena/observability/trace_store.py`:

```python
def write_provider_streams(
    self,
    *,
    task_id: str,
    raw_stdout: str,
    raw_stderr: str,
    scrubbed_stdout: str,
    scrubbed_stderr: str,
) -> ProviderStreamPaths:
    """Write four artifacts at:
        <root>/<run_id>/<task_id>/{stdout.raw, stderr.raw,
                                   stdout.scrubbed, stderr.scrubbed}
    Returns paths in a frozen dataclass. Raw paths are written first
    (forensic-recovery if scrubber has a bug). Per ADR-0004 §scrubber
    attachment."""
```

`ProviderResult.stdout_path` / `stderr_path` reference the scrubbed paths only.

## 6. Sandbox policy extension

`arena/sandbox/policy.py::_default_blocked_paths()` extends to include `<workspace_root>/traces/`:

```python
def _default_blocked_paths(workspace_root: Path | None = None) -> frozenset[Path]:
    """Canonical secret/credential/forensic paths providers must never read."""
    home = Path("~").expanduser().resolve()
    env_path = ...
    traces_path = (
        _resolve(workspace_root / "traces") if workspace_root is not None
        else _resolve(Path("traces"))
    )
    return frozenset({
        home / ".kaggle",
        home / ".codex",
        home / ".claude",
        env_path,
        traces_path,
    })
```

`blocked_paths` wins over `allowed_paths` for both reads (`SECRET_READ` denied) and writes (`PROTECTED_WRITE` denied). Two regression tests pin both invariants — even when packet's `allowed_paths` includes `traces/`, both read and write attempts are rejected.

## 7. Provider version drift (zero new API)

`record_provider_version(slug, provider, version, root)` is provider-name-agnostic — adding `("codex", "<parsed>")` and `("claude", "<parsed>")` baselines flows through the existing API unchanged. PR4's sticky-baseline semantics + `<provider_version_changed>` artifact-token pipeline cover real-CLI drift identically to the stub case.

CLI integration: `arena run-next` (and parallel paths) call `record_provider_version(..., version=provider.version)` after `_get_provider(...)`, where `provider.version` resolves to the parsed real version for real adapters or the hardcoded stub version for stubs. No new code on the baseline-recording side.

## 8. File structure

```
arena/providers/
├── codex.py               (NEW: real adapter, NDJSON parser)
├── claude.py              (NEW: real adapter, role+phase schema dispatch)
├── health.py              (NEW: ProviderHealth dataclass, HealthCode enum, check())
├── auth.py                (NEW: AUTH_EXPIRY_PATTERNS + matches_auth_expiry)
├── base.py                (modify: add ProviderUnavailable exception)
├── stub_codex.py          (cleanup: PR7 will → past tense)
└── stub_claude.py         (cleanup: PR7 will → past tense)

arena/observability/
└── trace_store.py         (extend: write_provider_streams + ProviderStreamPaths)

arena/sandbox/
└── policy.py              (extend: _default_blocked_paths includes traces/)

arena/cli.py               (extend:
                              - provider Typer subapp + provider health command
                              - eval-harness command + _StepResult helper
                              - doctor extension
                              - _get_provider real-provider branch with health-check)

arena/research_proxy/
└── question_generator.py  (cleanup: timeless wording)

arena/self_improvement/
└── scan.py                (cleanup: production CLI adapters wording)

tests/
├── test_provider_codex.py             (NEW: monkeypatch + shim integration)
├── test_provider_claude.py            (NEW: monkeypatch + shim integration)
├── test_provider_health.py            (NEW: stub paths + real shim paths)
├── test_provider_auth.py              (NEW: positive + negative regex coverage)
├── test_cli_provider_health.py        (NEW: CLI text + exit codes)
├── test_cli_eval_harness.py           (NEW: stub happy + partial failure + provider mapping)
├── test_cli_doctor.py                 (extend: provider section, doctor exits 0 on missing CLIs)
├── test_cli_get_provider.py           (NEW: real-provider resolution + ProviderUnavailable)
├── test_observability_trace_store.py  (extend: write_provider_streams)
├── test_observability_version_baseline.py  (extend: codex/claude provider names)
├── test_sandbox_policy.py             (extend: traces/ in _default_blocked_paths;
                                                 read AND write denial precedence)
├── test_phase0_acceptance.py          (NEW: 15 condition-style tests, §1.2 mapping table)
├── test_research_proxy_full_loop.py   (NEW: single sequential test, §6.2 step labels)
├── test_runbooks_exist.py             (NEW, optional: assert 3 files exist with key headers)
└── conftest.py                        (extend: shim_codex_executable, shim_claude_executable)

docs/phase0/runbooks/
├── auth_expiry.md         (NEW: §7.3 expansion + maintenance loop)
├── reboot.md              (NEW: §7.4 expansion)
└── cli_regression.md      (NEW: §7.5 expansion)

docs/architecture/
└── ADR-0004-PROVIDER-CLI-INVOCATION.md  (modify: status → "accepted (verified)";
                                                  4 punch-list items resolved inline)

pyproject.toml             (modify: fail_under 50 → 70; remove TODO(PR7) comment)
```

## 9. §1.2 closure-condition coverage map

Each of the 15 conditions has a representative test in `tests/test_phase0_acceptance.py::test_condition_NN_<name>`. The file's docstring carries the §1.2 wording verbatim for drift detection by-eyeball.

| § | Condition (abbrev.) | Mutable state isolation |
|---|---|---|
| 01 | Controller creates packets | fixture_workspace |
| 02 | Codex via adapter or stub | fixture_workspace |
| 03 | Claude via adapter or stub | fixture_workspace |
| 04 | Stdout/stderr captured/scrubbed/replayable | fixture_workspace |
| 05 | Tabular fixture init/eval/score | fixture_workspace |
| 06 | Calibration baseline completes (`status="completed"`, not `"ok"`) | fixture_workspace |
| 07 | Research-fusion proxy completes | fixture_workspace |
| 08 | Claude reviews ≥1 implementation | fixture_workspace |
| 09 | Scoreboard records metrics/cost/wall/artifacts/versions | fixture_workspace |
| 10 | Governor enforces hard ceilings | fixture_workspace |
| 11 | Kill switch stops run without LLM | tmp_path env var override |
| 12 | Sandbox denies secrets / blocks network | fixture_workspace |
| 13 | Memory updates proposed as deltas | fixture_workspace |
| 14 | SI freeze fires on regression | in-test sentinel.unlink() cleanup |
| 15 | CI passes with stubs (invokes StubCodexProvider with valid packet, asserts `status == "success"`, uses `tmp_path`) | tmp_path |

Token checks parse `artifact_paths` JSON before substring match (`paths = json.loads(row["artifact_paths"]); "<step:X>" in paths`) — no raw-JSON substring matching.

Tests are independent — pytest-xdist-safe, `pytest -k condition_06` filterable.

## 10. §6.2 full-loop coverage

`tests/test_research_proxy_full_loop.py` — single function `test_full_research_proxy_loop_under_stubs(fixture_workspace, monkeypatch)`. Drives the public CLI via `runner.invoke(app, [...])`. Every assertion has a `"§6.2 step N: <what failed>"` message. No shared orchestration helpers with `arena eval-harness`; small read-only scoreboard queries are inline.

Step coverage:

| §6.2 step | CLI invocation | Post-state assertion |
|---|---|---|
| 0 | `init-fixture`, `plan` | exit 0; queue.peek() schema-valid |
| 1 | `run-next --provider stub_codex` | calibration row exists, `status="completed"` |
| 2–7 | `research-proxy --provider stub_claude` | rows with `<step:question>`, `<step:digest>`, `<step:fusion>`, `<step:implementation>` (after `json.loads(artifact_paths)`) |
| 8 | `evaluate --latest` | exit 0 (redundant with research-proxy's internal evaluation; proves the operator path) |
| 9 | `review --provider stub_claude --experiment <impl>` | review row with `<step:review>` token |
| 10 | `memory propose --review <review>` | `memory/proposals/mem_*.json` file exists, schema-valid, `namespace="research"` |

## 11. Runbooks

Three narrative prose files at `docs/phase0/runbooks/`. Header structure: **When this fires** / **Severity** / **Time-to-recover** / **Source** / **Symptoms** / **Diagnose** / **Recover** / **Maintenance loop** / **Related**. Free-form within sections.

### 11.1 `auth_expiry.md`

Expansion of `PHASE_0_SINGLE_SCOPE_PLAN.md` §7.3. Documents:
- The three auth-detection layers (exit ≥64; stderr regex fallback; health-check failure)
- Recovery commands as **placeholders**: `codex login` / `claude login` shown as "(or the CLI's current documented auth command)"
- The maintenance loop for when an unknown auth phrase appears: capture verbatim phrase from `stderr.scrubbed` → add to `AUTH_EXPIRY_PATTERNS` → add regression test → update runbook
- Health probes are non-mutating and token-free; if a CLI changes that, treat as `BLOCKED_PROVIDER_CAPABILITY`

References: cli_regression.md (boundary cases), reboot.md (post-reboot auth refresh), ADR-0004 §"Auth-expiry surface".

### 11.2 `reboot.md`

Expansion of §7.4. Documents:
- The canonical post-reboot sequence: `arena doctor` + `arena provider health codex` + `arena provider health claude`
- The durable-state inventory the controller reconstructs from on next invocation (scoreboard, run dirs, traces, queue, worktrees, baselines, freeze sentinel)
- "Any task running during reboot is not auto-resumed; the operator reviews the last durable row/trace and decides how to re-issue." (No invented row status.)
- No `arena resume` command exists in Phase 0

### 11.3 `cli_regression.md`

Expansion of §7.5. Documents:
- `BLOCKED_PROVIDER_CAPABILITY` symptoms (health-check fails, baseline drift surfaces via `<provider_version_changed>`)
- Diagnose: compare baseline version to current `--version`; check `--help` flag list for renames/removals
- Recover: update `_build_argv()` in the affected wrapper; update ADR-0004's verified flag set; add or adjust regression test on the shim; optional downgrade pin
- What the controller does NOT do: browser fallback, model API fallback, auto-resume

### 11.4 Optional cheap coverage

`tests/test_runbooks_exist.py` — three-line test asserting each file exists and contains its canonical title header. Catches accidental delete/rename. Skip a link-check script — runbooks are narrative deliverables; PR is already full.

## 12. Coverage gate

`pyproject.toml [tool.coverage.report] fail_under` raises 50 → 70. Current coverage is 91.63% — well above 70%, so the gate restoration is essentially free. `TODO(PR7)` comment deletes; no replacement comment needed.

The gate flip lands in the **last commit on PR7** (after every implementation/test commit) so a transient coverage dip during mid-PR development can't break the gate prematurely.

## 13. ADR-0004 verified-at-PR7 resolutions

Four punch-list items from the ADR; each resolved in PR7 and the ADR updated inline in the cleanup commit:

| ADR open Q | PR7 resolution |
|---|---|
| #1 — Exact flag spelling | Pinned to the shim's accepted argv set: Codex `[exec, --json, --workspace-write, <ws>, --prompt-file, <path>]`; Claude `[-p, --input, <path>, --workspace, <ws>]`. Real CLI version drift surfaces via `BLOCKED_PROVIDER_CAPABILITY`. |
| #2 — Auth-expiry stderr fingerprint | Conservative seed list at `arena/providers/auth.py::AUTH_EXPIRY_PATTERNS`. Marked "not real-provider-verified yet"; runbook documents the maintenance loop for first real-auth-failure observation. |
| #3 — Codex terminal-event behaviour | If absent, parser returns sentinel mapping to `ProviderResult(status="failure", reason="missing_terminal_event")`. |
| #4 — Streaming vs buffering | PR7 buffers (per ADR's stated default). |

ADR status header changes from `accepted (forward-looking; verified-on-implement at PR7)` to `accepted (verified)`.

## 14. Cleanup pass touch-points

Behaviour-neutral except the coverage gate restore. Lands as the **last commit on PR7**.

| File:line | Action |
|---|---|
| `pyproject.toml:195-198` | Delete TODO(PR7) comment + comment block; flip `fail_under` 50 → 70 |
| `arena/providers/stub_codex.py:36` | "PR7 with real Codex will produce…" → past tense or drop PR ref |
| `arena/providers/stub_claude.py:410` | "After PR7's real Codex lands…" → past tense or drop PR ref |
| `arena/research_proxy/question_generator.py:16` | "PR7's real Claude will replace this with…" → "Real Claude adapters can replace this deterministic builder in production runs." (timeless) |
| `arena/research_proxy/question_generator.py:61` | "(or real Claude in PR7)" → "(or real Claude in production)" |
| `arena/self_improvement/scan.py:83` | "(or PR7's real adapters)" → "(or production CLI adapters)" |
| `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` | Status header + 4 punch-list resolutions + Open-Questions section → Resolved |
| `arena/budget/governor.py:39` | LEAVE — substring `_is_codex` / `_is_claude` still partitions cleanly with real names "codex" / "claude". Per Q4 rule. |
| Multiple `PR7+` annotations elsewhere | LEAVE — genuine post-Phase-0 work. Per Q4 rule. |

## 15. Out of scope (deferred to PR8+ or beyond)

- `arena resume --from-blocked` / `arena resume --dry-run` commands — runbooks are narrative-only per spec §14
- `arena provider login` CLI subcommand (the subapp seam exists for future extension)
- Closure-suite duplication inside `arena eval-harness` (Q3 rule: orchestration vs. acceptance are distinct surfaces)
- `--json` flag on `arena provider health` (text-first; deferred until programmatic consumer exists)
- Health-check result caching across `_get_provider` calls within an eval-harness run
- Real-CLI smoke tests in CI (per §1.3 non-goal: "CI jobs using real Codex or Claude subscription credentials")
- Glob/suffix support in SandboxPolicy (Phase-0 chose directory-granularity blocking for `traces/`)
- Externalizing `AUTH_EXPIRY_PATTERNS` into a YAML config (Q7 deferred; split out only if patterns proliferate)
- Schema changes — no schema files modified by PR7
- Phase enum changes — no new Phase values

## 16. Risk register (pre-implementation)

| Risk | Mitigation |
|---|---|
| Shim CLI on Windows: Python script + `.cmd` wrapper edge cases (PATH resolution, shebang interpretation) | conftest fixtures generate both forms; tests run on Windows CI; shim path is absolute (no PATH lookup ambiguity in production code) |
| Real adapter integration tests flake on subprocess timing (e.g., `TimeoutExpired` mismatch) | use generous `timeout_seconds=30` in tests; assert on `ProviderResult.status` not on wall_seconds bounds |
| `traces/` blocking breaks an existing test that reads from `traces/` for diagnostic | grep all tests for `traces/` reads before the policy change; extend the existing tests' allowed-paths if any are legitimately reading raw streams (none expected — scrubbed reads happen via TraceStore methods, not direct file IO) |
| ADR-0004 cleanup forgets a "verified at PR7" marker | last-commit cleanup grep'd against ADR before commit; spec self-review checks |
| Coverage gate flip exposes a hidden untested path | current coverage 91.63%; gate flip to 70 has 21+ percentage-point margin; if a PR7 module lands at <70%, the gate catches it pre-merge |
| `_get_provider` health-check on every resolution adds overhead to existing PR1-PR6 stub flows | stubs short-circuit health-check (no subprocess); only real adapters incur the 50-100ms; stub-only test runs are unaffected |

## 17. Plan-review preempts (carry from PR1–PR6)

Carry-forward conventions for any subagent implementing this PR:

- `from datetime import UTC, datetime`; `datetime.now(UTC).isoformat(timespec="seconds")` for date-time format-checker compatibility
- Use `StrEnum` where Phase enum is referenced; do NOT add Phase values without updating `schemas/task_packet.schema.json` (drift guard at `tests/test_controller_state.py`)
- Tests use the existing `fixture_workspace` conftest fixture for CLI tests; add `shim_codex_executable` / `shim_claude_executable` for shim integration tests
- Watch for pyupgrade modernizations (StrEnum, contextlib.AbstractContextManager, collections.abc imports for Iterator/Callable/Mapping)
- Use `.venv/Scripts/python.exe` for all Python invocations
- `ScoreboardStore.insert_experiment` takes `artifact_paths: list[str]` — pass a plain list, NOT `json.dumps(...)`
- `TraceStore.emit` validates against `event.schema.json` with `additionalProperties: false` on payload — only use allowed keys
- Stay schema-compatible: `UsageProxy` has exactly 6 fields (no `provider_calls` extension), `ProviderResult.status` enum is fixed
- `SandboxPolicy._default_blocked_paths` extension test must cover both read denial AND write denial precedence over packet `allowed_paths`

## 18. Acceptance

PR7 is merge-ready when all of the following are true:

1. All 15 §1.2 closure conditions pass under stub providers (`pytest tests/test_phase0_acceptance.py`).
2. The complete 10-step §6.2 research-proxy loop runs end-to-end under stubs (`pytest tests/test_research_proxy_full_loop.py`).
3. `arena provider health codex` and `arena provider health claude` work on a configured local machine (operator-verified, not CI-verified); fail cleanly with `BLOCKED_AUTH` / `BLOCKED_PROVIDER_CAPABILITY` / `NOT_FOUND` when they should.
4. `arena eval-harness tabular_binary_v1 --providers stub` exits 0 with all 9 steps reported as ok.
5. `arena doctor` exits 0 even with both real CLIs missing; `arena provider health <name>` exits 1 in the same situation.
6. Coverage gate at 70% holds on the merged trunk.
7. ruff check / ruff format / mypy clean.
8. pip-audit clean (carry from PR6 fix).
9. All 5 acceptance scripts green: `validate_schemas.py`, `validate_prompt_delimiters.py`, `fixture_smoke.py`, `static_sandbox_policy_check.py`, `check_migrations.py`.
10. ADR-0004 status reads `accepted (verified)` with all 4 punch-list items resolved inline.
11. Three runbooks exist at `docs/phase0/runbooks/{auth_expiry,reboot,cli_regression}.md` with the documented header structure.
12. PR7 is the LAST PR in the Phase-0 DAG. Phase 0 is closed when this merges to main.

## Self-review

This spec was synthesized from the brainstorming Q&A (Q1–Q7) and Sections 1–8 design walkthroughs. Cross-checks:

- **Spec-coverage:** every §11 deliverable from `2026-04-30-phase-0-implementation-dag-design.md` has a corresponding §3–§14 entry.
- **Internal consistency:** `HealthCode` enum is fixed (5 values); `ProviderResult.status` enum is the schema's exact 5 values; `UsageProxy` has the schema's exact 6 fields; `auth.py` is referenced under the same name in §3.1, §4.4, and §11.1.
- **Scope:** single PR. No decomposition needed. Estimated 2–3 hours sequential per the DAG spec; closer to 4–5 hours with the test surface area.
- **Ambiguity:** all design forks settled by Q&A (Q1 depth, Q2 closure shape, Q3 eval-harness scope, Q4 cleanup scope, Q5 full-loop shape, Q6 health surface, Q7 auth patterns).
- **No placeholders:** every "TBD" or "verify at PR7" in this spec resolves inline. ADR-0004's open questions resolve in §13.
