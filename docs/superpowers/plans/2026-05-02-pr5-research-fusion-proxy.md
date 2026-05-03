# PR5 (Research-Fusion Proxy steps 1-8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement steps 1-8 of `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §6.2 — the bounded research-fusion proxy loop that proposes a research question, digests a local method note, proposes a method fusion, scores it deterministically, implements the smallest proxy test, and evaluates the proxy. Steps 9-10 (review + memory proposal) are PR6's territory.

**Architecture:** Five new modules under `arena/research_proxy/` (question_generator, method_digest, fusion_proposal, fusion_scorer, package marker) plus extensions to both stub providers (stub_claude dispatches on `(role, phase)` to emit research_question.json / paper_digest.json / fusion_proposal.json; stub_codex dispatches on phase=FUSION_PROXY_IMPLEMENTED to emit a proxy submission.csv tagged with the fusion_id). One new CLI subcommand `arena research-proxy <slug> --provider stub_claude` orchestrates steps 1-8 in sequence, persisting FOUR experiment rows — one per provider invocation (research_proxy_question, research_proxy_digest, research_proxy_fusion, research_proxy_implementation). Each row carries its own experiment_id (via `store.get_next_experiment_id`) and task_id (matching numeric suffix), and its own usage_proxy from the corresponding ProviderResult. Each provider invocation goes through the existing watchdog/wrap_invoke path so observability + sandbox + waste detection apply to every step. The fusion_scorer is a pure deterministic gate between step 5 (proposal received) and step 7 (proxy implementation invoked) — a low score halts the chain before any code is written. The scoreboard records the `<fusion_id:fusion_NNNN>` token in `artifact_paths` starting at row 3 (fusion proposal) and row 4 (implementation), since fusion_id is not known until step 5 (mirrors PR4's `<PROVIDER_VERSION_CHANGED:from=...>` pattern; no schema change).

**Tech Stack:** Python 3.12 stdlib only (json, hashlib, pathlib, dataclasses); `jsonschema>=4.22` for schema validation (already a dep); `pandas` for the proxy submission.csv (already a dep via PR1's stub_codex). No new third-party libraries.

---

## Preconditions

- PR1, PR2, PR3, and PR4 are merged to `main`. Branch `pr5-research-fusion-proxy` exists from fresh `main`.
- Python venv at `.venv/` with Python 3.12.13 and dev deps installed.
- Coverage gate is `fail_under = 50` (set in PR0; restored to 70 in PR7).
- Baseline before any PR5 work: 237 tests pass, 93.54% coverage, all 6 CI scripts green.

## Forward-compat hooks already in place from PR1-PR4

- `schemas/research_question.schema.json` — `rq_<NNNN>` id pattern; required fields: schema_version (const "research_question.v1"), question_id, competition_slug, question (≥10 chars), motivation (≥10 chars), expected_mechanisms (1+ strings ≥3 chars), expected_cost (tiny/small/medium/large), risk (low/medium/high), smallest_test (≥10 chars), stop_condition (≥10 chars), source_refs (string array).
- `schemas/paper_digest.schema.json` — `pd_<NNNN>` id; requires schema_version (const "paper_digest.v1"), digest_id, source_id, title, source_type (local_method_note/paper/abstract/kaggle_writeup/other), trusted_status (trusted_fixture/quarantined_untrusted/human_verified/local_method_note), mechanisms (1+ objects with name/description/why_it_might_help), assumptions, datasets_or_tasks, metrics, implementation_clues, failure_modes, applicability (object with competition_slug/fit/reason), citations (1+ objects with ref/summary).
- `schemas/fusion_proposal.schema.json` — `fusion_<NNNN>` id; requires schema_version (const "fusion_proposal.v1"), fusion_id, competition_slug, title (≥5), hypothesis (≥20), mechanisms_combined (2+ objects with mechanism_name/source_ref/role_in_fusion), implementation_plan (object with files_to_create_or_modify 1+, algorithm_steps 2+, dependencies, expected_outputs 1+), smallest_proxy_test (object with description ≥20, dataset_slice, metric, success_threshold {metric, comparator >= > <= <, value}, max_runtime_minutes 1-60), ablation_plan (1+ objects), resource_estimate (object with cost_class tiny/small/medium/large, gpu_required bool, max_runtime_minutes ≥1), risks (string array), stop_condition (≥10), source_refs (1+ strings).
- `arena/schemas/validate.py` — `validate(name, instance)` works for "research_question", "paper_digest", "fusion_proposal" via cached `Draft202012Validator` with `FORMAT_CHECKER` (date-time enforced).
- `task_packet.schema.json` `role` enum already includes "research_proxy" (added in PR0; just unused before PR5).
- `Phase` StrEnum has `RESEARCH_QUESTION_CREATED`, `METHOD_DIGEST_CREATED`, `FUSION_PROPOSAL_CREATED`, `FUSION_PROXY_IMPLEMENTED`, `FUSION_PROXY_EVALUATED`, `FUSION_PROXY_REVIEWED`. PR5 uses 1-5 of these (REVIEWED is PR6).
- Method notes exist at `fixtures/tabular_binary_v1/paper_bundle/method_note_001.md` and `method_note_002.md` (trusted fixture, listed in fixture_manifest.yaml so `compute_fixture_set_digest` covers them).
- `StubClaudeProvider` and `StubCodexProvider` accept `event_emitter` + `failed_commands` kwargs (PR4); they currently dispatch only on the calibration role. PR5 extends `invoke()` with role/phase dispatch.
- `Watchdog.wrap_invoke(adapter, packet, *, sandbox=None, event_emitter=None)` — PR3+PR4 plumbing. PR5's research-proxy CLI uses the same path so each step gets sandbox + observability for free.
- `_persist_blocked_experiment(store, packet, run_id, adapter, breaker_or_reason, message, usage_proxy)` — PR2 helper; PR5 does NOT reuse it. Instead, PR5 uses a local `_persist_row` helper (calls `store.insert_experiment` + `store.update_experiment_score`) so each of the four rows can carry its own per-invocation usage_proxy.
- `arena/cli.py` constants: `RUNS_ROOT`, `WORKTREE_ROOT`, `FIXTURES_ROOT`, `TRACES_ROOT`, `PROVIDER_VERSION_CHANGED_TAG`. PR5 adds a sibling `FUSION_ID_TAG_PREFIX = "fusion_id"` constant.
- `arena/cli.py` helpers: `_store()`, `_latest_run_id()`, `_get_provider()`. PR5 reuses all three. (`_new_run_id()` is NOT used — PR5 attaches to the active run created by `arena init-fixture` via `_latest_run_id()`.)

---

## File structure

**Create (new modules):**

| Path | Responsibility |
|---|---|
| `arena/research_proxy/__init__.py` | Package marker; re-exports `generate_research_question`, `make_method_digest_packet`, `make_fusion_proposal_packet`, `score_fusion_proposal`, `is_eligible`, `read_method_note` for ergonomic imports. |
| `arena/research_proxy/question_generator.py` | `generate_research_question(*, competition_slug, question_id, source_refs)` — pure function returning a schema-valid `research_question` dict. `make_research_question_packet(*, competition_slug, run_id, experiment_id, question_id, source_refs)` — returns a task_packet with `role="research_proxy"`, `phase="RESEARCH_QUESTION_CREATED"`. |
| `arena/research_proxy/method_digest.py` | `read_method_note(path)` — pure: returns the file contents as a string (so the digest input is observable). `make_method_digest_packet(*, competition_slug, run_id, experiment_id, digest_id, method_note_path)` — returns a task_packet with phase `METHOD_DIGEST_CREATED`, includes the method note in `inputs`. `validate_paper_digest(payload)` — wraps `validate("paper_digest", payload)` for caller convenience. |
| `arena/research_proxy/fusion_proposal.py` | `make_fusion_proposal_packet(*, competition_slug, run_id, experiment_id, fusion_id, digest_path)` — returns a task_packet with phase `FUSION_PROPOSAL_CREATED`, includes the digest in `inputs`. `validate_fusion_proposal(payload)` — wraps `validate("fusion_proposal", payload)`. |
| `arena/research_proxy/fusion_scorer.py` | `score_fusion_proposal(proposal)` — pure deterministic scoring → `FusionScore(score: float, risk: float, cost: float, fit: float)`. `is_eligible(proposal)` — checks §6.3 eligibility checklist; returns `(passes: bool, reasons: list[str])`. `MIN_FUSION_SCORE = 0.4` constant. |

**Create (tests, flat per existing convention):**

| Path | Tests |
|---|---|
| `tests/test_stub_claude_research_proxy.py` | stub_claude emits valid research_question.json on `phase=RESEARCH_QUESTION_CREATED`; valid paper_digest.json on `phase=METHOD_DIGEST_CREATED`; valid fusion_proposal.json on `phase=FUSION_PROPOSAL_CREATED`; non-research-proxy roles still produce the empty-payload result (backward compat with PR1 calibration tests). |
| `tests/test_stub_codex_research_proxy.py` | stub_codex emits a `submission.csv` AND a `<fusion_id:fusion_NNNN>` artifact-path token when given `phase=FUSION_PROXY_IMPLEMENTED` with a fusion_id in `inputs`; calibration role still works (backward compat). |
| `tests/test_research_proxy_question_generator.py` | `generate_research_question` returns a schema-valid dict; `make_research_question_packet` returns a schema-valid task_packet with the expected role/phase; explicit ID format validation. |
| `tests/test_research_proxy_method_digest.py` | `read_method_note` returns file contents; raises FileNotFoundError on missing path. `make_method_digest_packet` builds a valid task_packet with phase=METHOD_DIGEST_CREATED and the method note in inputs. `validate_paper_digest` accepts schema-valid input and rejects invalid. |
| `tests/test_research_proxy_fusion_proposal.py` | `make_fusion_proposal_packet` builds a valid task_packet with phase=FUSION_PROPOSAL_CREATED. `validate_fusion_proposal` accepts schema-valid input and rejects invalid. |
| `tests/test_research_proxy_fusion_scorer.py` | `score_fusion_proposal` returns a deterministic FusionScore; high-cost / high-risk proposals score lower; `is_eligible` flags proposals missing 2+ mechanisms, lacking ablation plan, or referencing forbidden network calls; `MIN_FUSION_SCORE` boundary. |
| `tests/test_cli_research_proxy.py` | End-to-end `arena research-proxy tabular_binary_v1 --provider stub_claude` runs steps 1-8, exits 0, persists FOUR experiment rows (research_proxy_question / research_proxy_digest / research_proxy_fusion / research_proxy_implementation), writes 4 artifacts in separate per-step worktrees. Low-score-halt: rows 1-3 completed + NO row 4 (gate is pre-invoke for stub_codex, so no row inserted). Kill-switch (pre-invoke, no row) / pre-invoke cap halts (no row) / mid-chain pre-invoke cap halts (no row for the would-be step). Post-invoke BudgetExceeded persists a blocked row WITH `usage_proxy` so consumed usage is durable. Collision-free IDs after calibration (exp_0001 + exp_0002…exp_0005). Run-level cap regression after calibration (governor seeded from get_run_usage_totals). 13 tests total. |
| `tests/test_research_proxy_eligibility.py` | Every fusion proposal generated by stub_claude (against both method_note_001.md and method_note_002.md) satisfies the §6.3 eligibility checklist: 2+ mechanisms, task-fit explanation, smallest proxy test, ablation plan, resource estimate, risk list, stop condition, schema-valid, no forbidden tokens (no `import requests`, no `urllib`, no live URLs). |

**Modify:**

| Path | Change |
|---|---|
| `arena/providers/stub_claude.py` | `invoke` dispatches on `(task_packet["role"], task_packet["phase"])`. For `("research_proxy", "RESEARCH_QUESTION_CREATED")` → write `research_question.json` artifact + return ProviderResult with that path in artifacts. Same for METHOD_DIGEST_CREATED → `paper_digest.json`; FUSION_PROPOSAL_CREATED → `fusion_proposal.json`. All other (role, phase) combinations fall through to the existing empty-payload path (backward compat). |
| `arena/providers/stub_codex.py` | `invoke` dispatches on `(role, phase)`. For `("implementation", "FUSION_PROXY_IMPLEMENTED")` → write `submission.csv` (same shape as calibration: 0.5 constant) AND include `<fusion_id:{fusion_id}>` in the artifact path list, where fusion_id is read from `task_packet["inputs"]` (passed in as a path-style token like `worktrees/<slug>/<exp>/fusion_proposal.json`). For all other (role, phase) combinations, fall through to the existing calibration path. |
| `arena/cli.py` | Add `from arena.research_proxy.* import ...` imports. Add `FUSION_ID_TAG_PREFIX = "fusion_id"` constant. Add new Typer subcommand `research_proxy(competition_slug, provider="stub_claude")` that orchestrates steps 1-8 and persists FOUR experiment rows — one per provider invocation — via a local `_persist_row` helper that calls `store.insert_experiment` + `store.update_experiment_score`. Each invocation gets its own experiment_id from `store.get_next_experiment_id` and its own task_id (matching suffix). Each sub-step calls `_guarded_invoke` (check_can_invoke + wrap_invoke). Step 6 (fusion_scorer) is a pure function call — no provider invocation. Step 7 invokes stub_codex with the fusion_id reference. Step 8 evaluates the proxy submission.csv via `evaluate_fixture_submission`. A `_persist_inflight_blocked` helper records a status=blocked row for the in-flight step on POST-invoke exception (BudgetExceeded with `usage_proxy` from `record_post_invoke`, SandboxViolation inside `wrap_invoke`). Pre-invoke exceptions (KillSwitchActive, ProviderCallBreaker tripped in `check_can_invoke`) leave the scoreboard untouched — gated by an `in_flight["invocation_started"]` flag set only after `check_can_invoke` succeeds. Mirrors `arena run-next` (`arena/cli.py:185-377`). |

---

## Workflow note

PR5's research-proxy CLI is a SINGLE command that runs four provider invocations in sequence (steps 2, 4, 5, 7) plus three deterministic controller actions (steps 1, 3, 6, 8). Each provider invocation goes through the existing PR3+PR4 watchdog path so sandbox enforcement, trace events, and waste detection apply uniformly. The four artifacts (`research_question.json`, `paper_digest.json`, `fusion_proposal.json`, `submission.csv`) land under per-step worktrees keyed by each invocation's own `experiment_id`. The scoreboard gets FOUR rows per research-proxy invocation — one per provider invocation, with experiment_type research_proxy_question / research_proxy_digest / research_proxy_fusion / research_proxy_implementation — each carrying its own usage_proxy. The `<fusion_id:fusion_NNNN>` token appears in artifact_paths starting at row 3 (fusion), since fusion_id is first known after step 5; rows 1-2 (question, digest) are written before fusion_id is available.

If `score_fusion_proposal` returns below `MIN_FUSION_SCORE` OR `is_eligible` returns False, the CLI halts at step 6. Rows 1-3 (question, digest, fusion) are already persisted as "completed". NO fourth row is inserted — stub_codex was never invoked, so `provider_calls` (derived from `COUNT(*)` by `get_run_usage_totals`) must not increment. The proxy implementation (step 7) is NOT invoked — that's the deterministic gate the spec calls out. Pre-invoke failures (kill switch, ProviderCallBreaker tripped in `check_can_invoke`, fusion gate halt) leave the scoreboard untouched; only post-invoke failures (BudgetExceeded with `usage_proxy`, SandboxViolation inside `wrap_invoke`) persist a blocked row. Mirrors the `arena run-next` pattern in `arena/cli.py`.

Step 8 ("Controller evaluates the proxy") in PR5 means: evaluate the stub-emitted `submission.csv` against `hidden_labels.csv` via the existing `evaluate_fixture_submission`. Since stub_codex emits the same 0.5-constant submission as calibration, the score will be ~0.5 — consistent with calibration. The point is to prove the pipeline produces a SCORED row tagged with the fusion_id, not to win the fixture. PR7 with real Codex will produce non-trivial proxy implementations.

## Coordination note

Per the original DAG (spec §3+§9): PR5 and PR6 are parallel-safe. PR5 owns `arena/research_proxy/`, extends both stub providers' `invoke` (different code branches dispatching on (role, phase)), and adds the `arena research-proxy` CLI subcommand. PR6 owns `arena/memory/`, `arena/self_improvement/`, and adds `arena review`/`arena memory propose`/`arena self-improve scan` subcommands. The stub-provider extension surfaces are different (role, phase) tuples — PR5 handles `research_proxy` + the three creation phases; PR6 handles `review` role + `MEMORY_PROPOSAL_CREATED` phase. Different files for the new modules; different CLI commands. No file collision expected.

If PR6 lands first and PR5 rebases, PR5's stub-provider edits go in the same `invoke` method — easy three-way merge. If both PRs are running in parallel worktrees, the stub-provider files are the only potential conflict, and the conflict is two new branches in a dispatch tree — git's merge handles this cleanly in practice.

---

## Task 1: Stub Claude research-proxy artifact emission

**Files:**
- Modify: `arena/providers/stub_claude.py`
- Create: `tests/test_stub_claude_research_proxy.py`

- [ ] **Step 1: Write the failing stub-claude tests**

```python
# tests/test_stub_claude_research_proxy.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _research_packet(
    *,
    phase: str,
    inputs: list[str] | None = None,
    workspace_root: Path,
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0001",
    task_id: str = "task_0001",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": phase,
        "objective": (
            "Generate a research-proxy artifact for the Phase 0 stub harness."
        ),
        "inputs": inputs or ["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["research_question.json"],
        "success_criteria": ["valid"],
    }


def test_stub_claude_emits_research_question_json(tmp_path: Path) -> None:
    """phase=RESEARCH_QUESTION_CREATED → research_question.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(phase="RESEARCH_QUESTION_CREATED", workspace_root=tmp_path)
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    rq_path = next(p for p in artifact_paths if p.name == "research_question.json")
    assert rq_path.exists()
    payload = json.loads(rq_path.read_text(encoding="utf-8"))
    validate("research_question", payload)  # no raise = schema-valid
    assert payload["competition_slug"] == "tabular_binary_v1"
    assert payload["question_id"].startswith("rq_")


def test_stub_claude_emits_paper_digest_json(tmp_path: Path) -> None:
    """phase=METHOD_DIGEST_CREATED → paper_digest.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(
        phase="METHOD_DIGEST_CREATED",
        inputs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
        workspace_root=tmp_path,
    )
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    pd_path = next(p for p in artifact_paths if p.name == "paper_digest.json")
    assert pd_path.exists()
    payload = json.loads(pd_path.read_text(encoding="utf-8"))
    validate("paper_digest", payload)
    assert payload["digest_id"].startswith("pd_")
    assert payload["source_type"] == "local_method_note"
    assert payload["trusted_status"] == "trusted_fixture"
    assert len(payload["mechanisms"]) >= 1


def test_stub_claude_emits_fusion_proposal_json(tmp_path: Path) -> None:
    """phase=FUSION_PROPOSAL_CREATED → fusion_proposal.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _research_packet(
        phase="FUSION_PROPOSAL_CREATED",
        inputs=[
            "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
        ],
        workspace_root=tmp_path,
    )
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    fp_path = next(p for p in artifact_paths if p.name == "fusion_proposal.json")
    assert fp_path.exists()
    payload = json.loads(fp_path.read_text(encoding="utf-8"))
    validate("fusion_proposal", payload)
    assert payload["fusion_id"].startswith("fusion_")
    # 2+ mechanisms is a fusion-proposal schema requirement; verify ours satisfies it.
    assert len(payload["mechanisms_combined"]) >= 2
    assert "smallest_proxy_test" in payload
    assert "ablation_plan" in payload
    assert len(payload["ablation_plan"]) >= 1
    assert "resource_estimate" in payload


def test_stub_claude_calibration_path_unchanged(tmp_path: Path) -> None:
    """Backward compat: non-research-proxy roles still produce the empty-payload result."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    # Reuse the calibration-style packet from PR1 (role=implementation, phase=CALIBRATION_TASK_CREATED).
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission for the fixture.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid"],
    }
    result = provider.invoke(packet)
    assert result.status == "success"
    # Calibration path: no artifacts (PR1 baseline behavior).
    assert result.artifacts == []
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_claude_research_proxy.py -v`
Expected: 4 fail with the existing stub-claude implementation NOT emitting the JSONs (or asserting `len(result.artifacts) == 0`).

- [ ] **Step 2: Implement the role/phase dispatch in stub_claude**

Replace `arena/providers/stub_claude.py`. The key change is: AFTER the existing `validate("task_packet", task_packet)` call and the optional shell_command_observed emission (preserve both), branch on `(role, phase)`. For the three research-proxy phases, write a deterministic schema-valid JSON artifact under the workspace and add it to the result's `artifacts` list. The existing calibration path (no artifacts, empty stdout/stderr scrubbed files, status="success") remains the fall-through.

```python
# arena/providers/stub_claude.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_claude.v1"


class StubClaudeProvider(ProviderAdapter):
    """Deterministic stand-in for Claude during Phase 0 CI and local stub runs.

    PR1 ships the calibration skeleton (no artifacts). PR5 extends invoke()
    to dispatch on (role, phase): research_proxy + (RESEARCH_QUESTION_CREATED,
    METHOD_DIGEST_CREATED, FUSION_PROPOSAL_CREATED) phases write a
    schema-valid JSON artifact. PR6 extends with role=review +
    MEMORY_PROPOSAL_CREATED.

    Optional fields exercise observability: failed_commands is a list of
    (command_str, exit_code) pairs that the stub emits as
    shell_command_observed events through `event_emitter` before producing
    its normal result. Enables PR4's live waste-detector path tests
    (security acceptance test 5).
    """

    def __init__(
        self,
        workspace_root: str | Path = "worktrees",
        *,
        event_emitter: TraceStore | None = None,
        failed_commands: list[tuple[str, int]] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._event_emitter = event_emitter
        self._failed_commands = failed_commands or []

    @property
    def name(self) -> str:
        return "stub_claude"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
        # PR4 live waste path: emit shell_command_observed events for any
        # injected failed_commands.
        if self._event_emitter is not None:
            for command, exit_code in self._failed_commands:
                self._event_emitter.emit(
                    event_type="shell_command_observed",
                    severity="info" if exit_code == 0 else "warning",
                    task_id=task_packet["task_id"],
                    payload={"command": command, "exit_code": exit_code},
                )
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubClaudeProvider requires task_packet.experiment_id to be set")
        task_id = task_packet["task_id"]

        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)
        stdout_path = workspace / f"{task_id}.stub_claude.stdout.scrubbed"
        stderr_path = workspace / f"{task_id}.stub_claude.stderr.scrubbed"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")

        # PR5 dispatch: research_proxy role + creation phases write a
        # schema-valid artifact under the workspace.
        artifacts: list[str] = []
        role = task_packet["role"]
        phase = task_packet["phase"]
        if role == "research_proxy":
            payload, artifact_name = self._research_proxy_payload(slug, phase)
            if payload is not None and artifact_name is not None:
                artifact_path = workspace / artifact_name
                artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                artifacts.append(str(artifact_path))

        now = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            artifacts=artifacts,
            input_chars=0,
            output_chars=sum(Path(p).stat().st_size for p in artifacts),
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=now,
            finished_at=now,
        )

    def _research_proxy_payload(
        self, slug: str, phase: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return (payload, artifact_name) for the research-proxy phase, or
        (None, None) if the phase doesn't require artifact emission. Each
        payload is deterministic and schema-valid by construction."""
        if phase == "RESEARCH_QUESTION_CREATED":
            return self._research_question_payload(slug), "research_question.json"
        if phase == "METHOD_DIGEST_CREATED":
            return self._paper_digest_payload(slug), "paper_digest.json"
        if phase == "FUSION_PROPOSAL_CREATED":
            return self._fusion_proposal_payload(slug), "fusion_proposal.json"
        return None, None

    def _research_question_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "research_question.v1",
            "question_id": "rq_0001",
            "competition_slug": slug,
            "question": (
                "Does combining a monotonic GBDT with a stacked logistic-regression "
                "meta-learner reduce variance on the small tabular_binary_v1 fixture "
                "compared to a free-form GBDT baseline?"
            ),
            "motivation": (
                "Method note 001 argues monotonic constraints reduce variance on "
                "small training sets; method note 002 argues stacked diverse base "
                "learners reduce bias. The fixture is small (50 rows) so variance "
                "dominates — the combination should outperform either alone."
            ),
            "expected_mechanisms": [
                "monotonic gradient-boosted decision trees",
                "stacked logistic-regression meta-learner",
            ],
            "expected_cost": "small",
            "risk": "low",
            "smallest_test": (
                "5-fold CV on train.csv comparing baseline GBDT vs monotonic-GBDT "
                "+ stacked-LR ensemble. Report ROC-AUC mean + std; pass if the "
                "ensemble's mean is at least 0.02 above baseline."
            ),
            "stop_condition": (
                "Stop if the ensemble's CV mean is below baseline by more than 0.01 "
                "or training wall time exceeds 5 minutes per fold."
            ),
            "source_refs": [
                "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
                "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
            ],
        }

    def _paper_digest_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "paper_digest.v1",
            "digest_id": "pd_0001",
            "source_id": "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "title": (
                "Monotonic Gradient-Boosted Decision Trees for Tabular Binary "
                "Classification"
            ),
            "source_type": "local_method_note",
            "trusted_status": "trusted_fixture",
            "mechanisms": [
                {
                    "name": "monotonic_gbdt",
                    "description": (
                        "Gradient-boosted decision tree ensemble where splits on a "
                        "designated feature are constrained to produce monotonically "
                        "non-decreasing log-odds with respect to that feature."
                    ),
                    "why_it_might_help": (
                        "Reduces variance from spurious non-monotone splits in small "
                        "training sets without sacrificing meaningful signal when "
                        "the target relationship is genuinely monotonic."
                    ),
                }
            ],
            "assumptions": [
                "Target log-odds is monotonic in the constrained feature in the population.",
                "Training set is small enough that variance dominates bias.",
            ],
            "datasets_or_tasks": ["tabular_binary_v1"],
            "metrics": ["ROC-AUC"],
            "implementation_clues": [
                "LightGBM monotone_constraints parameter",
                "CatBoost monotone_constraints parameter",
            ],
            "failure_modes": [
                "True relationship is non-monotonic; constraint kills useful signal.",
                "Wrong feature is constrained.",
            ],
            "applicability": {
                "competition_slug": slug,
                "fit": "high",
                "reason": (
                    "Fixture has continuous numeric features with plausibly monotonic "
                    "relationship to target; small training set magnifies variance, "
                    "which monotonic constraints address directly."
                ),
            },
            "citations": [
                {
                    "ref": "method_note_001.md",
                    "summary": (
                        "Local trusted method note describing monotonic GBDTs for "
                        "tabular binary classification on small datasets."
                    ),
                }
            ],
        }

    def _fusion_proposal_payload(self, slug: str) -> dict[str, Any]:
        return {
            "schema_version": "fusion_proposal.v1",
            "fusion_id": "fusion_0001",
            "competition_slug": slug,
            "title": "Monotonic-GBDT + Stacked Logistic-Regression Meta-Learner",
            "hypothesis": (
                "On the tabular_binary_v1 fixture, combining a monotonic GBDT base "
                "learner with a stacked logistic-regression meta-learner over OOF "
                "predictions will reduce CV ROC-AUC variance compared to either "
                "method alone, by simultaneously addressing variance (monotonic "
                "constraints) and bias (diverse stacked learners)."
            ),
            "mechanisms_combined": [
                {
                    "mechanism_name": "monotonic_gbdt",
                    "source_ref": "method_note_001.md",
                    "role_in_fusion": (
                        "Acts as the variance-reducing base learner with monotonic "
                        "constraints on x2 (the most predictive feature)."
                    ),
                },
                {
                    "mechanism_name": "stacked_logistic_regression",
                    "source_ref": "method_note_002.md",
                    "role_in_fusion": (
                        "Acts as the meta-learner combining OOF predictions from the "
                        "monotonic GBDT with predictions from a linear logistic "
                        "regression base learner to add diversity."
                    ),
                },
            ],
            "implementation_plan": {
                "files_to_create_or_modify": [
                    "submission.csv",
                ],
                "algorithm_steps": [
                    "Load train.csv and test.csv from fixtures/<slug>/.",
                    (
                        "Train a monotonic GBDT (LightGBM, monotone_constraints "
                        "applied to x2) with 5-fold CV; collect OOF predictions on "
                        "the train set and full-train predictions on the test set."
                    ),
                    (
                        "Train a logistic regression base learner with the same 5 "
                        "folds; collect OOF train predictions and full-train test "
                        "predictions."
                    ),
                    (
                        "Stack OOF predictions as features for a meta logistic "
                        "regression on the train labels; predict the test set "
                        "stacked features to produce final probabilities."
                    ),
                    "Write submission.csv with columns id, target.",
                ],
                "dependencies": ["lightgbm>=4.0", "scikit-learn>=1.3", "pandas>=2.0"],
                "expected_outputs": ["submission.csv"],
            },
            "smallest_proxy_test": {
                "description": (
                    "5-fold CV on train.csv with both base learners + stacked meta. "
                    "Report mean ROC-AUC vs the calibration baseline of 0.5 constant."
                ),
                "dataset_slice": "train",
                "metric": "roc_auc",
                "success_threshold": {
                    "metric": "roc_auc",
                    "comparator": ">=",
                    "value": 0.5,
                },
                "max_runtime_minutes": 5,
            },
            "ablation_plan": [
                {
                    "name": "remove_monotonicity",
                    "remove_or_change": (
                        "Drop monotone_constraints from the GBDT; train without "
                        "constraints."
                    ),
                    "expected_signal": (
                        "Variance increases; CV ROC-AUC std grows. Confirms the "
                        "monotonic constraint contributes."
                    ),
                },
                {
                    "name": "remove_stacking",
                    "remove_or_change": (
                        "Use only the monotonic GBDT base learner without the "
                        "stacked logistic regression."
                    ),
                    "expected_signal": (
                        "Bias increases; CV ROC-AUC mean drops. Confirms the "
                        "stacking contributes."
                    ),
                },
            ],
            "resource_estimate": {
                "cost_class": "small",
                "gpu_required": False,
                "max_runtime_minutes": 10,
            },
            "risks": [
                "Target may be non-monotonic in x2; constraint hurts.",
                "Two base learners on 50 rows may overfit the meta-learner.",
            ],
            "stop_condition": (
                "Stop if CV ROC-AUC mean falls more than 0.05 below baseline OR "
                "training wall time exceeds 5 minutes per fold."
            ),
            "source_refs": [
                "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
                "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
            ],
        }
```

- [ ] **Step 3: Run the stub-claude tests + full suite**

```bash
.venv/Scripts/python.exe -m pytest tests/test_stub_claude_research_proxy.py -v
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 4 new tests pass; full suite at 241 (was 237; +4). All checks clean.

- [ ] **Step 4: Commit**

```bash
git add arena/providers/stub_claude.py tests/test_stub_claude_research_proxy.py
git commit -m "$(cat <<'EOF'
feat(providers): stub_claude dispatches research-proxy artifacts on (role, phase)

stub_claude.invoke now branches on task_packet["role"] and ["phase"].
For role="research_proxy" + (RESEARCH_QUESTION_CREATED, METHOD_DIGEST_CREATED,
FUSION_PROPOSAL_CREATED), writes a deterministic schema-valid JSON artifact
under the workspace and adds it to ProviderResult.artifacts. All other
(role, phase) tuples fall through to the existing calibration empty-payload
path so PR1+PR4 e2e tests continue to pass.

Each payload is constructed inline as a Python dict and validated via the
existing arena.schemas.validate path (Draft202012Validator + format-checker).
The fusion proposal satisfies the §6.3 eligibility checklist by construction
(2 mechanisms_combined, ablation_plan with 2 entries, smallest_proxy_test,
resource_estimate, risks, stop_condition, source_refs).

Tests verify each phase produces a schema-valid artifact and that the
calibration path is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Question generator + method digest packet builders

**Files:**
- Create: `arena/research_proxy/__init__.py` (bare marker)
- Create: `arena/research_proxy/question_generator.py`
- Create: `arena/research_proxy/method_digest.py`
- Create: `tests/test_research_proxy_question_generator.py`
- Create: `tests/test_research_proxy_method_digest.py`

- [ ] **Step 1: Write failing question_generator tests**

```python
# tests/test_research_proxy_question_generator.py
from __future__ import annotations

import pytest

from arena.research_proxy.question_generator import (
    generate_research_question,
    make_research_question_packet,
)
from arena.schemas.validate import validate


def test_generate_research_question_returns_schema_valid() -> None:
    question = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
    )
    validate("research_question", question)
    assert question["question_id"] == "rq_0001"
    assert question["competition_slug"] == "tabular_binary_v1"
    assert len(question["expected_mechanisms"]) >= 1


def test_generate_research_question_id_pattern() -> None:
    """question_id must match ^rq_[0-9]{4,}$ per schema."""
    question = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="rq_9999",
        source_refs=["fixtures/method_note.md"],
    )
    validate("research_question", question)
    # Reject malformed id at construction time.
    from jsonschema import ValidationError

    bad = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="not_an_rq_id",
        source_refs=["fixtures/method_note.md"],
    )
    with pytest.raises(ValidationError):
        validate("research_question", bad)


def test_make_research_question_packet_is_schema_valid_task_packet() -> None:
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_2026_05_02_001",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "RESEARCH_QUESTION_CREATED"
    assert packet["competition_slug"] == "tabular_binary_v1"
    assert packet["experiment_id"] == "exp_0001"


def test_make_research_question_packet_includes_method_notes_in_inputs() -> None:
    """The method notes the question references should be in the packet's
    inputs list so the planner/sandbox sees them as readable inputs."""
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=[
            "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
        ],
    )
    for ref in packet.get("inputs", []):
        # All inputs are workspace-relative paths.
        assert not ref.startswith("/")
    assert any("method_note_001.md" in p for p in packet["inputs"])
    assert any("method_note_002.md" in p for p in packet["inputs"])
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_question_generator.py -v`
Expected: ImportError on `arena.research_proxy.question_generator`.

- [ ] **Step 2: Implement question_generator**

```python
# arena/research_proxy/__init__.py
from __future__ import annotations
```

```python
# arena/research_proxy/question_generator.py
from __future__ import annotations

from typing import Any


def generate_research_question(
    *,
    competition_slug: str,
    question_id: str,
    source_refs: list[str],
) -> dict[str, Any]:
    """Build a deterministic schema-valid research_question payload.

    Phase 0 stub: returns a fixed question keyed to the tabular_binary_v1
    fixture's two method notes. PR7's real Claude will replace this with
    LLM-generated content.
    """
    return {
        "schema_version": "research_question.v1",
        "question_id": question_id,
        "competition_slug": competition_slug,
        "question": (
            "Does combining a monotonic GBDT with a stacked logistic-regression "
            "meta-learner reduce CV ROC-AUC variance on the small "
            f"{competition_slug} fixture compared to a free-form GBDT baseline?"
        ),
        "motivation": (
            "The fixture is small (50 train rows) so variance dominates. "
            "Method note 001 argues monotonic constraints reduce variance; "
            "method note 002 argues stacked diverse base learners reduce bias. "
            "Combining both should outperform either alone."
        ),
        "expected_mechanisms": [
            "monotonic gradient-boosted decision trees",
            "stacked logistic-regression meta-learner",
        ],
        "expected_cost": "small",
        "risk": "low",
        "smallest_test": (
            "5-fold CV on train.csv comparing baseline GBDT vs monotonic-GBDT "
            "+ stacked-LR ensemble; report ROC-AUC mean + std."
        ),
        "stop_condition": (
            "Stop if ensemble CV mean is below baseline by more than 0.01 OR "
            "training wall time exceeds 5 minutes per fold."
        ),
        "source_refs": list(source_refs),
    }


def make_research_question_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    question_id: str,
    source_refs: list[str],
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude (or real Claude in PR7)
    to emit a research_question.json artifact for `competition_slug`.

    The source_refs become the packet's `inputs` so the sandbox sees the
    method notes as readable. The packet's allowed_paths is the experiment's
    own worktree.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": "RESEARCH_QUESTION_CREATED",
        "objective": (
            f"Generate a research question for {competition_slug} "
            "based on the listed method-note source refs. The output "
            "must satisfy schemas/research_question.schema.json."
        ),
        "inputs": list(source_refs),
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["research_question.json"],
        "success_criteria": ["valid_schema"],
    }
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_question_generator.py -v`
Expected: 4 passed.

- [ ] **Step 3: Write failing method_digest tests**

```python
# tests/test_research_proxy_method_digest.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from arena.research_proxy.method_digest import (
    make_method_digest_packet,
    read_method_note,
    validate_paper_digest,
)
from arena.schemas.validate import validate


def test_read_method_note_returns_file_contents(tmp_path: Path) -> None:
    note = tmp_path / "method_note_test.md"
    note.write_text("# Test method note\n\nA mechanism description.", encoding="utf-8")
    assert read_method_note(note).startswith("# Test method note")


def test_read_method_note_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_method_note(tmp_path / "no_such_note.md")


def test_make_method_digest_packet_is_schema_valid_task_packet() -> None:
    packet = make_method_digest_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        digest_id="pd_0001",
        method_note_path="fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "METHOD_DIGEST_CREATED"
    assert "method_note_001.md" in packet["inputs"][0]


def test_validate_paper_digest_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "paper_digest.v1",
        "digest_id": "pd_0001",
        "source_id": "fixtures/method_note_001.md",
        "title": "Method Note 001 — Monotonic GBDTs",
        "source_type": "local_method_note",
        "trusted_status": "trusted_fixture",
        "mechanisms": [
            {
                "name": "monotonic_gbdt",
                "description": "Gradient-boosted trees with monotone constraints.",
                "why_it_might_help": "Reduces variance on small training sets.",
            }
        ],
        "assumptions": ["assumption A"],
        "datasets_or_tasks": ["tabular_binary_v1"],
        "metrics": ["ROC-AUC"],
        "implementation_clues": ["LightGBM monotone_constraints"],
        "failure_modes": ["non-monotonic truth"],
        "applicability": {
            "competition_slug": "tabular_binary_v1",
            "fit": "high",
            "reason": "Fixture features look monotonic in the target.",
        },
        "citations": [
            {"ref": "method_note_001.md", "summary": "Local trusted method note."}
        ],
    }
    validate_paper_digest(payload)  # no raise


def test_validate_paper_digest_rejects_missing_required_field() -> None:
    payload = {
        "schema_version": "paper_digest.v1",
        # missing digest_id (required)
        "source_id": "x",
        "title": "x",
        "source_type": "local_method_note",
        "trusted_status": "trusted_fixture",
        "mechanisms": [],  # also bad: minItems=1
        "assumptions": [],
        "datasets_or_tasks": [],
        "metrics": [],
        "implementation_clues": [],
        "failure_modes": [],
        "applicability": {},
        "citations": [],
    }
    with pytest.raises(ValidationError):
        validate_paper_digest(payload)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_method_digest.py -v`
Expected: ImportError on `arena.research_proxy.method_digest`.

- [ ] **Step 4: Implement method_digest**

```python
# arena/research_proxy/method_digest.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from arena.schemas.validate import validate


def read_method_note(path: str | Path) -> str:
    """Read the contents of a local method note file.

    Phase 0 method notes are trusted fixture inputs at
    fixtures/<slug>/paper_bundle/method_note_NNN.md. Returns the raw text.
    Caller is responsible for passing it as `inputs` in the task packet
    so the sandbox sees it as a readable input.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"missing method note: {p}")
    return p.read_text(encoding="utf-8")


def make_method_digest_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    digest_id: str,
    method_note_path: str,
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude to digest one local
    method note into a paper_digest.json artifact.

    The method note path is included in `inputs` so the sandbox treats
    it as a readable input. The output (paper_digest.json) lands under
    the experiment's worktree.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": "METHOD_DIGEST_CREATED",
        "objective": (
            f"Read the method note at {method_note_path} and produce a "
            "paper_digest.json that satisfies "
            "schemas/paper_digest.schema.json. Set source_type to "
            "local_method_note and trusted_status to trusted_fixture."
        ),
        "inputs": [method_note_path],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["paper_digest.json"],
        "success_criteria": ["valid_schema"],
    }


def validate_paper_digest(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/paper_digest.schema.json. Raises
    jsonschema.ValidationError on any failure. Thin wrapper for caller
    convenience; equivalent to `validate("paper_digest", payload)`."""
    validate("paper_digest", payload)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_method_digest.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 250 tests pass (was 241; +9 = 4 question_generator + 5 method_digest). All checks clean.

- [ ] **Step 6: Commit**

```bash
git add arena/research_proxy/__init__.py arena/research_proxy/question_generator.py \
        arena/research_proxy/method_digest.py \
        tests/test_research_proxy_question_generator.py \
        tests/test_research_proxy_method_digest.py
git commit -m "$(cat <<'EOF'
feat(research_proxy): question generator + method digest packet builders

generate_research_question(competition_slug, question_id, source_refs)
returns a deterministic schema-valid research_question.v1 payload.
make_research_question_packet wraps it in a task_packet with
role=research_proxy, phase=RESEARCH_QUESTION_CREATED — the source_refs
become inputs so the sandbox sees the method notes as readable.

read_method_note(path) returns file contents (raises FileNotFoundError
on missing). make_method_digest_packet builds a task_packet with
phase=METHOD_DIGEST_CREATED and the method note in inputs.
validate_paper_digest is a thin wrapper over arena.schemas.validate
for caller convenience.

Both packets target the active experiment's worktree as
allowed_paths and include the standard secret-store + hidden_labels
blocked_paths so PR3's sandbox enforcement applies.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Fusion proposal builder + deterministic scorer/eligibility gate

**Files:**
- Create: `arena/research_proxy/fusion_proposal.py`
- Create: `arena/research_proxy/fusion_scorer.py`
- Create: `tests/test_research_proxy_fusion_proposal.py`
- Create: `tests/test_research_proxy_fusion_scorer.py`

- [ ] **Step 1: Write failing fusion_proposal tests**

```python
# tests/test_research_proxy_fusion_proposal.py
from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.research_proxy.fusion_proposal import (
    make_fusion_proposal_packet,
    validate_fusion_proposal,
)
from arena.schemas.validate import validate


def test_make_fusion_proposal_packet_is_schema_valid_task_packet() -> None:
    packet = make_fusion_proposal_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        fusion_id="fusion_0001",
        digest_path="worktrees/tabular_binary_v1/exp_0001/paper_digest.json",
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "FUSION_PROPOSAL_CREATED"
    assert "paper_digest.json" in packet["inputs"][0]


def test_validate_fusion_proposal_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Test fusion title",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {
                "mechanism_name": "mech_a",
                "source_ref": "ref_a",
                "role_in_fusion": "primary base learner role.",
            },
            {
                "mechanism_name": "mech_b",
                "source_ref": "ref_b",
                "role_in_fusion": "secondary stacking role.",
            },
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["step1.", "step2."],
            "dependencies": ["pandas"],
            "expected_outputs": ["submission.csv"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [
            {"name": "abl_a", "remove_or_change": "x", "expected_signal": "y"}
        ],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 10,
        },
        "risks": ["risk1"],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref_a"],
    }
    validate_fusion_proposal(payload)  # no raise


def test_validate_fusion_proposal_rejects_one_mechanism() -> None:
    """Schema requires minItems=2 on mechanisms_combined."""
    payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Bad fusion",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {
                "mechanism_name": "lonely_mech",
                "source_ref": "ref",
                "role_in_fusion": "the only mechanism here.",
            }
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["a"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": [],
            "expected_outputs": ["o"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [{"name": "a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 5,
        },
        "risks": [],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref"],
    }
    with pytest.raises(ValidationError):
        validate_fusion_proposal(payload)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_fusion_proposal.py -v`
Expected: ImportError on `arena.research_proxy.fusion_proposal`.

- [ ] **Step 2: Implement fusion_proposal**

```python
# arena/research_proxy/fusion_proposal.py
from __future__ import annotations

from typing import Any

from arena.schemas.validate import validate


def make_fusion_proposal_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    fusion_id: str,
    digest_path: str,
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude to propose a method
    fusion grounded in a previously-emitted paper_digest.

    The digest path is included in `inputs` so the sandbox treats it
    as a readable input (it lives under the experiment's own worktree
    after the previous step's stub_claude invocation). The output
    (fusion_proposal.json) lands alongside it.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "research_proxy",
        "phase": "FUSION_PROPOSAL_CREATED",
        "objective": (
            f"Read the digest at {digest_path} and propose a method "
            "fusion combining at least two mechanisms. The output must "
            "satisfy schemas/fusion_proposal.schema.json including the "
            "§6.3 eligibility checklist (2+ mechanisms_combined, "
            "smallest_proxy_test, ablation_plan, resource_estimate, "
            "risks, stop_condition, source_refs)."
        ),
        "inputs": [digest_path],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 10,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["fusion_proposal.json"],
        "success_criteria": ["valid_schema", "two_or_more_mechanisms"],
    }


def validate_fusion_proposal(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/fusion_proposal.schema.json.
    Raises jsonschema.ValidationError on any failure. Thin wrapper over
    arena.schemas.validate."""
    validate("fusion_proposal", payload)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_fusion_proposal.py -v`
Expected: 3 passed.

- [ ] **Step 3: Write failing fusion_scorer tests**

```python
# tests/test_research_proxy_fusion_scorer.py
from __future__ import annotations

import pytest

from arena.research_proxy.fusion_scorer import (
    MIN_FUSION_SCORE,
    FusionScore,
    is_eligible,
    score_fusion_proposal,
)


def _valid_proposal() -> dict:
    return {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Valid fusion",
        "hypothesis": "A long-enough hypothesis string for the schema.",
        "mechanisms_combined": [
            {"mechanism_name": "a", "source_ref": "r_a", "role_in_fusion": "primary."},
            {"mechanism_name": "b", "source_ref": "r_b", "role_in_fusion": "secondary."},
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": ["pandas"],
            "expected_outputs": ["submission.csv"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [
            {"name": "abl_a", "remove_or_change": "x", "expected_signal": "y"}
        ],
        "resource_estimate": {
            "cost_class": "small",
            "gpu_required": False,
            "max_runtime_minutes": 10,
        },
        "risks": ["risk1"],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["ref_a"],
    }


def test_score_fusion_proposal_returns_FusionScore_with_components() -> None:
    proposal = _valid_proposal()
    s = score_fusion_proposal(proposal)
    assert isinstance(s, FusionScore)
    assert 0.0 <= s.score <= 1.0
    assert 0.0 <= s.risk <= 1.0
    assert 0.0 <= s.cost <= 1.0
    assert 0.0 <= s.fit <= 1.0


def test_score_is_higher_for_low_cost_low_risk_high_fit() -> None:
    proposal = _valid_proposal()
    proposal["resource_estimate"]["cost_class"] = "tiny"
    proposal["risks"] = []
    s_low = score_fusion_proposal(proposal)

    proposal["resource_estimate"]["cost_class"] = "large"
    proposal["risks"] = ["r1", "r2", "r3", "r4", "r5"]
    s_high = score_fusion_proposal(proposal)

    assert s_low.score > s_high.score


def test_score_is_deterministic() -> None:
    proposal = _valid_proposal()
    a = score_fusion_proposal(proposal)
    b = score_fusion_proposal(proposal)
    assert a == b


def test_is_eligible_passes_for_well_formed_proposal() -> None:
    proposal = _valid_proposal()
    passes, reasons = is_eligible(proposal)
    assert passes is True
    assert reasons == []


def test_is_eligible_rejects_proposal_with_one_mechanism() -> None:
    proposal = _valid_proposal()
    proposal["mechanisms_combined"] = proposal["mechanisms_combined"][:1]
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("two or more mechanisms" in r.lower() for r in reasons)


def test_is_eligible_rejects_proposal_with_empty_ablation_plan() -> None:
    proposal = _valid_proposal()
    proposal["ablation_plan"] = []
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("ablation" in r.lower() for r in reasons)


def test_is_eligible_rejects_proposal_referencing_forbidden_network() -> None:
    """Per §6.3: no forbidden network dependency. Check that any literal
    URL or `import requests` in implementation_plan trips the gate."""
    proposal = _valid_proposal()
    proposal["implementation_plan"]["dependencies"].append("requests")
    passes, reasons = is_eligible(proposal)
    assert passes is False
    assert any("network" in r.lower() or "requests" in r.lower() for r in reasons)


def test_min_fusion_score_constant_is_in_range() -> None:
    assert 0.0 < MIN_FUSION_SCORE < 1.0
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_fusion_scorer.py -v`
Expected: ImportError on `arena.research_proxy.fusion_scorer`.

- [ ] **Step 4: Implement fusion_scorer**

```python
# arena/research_proxy/fusion_scorer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Minimum score below which the controller halts the research-proxy chain
# at step 6 (between fusion proposal and proxy implementation). Tuned to
# accept the deterministic stub fusion (score ~ 0.7) and reject obviously
# bad proposals (large cost + many risks).
MIN_FUSION_SCORE = 0.4


@dataclass(frozen=True)
class FusionScore:
    """Decomposed fusion score: each component in [0, 1], `score` is the
    weighted average. Caller compares score against MIN_FUSION_SCORE."""

    score: float
    risk: float
    cost: float
    fit: float


_COST_RANK = {"tiny": 1.0, "small": 0.8, "medium": 0.5, "large": 0.2}
_FIT_RANK = {"high": 1.0, "medium": 0.6, "low": 0.2}

_FORBIDDEN_NETWORK_TOKENS = (
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "http://",
    "https://",
    "socket",
)
_FORBIDDEN_UNTRUSTED_IMPORTS = (
    "subprocess",  # Phase 0: no shelling out from research-proxy code
    "os.system",
    "eval(",
    "exec(",
)


def score_fusion_proposal(proposal: dict[str, Any]) -> FusionScore:
    """Deterministic scoring: cost, risk, fit components → weighted score.

    Higher score = better. cost component = 1 - normalized cost class
    rank; risk component = 1 - clamped(len(risks)/5); fit component
    derived from applicability (digest field — but proposal doesn't
    carry it; use mechanism count as a fit proxy: more mechanisms
    combined = better fit signal).
    """
    cost_class = proposal["resource_estimate"]["cost_class"]
    cost = _COST_RANK.get(cost_class, 0.5)

    n_risks = len(proposal.get("risks", []))
    risk = max(0.0, 1.0 - min(n_risks, 5) / 5.0)

    n_mech = len(proposal.get("mechanisms_combined", []))
    fit = min(1.0, n_mech / 3.0)  # 2 mechs → 0.67, 3 → 1.0

    # Equal-weighted average; tweakable in PR7.
    score = (cost + risk + fit) / 3.0
    return FusionScore(score=score, risk=risk, cost=cost, fit=fit)


def is_eligible(proposal: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check the §6.3 eligibility checklist.

    Returns (passes, reasons). Each reason is a short string explaining
    one rule that failed. An eligible proposal returns (True, []).

    Checks:
    - 2+ mechanisms_combined (also a schema requirement; double-check)
    - smallest_proxy_test present + non-trivial
    - ablation_plan non-empty
    - resource_estimate present with all required fields
    - risks is a list (may be empty; spec only says "risk list")
    - stop_condition non-empty
    - source_refs non-empty
    - No forbidden network token in implementation_plan.dependencies or
      .algorithm_steps
    - No forbidden untrusted-code-import token in algorithm_steps
    """
    reasons: list[str] = []

    if len(proposal.get("mechanisms_combined", [])) < 2:
        reasons.append("two or more mechanisms required")

    spt = proposal.get("smallest_proxy_test", {})
    if not spt or len(spt.get("description", "")) < 20:
        reasons.append("smallest proxy test missing or too short")

    if len(proposal.get("ablation_plan", [])) < 1:
        reasons.append("ablation plan missing")

    re_est = proposal.get("resource_estimate", {})
    for required in ("cost_class", "gpu_required", "max_runtime_minutes"):
        if required not in re_est:
            reasons.append(f"resource_estimate missing {required}")

    if "risks" not in proposal:
        reasons.append("risk list missing")

    if len(proposal.get("stop_condition", "")) < 10:
        reasons.append("stop_condition missing or too short")

    if not proposal.get("source_refs"):
        reasons.append("source_refs empty")

    impl = proposal.get("implementation_plan", {})
    haystack_parts: list[str] = []
    haystack_parts.extend(impl.get("dependencies", []))
    haystack_parts.extend(impl.get("algorithm_steps", []))
    haystack = " ".join(haystack_parts).lower()
    for token in _FORBIDDEN_NETWORK_TOKENS:
        if token in haystack:
            reasons.append(f"forbidden network dependency token: {token}")
            break
    for token in _FORBIDDEN_UNTRUSTED_IMPORTS:
        if token in haystack:
            reasons.append(f"forbidden untrusted-code import: {token}")
            break

    return (len(reasons) == 0, reasons)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_fusion_scorer.py -v`
Expected: 8 passed.

- [ ] **Step 5: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 261 tests pass (was 250; +3 fusion_proposal + 8 fusion_scorer = +11). All checks clean.

- [ ] **Step 6: Commit**

```bash
git add arena/research_proxy/fusion_proposal.py arena/research_proxy/fusion_scorer.py \
        tests/test_research_proxy_fusion_proposal.py tests/test_research_proxy_fusion_scorer.py
git commit -m "$(cat <<'EOF'
feat(research_proxy): fusion_proposal packet builder + deterministic scorer

make_fusion_proposal_packet builds a task_packet with phase=FUSION_PROPOSAL_CREATED
and the previously-emitted digest as input. validate_fusion_proposal is
the schema wrapper.

fusion_scorer.score_fusion_proposal returns a FusionScore(score, risk,
cost, fit) — deterministic, equal-weighted average of three components:
- cost: 1 - normalized cost_class rank (tiny=1.0 … large=0.2)
- risk: 1 - clamped(len(risks)/5)
- fit: clamped(len(mechanisms_combined)/3)

is_eligible enforces the §6.3 checklist: 2+ mechanisms, non-trivial
smallest_proxy_test, ablation_plan, complete resource_estimate, risks
list, stop_condition, non-empty source_refs, plus negative checks for
forbidden network tokens (requests, urllib, http://, etc.) and
untrusted-code imports (subprocess, os.system, eval, exec) in
implementation_plan.dependencies + algorithm_steps.

MIN_FUSION_SCORE = 0.4 — the gate constant. The CLI in Task 5 halts
the chain before stub_codex if score < MIN_FUSION_SCORE OR is_eligible
returns False.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Stub Codex proxy implementation dispatch

**Files:**
- Modify: `arena/providers/stub_codex.py`
- Create: `tests/test_stub_codex_research_proxy.py`

- [ ] **Step 1: Write failing stub_codex tests**

```python
# tests/test_stub_codex_research_proxy.py
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from arena.providers.stub_codex import StubCodexProvider


def _proxy_packet(
    *,
    workspace_root: Path,
    fusion_id: str = "fusion_0001",
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0001",
    task_id: str = "task_0001",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "FUSION_PROXY_IMPLEMENTED",
        "objective": (
            f"Implement the smallest proxy test for fusion {fusion_id}. "
            "The packet's inputs[0] is the fusion_proposal.json path."
        ),
        "inputs": [
            f"worktrees/{competition_slug}/{experiment_id}/fusion_proposal.json",
            f"fixtures/{competition_slug}/test.csv",
        ],
        "allowed_paths": [f"worktrees/{competition_slug}/{experiment_id}/"],
        "blocked_paths": [
            "~/.kaggle/",
            "~/.codex/",
            "~/.claude/",
            ".env",
            f"fixtures/{competition_slug}/hidden_labels.csv",
        ],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid"],
    }


@pytest.fixture
def fixture_workspace_with_fusion(fixture_workspace: Path) -> Path:
    """Bootstrap a fixture workspace AND drop a fusion_proposal.json into
    the experiment worktree so stub_codex can read it."""
    workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0001"
    workspace.mkdir(parents=True, exist_ok=True)
    fusion_payload = {
        "schema_version": "fusion_proposal.v1",
        "fusion_id": "fusion_0001",
        "competition_slug": "tabular_binary_v1",
        "title": "Test fusion",
        "hypothesis": "A 20+ char hypothesis string for the schema.",
        "mechanisms_combined": [
            {"mechanism_name": "a", "source_ref": "r1", "role_in_fusion": "primary."},
            {"mechanism_name": "b", "source_ref": "r2", "role_in_fusion": "secondary."},
        ],
        "implementation_plan": {
            "files_to_create_or_modify": ["submission.csv"],
            "algorithm_steps": ["s1.", "s2."],
            "dependencies": [],
            "expected_outputs": ["submission.csv"],
        },
        "smallest_proxy_test": {
            "description": "A 20+ char description of the smallest proxy test.",
            "dataset_slice": "train",
            "metric": "roc_auc",
            "success_threshold": {"metric": "roc_auc", "comparator": ">=", "value": 0.5},
            "max_runtime_minutes": 5,
        },
        "ablation_plan": [{"name": "a", "remove_or_change": "x", "expected_signal": "y"}],
        "resource_estimate": {"cost_class": "small", "gpu_required": False, "max_runtime_minutes": 5},
        "risks": [],
        "stop_condition": "Stop if metric drops below threshold.",
        "source_refs": ["r1"],
    }
    (workspace / "fusion_proposal.json").write_text(
        json.dumps(fusion_payload), encoding="utf-8"
    )
    return fixture_workspace


def test_stub_codex_emits_submission_with_fusion_id_artifact(
    fixture_workspace_with_fusion: Path,
) -> None:
    """Phase=FUSION_PROXY_IMPLEMENTED → submission.csv + <fusion_id:fusion_NNNN> token."""
    provider = StubCodexProvider(workspace_root=fixture_workspace_with_fusion / "worktrees")
    packet = _proxy_packet(workspace_root=fixture_workspace_with_fusion)
    result = provider.invoke(packet)
    assert result.status == "success"
    # submission.csv exists and has the calibration shape (id, target).
    submission_path = next(p for p in result.artifacts if p.endswith("submission.csv"))
    df = pd.read_csv(submission_path)
    assert list(df.columns) == ["id", "target"]
    # fusion_id token in artifacts so the scoreboard row links back to the proposal.
    assert any(a.startswith("<fusion_id:fusion_0001>") for a in result.artifacts)


def test_stub_codex_calibration_path_unchanged(fixture_workspace: Path) -> None:
    """Backward compat: existing PR1 calibration packet still emits a
    submission.csv WITHOUT the fusion_id token."""
    provider = StubCodexProvider(workspace_root=fixture_workspace / "worktrees")
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Calibration baseline.",
        "inputs": ["fixtures/tabular_binary_v1/test.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid"],
    }
    result = provider.invoke(packet)
    assert result.status == "success"
    assert any(a.endswith("submission.csv") for a in result.artifacts)
    # No fusion_id token on calibration runs.
    assert not any(a.startswith("<fusion_id:") for a in result.artifacts)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_codex_research_proxy.py -v`
Expected: 2 fail because stub_codex doesn't yet read the fusion_id from the input or emit the token.

- [ ] **Step 2: Implement the dispatch in stub_codex**

In `arena/providers/stub_codex.py`, AFTER the existing logic that writes `submission.csv`, branch on phase. For `phase == "FUSION_PROXY_IMPLEMENTED"`, read the fusion_id from the first input (a path ending in `fusion_proposal.json`) and append a `<fusion_id:{fusion_id}>` token to the artifact list.

Replace `arena/providers/stub_codex.py`:

```python
# arena/providers/stub_codex.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_codex.v1"


class StubCodexProvider(ProviderAdapter):
    """Deterministic stand-in for Codex during Phase 0 CI and local stub runs.

    For role=implementation calibration tasks, emits a submission.csv with
    constant 0.5 target predictions for every row in test.csv. The score
    against hidden_labels will be ~0.5 (random); the goal is to prove the
    pipeline, not to win the fixture.

    For role=implementation + phase=FUSION_PROXY_IMPLEMENTED (PR5), reads
    the fusion_id from the inputs[0] (a path ending in fusion_proposal.json)
    and appends a <fusion_id:{fusion_id}> token to the artifact list so the
    scoreboard row links back to the proposal. The submission shape is
    identical to calibration (constant 0.5 in Phase 0; PR7 with real
    Codex will produce non-trivial implementations).

    Path assumption: invoke() reads `fixtures/<slug>/test.csv` relative to
    the current working directory. The Phase 0 CLI invokes from repo root,
    so this is consistent with the rest of the harness.

    Optional fields exercise observability: failed_commands is a list of
    (command_str, exit_code) pairs that the stub emits as
    shell_command_observed events through `event_emitter` before producing
    its normal result.
    """

    def __init__(
        self,
        workspace_root: str | Path = "worktrees",
        *,
        event_emitter: TraceStore | None = None,
        failed_commands: list[tuple[str, int]] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._event_emitter = event_emitter
        self._failed_commands = failed_commands or []

    @property
    def name(self) -> str:
        return "stub_codex"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
        if self._event_emitter is not None:
            for command, exit_code in self._failed_commands:
                self._event_emitter.emit(
                    event_type="shell_command_observed",
                    severity="info" if exit_code == 0 else "warning",
                    task_id=task_packet["task_id"],
                    payload={"command": command, "exit_code": exit_code},
                )
        task_id = task_packet["task_id"]
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubCodexProvider requires task_packet.experiment_id to be set")

        started = datetime.now(UTC).isoformat(timespec="seconds")
        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)

        test_path = Path("fixtures") / slug / "test.csv"
        test_df = pd.read_csv(test_path)
        submission = pd.DataFrame({"id": test_df["id"], "target": 0.5})
        submission_path = workspace / "submission.csv"
        submission.to_csv(submission_path, index=False)

        artifacts: list[str] = [str(submission_path)]
        # PR5: link the proxy submission back to its fusion_id so the
        # scoreboard row carries the connection.
        if task_packet["phase"] == "FUSION_PROXY_IMPLEMENTED":
            fusion_id = self._read_fusion_id_from_inputs(task_packet["inputs"])
            if fusion_id is not None:
                artifacts.append(f"<fusion_id:{fusion_id}>")

        finished = datetime.now(UTC).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(workspace / "stdout.scrubbed"),
            stderr_path=str(workspace / "stderr.scrubbed"),
            artifacts=artifacts,
            input_chars=0,
            output_chars=submission_path.stat().st_size,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=started,
            finished_at=finished,
        )

    def _read_fusion_id_from_inputs(self, inputs: list[str]) -> str | None:
        """Find the first input ending in `fusion_proposal.json` and read
        its `fusion_id` field. Returns None if no such input exists or
        the file is missing/malformed (the caller treats absence as
        skipping the token; missing fusion_id on a FUSION_PROXY_IMPLEMENTED
        packet is a programming error caught upstream by the CLI)."""
        for input_path in inputs:
            if not input_path.endswith("fusion_proposal.json"):
                continue
            p = Path(input_path)
            if not p.exists():
                return None
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            return payload.get("fusion_id")
        return None
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_stub_codex_research_proxy.py -v`
Expected: 2 passed.

- [ ] **Step 3: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 263 tests pass (was 261; +2). All checks clean.

- [ ] **Step 4: Commit**

```bash
git add arena/providers/stub_codex.py tests/test_stub_codex_research_proxy.py
git commit -m "$(cat <<'EOF'
feat(providers): stub_codex appends <fusion_id:...> token on FUSION_PROXY_IMPLEMENTED

For role=implementation + phase=FUSION_PROXY_IMPLEMENTED, stub_codex now
reads the fusion_id from inputs[0] (expected to be a path ending in
fusion_proposal.json) and appends a <fusion_id:{fusion_id}> token to the
ProviderResult.artifacts list.

The token is the link between the scoreboard row and the originating
fusion proposal. The CLI in Task 5 surfaces it through artifact_paths
so `arena replay` can reconstruct the chain.

The submission.csv shape is identical to calibration (constant 0.5);
PR7 with real Codex will produce non-trivial implementations grounded
in the fusion proposal. Backward compat: calibration packets (phase=
CALIBRATION_TASK_CREATED) continue to emit only submission.csv.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `arena research-proxy` CLI orchestration

**Files:**
- Modify: `arena/cli.py` (add `research_proxy` subcommand + `FUSION_ID_TAG_PREFIX` constant + research_proxy imports)
- Create: `tests/test_cli_research_proxy.py`

- [ ] **Step 1: Add the imports + constant + subcommand to cli.py**

In `arena/cli.py`, first ensure `ProviderResult` is on the existing `from arena.providers.base import` line (it may already be there from PR2/PR4; do NOT add a duplicate import):

```python
from arena.providers.base import ProviderAdapter, ProviderResult, UsageProxy
```

Then add the following imports near the existing `from arena.research_proxy.*` block (place after the `arena.providers.*` imports):

```python
from arena.research_proxy.fusion_proposal import (
    make_fusion_proposal_packet,
    validate_fusion_proposal,
)
from arena.research_proxy.fusion_scorer import (
    MIN_FUSION_SCORE,
    is_eligible,
    score_fusion_proposal,
)
from arena.research_proxy.method_digest import (
    make_method_digest_packet,
    validate_paper_digest,
)
from arena.research_proxy.question_generator import make_research_question_packet
```

Add a module-level constant near `PROVIDER_VERSION_CHANGED_TAG`:

```python
# Token prefix used in artifact_paths to link a research-proxy experiment
# row to its fusion proposal. Mirrors PROVIDER_VERSION_CHANGED_TAG: not a
# Phase enum value, just metadata in artifact_paths.
FUSION_ID_TAG_PREFIX = "fusion_id"
```

Add the new subcommand at the end of `arena/cli.py` (after the existing `replay`/`report` subcommands). The full text:

```python
@app.command("research-proxy")
def research_proxy(
    competition_slug: str,
    provider: str = typer.Option(
        "stub_claude",
        "--provider",
        help="Provider to use for the research/digest/fusion steps. The "
        "implementation step (step 7) always uses stub_codex in PR5.",
    ),
) -> None:
    """Run the §6.2 research-fusion proxy loop steps 1-8 against the
    first method note in fixtures/<slug>/paper_bundle/.

    Persists FOUR experiment rows under one run_id — one per provider
    invocation (research_proxy_question, research_proxy_digest,
    research_proxy_fusion, research_proxy_implementation). Each row carries
    its own usage_proxy from the corresponding ProviderResult, so
    `arena budget status` and pre-invoke caps see all four calls. The
    fusion_id token appears in artifact_paths starting from row 3 (when
    fusion_id is first known). The implementation row (row 4) gets the
    score via `arena evaluate`'s flow.

    On step-6 gate failure, rows 1-3 are completed and NO row 4 is
    inserted — stub_codex was never invoked, so provider_calls (derived
    from COUNT(*) by get_run_usage_totals) must not increment. On
    POST-invoke exception (BudgetExceeded from record_post_invoke,
    SandboxViolation inside wrap_invoke), the in-flight step's row is
    inserted as status=blocked with the partial state captured AND
    usage_proxy threaded through from the exception. Pre-invoke
    exceptions (KillSwitchActive, ProviderCallBreaker tripped in
    check_can_invoke) leave the scoreboard untouched. Mirrors
    arena run-next in arena/cli.py:185-377.
    """
    if provider not in {"stub_claude"}:
        raise typer.BadParameter(
            f"unknown research provider {provider!r}; PR5 supports only stub_claude"
        )

    method_note_path = (
        f"fixtures/{competition_slug}/paper_bundle/method_note_001.md"
    )
    if not Path(method_note_path).exists():
        raise typer.BadParameter(f"method note missing: {method_note_path}")

    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(
            f"no run for {competition_slug}; "
            f"run `arena init-fixture {competition_slug}` first"
        )
    store = _store()

    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)
    sandbox_policy = SandboxPolicy.from_packet(
        {
            "allowed_paths": [],  # set per-step
            "blocked_paths": [
                "~/.kaggle/",
                "~/.codex/",
                "~/.claude/",
                ".env",
                f"fixtures/{competition_slug}/hidden_labels.csv",
            ],
        },
        workspace_root=Path.cwd(),
    )

    research_adapter = _get_provider(provider, event_emitter=trace_store)
    impl_adapter = _get_provider("stub_codex", event_emitter=trace_store)

    # Seed governor accumulators from prior usage on this run so PR5
    # respects run-level provider-call caps already consumed by
    # calibration or earlier research-proxy invocations.
    totals = store.get_run_usage_totals(competition_slug, run_id)
    accumulators = RunAccumulators(
        provider_calls=int(totals["provider_calls"]),
        codex_calls=int(totals["codex_calls"]),
        claude_calls=int(totals["claude_calls"]),
        wall_seconds=float(totals["wall_seconds"]),
        input_chars=int(totals["input_chars"]),
        output_chars=int(totals["output_chars"]),
        waste_events=int(totals["waste_events"]),
    )
    governor = BudgetGovernor(Phase0HardCeilings.from_env(), accumulators=accumulators)
    watchdog = Watchdog(governor=governor)

    def _step_ids() -> tuple[str, str]:
        """Mint matched (experiment_id, task_id) for the next step.

        Each invocation gets its own row in the experiments table so
        provider_calls (derived from COUNT(*) by get_run_usage_totals) is
        accurate. task_id matches the numeric suffix so trace events
        cluster by step in arena replay output.
        """
        exp_id = store.get_next_experiment_id(competition_slug)
        task_id = exp_id.replace("exp_", "task_")
        return exp_id, task_id

    def _guarded_invoke(adapter: ProviderAdapter, packet: dict) -> ProviderResult:
        """Run check_can_invoke + wrap_invoke with the same sandbox.

        check_can_invoke catches kill-switch + pre-invoke provider-call cap
        BEFORE any invoke work runs. wrap_invoke catches SandboxViolation,
        mid-invoke BudgetExceeded (live waste detector), and post-invoke
        BudgetExceeded.

        Sets in_flight["invocation_started"] = True ONLY after
        check_can_invoke succeeds, so the outer except handlers can tell
        whether the failure was pre-invoke (no row) vs post-invoke (row
        with usage_proxy). Mirrors arena/cli.py:185-194 (run-next).
        """
        # Build a packet-scoped sandbox: each step's allowed_paths is its
        # own experiment worktree.
        per_step_sandbox = SandboxRunner(
            SandboxPolicy.from_packet(packet, workspace_root=Path.cwd())
        )
        # Pre-invoke: kill switch + run-level provider-call cap.
        # Failures here mean NO invocation happened, so the outer except
        # must NOT persist a blocked row. invocation_started stays False.
        watchdog.check_can_invoke(adapter.name)
        # Past this point, we are about to invoke. From here on, an
        # exception (BudgetExceeded post-invoke, SandboxViolation, etc.)
        # reflects work that actually started, and a blocked row is
        # appropriate.
        in_flight["invocation_started"] = True
        return watchdog.wrap_invoke(
            adapter, packet, sandbox=per_step_sandbox, event_emitter=trace_store
        )

    def _persist_row(
        *,
        experiment_id: str,
        task_id: str,
        experiment_type: str,
        adapter_name: str,
        adapter_version: str,
        status: str,
        artifact_paths: list[str],
        usage_proxy: dict | None,
        score: float | None = None,
        valid_submission: bool | None = None,
    ) -> None:
        """Insert one research-proxy experiment row with consistent shape.

        usage_proxy=None means no usage was reported (e.g. SandboxViolation
        with no usage attached); the row records zeros. For post-invoke
        BudgetExceeded the caller MUST pass usage_proxy=exc.usage_proxy
        so the consumed usage is durable for the next run's seeded
        accumulators.
        """
        store.insert_experiment(
            experiment_id=experiment_id,
            run_id=run_id,
            competition_slug=competition_slug,
            task_id=task_id,
            experiment_type=experiment_type,
            provider=adapter_name,
            provider_version=adapter_version,
            status=status,
            metric_name="roc_auc",
            valid_submission=valid_submission,
            artifact_paths=artifact_paths,
            trace_path=None,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            input_chars=int(usage_proxy["input_chars"]) if usage_proxy else 0,
            output_chars=int(usage_proxy["output_chars"]) if usage_proxy else 0,
            wall_seconds=float(usage_proxy["wall_seconds"]) if usage_proxy else 0.0,
            shell_commands=int(usage_proxy["shell_commands"]) if usage_proxy else 0,
            failed_commands=int(usage_proxy["failed_commands"]) if usage_proxy else 0,
            waste_events=int(usage_proxy["waste_events"]) if usage_proxy else 0,
        )
        if score is not None:
            store.update_experiment_score(experiment_id, score=score)

    # Track the in-flight step so a POST-invoke exception can persist a
    # blocked row for the failing step. invocation_started is set to True
    # ONLY after check_can_invoke succeeds in _guarded_invoke; pre-invoke
    # failures (KillSwitchActive, ProviderCallBreaker in check_can_invoke)
    # leave it False so no row is inserted — keeps COUNT(*)-derived
    # provider_calls accurate. Mirrors arena run-next in arena/cli.py.
    in_flight: dict[str, str | bool | None] = {
        "experiment_id": None,
        "task_id": None,
        "experiment_type": None,
        "adapter_name": None,
        "adapter_version": None,
        "invocation_started": False,
    }

    fusion_id_known: str | None = None  # populated after step 5

    try:
        # Step 1+2+3: research_question task → stub_claude → validate.
        rq_exp, rq_task = _step_ids()
        in_flight.update(
            experiment_id=rq_exp,
            task_id=rq_task,
            experiment_type="research_proxy_question",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            # Reset for each new step; _guarded_invoke flips this to True
            # only after check_can_invoke succeeds.
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, rq_exp)
        trace_store.emit(
            event_type="run_started",
            severity="info",
            payload={
                "phase": Phase.NEW.value,
                "message": "research-proxy run started",
            },
        )
        rq_packet = make_research_question_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=rq_exp,
            task_id=rq_task,
            question_id="rq_0001",
            source_refs=[method_note_path],
        )
        rq_result = _guarded_invoke(research_adapter, rq_packet)
        rq_artifact = next(
            a for a in rq_result.artifacts if a.endswith("research_question.json")
        )
        rq_payload = json.loads(Path(rq_artifact).read_text(encoding="utf-8"))
        from arena.schemas.validate import validate as _validate_schema

        _validate_schema("research_question", rq_payload)
        _persist_row(
            experiment_id=rq_exp,
            task_id=rq_task,
            experiment_type="research_proxy_question",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=[rq_artifact],
            usage_proxy=rq_result.usage_proxy,
        )
        console.print(
            f"[green]step 1-3 ok[/green]: research_question {rq_payload['question_id']}"
        )

        # Step 4: digest → paper_digest.json.
        digest_exp, digest_task = _step_ids()
        in_flight.update(
            experiment_id=digest_exp,
            task_id=digest_task,
            experiment_type="research_proxy_digest",
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, digest_exp)
        digest_packet = make_method_digest_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=digest_exp,
            task_id=digest_task,
            digest_id="pd_0001",
            method_note_path=method_note_path,
        )
        digest_result = _guarded_invoke(research_adapter, digest_packet)
        digest_artifact = next(
            a for a in digest_result.artifacts if a.endswith("paper_digest.json")
        )
        digest_payload = json.loads(Path(digest_artifact).read_text(encoding="utf-8"))
        validate_paper_digest(digest_payload)
        _persist_row(
            experiment_id=digest_exp,
            task_id=digest_task,
            experiment_type="research_proxy_digest",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=[digest_artifact],
            usage_proxy=digest_result.usage_proxy,
        )
        console.print(
            f"[green]step 4 ok[/green]: paper_digest {digest_payload['digest_id']}"
        )

        # Step 5: fusion proposal → fusion_proposal.json.
        fp_exp, fp_task = _step_ids()
        in_flight.update(
            experiment_id=fp_exp,
            task_id=fp_task,
            experiment_type="research_proxy_fusion",
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, fp_exp)
        fp_packet = make_fusion_proposal_packet(
            competition_slug=competition_slug,
            run_id=run_id,
            experiment_id=fp_exp,
            task_id=fp_task,
            fusion_id="fusion_0001",
            digest_path=digest_artifact,
        )
        fp_result = _guarded_invoke(research_adapter, fp_packet)
        fp_artifact = next(
            a for a in fp_result.artifacts if a.endswith("fusion_proposal.json")
        )
        fp_payload = json.loads(Path(fp_artifact).read_text(encoding="utf-8"))
        validate_fusion_proposal(fp_payload)
        fusion_id_known = fp_payload["fusion_id"]
        fusion_token = f"<{FUSION_ID_TAG_PREFIX}:{fusion_id_known}>"
        _persist_row(
            experiment_id=fp_exp,
            task_id=fp_task,
            experiment_type="research_proxy_fusion",
            adapter_name=research_adapter.name,
            adapter_version=research_adapter.version,
            status="completed",
            artifact_paths=[fp_artifact, fusion_token],
            usage_proxy=fp_result.usage_proxy,
        )
        console.print(
            f"[green]step 5 ok[/green]: fusion_proposal {fusion_id_known}"
        )

        # Step 6: deterministic gate. Halt before stub_codex if score is
        # too low OR is_eligible returns False. NO row is inserted because
        # stub_codex was never invoked — provider_calls (derived from
        # COUNT(*) by get_run_usage_totals) must not increment for a
        # would-be call that never happened.
        fusion_score = score_fusion_proposal(fp_payload)
        eligible, reasons = is_eligible(fp_payload)
        console.print(
            f"[blue]step 6 score={fusion_score.score:.3f} "
            f"(cost={fusion_score.cost:.2f} risk={fusion_score.risk:.2f} "
            f"fit={fusion_score.fit:.2f}) eligible={eligible}[/blue]"
        )
        if fusion_score.score < MIN_FUSION_SCORE or not eligible:
            gate_message = (
                f"fusion gate failed: score={fusion_score.score:.3f} "
                f"(min={MIN_FUSION_SCORE}); reasons={reasons or ['low score']}"
            )
            # NO row inserted: stub_codex was never invoked, so
            # provider_calls must not increment. The 3 successful rows
            # (question, digest, fusion) already in scoreboard tell the
            # operator exactly how far the chain got. The fusion row's
            # artifact_paths carries the fusion_proposal JSON path; the
            # gate decision is reproducible from that payload via
            # arena replay or score_fusion_proposal.
            console.print(f"[red]{gate_message}[/red]")
            raise typer.Exit(code=2)

        # Step 7: stub_codex implements the proxy.
        proxy_exp, proxy_task = _step_ids()
        in_flight.update(
            experiment_id=proxy_exp,
            task_id=proxy_task,
            experiment_type="research_proxy_implementation",
            adapter_name=impl_adapter.name,
            adapter_version=impl_adapter.version,
            invocation_started=False,
        )
        create_workspace(WORKTREE_ROOT, competition_slug, proxy_exp)
        proxy_packet = {
            "schema_version": "task_packet.v1",
            "task_id": proxy_task,
            "competition_slug": competition_slug,
            "experiment_id": proxy_exp,
            "provider": "stub_codex",
            "role": "implementation",
            "phase": "FUSION_PROXY_IMPLEMENTED",
            "objective": (
                f"Implement the smallest proxy test from fusion_proposal "
                f"{fusion_id_known}. Inputs[0] is the fusion proposal "
                "path; emit submission.csv that satisfies "
                "fixtures/<slug>/sample_submission.csv columns."
            ),
            "inputs": [fp_artifact, f"fixtures/{competition_slug}/test.csv"],
            "allowed_paths": [f"worktrees/{competition_slug}/{proxy_exp}/"],
            "blocked_paths": [
                "~/.kaggle/",
                "~/.codex/",
                "~/.claude/",
                ".env",
                f"fixtures/{competition_slug}/hidden_labels.csv",
            ],
            "budgets": {
                "max_wall_minutes": 20,
                "max_shell_commands": 35,
                "max_failed_commands": 5,
                "max_input_chars": 75000,
                "max_output_chars": 25000,
            },
            "required_outputs": ["submission.csv"],
            "success_criteria": ["valid"],
        }
        proxy_result = _guarded_invoke(impl_adapter, proxy_packet)
        submission_path = next(
            a for a in proxy_result.artifacts if a.endswith("submission.csv")
        )
        # stub_codex appends <fusion_id:fusion_NNNN> on FUSION_PROXY_IMPLEMENTED.
        # Use the existing token if present, otherwise fall through to fusion_token.
        fusion_id_token = next(
            (a for a in proxy_result.artifacts if a.startswith(f"<{FUSION_ID_TAG_PREFIX}:")),
            fusion_token,
        )
        console.print(f"[green]step 7 ok[/green]: proxy submission {submission_path}")

        # Step 8: evaluate the proxy submission.
        hidden = FIXTURES_ROOT / competition_slug / "hidden_labels.csv"
        eval_result = evaluate_fixture_submission(submission_path, hidden)
        if not eval_result.valid_submission:
            _persist_row(
                experiment_id=proxy_exp,
                task_id=proxy_task,
                experiment_type="research_proxy_implementation",
                adapter_name=impl_adapter.name,
                adapter_version=impl_adapter.version,
                status="blocked",
                artifact_paths=[
                    submission_path,
                    fusion_id_token,
                    "<blocked:InvalidSubmission>",
                    f"<message:{(eval_result.error or 'invalid')[:200]}>",
                ],
                usage_proxy=proxy_result.usage_proxy,
            )
            console.print(f"[red]step 8 invalid submission: {eval_result.error}[/red]")
            raise typer.Exit(code=1)
        assert eval_result.score is not None
        console.print(f"[green]step 8 ok[/green]: score={eval_result.score:.6f}")

        _persist_row(
            experiment_id=proxy_exp,
            task_id=proxy_task,
            experiment_type="research_proxy_implementation",
            adapter_name=impl_adapter.name,
            adapter_version=impl_adapter.version,
            status="completed",
            artifact_paths=[submission_path, fusion_id_token],
            usage_proxy=proxy_result.usage_proxy,
            score=eval_result.score,
            valid_submission=True,
        )

        # Emit score_recorded for replay (mirrors the evaluate command).
        trace_store.emit(
            event_type="score_recorded",
            severity="info",
            task_id=proxy_task,
            payload={
                "score": eval_result.score,
                "metric_name": "roc_auc",
                "experiment_id": proxy_exp,
                "status": "valid",
            },
        )

        console.print(
            f"[bold green]research-proxy complete[/bold green] — "
            f"fusion_id={fusion_id_known} score={eval_result.score:.6f}"
        )
    except KillSwitchActive as exc:
        # Always pre-invoke (check_can_invoke is the only place that
        # raises this). No provider call happened → no row.
        console.print(f"[red]kill switch active: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except BudgetExceeded as exc:
        if in_flight["invocation_started"]:
            # Post-invoke: provider returned, then per-task cap or
            # post-invoke run-level cap tripped in record_post_invoke.
            # Persist with usage_proxy from the exception so consumed
            # usage is durable for the next run's seeded accumulators.
            _persist_inflight_blocked(
                _persist_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=exc.usage_proxy,
            )
        # Pre-invoke (ProviderCallBreaker tripped in check_can_invoke):
        # no row — that would inflate COUNT(*) into a fake provider call.
        console.print(f"[red]budget exceeded ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except SandboxViolation as exc:
        # SandboxViolation only fires from inside wrap_invoke (sandbox is
        # active during adapter.invoke), so invocation_started is always
        # True here. Defensive guard kept for symmetry.
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=None,
            )
        console.print(
            f"[red]sandbox violation ({exc.breaker.value}): {exc}[/red]"
        )
        raise typer.Exit(code=2) from exc


def _persist_inflight_blocked(
    persist_row,
    in_flight: dict,
    breaker_or_reason: str,
    message: str,
    *,
    usage_proxy: UsageProxy | None = None,
) -> None:
    """Insert a status=blocked row for the in-flight step on mid-chain
    exception. Skips if no step has started yet OR if invocation never
    began (check_can_invoke raised pre-invoke). When usage_proxy is
    provided (post-invoke BudgetExceeded), the row records the consumed
    usage so arena budget status reflects what the failing call cost."""
    if in_flight["experiment_id"] is None:
        return
    if not in_flight.get("invocation_started"):
        # Defense-in-depth: if the caller forgot to gate on this flag,
        # we still skip the row insertion to avoid inflating provider_calls.
        return
    persist_row(
        experiment_id=in_flight["experiment_id"],
        task_id=in_flight["task_id"],
        experiment_type=in_flight["experiment_type"],
        adapter_name=in_flight["adapter_name"] or "unknown",
        adapter_version=in_flight["adapter_version"] or "unknown",
        status="blocked",
        artifact_paths=[
            f"<blocked:{breaker_or_reason}>",
            f"<message:{message[:200]}>",
        ],
        usage_proxy=usage_proxy,
    )
```

- [ ] **Step 2: Write the failing CLI tests**

```python
# tests/test_cli_research_proxy.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def test_research_proxy_runs_steps_1_through_8(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: arena research-proxy tabular_binary_v1 --provider stub_claude
    runs steps 1-8 against method_note_001.md and produces all 4 artifacts +
    four scoreboard rows (one per provider invocation)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 0, result.output
    assert "fusion_id=fusion_0001" in result.output
    assert "score=" in result.output

    # Artifacts land in separate per-step worktrees (exp_0001 through exp_0004).
    wt_root = fixture_workspace / "worktrees" / "tabular_binary_v1"
    assert (wt_root / "exp_0001" / "research_question.json").exists()
    assert (wt_root / "exp_0002" / "paper_digest.json").exists()
    assert (wt_root / "exp_0003" / "fusion_proposal.json").exists()
    assert (wt_root / "exp_0004" / "submission.csv").exists()

    # Four scoreboard rows: question, digest, fusion, implementation.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_id, experiment_type, status, score, artifact_paths "
            "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        types = [r["experiment_type"] for r in rows]
        assert "research_proxy_question" in types
        assert "research_proxy_digest" in types
        assert "research_proxy_fusion" in types
        assert "research_proxy_implementation" in types
        # All four completed.
        assert all(r["status"] == "completed" for r in rows)
        # Fusion + implementation rows carry the fusion_id token.
        fusion_rows = [r for r in rows if r["experiment_type"] == "research_proxy_fusion"]
        assert len(fusion_rows) == 1
        assert any(
            "<fusion_id:fusion_0001>" in p
            for p in json.loads(fusion_rows[0]["artifact_paths"])
        )
        # Implementation row has the score.
        impl_rows = [
            r for r in rows if r["experiment_type"] == "research_proxy_implementation"
        ]
        assert len(impl_rows) == 1
        assert impl_rows[0]["score"] is not None
    finally:
        store.close()


def test_research_proxy_halts_at_fusion_gate_below_min_score(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force MIN_FUSION_SCORE above the deterministic stub's score so the
    chain halts at step 6. Asserts rows 1-3 completed + NO row 4 (since
    stub_codex was never invoked, no provider_calls increment), and
    that submission.csv is NOT created (stub_codex was never invoked)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setattr(
        "arena.research_proxy.fusion_scorer.MIN_FUSION_SCORE", 0.99
    )
    # Rebind the symbol the CLI imports.
    monkeypatch.setattr("arena.cli.MIN_FUSION_SCORE", 0.99)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 2
    assert "fusion gate failed" in result.output

    wt_root = fixture_workspace / "worktrees" / "tabular_binary_v1"
    # Steps 1-5 produced their artifacts (exp_0001 through exp_0003); step 7 did NOT.
    assert (wt_root / "exp_0003" / "fusion_proposal.json").exists()
    assert not any((wt_root / f"exp_{i:04d}" / "submission.csv").exists() for i in range(1, 6))

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_type, status, artifact_paths FROM experiments "
            "WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        # Steps 1-3 + 5 produced their rows as completed. NO implementation
        # row exists because stub_codex was never invoked at the gate.
        types_and_status = [(r["experiment_type"], r["status"]) for r in rows]
        assert ("research_proxy_question", "completed") in types_and_status
        assert ("research_proxy_digest", "completed") in types_and_status
        assert ("research_proxy_fusion", "completed") in types_and_status
        # No implementation row (provider_calls must equal 3, not 4).
        assert not any(
            t == "research_proxy_implementation" for t, _ in types_and_status
        )
    finally:
        store.close()


def test_research_proxy_rejects_unknown_provider(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR5 supports only stub_claude as the research provider."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_codex"],
    )
    assert result.exit_code != 0
    assert "unknown research provider" in result.output


def test_research_proxy_rejects_missing_method_note(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If method_note_001.md is missing, the CLI fails fast before any
    provider invocation or scoreboard write."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    method_note = (
        fixture_workspace
        / "fixtures"
        / "tabular_binary_v1"
        / "paper_bundle"
        / "method_note_001.md"
    )
    method_note.unlink()
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code != 0
    assert "method note missing" in result.output


def test_research_proxy_blocks_on_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_can_invoke must fire BEFORE the first wrap_invoke. Setting
    ARENA_KILL_SWITCH should halt research-proxy at step 1."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "kill switch active" in result.output.lower()


def test_research_proxy_blocks_on_pre_invoke_budget_cap(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider-call run-level cap fires via check_can_invoke. Setting the
    cap to 0 should halt research-proxy before step 2's first invoke."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setenv("ARENA_PROVIDER_CALLS_TOTAL", "0")

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "budget exceeded" in result.output.lower() or "ProviderCallBreaker" in result.output


def test_research_proxy_does_not_collide_after_calibration(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration first creates exp_0001; research-proxy mints 4 more
    (exp_0002 through exp_0005) via get_next_experiment_id — one per
    provider invocation, with no primary-key collision."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    # Now research-proxy MUST get different experiment_ids (exp_0002 through exp_0005).
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_id FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        ids = [r["experiment_id"] for r in rows]
        assert "exp_0001" in ids  # calibration
        # research-proxy persists 4 rows; total ≥ 5 distinct IDs.
        assert len(set(ids)) >= 5
        # And the research-proxy rows are exp_0002 through exp_0005.
        assert {"exp_0002", "exp_0003", "exp_0004", "exp_0005"}.issubset(set(ids))
    finally:
        store.close()


def test_research_proxy_persists_usage_totals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each of the 4 per-invocation usage_proxy values must be stored on
    its own experiment row so arena budget status sees actual cost per step.
    Stubs report zero usage, but stub_codex's submission.csv contributes
    output_chars (the file size is read in build_result) — that's a useful
    smoke check for the implementation row."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_type, output_chars, input_chars, wall_seconds, "
            "shell_commands, failed_commands, waste_events "
            "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        assert len(rows) == 4
        # All 4 rows have non-negative usage fields.
        for row in rows:
            assert row["output_chars"] >= 0
            assert row["input_chars"] >= 0
            assert row["wall_seconds"] >= 0.0
            assert row["shell_commands"] >= 0
            assert row["failed_commands"] >= 0
            assert row["waste_events"] >= 0
        # Implementation row (stub_codex) MUST contribute non-zero
        # output_chars from the submission.csv file size (build_result
        # calls submission_path.stat().st_size). A zero here would
        # indicate the usage_proxy round-trip from ProviderResult →
        # insert_experiment is broken.
        impl_row = next(r for r in rows if r["experiment_type"] == "research_proxy_implementation")
        assert impl_row["output_chars"] > 0
    finally:
        store.close()


def test_research_proxy_respects_run_level_provider_call_cap_after_calibration(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration consumes one provider call; cap research-proxy at the
    new total to verify the governor seeds from get_run_usage_totals."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])

    # Calibration consumed 1 provider call. Cap research-proxy at 1 so its
    # very first invoke fails check_can_invoke (would-be call count = 2).
    monkeypatch.setenv("ARENA_PROVIDER_CALLS_TOTAL", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    output_lower = result.output.lower()
    assert "budget exceeded" in output_lower or "provider" in output_lower


def test_research_proxy_does_not_persist_row_on_pre_invoke_provider_call_cap(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the run-level provider-call cap allows N invocations and
    research-proxy needs N+1, the (N+1)th call halts in check_can_invoke
    BEFORE wrap_invoke runs. No provider invocation happened for that
    step, so no scoreboard row should be inserted — otherwise COUNT(*)
    would inflate get_run_usage_totals.provider_calls and the next run's
    seeded budget would be wrong."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Cap at 2 provider calls. Steps 2 (question) + 4 (digest) succeed;
    # step 5 (fusion) fails check_can_invoke (third call would-be).
    monkeypatch.setenv("ARENA_PROVIDER_CALLS_TOTAL", "2")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "budget exceeded" in result.output.lower() or "ProviderCallBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_type, status FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        types_and_status = [(r["experiment_type"], r["status"]) for r in rows]
        # Exactly TWO rows: the two completed steps. No row for the
        # pre-invoke-blocked third step. provider_calls = COUNT(*) = 2,
        # matching the cap.
        assert len(rows) == 2
        assert ("research_proxy_question", "completed") in types_and_status
        assert ("research_proxy_digest", "completed") in types_and_status
        assert not any(
            t == "research_proxy_fusion" for t, _ in types_and_status
        )
        # Verify the seeded-budget invariant: get_run_usage_totals must
        # report provider_calls == 2, NOT 3.
        run_row = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE competition_slug = ? LIMIT 1",
            ("tabular_binary_v1",),
        ).fetchone()
        run_id = run_row["run_id"]
        totals = store.get_run_usage_totals("tabular_binary_v1", run_id)
        assert totals["provider_calls"] == 2
    finally:
        store.close()


def test_research_proxy_does_not_persist_row_on_first_call_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill switch active before research-proxy starts. The first
    check_can_invoke (step 2) raises KillSwitchActive — no provider call
    happens, so no scoreboard row is inserted. provider_calls must
    remain 0."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "kill switch" in result.output.lower()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_id FROM experiments WHERE competition_slug = ?",
            ("tabular_binary_v1",),
        ).fetchall()
        # No invocations succeeded, so no rows.
        assert len(rows) == 0
    finally:
        store.close()


def test_research_proxy_does_not_persist_row_on_fusion_gate_block(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When score_fusion_proposal returns below MIN_FUSION_SCORE, the
    chain halts before stub_codex runs. The 3 completed rows (question,
    digest, fusion) reflect the work that actually happened; no fourth
    row is inserted because no provider call occurred for the
    implementation step. provider_calls must equal 3, not 4."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Force the gate to fail by setting the threshold above what the
    # stub_claude payload can score. The default stub fusion proposal
    # produces a known deterministic score; ARENA_MIN_FUSION_SCORE=1.0
    # guarantees the gate fails (score is in [0, 1] and 1.0 is the
    # strict ceiling).
    monkeypatch.setenv("ARENA_MIN_FUSION_SCORE", "1.0")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "fusion gate failed" in result.output.lower()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_type, status FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        types_and_status = [(r["experiment_type"], r["status"]) for r in rows]
        # Three rows: question, digest, fusion (all completed). No
        # implementation row.
        assert len(rows) == 3
        assert ("research_proxy_question", "completed") in types_and_status
        assert ("research_proxy_digest", "completed") in types_and_status
        assert ("research_proxy_fusion", "completed") in types_and_status
        assert not any(
            t == "research_proxy_implementation" for t, _ in types_and_status
        )
        # Seeded-budget invariant.
        run_row = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE competition_slug = ? LIMIT 1",
            ("tabular_binary_v1",),
        ).fetchone()
        totals = store.get_run_usage_totals("tabular_binary_v1", run_row["run_id"])
        assert totals["provider_calls"] == 3
    finally:
        store.close()


def test_research_proxy_persists_post_invoke_budget_blocked_row_with_usage(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-invoke BudgetExceeded (run-level output_chars cap) — the
    provider returned a result, then record_post_invoke detected the
    over-cap usage and raised BudgetExceeded with usage_proxy populated.
    The blocked row MUST persist that usage_proxy so arena budget status
    and the next-run seeded accumulators reflect what was consumed.
    Reproduces the PR2 bug class for the research-proxy chain.

    Note: arena/budget/policy.py exposes only run-level char caps
    (ARENA_PHASE0_OUTPUT_CHARS_CAP). Setting it to 1 trips post-invoke
    after the first stub_claude call (which writes
    research_question.json — file size > 1 byte, build_result reports
    that as output_chars). record_post_invoke raises BudgetExceeded
    with usage_proxy attached AFTER invoke completed —
    invocation_started is True, so a blocked row IS persisted, with the
    consumed usage attached."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_type, status, output_chars "
            "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        # Exactly one row: the question step, status=blocked, with
        # usage_proxy persisted (output_chars > 0 reflects the consumed
        # usage from the exception's usage_proxy).
        assert len(rows) == 1
        assert rows[0]["experiment_type"] == "research_proxy_question"
        assert rows[0]["status"] == "blocked"
        assert rows[0]["output_chars"] > 0
    finally:
        store.close()
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_research_proxy.py -v`
Expected: 13 passed.

- [ ] **Step 3: Run full suite + lint + mypy + all CI scripts**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
.venv/Scripts/python.exe scripts/validate_schemas.py
.venv/Scripts/python.exe scripts/validate_prompt_delimiters.py
.venv/Scripts/python.exe scripts/fixture_smoke.py
.venv/Scripts/python.exe scripts/static_sandbox_policy_check.py
.venv/Scripts/python.exe scripts/validate_memory_examples.py
.venv/Scripts/python.exe scripts/check_migrations.py
```

Expected: 276 tests pass (was 263; +13 cli research-proxy tests). All 6 CI scripts green. ruff/format/mypy clean.

- [ ] **Step 4: Commit**

```bash
git add arena/cli.py tests/test_cli_research_proxy.py
git commit -m "$(cat <<'EOF'
feat(cli): arena research-proxy orchestrates §6.2 steps 1-8 with 4-row design

New Typer subcommand `research-proxy <slug> --provider stub_claude`
orchestrates the 8-step research-fusion proxy loop. Each of the 4 provider
invocations gets its own experiment_id (via get_next_experiment_id) and
task_id (matching suffix) so COUNT(*) and per-task trace replay both
work correctly. Each invocation goes through _guarded_invoke (check_can_invoke
+ wrap_invoke) so PR3 sandbox enforcement, PR4 trace events, and PR4 live
waste detection apply uniformly.

BudgetGovernor is seeded from store.get_run_usage_totals(slug, run_id) so
run-level provider-call caps correctly account for prior calibration or
earlier research-proxy invocations.

Step 6 (fusion_scorer + is_eligible) is a pure function gate. If the
proposal scores below MIN_FUSION_SCORE OR fails the §6.3 eligibility
checklist, the CLI exits code 2 WITHOUT inserting a fourth row —
stub_codex was never invoked, so provider_calls (derived from COUNT(*)
by get_run_usage_totals) must not increment. Rows 1-3 (question/digest/
fusion) are already persisted as completed. No submission.csv is written.

On success, four rows are persisted:
- research_proxy_question (row 1) — rq artifact
- research_proxy_digest (row 2) — digest artifact
- research_proxy_fusion (row 3) — fusion artifact + fusion_id token
- research_proxy_implementation (row 4) — submission.csv + score

On POST-invoke exception (BudgetExceeded from record_post_invoke,
SandboxViolation inside wrap_invoke), _persist_inflight_blocked inserts
a blocked row for the in-flight step WITH usage_proxy threaded through
from the exception so consumed usage is durable. Pre-invoke exceptions
(KillSwitchActive, ProviderCallBreaker tripped in check_can_invoke)
leave the scoreboard untouched — gated by an in_flight["invocation_started"]
flag set only after check_can_invoke succeeds. Mirrors arena run-next
in arena/cli.py:185-377.

The trace store receives run_started + score_recorded for the controller
actions. arena replay <run_id> reconstructs the full chain via 4 task_ids.

Tests (13): happy path (4 rows, all completed, fusion_id on rows 3+4,
score on row 4), gate-block (3 completed + NO impl row — provider_calls=3),
unknown-provider rejection, missing-method-note fail-fast, kill-switch halt
(first-call kill switch leaves provider_calls=0), pre-invoke cap at 0,
collision-free IDs after calibration, per-row usage values (impl row
output_chars > 0 from submission.csv), run-level cap after calibration,
mid-chain pre-invoke cap leaves only completed rows (no blocked row,
provider_calls=cap), post-invoke BudgetExceeded persists blocked row WITH
usage_proxy.

PR5 explicitly does NOT close the loop with review or memory proposal
— that's PR6's research_review.json + memory_update.json work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Eligibility checklist enforcement

**Files:**
- Create: `tests/test_research_proxy_eligibility.py`

- [ ] **Step 1: Write the eligibility tests**

```python
# tests/test_research_proxy_eligibility.py
"""Acceptance tests for the §6.3 eligibility checklist (the spec's
'A fusion proposal is eligible only if it has...' list).

These tests verify the deterministic stub_claude fusion proposals
satisfy every item in the checklist. Real Claude in PR7 will produce
varied output and these tests serve as the contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.research_proxy.fusion_scorer import is_eligible


def _emit_proposal_via_cli(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> dict:
    """Run a research-proxy session and read the resulting fusion_proposal.json."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0
    # With the 4-row design, step 3 (exp_0001), step 4 (exp_0002), step 5 (exp_0003),
    # step 7 (exp_0004). fusion_proposal.json is written by the third invocation.
    fp_path = (
        fixture_workspace
        / "worktrees"
        / "tabular_binary_v1"
        / "exp_0003"
        / "fusion_proposal.json"
    )
    return json.loads(fp_path.read_text(encoding="utf-8"))


def test_stub_proposal_has_two_or_more_mechanisms(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert len(proposal["mechanisms_combined"]) >= 2


def test_stub_proposal_has_smallest_proxy_test(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    spt = proposal["smallest_proxy_test"]
    assert len(spt["description"]) >= 20
    assert spt["dataset_slice"]
    assert spt["metric"]
    assert "value" in spt["success_threshold"]
    assert spt["max_runtime_minutes"] <= 60


def test_stub_proposal_has_ablation_plan(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert len(proposal["ablation_plan"]) >= 1
    for ablation in proposal["ablation_plan"]:
        assert "name" in ablation
        assert "remove_or_change" in ablation
        assert "expected_signal" in ablation


def test_stub_proposal_has_complete_resource_estimate(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    re_est = proposal["resource_estimate"]
    assert re_est["cost_class"] in {"tiny", "small", "medium", "large"}
    assert isinstance(re_est["gpu_required"], bool)
    assert re_est["max_runtime_minutes"] >= 1


def test_stub_proposal_has_risk_list_and_stop_condition(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    assert isinstance(proposal["risks"], list)
    assert len(proposal["stop_condition"]) >= 10


def test_stub_proposal_passes_is_eligible(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deterministic stub fusion proposal must pass the full §6.3
    checklist as encoded by is_eligible."""
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    passes, reasons = is_eligible(proposal)
    assert passes is True, f"reasons: {reasons}"


def test_stub_proposal_has_no_forbidden_network_dependency(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    deps = proposal["implementation_plan"]["dependencies"]
    forbidden = {"requests", "urllib", "httpx", "aiohttp"}
    assert not any(any(f in d for f in forbidden) for d in deps), deps


def test_stub_proposal_has_no_untrusted_code_import(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _emit_proposal_via_cli(fixture_workspace, monkeypatch)
    blob = " ".join(proposal["implementation_plan"]["algorithm_steps"])
    forbidden = {"subprocess", "os.system", "eval(", "exec("}
    assert not any(f in blob.lower() for f in forbidden)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_research_proxy_eligibility.py -v`
Expected: 8 passed.

- [ ] **Step 2: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 284 tests pass (was 276; +8). All checks clean.

- [ ] **Step 3: Commit**

```bash
git add tests/test_research_proxy_eligibility.py
git commit -m "$(cat <<'EOF'
test(research_proxy): §6.3 eligibility checklist enforcement

Eight tests verify the deterministic stub_claude fusion proposal
satisfies every item in PHASE_0_SINGLE_SCOPE_PLAN.md §6.3:

- 2+ mechanisms_combined
- non-trivial smallest_proxy_test
- ablation_plan with name/remove_or_change/expected_signal entries
- complete resource_estimate (cost_class, gpu_required, max_runtime_minutes)
- risks list (may be empty per spec; spec only says "risk list")
- stop_condition ≥10 chars
- is_eligible returns (True, []) for the full proposal
- no forbidden network dependencies (requests, urllib, httpx, aiohttp)
- no untrusted-code imports (subprocess, os.system, eval(, exec()

Each test runs `arena research-proxy tabular_binary_v1 --provider
stub_claude` against the live fixture workspace and reads
fusion_proposal.json from the experiment worktree, so this is full
end-to-end coverage rather than unit-level — the same test would
catch a regression in stub_claude or the CLI's orchestration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## PR5 acceptance recap

After Task 6, the following must all be true on a clean clone:

```bash
pip install '.[dev]'
pytest --cov=arena -q
ruff check . && ruff format --check .
mypy arena
python scripts/validate_schemas.py
python scripts/validate_prompt_delimiters.py
python scripts/fixture_smoke.py
python scripts/static_sandbox_policy_check.py
python scripts/validate_memory_examples.py
python scripts/check_migrations.py

# Full E2E still works (no PR5 regression on the calibration happy path):
arena init-fixture tabular_binary_v1
arena plan tabular_binary_v1
arena run-next tabular_binary_v1 --provider stub_codex   # exits 0
arena evaluate tabular_binary_v1 --latest                # score=0.500000

# New PR5 path:
arena init-fixture tabular_binary_v1
arena research-proxy tabular_binary_v1 --provider stub_claude   # exits 0
arena replay <run_id>                                            # shows the chain

# Acceptance criteria (eligibility checklist + happy path):
pytest tests/test_research_proxy_eligibility.py tests/test_cli_research_proxy.py -v
```

PR5 acceptance is met when:

1. `arena research-proxy tabular_binary_v1 --provider stub_claude` runs steps 1 → 8 against `fixtures/tabular_binary_v1/paper_bundle/method_note_001.md`, producing all 4 artifacts (research_question.json, paper_digest.json, fusion_proposal.json, submission.csv) in separate per-step worktrees.
2. All fusion proposals satisfy the §6.3 eligibility checklist (verified by `tests/test_research_proxy_eligibility.py`).
3. The scoreboard records FOUR experiment rows (experiment_type = research_proxy_question / research_proxy_digest / research_proxy_fusion / research_proxy_implementation), all `status="completed"`, with `<fusion_id:fusion_NNNN>` in rows 3-4.
4. The deterministic gate at step 6 halts the chain when `score < MIN_FUSION_SCORE` OR `is_eligible` returns False — rows 1-3 are completed, NO row 4 is inserted (stub_codex was never invoked, so provider_calls stays at 3), exit code 2, no submission.csv written.
5. `arena replay <run_id>` reconstructs the full 4-step chain from the trace store, with 4 distinct task_ids producing 4 task summaries.
6. PR1 + PR2 + PR3 + PR4 e2e tests still pass — the new role/phase dispatch is additive.

This unblocks PR6 (Reviews + Memory + Self-Improvement Freeze). PR6 will:
- Add `arena/memory/` and `arena/self_improvement/`
- Extend stub_claude with role="review" + phase="FUSION_PROXY_REVIEWED" → emit research_review.json
- Add `arena review` / `arena memory propose` / `arena self-improve scan` CLI commands
- Replace `scripts/validate_memory_examples.py` with a proper test suite

---

## Self-review

**Spec coverage** (against `docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md` §9 PR5 + `docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md` §6.2 + §6.3):

| Spec item | Task |
|---|---|
| `arena/research_proxy/question_generator.py` | Task 2 |
| `arena/research_proxy/method_digest.py` | Task 2 |
| `arena/research_proxy/fusion_proposal.py` | Task 3 |
| `arena/research_proxy/fusion_scorer.py` | Task 3 |
| Stub Claude extension | Task 1 |
| Stub Codex extension (proxy implementation + fusion_id link) | Task 4 |
| CLI `arena research-proxy` | Task 5 |
| §6.2 step 1 (controller writes research question task) | Task 5 (CLI builds the packet via Task 2's helper) |
| §6.2 step 2 (Claude proposes 3-5 questions) | Task 1 (stub emits 1; PR7 real Claude does multiples) |
| §6.2 step 3 (controller validates schema and filters) | Task 5 (CLI calls validate_schema after stub_claude returns) |
| §6.2 step 4 (Claude digests one method note) | Task 1 + Task 2 packet builder |
| §6.2 step 5 (Claude proposes one method fusion) | Task 1 + Task 3 packet builder |
| §6.2 step 6 (controller scores deterministically) | Task 3 fusion_scorer + Task 5 gate |
| §6.2 step 7 (Codex implements smallest proxy test) | Task 4 + Task 5 packet construction |
| §6.2 step 8 (controller evaluates the proxy) | Task 5 (uses existing evaluate_fixture_submission) |
| §6.3 eligibility checklist | Task 3 is_eligible + Task 6 acceptance tests |
| Acceptance: produces valid digest, fusion proposal, proxy implementation | Task 5 + Task 6 |
| Acceptance: scoreboard records proxy linked to fusion_id | Task 4 + Task 5 |
| Acceptance: PR5 does NOT close the loop with review/memory | by omission — none of the seven tasks touch arena/memory/ or research_review.schema.json |
| Tests in same commit as code | Every task |
| Coverage gate at 50% during PR5 | unchanged from PR0; verified each task |

No gaps.

**Placeholder scan:** No TBD / TODO / "implement later" / "similar to" / "add error handling" placeholders. Every step has actual code or an exact command with expected output.

**Type consistency:**

- `generate_research_question(*, competition_slug, question_id, source_refs)` — keyword-only signature consistent across question_generator and the CLI's call site (Task 5 passes `source_refs=[method_note_path]`).
- `make_research_question_packet(*, competition_slug, run_id, experiment_id, task_id, question_id, source_refs)` — same pattern; CLI uses identical kwargs.
- `make_method_digest_packet(*, competition_slug, run_id, experiment_id, task_id, digest_id, method_note_path)` — keyword-only; CLI passes `task_id=task_id`, `digest_id="pd_0001"`, `method_note_path="fixtures/.../method_note_001.md"`.
- `make_fusion_proposal_packet(*, competition_slug, run_id, experiment_id, task_id, fusion_id, digest_path)` — keyword-only; CLI passes `task_id=task_id`, `digest_path=digest_artifact` (the path returned by stub_claude).
- `score_fusion_proposal(proposal) -> FusionScore` — single positional; FusionScore has `.score`, `.risk`, `.cost`, `.fit` fields used in CLI's console message and gate comparison.
- `is_eligible(proposal) -> tuple[bool, list[str]]` — used identically in fusion_scorer tests, CLI gate, and Task 6 eligibility tests.
- `MIN_FUSION_SCORE = 0.4` — module-level constant in fusion_scorer.py; CLI imports it; Task 5's monkeypatch test rebinds both `arena.research_proxy.fusion_scorer.MIN_FUSION_SCORE` AND `arena.cli.MIN_FUSION_SCORE` (because the CLI imports the value, not the module — careful here).
- `FUSION_ID_TAG_PREFIX = "fusion_id"` — module-level constant in cli.py; matches the literal string stub_codex emits in `<fusion_id:{fusion_id}>` artifact-path tokens.
- Task packet schema fields used: `task_id`, `competition_slug`, `experiment_id`, `provider`, `role`, `phase`, `objective`, `inputs`, `allowed_paths`, `blocked_paths`, `budgets`, `required_outputs`, `success_criteria` — consistent across all four packet builders.

**Notable design choices made during planning (not in the spec):**

1. **`arena research-proxy` is ONE command, not four queue dequeues.** The spec says "runs steps 1 → 8 against method_note_001.md" — one command. Four sub-invocations through `wrap_invoke` give us the same observability + sandbox benefits without forcing the operator to dequeue four times. PR7's real-provider runs may want to break this apart for resumability, but PR5 keeps it monolithic.
2. **`fusion_scorer` is a pure function gate, not a provider invocation.** §6.2 step 6 is "Controller scores the fusion deterministically" — that's a controller action, not a Claude/Codex action. The score is computed from the FusionProposal payload alone; no LLM call.
3. **Stub providers dispatch on `(role, phase)`** rather than only on role. This is necessary because role="research_proxy" alone doesn't tell the stub which artifact to emit (research_question vs digest vs fusion_proposal). The `(role, phase)` tuple is the natural key.
4. **`<fusion_id:fusion_NNNN>` token appears in artifact_paths starting at row 3 (fusion proposal) and row 4 (implementation).** Rows 1-2 (question, digest) are written before fusion_id is known — it's only extracted from the fusion_proposal.json payload after step 5 returns. This mirrors PR4's `<PROVIDER_VERSION_CHANGED:from=...>` pattern — metadata in artifact_paths, no schema migration. Future PR (probably PR6 or PR7) might add a `fusion_id` column to the experiments table; until then the token is the link.
5. **Each provider invocation produces its own experiment row** with its own experiment_id (via `get_next_experiment_id`) and task_id (matching suffix). usage_proxy from each ProviderResult is persisted on its own row, so `arena budget status` and pre-invoke caps see all four calls correctly. This is the "per-row" pattern consistent with calibration's one-row-per-run-next.
6. **Gate failure persists NO row.** Earlier draft inserted a synthetic `<blocked:FusionGateBlocked>` row tagged as the implementation step, but that inflates `COUNT(*)`-derived `provider_calls` for a step that never invoked any provider. The 3 successful rows (question, digest, fusion) already in scoreboard tell the operator exactly how far the chain got, and the fusion row's artifact_paths carries the fusion_proposal JSON path so the gate decision is reproducible from that payload via `arena replay` or `score_fusion_proposal`. Mirrors `arena run-next`'s pre-invoke pattern.
7. **`get_next_experiment_id` is called once per provider invocation.** `_latest_run_id()` is called instead of `_new_run_id()`, reusing the existing run created by `arena init-fixture`. Each of the 4 invocations calls `store.get_next_experiment_id(competition_slug)` to mint a collision-free experiment_id (exp_0002 through exp_0005 after calibration's exp_0001). If no run exists yet the CLI fails fast with a clear error message.
11. **Persisting four rows (one per provider invocation) means COUNT(*) — the basis for `provider_calls` in `get_run_usage_totals` — accurately counts research-proxy's four sub-invocations.** Other approaches (one row + new column, or reading from traces) would have required either a schema migration or coupling the budget governor to the trace store. Per-row is consistent with calibration's one-row-per-run-next pattern.
12. **BudgetGovernor is seeded from `store.get_run_usage_totals(slug, run_id)`** before the first `check_can_invoke` call. This mirrors `arena run-next`'s approach (PR2) and ensures calibration's provider call is visible to research-proxy's pre-invoke cap check. Without seeding, research-proxy would allow one extra call beyond the total cap on the first invocation after calibration.
8. **`MIN_FUSION_SCORE = 0.4` is the gate.** The stub fusion has score ~0.7 (cost=0.8, risk=1.0, fit=0.67), well above 0.4. A random low-cost low-risk proposal might still pass; a real Claude proposal with cost_class=large + 5+ risks would be ~0.27 and gate. The exact threshold is tunable in PR7 once we have data.
9. **Method notes are read by stub_claude implicitly.** The packet's `inputs` field carries the method-note path so the sandbox sees it as readable, but the stub doesn't actually parse the file — its payload is hardcoded. PR7's real Claude will read inputs[0] and digest it. The current behavior is observable (the path appears in the trace's task_started event payload) but not enforced.
10. **No `arena/research_proxy/runner.py` orchestration module.** The orchestration logic lives in the CLI command. This is the simplest approach for PR5; if PR6 or PR7 adds a non-CLI caller (e.g., autonomous loop scheduler), the orchestration could be extracted.
13. **Pre-invoke breakers (KillSwitchActive, ProviderCallBreaker tripped in `check_can_invoke`, fusion gate halt) do NOT persist scoreboard rows — no provider invocation occurred, so `COUNT(*)`-derived `provider_calls` stays accurate. Only post-invoke failures (BudgetExceeded with `usage_proxy` from `record_post_invoke`, SandboxViolation inside `wrap_invoke`) persist a blocked row.** The distinction is enforced by an `in_flight["invocation_started"]` flag set to True in `_guarded_invoke` only after `check_can_invoke` succeeds; outer except handlers gate `_persist_inflight_blocked` on that flag. The post-invoke BudgetExceeded path threads `exc.usage_proxy` through to the row so consumed usage is durable for the next run's seeded accumulators (closes the PR2 bug class for the research-proxy chain). Mirrors the `arena run-next` pattern in `arena/cli.py:185-377`.
