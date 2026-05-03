# PR6 (Reviews + Memory + Self-Improvement Freeze) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land §6.2 steps 9–10 (review + memory proposal) plus the self-improvement scan + freeze gate as three composable standalone CLI commands, preserving PR5's `COUNT(*) == provider_calls` invariant.

**Architecture:** Three new control-plane subsystems under `arena/review/`, `arena/memory/`, and `arena/self_improvement/`. Only `arena review` invokes a provider (stub_claude with new `(role="review", phase="FUSION_PROXY_REVIEWED")` dispatch) and creates a scoreboard row; `arena memory propose` and `arena self-improve scan` are deterministic-controller actions that emit durable artifacts on disk plus trace events but never persist rows. Trace-event payloads are constrained to the keys `schemas/event.schema.json` enumerates (the schema sets `additionalProperties: false`, so any key outside `{message, phase, proposal_id, memory_update_id, experiment_id, review_id, path, paths, status, reason, evidence, ...}` will reject at `TraceStore.emit` validation).

**Tech Stack:** Python 3.12 stdlib only (json, hashlib, dataclasses, pathlib, difflib); `jsonschema>=4.22` for schema validation (already a dep); `typer` for CLI subcommands (already a dep). No new third-party libraries.

---

## Preconditions

- PR1, PR2, PR3, PR4, PR5 are merged to `main`. Branch `pr6-reviews-memory-si-freeze` exists from fresh `main`.
- Python venv at `.venv/` with Python 3.12 and dev deps installed.
- Coverage gate is `fail_under = 50` (PR0 set it; PR7 restores 70).
- Baseline: 294 tests pass, ruff/format/mypy clean, plus the external acceptance scripts (`validate_schemas.py`, `validate_prompt_delimiters.py`, `fixture_smoke.py`, `static_sandbox_policy_check.py`, `validate_memory_examples.py`, `check_migrations.py`) all green. Task 7 of this plan replaces `validate_memory_examples.py` with a proper test suite; the final acceptance-scripts list shrinks to 5.

## Forward-compat hooks already in place from PR0–PR5

- All 4 schemas exist: `schemas/{review,research_review,memory_update,self_improvement_proposal}.schema.json`. Loader at `arena/schemas/loader.py` resolves them by name; `arena/schemas/validate.py:validate(name, instance)` uses a cached `Draft202012Validator` with format-checker.
- Phase enum has `FUSION_PROXY_REVIEWED`, `MEMORY_PROPOSAL_CREATED`, `SELF_IMPROVEMENT_SCAN_COMPLETED`, `BLOCKED_REPRODUCIBILITY` (`arena/controller/state.py`).
- Event-type enum (`schemas/event.schema.json:25-46`) already includes `review_recorded`, `memory_proposal_created`, `self_improvement_scan_completed`.
- `task_packet.role` enum already includes `review`.
- `arena/cli.py` constants: `RUNS_ROOT`, `WORKTREE_ROOT`, `FIXTURES_ROOT`, `TRACES_ROOT`, `PROVIDER_VERSION_CHANGED_TAG`, `FUSION_ID_TAG_PREFIX`. All reused by PR6.
- Module-level helpers in `arena/cli.py`: `_store()`, `_latest_run_id()`, `_get_provider()`, `_persist_inflight_blocked()`, `_require_artifact()`. All reused by PR6.
- `StubClaudeProvider._research_proxy_payload(slug, phase)` dispatch table at `arena/providers/stub_claude.py:107-119`. Task 1 extends it with one more case.
- `arena run-next` (`arena/cli.py:185-377`) and `arena research-proxy` (`arena/cli.py:620+`) are the canonical templates for the precheck → guarded-invoke → persist pattern. Task 2's `arena review` mirrors them exactly.
- Memory wiki at `docs/memory/UNIFIED_MEMORY_WIKI.md` (read-only target for `arena/memory/diff.py`).
- `memory/proposals/` and `self_improvement/proposals/` directories do NOT exist yet — first run of `arena memory propose` / `arena self-improve scan` creates them via `Path.mkdir(parents=True, exist_ok=True)`.

## File structure

**Create (new modules):**

| Path | Responsibility |
|---|---|
| `arena/review/__init__.py` | Bare marker (just `from __future__ import annotations`). |
| `arena/review/packet.py` | `make_review_packet(*, competition_slug, run_id, experiment_id, task_id, review_id, subject_experiment_id, fusion_proposal_path, submission_path)` — task_packet builder with role="review", phase="FUSION_PROXY_REVIEWED". `validate_research_review(payload)` thin wrapper. |
| `arena/memory/__init__.py` | Bare marker. |
| `arena/memory/proposal.py` | `synthesize_memory_proposal(review_payload, *, proposal_id, namespace="research")`. `get_next_proposal_id(proposals_dir)`. `validate_memory_update(payload)` thin wrapper. |
| `arena/memory/validator.py` | `check_evidence(proposal) -> list[str]` — semantic checks beyond schema (operation-specific prior_claim requirements; contradiction detection: claim != prior_claim on modify/deprecate/remove). |
| `arena/memory/diff.py` | `render_diff(proposal, wiki_path) -> str` — pure function; produces a unified-diff-style string scoped by namespace. Read-only. |
| `arena/self_improvement/__init__.py` | Bare marker. |
| `arena/self_improvement/scan.py` | `Finding` frozen dataclass; `scan_runs(slug, *, store, runs_root, baselines_root)` walks scoreboard + traces + baselines. |
| `arena/self_improvement/proposal.py` | `make_self_improvement_proposal(finding, *, proposal_id)`. `get_next_sip_id(proposals_dir)`. `validate_self_improvement_proposal(payload)` thin wrapper. |
| `arena/self_improvement/freeze.py` | `Metrics` + `ComparisonResult` + `FreezeDecision` dataclasses; `evaluate_freeze(findings)`; `apply_freeze(decision, sentinel_path)`; `is_frozen(sentinel_path)`. |
| `arena/self_improvement/champion_challenger.py` | `compare_metrics(champion: Metrics, challenger: Metrics) -> ComparisonResult` — pure helper, library-only. |

**Create (tests):**

| Path | Tests |
|---|---|
| `tests/test_stub_claude_review.py` | 5 tests: emits valid research_review.json on FUSION_PROXY_REVIEWED; subject_id from inputs[0]; default decision=accept + risk=low; calibration backward-compat; monkeypatch override. |
| `tests/test_research_review_packet.py` | 3 tests: builder shape; schema validation; review_id pattern. |
| `tests/test_cli_review.py` | 7 tests: happy path; missing impl experiment; impl row missing fusion_id token; PR4 fixture-digest drift blocks; provider-version drift tags row; pre-invoke kill switch (no row); post-invoke BudgetExceeded persists row WITH usage_proxy. |
| `tests/test_memory_proposal.py` | 6 tests: synthesizes valid memory_update for actionable review; no-op observation for empty review; deterministic proposal_id minting; namespace="research"; review_status="proposed"; evidence array points to review_id. |
| `tests/test_memory_validator.py` | 4 tests: contradiction detection on modify; operation-specific prior_claim; valid proposals pass; rejects empty evidence. |
| `tests/test_memory_diff.py` | 3 tests: renders diff against wiki; namespace-scoped output; pure function (no file mutation). |
| `tests/test_cli_memory_propose.py` | 7 tests: happy path; no-op fallback; missing review experiment; deterministic proposal_id + no scoreboard row; schema-invalid research_review.json caught cleanly; trace event lands under the review row's run (cross-run linkage regression). |
| `tests/test_self_improvement_scan.py` | 6 tests: detects blocked rows; detects score regression; detects waste threshold; idempotent (no duplicate proposals); proposal IDs monotonic; clean scoreboard produces zero findings. |
| `tests/test_self_improvement_freeze.py` | 5 tests: each §7.3 trigger fires; apply_freeze writes sentinel; sentinel JSON metadata block parses; is_frozen reads sentinel; unfreeze deletes sentinel. |
| `tests/test_self_improvement_champion_challenger.py` | 3 tests: returns ComparisonResult; flags regression; pure function. |
| `tests/test_cli_self_improve_scan.py` | 5 tests: happy path (no findings); finding triggers freeze + sentinel; trace event emitted with allowed payload keys; no scoreboard row; idempotent across runs. |
| `tests/test_memory_proposal_examples.py` | 6 tests: each `operation` enum path (add/modify/deprecate/remove) + prior_claim conditional + contradiction detection. **Replaces** `scripts/validate_memory_examples.py`. |

**Modify:**

| Path | Change |
|---|---|
| `arena/providers/stub_claude.py` | Extend `_research_proxy_payload` to dispatch on role="review" + phase="FUSION_PROXY_REVIEWED" → `_research_review_payload(slug, subject_id)`. Add module-level `_RESEARCH_REVIEW_DEFAULT_DECISION = "accept"` etc. for monkey-patch override. |
| `arena/cli.py` | Add three subcommands: `review`, `memory propose`, `self-improve scan`. Reuse existing helpers (`_store`, `_latest_run_id`, `_get_provider`, `_persist_inflight_blocked`, `_require_artifact`). |
| `README.md` (and any other root-level `*.md` that contains the phrase) | If "6 CI scripts" / "all 6 CI scripts" appears in any root-level live doc, replace with "5 external acceptance scripts + memory-examples test suite" or list them explicitly. This repo has `README.md` at root and NO `CLAUDE.md`; if `README.md` contains no such phrase, no doc edit is needed. Historical plan files under `docs/superpowers/plans/` are write-once and MUST NOT be edited. Done in Task 7. |

**Delete:**

| Path | Reason |
|---|---|
| `scripts/validate_memory_examples.py` | Replaced by `tests/test_memory_proposal_examples.py`. Done in Task 7. |

---

## Workflow note

**PR5 invariants this plan preserves:**
- `experiments` row ⇔ provider invocation. `arena review` is a provider invocation (creates row); `arena memory propose` and `arena self-improve scan` are not (no row).
- `COUNT(*) == provider_calls` from `ScoreboardStore.get_run_usage_totals`.
- Pre-invoke failures don't insert rows; post-invoke `BudgetExceeded` persists a blocked row WITH `exc.usage_proxy` threaded through.
- All `_persist_row` calls use `experiment_type="research_proxy"` (the schema enum value); the step name lives in `artifact_paths` as a `<step:NAME>` token (FIRST element).
- PR4 reproducibility checks fire BEFORE any provider invocation (`compute_fixture_set_digest` + `record_provider_version`).

**The trace-event payload key set is the single load-bearing constraint introduced by PR6.** `schemas/event.schema.json`'s `payload` object sets `additionalProperties: false`. Every PR6 trace-event emission must use ONLY these keys: `message`, `provider`, `provider_version`, `status`, `phase`, `path`, `paths`, `command`, `exit_code`, `breaker`, `evidence`, `score`, `metric_name`, `reason`, `schema_name`, `review_id`, `memory_update_id`, `proposal_id`, `experiment_id`, `url`, `network_domain`, `previous_hash`, `new_hash`, `sha256`, `usage_proxy`. Any new key requires schema migration (out of PR6 scope).

---

## Coordination note

PR6 owns `arena/review/`, `arena/memory/`, `arena/self_improvement/`, plus targeted extensions to `arena/cli.py` and `arena/providers/stub_claude.py`. No overlap with PR5's research-proxy lane. PR7 (Real Codex/Claude + close-the-loop) gets its own plan after PR6 lands.

---

## Task 1: Stub Claude review dispatch

**Files:**
- Modify: `arena/providers/stub_claude.py`
- Create: `tests/test_stub_claude_review.py`

- [ ] **Step 1: Write the failing stub-claude-review tests**

Create `tests/test_stub_claude_review.py`:

```python
# tests/test_stub_claude_review.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _review_packet(
    *,
    workspace_root: Path,
    competition_slug: str = "tabular_binary_v1",
    experiment_id: str = "exp_0006",
    task_id: str = "task_0006",
    subject_experiment_id: str = "exp_0004",
) -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "review",
        "phase": "FUSION_PROXY_REVIEWED",
        "objective": (
            f"Review proxy implementation {subject_experiment_id} against "
            "the originating fusion_proposal.json. Output must satisfy "
            "schemas/research_review.schema.json."
        ),
        "inputs": [
            f"worktrees/{competition_slug}/{subject_experiment_id}/submission.csv",
            f"worktrees/{competition_slug}/exp_0003/fusion_proposal.json",
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
            "max_wall_minutes": 5,
            "max_shell_commands": 5,
            "max_failed_commands": 2,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["research_review.json"],
        "success_criteria": ["valid_schema"],
    }


def test_stub_claude_emits_research_review_json(tmp_path: Path) -> None:
    """phase=FUSION_PROXY_REVIEWED → research_review.json artifact."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    assert result.status == "success"
    artifact_paths = [Path(p) for p in result.artifacts]
    rr_path = next(p for p in artifact_paths if p.name == "research_review.json")
    assert rr_path.exists()
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    validate("research_review", payload)


def test_stub_claude_review_extracts_subject_id_from_inputs(tmp_path: Path) -> None:
    """subject_id is parsed from inputs[0]'s worktree path segment.

    Mirrors stub_codex._read_fusion_id_from_inputs: the stub does not
    invent identity — it reads it from the packet the CLI hands it.
    """
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(
        workspace_root=tmp_path,
        subject_experiment_id="exp_0004",
    )
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["subject_id"] == "exp_0004"


def test_stub_claude_review_default_decision_is_accept(tmp_path: Path) -> None:
    """Default deterministic stub verdict is decision=accept, risk=low,
    required_fixes=[]. Tests that need other decisions monkey-patch the
    module-level _RESEARCH_REVIEW_DEFAULT_* constants."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "accept"
    assert payload["risk_level"] == "low"
    assert payload["required_fixes"] == []


def test_stub_claude_review_decision_can_be_overridden_via_module_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkey-patching the module-level default constant changes the
    stub's verdict — same pattern PR5 uses for MIN_FUSION_SCORE."""
    monkeypatch.setattr(
        "arena.providers.stub_claude._RESEARCH_REVIEW_DEFAULT_DECISION", "revise"
    )
    monkeypatch.setattr(
        "arena.providers.stub_claude._RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES",
        ["Add a baseline ablation comparing GBDT-only vs the full ensemble."],
    )
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = _review_packet(workspace_root=tmp_path)
    result = provider.invoke(packet)
    rr_path = next(Path(p) for p in result.artifacts if p.endswith("research_review.json"))
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "revise"
    assert len(payload["required_fixes"]) == 1


def test_stub_claude_calibration_path_unchanged(tmp_path: Path) -> None:
    """Backward compat with PR1: the calibration packet (role=
    implementation, phase=CALIBRATION_TASK_CREATED) still produces the
    empty-payload result."""
    provider = StubClaudeProvider(workspace_root=tmp_path)
    packet = {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Calibration baseline.",
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
    assert result.artifacts == []
```

- [ ] **Step 2: Run failing tests to confirm**

```bash
.venv/Scripts/python.exe -m pytest tests/test_stub_claude_review.py -v
```

Expected: 4 failures (the calibration backward-compat test passes; the 4 review tests fail because `_research_proxy_payload` doesn't yet handle phase="FUSION_PROXY_REVIEWED").

- [ ] **Step 3: Add module-level review constants and dispatch case**

Open `arena/providers/stub_claude.py`. Just after the `_VERSION = "stub_claude.v1"` line (line ~13), add:

```python
# PR6 stub review defaults — monkey-patchable by tests for rejection
# paths. Not exposed in the schema; just stub knobs.
_RESEARCH_REVIEW_DEFAULT_DECISION = "accept"
_RESEARCH_REVIEW_DEFAULT_RISK_LEVEL = "low"
_RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES: list[str] = []
```

In the `invoke` method's role dispatch (line ~81), extend the branch from `if role == "research_proxy":` to also cover `role == "review"`. Replace the existing block:

```python
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
```

with:

```python
        # PR5+PR6 dispatch: research_proxy + review roles emit a
        # schema-valid artifact under the workspace.
        artifacts: list[str] = []
        role = task_packet["role"]
        phase = task_packet["phase"]
        payload: dict[str, Any] | None = None
        artifact_name: str | None = None
        if role == "research_proxy":
            payload, artifact_name = self._research_proxy_payload(slug, phase)
        elif role == "review":
            payload, artifact_name = self._review_payload(
                slug, phase, task_packet["inputs"]
            )
        if payload is not None and artifact_name is not None:
            artifact_path = workspace / artifact_name
            artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            artifacts.append(str(artifact_path))
```

Then add two new methods at the end of the class (after `_fusion_proposal_payload`):

```python
    def _review_payload(
        self, slug: str, phase: str, inputs: list[str]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return (payload, artifact_name) for the review phase, or
        (None, None) if the phase doesn't require artifact emission.

        The CLI passes the implementation row's submission.csv path as
        inputs[0]; we extract subject_id from its worktree path segment
        (`worktrees/<slug>/<exp>/submission.csv` → `<exp>`) the same way
        stub_codex extracts fusion_id from inputs[0] in PR5 Task 4.
        """
        if phase != "FUSION_PROXY_REVIEWED":
            return None, None
        subject_id = self._read_subject_id_from_inputs(inputs)
        if subject_id is None:
            return None, None
        return self._research_review_payload(slug, subject_id), "research_review.json"

    def _read_subject_id_from_inputs(self, inputs: list[str]) -> str | None:
        """Find the first input whose path matches
        `worktrees/<slug>/<exp_id>/submission.csv` and return <exp_id>.
        Returns None if no matching input exists or the path shape is
        unexpected.
        """
        for input_path in inputs:
            if not input_path.endswith("submission.csv"):
                continue
            parts = Path(input_path).parts
            # Expect ("worktrees", slug, exp_id, "submission.csv")
            if len(parts) >= 4 and parts[-4] == "worktrees":
                exp_id = parts[-2]
                if exp_id.startswith("exp_"):
                    return exp_id
        return None

    def _research_review_payload(
        self, slug: str, subject_id: str
    ) -> dict[str, Any]:
        return {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": slug,
            "subject_id": subject_id,
            "decision": _RESEARCH_REVIEW_DEFAULT_DECISION,
            "summary": (
                f"Reviewed proxy implementation {subject_id} against the "
                "monotonic-GBDT + stacked-LR fusion. Submission.csv is "
                "schema-valid; pipeline integrity confirmed."
            ),
            "strengths": [
                "Submission shape matches sample_submission.csv columns.",
                "Proxy implementation completed within budget caps.",
            ],
            "weaknesses": [
                "Phase-0 stub produces a constant 0.5 baseline; "
                "no signal extracted from the fusion proposal.",
            ],
            "required_fixes": list(_RESEARCH_REVIEW_DEFAULT_REQUIRED_FIXES),
            "follow_up_recommendations": [
                "After PR7's real Codex lands, re-run with the same fusion "
                "and compare ROC-AUC against the calibration baseline.",
            ],
            "risk_level": _RESEARCH_REVIEW_DEFAULT_RISK_LEVEL,
        }
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_stub_claude_review.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 299 passed (was 294, +5). All checks clean.

- [ ] **Step 6: Commit**

```bash
git add arena/providers/stub_claude.py tests/test_stub_claude_review.py
git commit -m "$(cat <<'EOF'
feat(providers): stub_claude dispatches research_review.json on FUSION_PROXY_REVIEWED

Extends invoke()'s (role, phase) dispatch from PR5: role="review" +
phase="FUSION_PROXY_REVIEWED" emits a schema-valid research_review.json.

Subject_id is extracted from inputs[0] (the implementation row's
submission.csv path), mirroring how stub_codex extracts fusion_id from
inputs[0]. Default decision="accept", risk_level="low",
required_fixes=[]; tests monkey-patch the module-level
_RESEARCH_REVIEW_DEFAULT_* constants for the rejection paths.

Calibration path unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: arena review CLI + packet builder

**Files:**
- Create: `arena/review/__init__.py`
- Create: `arena/review/packet.py`
- Create: `tests/test_research_review_packet.py`
- Create: `tests/test_cli_review.py`
- Modify: `arena/cli.py`

- [ ] **Step 1: Write failing packet-builder tests**

Create `tests/test_research_review_packet.py`:

```python
# tests/test_research_review_packet.py
from __future__ import annotations

from arena.review.packet import make_review_packet, validate_research_review
from arena.schemas.validate import validate


def test_make_review_packet_is_schema_valid_task_packet() -> None:
    packet = make_review_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0006",
        task_id="task_0006",
        review_id="rr_0001",
        subject_experiment_id="exp_0004",
        fusion_proposal_path="worktrees/tabular_binary_v1/exp_0003/fusion_proposal.json",
        submission_path="worktrees/tabular_binary_v1/exp_0004/submission.csv",
    )
    validate("task_packet", packet)
    assert packet["role"] == "review"
    assert packet["phase"] == "FUSION_PROXY_REVIEWED"
    # Submission path FIRST so stub_claude's _read_subject_id_from_inputs
    # picks it up (stub matches inputs[0]).
    assert packet["inputs"][0].endswith("submission.csv")
    assert any("fusion_proposal.json" in p for p in packet["inputs"])


def test_validate_research_review_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "research_review.v1",
        "review_id": "rr_0001",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept",
        "summary": "A 10+ char summary string.",
        "strengths": ["s1"],
        "weaknesses": ["w1"],
        "required_fixes": [],
        "follow_up_recommendations": ["f1"],
        "risk_level": "low",
    }
    validate_research_review(payload)  # no raise


def test_review_id_pattern_enforced_by_schema() -> None:
    """research_review schema enforces review_id ^rr_[0-9]{4,}$."""
    from jsonschema import ValidationError

    import pytest

    bad = {
        "schema_version": "research_review.v1",
        "review_id": "not_an_rr_id",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept",
        "summary": "A 10+ char summary string.",
        "strengths": [],
        "weaknesses": [],
        "required_fixes": [],
        "follow_up_recommendations": [],
        "risk_level": "low",
    }
    with pytest.raises(ValidationError):
        validate_research_review(bad)
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_research_review_packet.py -v
```

Expected: 3 ImportError on `arena.review.packet`.

- [ ] **Step 3: Implement packet builder**

Create `arena/review/__init__.py`:

```python
from __future__ import annotations
```

Create `arena/review/packet.py`:

```python
# arena/review/packet.py
from __future__ import annotations

from typing import Any

from arena.schemas.validate import validate


def make_review_packet(
    *,
    competition_slug: str,
    run_id: str,
    experiment_id: str,
    task_id: str,
    review_id: str,
    subject_experiment_id: str,
    fusion_proposal_path: str,
    submission_path: str,
) -> dict[str, Any]:
    """Build the task_packet that asks stub_claude to emit a
    research_review.json reviewing the implementation row identified by
    `subject_experiment_id`.

    `submission_path` is placed at inputs[0] so the stub can extract
    subject_id via _read_subject_id_from_inputs (parses the worktree
    path segment). `fusion_proposal_path` is included so the reviewer
    can reference the originating proposal.

    `run_id` is accepted for forward-compat with Task 5's CLI
    orchestration (the review packet is consumed by the same
    arena run-next-style precheck flow). The packet schema does not
    have a run_id field; it lives at the run record level.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": "stub_claude",
        "role": "review",
        "phase": "FUSION_PROXY_REVIEWED",
        "objective": (
            f"Review proxy implementation {subject_experiment_id} against "
            f"fusion {fusion_proposal_path}. Output must satisfy "
            "schemas/research_review.schema.json."
        ),
        "inputs": [submission_path, fusion_proposal_path],
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
        "required_outputs": ["research_review.json"],
        "success_criteria": ["valid_schema"],
    }


def validate_research_review(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/research_review.schema.json.
    Thin wrapper over arena.schemas.validate.validate."""
    validate("research_review", payload)
```

- [ ] **Step 4: Run tests; confirm 3 passed**

```bash
.venv/Scripts/python.exe -m pytest tests/test_research_review_packet.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Write the failing CLI tests**

Create `tests/test_cli_review.py`:

```python
# tests/test_cli_review.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _run_research_proxy_first(runner: CliRunner) -> None:
    """Bootstrap a research-proxy run so we have an implementation row
    to review."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 0, result.output


def test_arena_review_happy_path(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arena review against a research-proxy impl row succeeds, persists
    a row with <step:review> token, emits valid research_review.json."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    # The impl row from research-proxy is exp_0004.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output

    # New review experiment row exists at exp_0005.
    rev_workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0005"
    assert (rev_workspace / "research_review.json").exists()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_id, experiment_type, status, artifact_paths "
            "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            ("tabular_binary_v1",),
        ).fetchall()
        # 4 research-proxy rows + 1 review row.
        assert len(rows) == 5
        rev_row = rows[-1]
        assert rev_row["experiment_id"] == "exp_0005"
        assert rev_row["experiment_type"] == "research_proxy"
        assert rev_row["status"] == "completed"
        paths = json.loads(rev_row["artifact_paths"])
        assert paths[0] == "<step:review>"
        assert any(p.endswith("research_review.json") for p in paths)
    finally:
        store.close()


def test_arena_review_missing_impl_experiment(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--experiment <exp_id> must exist in the scoreboard."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_9999",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "exp_9999" in result.output


def test_arena_review_impl_row_missing_fusion_id_token(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewing a non-research-proxy row (e.g., calibration) must fail
    cleanly: calibration rows lack the <fusion_id:...> token."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(
        app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"]
    )
    # exp_0001 is the calibration row — no fusion_id token.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0001",
        ],
    )
    assert result.exit_code != 0
    assert "fusion" in result.output.lower()


def test_arena_review_blocks_on_fixture_digest_drift(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR4 reproducibility: arena review runs the same precheck as
    arena research-proxy. Mutating train.csv after the baseline is
    recorded must halt with BLOCKED_REPRODUCIBILITY before any
    provider invocation."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    train = fixture_workspace / "fixtures" / "tabular_binary_v1" / "train.csv"
    train.write_text("id,x1,x2,target\n0,0,0,0\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2
    assert "fixture digest drift" in result.output.lower()


def test_arena_review_tags_provider_version_drift(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When stub_claude's version changes after the baseline is
    recorded, the review row carries <PROVIDER_VERSION_CHANGED:from=...>
    in its artifact_paths."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    monkeypatch.setattr("arena.providers.stub_claude._VERSION", "stub_claude.v2")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        row = store._require_conn().execute(
            "SELECT artifact_paths FROM experiments WHERE experiment_id = ?",
            ("exp_0005",),
        ).fetchone()
        paths = json.loads(row["artifact_paths"])
        assert any(
            p.startswith("<PROVIDER_VERSION_CHANGED:from=stub_claude.v1>")
            for p in paths
        ), paths
    finally:
        store.close()


def test_arena_review_blocks_on_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARENA_KILL_SWITCH halts arena review at check_can_invoke; no
    scoreboard row inserted."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)
    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2
    assert "kill switch" in result.output.lower()
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = store._require_conn().execute(
            "SELECT experiment_id FROM experiments WHERE competition_slug = ?",
            ("tabular_binary_v1",),
        ).fetchall()
        # Only the 4 research-proxy rows; no review row inserted.
        assert len(rows) == 4
    finally:
        store.close()


def test_arena_review_attaches_to_impl_rows_run_not_latest(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cross-run linkage: stub fusion_id is deterministic
    (fusion_0001), so a second `arena init-fixture` + new research-proxy
    creates a new run with the SAME fusion_id. `arena review --experiment
    <impl from first run>` must attach to the FIRST run, not the latest,
    AND must locate the fusion row from the FIRST run."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)
    # Capture the first run's id from exp_0004.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        first_run = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE experiment_id = ?",
            ("exp_0004",),
        ).fetchone()["run_id"]
    finally:
        store.close()

    # Start a second run; produces exp_0005..exp_0008 with a different run_id.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )

    # Review the FIRST run's impl row. The new review row should be
    # attached to first_run, not the latest run.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rev_row = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE experiment_id = ?",
            ("exp_0009",),
        ).fetchone()
        assert rev_row is not None
        assert rev_row["run_id"] == first_run
    finally:
        store.close()


def test_arena_review_persists_post_invoke_budget_blocked_row_with_usage(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-invoke BudgetExceeded (run-level output_chars cap=1) must
    persist a blocked row WITH usage_proxy threaded through. Mirrors
    the equivalent research-proxy regression."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        row = store._require_conn().execute(
            "SELECT status, output_chars, artifact_paths "
            "FROM experiments WHERE experiment_id = ?",
            ("exp_0005",),
        ).fetchone()
        assert row is not None
        assert row["status"] == "blocked"
        assert row["output_chars"] > 0
        paths = json.loads(row["artifact_paths"])
        assert paths[0] == "<step:review>"
    finally:
        store.close()
```

- [ ] **Step 6: Run failing CLI tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review.py -v
```

Expected: 8 failures — `arena review` subcommand doesn't exist yet.

- [ ] **Step 7: Add the `review` subcommand to `arena/cli.py`**

In `arena/cli.py`, add this import near the existing research-proxy import block:

```python
from arena.review.packet import make_review_packet, validate_research_review
```

Then add the new subcommand at the end of the file, BEFORE the existing `_require_artifact` helper (i.e., place it after `research_proxy` and before the module-level `_require_artifact`):

```python
@app.command("review")
def review(
    competition_slug: str,
    provider: str = typer.Option(
        "stub_claude",
        "--provider",
        help="Provider to use for the review step. PR6 supports only stub_claude.",
    ),
    experiment: str = typer.Option(
        ...,
        "--experiment",
        help="experiment_id of the research-proxy implementation row to review.",
    ),
) -> None:
    """Run §6.2 step 9 (Claude review) against a previously-completed
    research-proxy implementation row.

    Resolves the impl row from the scoreboard, extracts its
    <fusion_id:...> token + submission.csv artifact, locates the
    originating fusion_proposal.json, and invokes stub_claude with
    role="review" + phase="FUSION_PROXY_REVIEWED" to emit a
    research_review.json artifact.

    Persists ONE scoreboard row (experiment_type="research_proxy",
    <step:review> token in artifact_paths). Mirrors arena run-next /
    arena research-proxy's pre-invoke vs post-invoke discipline:
    KillSwitchActive / pre-invoke ProviderCallBreaker / fixture-digest
    drift = no row; post-invoke BudgetExceeded with usage_proxy =
    blocked row WITH consumed usage threaded through.
    """
    if provider not in {"stub_claude"}:
        raise typer.BadParameter(
            f"unknown review provider {provider!r}; PR6 supports only stub_claude"
        )

    store = _store()

    # Resolve the impl row + its run_id FIRST. The review row must be
    # attached to the SAME run as the impl row (not _latest_run_id()),
    # otherwise a second `arena init-fixture` followed by `arena review
    # --experiment exp_0004` would attach the review to the new run
    # while reading impl artifacts from the old one. The fusion_token
    # is also deterministic (fusion_0001) across runs, so the fusion
    # lookup MUST also filter by run_id to avoid cross-run linkage.
    impl_row = store._require_conn().execute(
        "SELECT experiment_id, run_id, artifact_paths FROM experiments "
        "WHERE competition_slug = ? AND experiment_id = ?",
        (competition_slug, experiment),
    ).fetchone()
    if impl_row is None:
        raise typer.BadParameter(
            f"experiment {experiment} not found for {competition_slug}"
        )
    run_id = impl_row["run_id"]
    if not run_id:
        raise typer.BadParameter(
            f"experiment {experiment} has no run_id (corrupt scoreboard?)"
        )
    impl_paths: list[str] = json.loads(impl_row["artifact_paths"])

    fusion_token = next(
        (p for p in impl_paths if p.startswith(f"<{FUSION_ID_TAG_PREFIX}:")),
        None,
    )
    if fusion_token is None:
        raise typer.BadParameter(
            f"experiment {experiment} is not a research-proxy implementation "
            f"row (no <{FUSION_ID_TAG_PREFIX}:...> token in artifact_paths)"
        )
    fusion_id = fusion_token[len(f"<{FUSION_ID_TAG_PREFIX}:") : -1]

    submission_path = next(
        (p for p in impl_paths if p.endswith("submission.csv")),
        None,
    )
    if submission_path is None:
        raise typer.BadParameter(
            f"experiment {experiment} has no submission.csv in artifact_paths"
        )

    # Find the fusion row whose artifact_paths contains the same fusion_token
    # AND has the <step:fusion> marker AND lives in the SAME run as the
    # impl row. fusion_token is deterministic across runs (fusion_0001),
    # so without the run_id filter we could match a different run's row.
    fusion_row = store._require_conn().execute(
        "SELECT experiment_id, artifact_paths FROM experiments "
        "WHERE competition_slug = ? AND run_id = ? "
        "AND artifact_paths LIKE ? AND artifact_paths LIKE ?",
        (competition_slug, run_id, f"%{fusion_token}%", "%<step:fusion>%"),
    ).fetchone()
    if fusion_row is None:
        raise typer.BadParameter(
            f"could not locate originating fusion_proposal.json for "
            f"{fusion_id} (corrupt scoreboard?)"
        )
    fusion_paths: list[str] = json.loads(fusion_row["artifact_paths"])
    fusion_proposal_path = next(
        (p for p in fusion_paths if p.endswith("fusion_proposal.json")),
        None,
    )
    if fusion_proposal_path is None:
        raise typer.BadParameter(
            f"fusion row {fusion_row['experiment_id']} has no "
            "fusion_proposal.json in artifact_paths (corrupt scoreboard?)"
        )

    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)
    review_adapter = _get_provider(provider, event_emitter=trace_store)

    # PR4 reproducibility precheck — same shape as arena research-proxy.
    try:
        fixture_hash = compute_fixture_set_digest(FIXTURES_ROOT / competition_slug)
        _is_new_fixture, drifted_from_fixture = record_fixture_hash(
            competition_slug=competition_slug,
            fixture_hash=fixture_hash,
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        if isinstance(exc, FileNotFoundError):
            message = f"fixture manifest missing for {competition_slug}: {exc}"
        else:
            message = f"fixture state read failed for {competition_slug}: {exc}"
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    if drifted_from_fixture:
        message = (
            f"fixture digest drift for {competition_slug}: "
            f"was {drifted_from_fixture}, now {fixture_hash}"
        )
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2)

    trace_store.emit(
        event_type="run_started",
        severity="info",
        payload={
            "sha256": fixture_hash,
            "previous_hash": drifted_from_fixture or "",
            "phase": Phase.NEW.value,
        },
    )

    try:
        _is_new_review, drifted_from_review = record_provider_version(
            competition_slug=competition_slug,
            provider=review_adapter.name,
            version=review_adapter.version,
        )
    except json.JSONDecodeError as exc:
        message = f"provider version baseline corrupt for {competition_slug}: {exc}"
        console.print(
            f"[red]review blocked: {Phase.BLOCKED_REPRODUCIBILITY.value} ({message})[/red]"
        )
        raise typer.Exit(code=2) from exc

    trace_store.emit(
        event_type="provider_version_recorded",
        severity="warning" if drifted_from_review else "info",
        payload={
            "provider": review_adapter.name,
            "provider_version": review_adapter.version,
            "previous_hash": drifted_from_review or "",
        },
    )

    review_drift_tag = (
        f"<{PROVIDER_VERSION_CHANGED_TAG}:from={drifted_from_review}>"
        if drifted_from_review
        else None
    )
    review_drift_extras = [review_drift_tag] if review_drift_tag else []

    # Seed governor accumulators from prior usage on this run.
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

    rev_exp = store.get_next_experiment_id(competition_slug)
    rev_task = rev_exp.replace("exp_", "task_")
    create_workspace(WORKTREE_ROOT, competition_slug, rev_exp)

    rev_packet = make_review_packet(
        competition_slug=competition_slug,
        run_id=run_id,
        experiment_id=rev_exp,
        task_id=rev_task,
        review_id="rr_0001",
        subject_experiment_id=experiment,
        fusion_proposal_path=fusion_proposal_path,
        submission_path=submission_path,
    )

    in_flight: dict[str, str | bool | None] = {
        "experiment_id": rev_exp,
        "task_id": rev_task,
        "step": "review",
        "adapter_name": review_adapter.name,
        "adapter_version": review_adapter.version,
        "invocation_started": False,
    }

    def _persist_review_row(
        *,
        experiment_id: str,
        task_id: str,
        experiment_type: str,
        adapter_name: str,
        adapter_version: str,
        status: str,
        artifact_paths: list[str],
        usage_proxy: UsageProxy | None,
        score: float | None = None,
        valid_submission: bool | None = None,
    ) -> None:
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

    try:
        per_step_sandbox = SandboxRunner(
            SandboxPolicy.from_packet(rev_packet, workspace_root=Path.cwd())
        )
        watchdog.check_can_invoke(review_adapter.name)
        in_flight["invocation_started"] = True
        rev_result = watchdog.wrap_invoke(
            review_adapter,
            rev_packet,
            sandbox=per_step_sandbox,
            event_emitter=trace_store,
        )
        rev_artifact = _require_artifact(
            rev_result.artifacts,
            suffix="research_review.json",
            step_label="review",
            provider_name=review_adapter.name,
        )
        rev_payload = json.loads(Path(rev_artifact).read_text(encoding="utf-8"))
        validate_research_review(rev_payload)

        _persist_review_row(
            experiment_id=rev_exp,
            task_id=rev_task,
            experiment_type="research_proxy",
            adapter_name=review_adapter.name,
            adapter_version=review_adapter.version,
            status="completed",
            artifact_paths=["<step:review>", rev_artifact, *review_drift_extras],
            usage_proxy=rev_result.usage_proxy,
        )
        trace_store.emit(
            event_type="review_recorded",
            severity="info",
            task_id=rev_task,
            payload={
                "review_id": rev_payload["review_id"],
                "experiment_id": rev_exp,
                "status": rev_payload["decision"],
                "path": rev_artifact,
            },
        )
        console.print(
            f"[bold green]review complete[/bold green] — review_id="
            f"{rev_payload['review_id']} decision={rev_payload['decision']}"
        )
    except KillSwitchActive as exc:
        console.print(f"[red]kill switch active: {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except BudgetExceeded as exc:
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_review_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=exc.usage_proxy,
            )
        console.print(f"[red]budget exceeded ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc
    except SandboxViolation as exc:
        if in_flight["invocation_started"]:
            _persist_inflight_blocked(
                _persist_review_row,
                in_flight,
                exc.breaker.value,
                str(exc),
                usage_proxy=None,
            )
        console.print(f"[red]sandbox violation ({exc.breaker.value}): {exc}[/red]")
        raise typer.Exit(code=2) from exc
```

- [ ] **Step 8: Run CLI tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_review.py -v
```

Expected: 8 passed.

- [ ] **Step 9: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 310 passed (was 299, +11 = 3 packet builder + 8 CLI tests including the cross-run-linkage regression). All checks clean.

- [ ] **Step 10: Commit**

```bash
git add arena/review/__init__.py arena/review/packet.py \
        tests/test_research_review_packet.py tests/test_cli_review.py \
        arena/cli.py
git commit -m "$(cat <<'EOF'
feat(cli,review): arena review subcommand for §6.2 step 9

New `arena review <slug> --provider stub_claude --experiment <impl_exp>`
subcommand. Resolves the implementation row from the scoreboard,
extracts the <fusion_id:...> token + submission.csv path + originating
fusion_proposal.json, and invokes stub_claude with role="review" +
phase="FUSION_PROXY_REVIEWED" to produce a schema-valid
research_review.json.

Persists 1 scoreboard row (experiment_type="research_proxy",
<step:review> token first in artifact_paths). Provider_calls +1.

Mirrors arena research-proxy's pre-invoke vs post-invoke discipline:
- KillSwitchActive, pre-invoke ProviderCallBreaker, and fixture-digest
  drift halt without inserting a row.
- Post-invoke BudgetExceeded persists a blocked row WITH
  exc.usage_proxy. SandboxViolation persists with usage_proxy=None.

PR4 reproducibility precheck (compute_fixture_set_digest +
record_provider_version) fires before any provider invocation. Drift
on stub_claude's version tags the review row with
<PROVIDER_VERSION_CHANGED:from=...> in artifact_paths.

Emits review_recorded trace event after success (payload uses only
event.schema.json-permitted keys: review_id, experiment_id, status,
path).

7 regression tests cover: happy path, missing impl experiment,
non-research-proxy row (no fusion_id token), fixture drift, version
drift tagging, kill switch, post-invoke BudgetExceeded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Memory proposal builder + validator + diff

**Files:**
- Create: `arena/memory/__init__.py`
- Create: `arena/memory/proposal.py`
- Create: `arena/memory/validator.py`
- Create: `arena/memory/diff.py`
- Create: `tests/test_memory_proposal.py`
- Create: `tests/test_memory_validator.py`
- Create: `tests/test_memory_diff.py`

- [ ] **Step 1: Write failing memory_proposal tests**

Create `tests/test_memory_proposal.py`:

```python
# tests/test_memory_proposal.py
from __future__ import annotations

from pathlib import Path

from arena.memory.proposal import (
    get_next_proposal_id,
    synthesize_memory_proposal,
    validate_memory_update,
)


def _review_payload(*, required_fixes: list[str] | None = None) -> dict:
    return {
        "schema_version": "research_review.v1",
        "review_id": "rr_0001",
        "competition_slug": "tabular_binary_v1",
        "subject_id": "exp_0004",
        "decision": "accept" if not required_fixes else "revise",
        "summary": "Reviewed proxy implementation; integrity confirmed.",
        "strengths": ["s1"],
        "weaknesses": ["w1"],
        "required_fixes": required_fixes or [],
        "follow_up_recommendations": [],
        "risk_level": "low",
    }


def test_synthesize_actionable_review_produces_add_in_research_namespace() -> None:
    """A review with required_fixes produces an add op claiming the
    first required_fix in the research namespace."""
    review = _review_payload(
        required_fixes=["Add a baseline ablation comparing GBDT-only vs ensemble."]
    )
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    validate_memory_update(proposal)
    assert proposal["proposal_id"] == "mem_0001"
    assert proposal["namespace"] == "research"
    assert proposal["operation"] == "add"
    assert "baseline ablation" in proposal["claim"]
    assert proposal["review_status"] == "proposed"


def test_synthesize_empty_review_produces_noop_observation() -> None:
    """A review with no required_fixes / follow_up_recommendations
    produces a schema-valid no-op observation. Captures audit trail."""
    review = _review_payload(required_fixes=[])
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    validate_memory_update(proposal)
    assert proposal["operation"] == "add"
    assert "no actionable findings" in proposal["claim"].lower()
    assert proposal["confidence"] == "low"
    assert proposal["risk"] == "low"


def test_synthesize_evidence_points_to_review() -> None:
    """The evidence array must reference the review (type=trace)."""
    review = _review_payload()
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert len(proposal["evidence"]) >= 1
    assert proposal["evidence"][0]["type"] == "trace"
    assert proposal["evidence"][0]["ref"] == "rr_0001"


def test_synthesize_namespace_defaults_to_research() -> None:
    review = _review_payload()
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert proposal["namespace"] == "research"


def test_synthesize_review_status_is_proposed() -> None:
    """No proposal is auto-accepted; review_status='proposed' always."""
    review = _review_payload(required_fixes=["fix one"])
    proposal = synthesize_memory_proposal(review, proposal_id="mem_0001")
    assert proposal["review_status"] == "proposed"


def test_get_next_proposal_id_monotonic(tmp_path: Path) -> None:
    """Mints mem_0001 in an empty dir; mem_0002 after mem_0001 exists."""
    proposals_dir = tmp_path / "memory" / "proposals"
    assert get_next_proposal_id(proposals_dir) == "mem_0001"
    proposals_dir.mkdir(parents=True)
    (proposals_dir / "mem_0001.json").write_text("{}", encoding="utf-8")
    assert get_next_proposal_id(proposals_dir) == "mem_0002"
    (proposals_dir / "mem_0002.json").write_text("{}", encoding="utf-8")
    (proposals_dir / "mem_0009.json").write_text("{}", encoding="utf-8")
    assert get_next_proposal_id(proposals_dir) == "mem_0010"
```

- [ ] **Step 2: Write failing memory_validator tests**

Create `tests/test_memory_validator.py`:

```python
# tests/test_memory_validator.py
from __future__ import annotations

from arena.memory.validator import check_evidence


def _proposal(**overrides) -> dict:
    base = {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": "add",
        "claim": "A non-trivial claim string.",
        "delta": "A non-trivial delta string.",
        "evidence": [
            {
                "type": "trace",
                "ref": "rr_0001",
                "quote_or_summary": "summary here",
            }
        ],
        "confidence": "medium",
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": "low",
        "review_status": "proposed",
    }
    base.update(overrides)
    return base


def test_valid_add_proposal_passes() -> None:
    issues = check_evidence(_proposal(operation="add"))
    assert issues == []


def test_modify_without_prior_claim_fails() -> None:
    """The schema's allOf branch already requires prior_claim on modify;
    check_evidence ALSO surfaces this as a semantic issue (defense in
    depth + clearer message)."""
    proposal = _proposal(operation="modify")
    # No prior_claim set.
    issues = check_evidence(proposal)
    assert any("prior_claim" in i.lower() for i in issues)


def test_modify_with_identical_claim_and_prior_claim_fails() -> None:
    """A 'modify' that doesn't actually change the claim is a no-op
    that should be rejected."""
    proposal = _proposal(
        operation="modify",
        claim="Same claim",
        prior_claim="Same claim",
    )
    issues = check_evidence(proposal)
    assert any(
        "claim" in i.lower() and "prior_claim" in i.lower() for i in issues
    )


def test_empty_evidence_fails() -> None:
    """Schema requires minItems=1; the validator double-checks."""
    proposal = _proposal()
    proposal["evidence"] = []
    issues = check_evidence(proposal)
    assert any("evidence" in i.lower() for i in issues)
```

- [ ] **Step 3: Write failing memory_diff tests**

Create `tests/test_memory_diff.py`:

```python
# tests/test_memory_diff.py
from __future__ import annotations

import hashlib
from pathlib import Path

from arena.memory.diff import render_diff


def _proposal() -> dict:
    return {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": "add",
        "claim": "Stack diverse base learners to reduce bias.",
        "delta": "Add this constraint to the research namespace.",
        "evidence": [
            {"type": "trace", "ref": "rr_0001", "quote_or_summary": "x"}
        ],
        "confidence": "medium",
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": "low",
        "review_status": "proposed",
    }


def test_render_diff_returns_unified_diff_string(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "# Memory Wiki\n\nresearch/\n  Existing claim.\n",
        encoding="utf-8",
    )
    out = render_diff(_proposal(), wiki_path=wiki)
    assert isinstance(out, str)
    # Unified-diff markers.
    assert out.startswith("---") or "+++" in out or "@@ " in out


def test_render_diff_is_namespace_scoped(tmp_path: Path) -> None:
    """The diff should only mention the proposal's namespace section,
    not unrelated parts of the wiki."""
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "invariants/\n  Inv claim.\nresearch/\n  Existing claim.\n",
        encoding="utf-8",
    )
    out = render_diff(_proposal(), wiki_path=wiki)
    # The proposal's claim should appear in the diff.
    assert "Stack diverse base learners" in out


def test_render_diff_does_not_mutate_wiki(tmp_path: Path) -> None:
    """Pure function: the wiki file's bytes + mtime must be identical
    before and after render_diff."""
    wiki = tmp_path / "wiki.md"
    original = "# Memory Wiki\n\nresearch/\n  Existing claim.\n"
    wiki.write_text(original, encoding="utf-8")
    before_hash = hashlib.sha256(wiki.read_bytes()).hexdigest()
    render_diff(_proposal(), wiki_path=wiki)
    after_hash = hashlib.sha256(wiki.read_bytes()).hexdigest()
    assert before_hash == after_hash
    assert wiki.read_text(encoding="utf-8") == original
```

- [ ] **Step 4: Run failing tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_memory_proposal.py tests/test_memory_validator.py tests/test_memory_diff.py -v
```

Expected: 13 ImportError (modules don't exist).

- [ ] **Step 5: Implement `arena/memory/__init__.py` + `proposal.py`**

Create `arena/memory/__init__.py`:

```python
from __future__ import annotations
```

Create `arena/memory/proposal.py`:

```python
# arena/memory/proposal.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from arena.schemas.validate import validate

_PROPOSAL_ID_RE = re.compile(r"^mem_(\d+)\.json$")
_CONFIDENCE_BY_RISK = {"low": "low", "medium": "medium", "high": "high"}


def synthesize_memory_proposal(
    review_payload: dict[str, Any],
    *,
    proposal_id: str,
    namespace: str = "research",
) -> dict[str, Any]:
    """Build a deterministic schema-valid memory_update.v1 payload from
    a research_review.json review payload.

    If the review has actionable content (required_fixes non-empty or
    follow_up_recommendations non-empty), build an `add` op claiming
    the first actionable item. Otherwise emit a no-op observation.

    Phase 0: namespace is always "research" (PR6 reviews are
    research-proxy outputs). PR7+ may derive from review subject type.
    """
    review_id = review_payload["review_id"]
    summary = review_payload["summary"]
    risk_level = review_payload.get("risk_level", "low")
    fixes: list[str] = review_payload.get("required_fixes") or []
    recs: list[str] = review_payload.get("follow_up_recommendations") or []

    actionable = fixes[0] if fixes else (recs[0] if recs else None)
    if actionable is not None:
        claim = actionable
        delta = (
            f"Add this constraint to the {namespace} namespace based on "
            f"review {review_id}."
        )
        confidence = _CONFIDENCE_BY_RISK.get(risk_level, "medium")
        risk = risk_level
    else:
        claim = f"No actionable findings from review {review_id}."
        delta = "No-op observation; review accepted with no required changes."
        confidence = "low"
        risk = "low"

    return {
        "schema_version": "memory_update.v1",
        "proposal_id": proposal_id,
        "namespace": namespace,
        "operation": "add",
        "claim": claim,
        "delta": delta,
        "evidence": [
            {
                "type": "trace",
                "ref": review_id,
                "quote_or_summary": summary,
            }
        ],
        "confidence": confidence,
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": risk,
        "review_status": "proposed",
    }


def get_next_proposal_id(proposals_dir: Path = Path("memory/proposals")) -> str:
    """Mint the next mem_NNNN id by scanning `proposals_dir` for files
    matching `mem_<digits>.json`. Returns mem_0001 for an empty / missing
    directory.
    """
    if not proposals_dir.exists():
        return "mem_0001"
    max_n = 0
    for entry in proposals_dir.iterdir():
        m = _PROPOSAL_ID_RE.match(entry.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"mem_{max_n + 1:04d}"


def validate_memory_update(payload: dict[str, Any]) -> None:
    """Validate `payload` against schemas/memory_update.schema.json.
    Thin wrapper over arena.schemas.validate.validate."""
    validate("memory_update", payload)
```

- [ ] **Step 6: Implement `arena/memory/validator.py`**

Create `arena/memory/validator.py`:

```python
# arena/memory/validator.py
from __future__ import annotations

from typing import Any

_OPS_REQUIRING_PRIOR_CLAIM = {"modify", "deprecate", "remove"}


def check_evidence(proposal: dict[str, Any]) -> list[str]:
    """Run semantic checks on a memory_update proposal beyond what the
    schema enforces.

    Returns a list of issue strings; empty list means valid.

    Checks:
    - operation in {modify, deprecate, remove} requires non-empty
      prior_claim.
    - operation=modify must have claim != prior_claim (otherwise it's
      a no-op).
    - evidence list must be non-empty (also a schema constraint;
      double-checked here for clearer error messages).
    """
    issues: list[str] = []
    operation = proposal.get("operation")
    claim = proposal.get("claim", "")
    prior_claim = proposal.get("prior_claim")

    if operation in _OPS_REQUIRING_PRIOR_CLAIM:
        if not prior_claim:
            issues.append(
                f"operation={operation!r} requires a non-empty prior_claim"
            )
        elif operation == "modify" and prior_claim == claim:
            issues.append(
                "operation=modify with identical claim and prior_claim is a "
                "no-op; modify must change the claim"
            )

    evidence = proposal.get("evidence") or []
    if not evidence:
        issues.append("evidence array must be non-empty")

    return issues
```

- [ ] **Step 7: Implement `arena/memory/diff.py`**

Create `arena/memory/diff.py`:

```python
# arena/memory/diff.py
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


def render_diff(
    proposal: dict[str, Any],
    wiki_path: Path = Path("docs/memory/UNIFIED_MEMORY_WIKI.md"),
) -> str:
    """Render a unified-diff-style string showing what `proposal` would
    change in the unified memory wiki, scoped to the proposal's
    namespace.

    Pure function: never mutates `wiki_path`. The caller is responsible
    for any actual merge — this only renders what the merge would look
    like.

    For PR6, the diff treats the proposal as an `add` to the namespace
    section: the synthesized "after" text inserts the claim under the
    namespace heading. modify/deprecate/remove are rendered analogously
    using `prior_claim` to locate the line to change. The wiki itself
    is read-only; the caller decides whether to apply the diff.
    """
    namespace = proposal.get("namespace", "")
    claim = proposal.get("claim", "")
    operation = proposal.get("operation", "add")
    prior_claim = proposal.get("prior_claim") or ""
    proposal_id = proposal.get("proposal_id", "")

    wiki_text = wiki_path.read_text(encoding="utf-8")
    before_lines = wiki_text.splitlines(keepends=True)

    after_lines: list[str] = list(before_lines)
    section_marker = f"{namespace}/"
    insertion_index = -1
    for i, line in enumerate(after_lines):
        if line.strip().startswith(section_marker):
            # Insert just after the section header.
            insertion_index = i + 1
            break

    new_line = f"  [{proposal_id}] {claim}\n"
    if operation == "add":
        if insertion_index >= 0:
            after_lines.insert(insertion_index, new_line)
        else:
            # No section yet — append a fresh one at the end.
            after_lines.append(f"\n{section_marker}\n{new_line}")
    elif operation in {"modify", "deprecate", "remove"}:
        # Find the prior_claim line within the namespace section and
        # replace / annotate it.
        if prior_claim:
            for i, line in enumerate(after_lines):
                if prior_claim in line:
                    if operation == "remove":
                        after_lines[i] = ""
                    elif operation == "deprecate":
                        after_lines[i] = (
                            f"  [DEPRECATED via {proposal_id}] "
                            f"{prior_claim}\n"
                        )
                    else:  # modify
                        after_lines[i] = new_line
                    break

    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=str(wiki_path),
        tofile=f"{wiki_path} (after {proposal_id})",
        n=3,
    )
    return "".join(diff)
```

- [ ] **Step 8: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_memory_proposal.py tests/test_memory_validator.py tests/test_memory_diff.py -v
```

Expected: 13 passed (6 proposal + 4 validator + 3 diff).

- [ ] **Step 9: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 323 passed (was 310, +13). All checks clean.

- [ ] **Step 10: Commit**

```bash
git add arena/memory/__init__.py arena/memory/proposal.py \
        arena/memory/validator.py arena/memory/diff.py \
        tests/test_memory_proposal.py tests/test_memory_validator.py \
        tests/test_memory_diff.py
git commit -m "$(cat <<'EOF'
feat(memory): proposal synthesizer + semantic validator + read-only diff

arena/memory/proposal.py:
- synthesize_memory_proposal(review_payload, *, proposal_id,
  namespace="research") deterministically builds a schema-valid
  memory_update.v1 from a research_review payload. If the review has
  actionable content (required_fixes or follow_up_recommendations),
  uses required_fixes[0] (or recs[0]) as the claim; otherwise emits a
  no-op observation captured for audit trail. namespace="research"
  always in PR6.
- get_next_proposal_id(proposals_dir) mints mem_NNNN by filesystem
  scan; mem_0001 for empty/missing dir.
- validate_memory_update wraps arena.schemas.validate.

arena/memory/validator.py:
- check_evidence(proposal) -> list[str] runs semantic checks beyond
  the schema: operation in {modify, deprecate, remove} requires
  prior_claim; operation=modify requires claim != prior_claim;
  evidence non-empty. Empty list = valid.

arena/memory/diff.py:
- render_diff(proposal, wiki_path) is a pure function returning a
  unified-diff string showing what the proposal WOULD change in
  docs/memory/UNIFIED_MEMORY_WIKI.md. Never mutates wiki_path.
  Add/modify/deprecate/remove all rendered; add inserts under the
  namespace heading.

13 tests cover all cases including the no-op observation, namespace
scoping, contradiction detection, and read-only diff verification.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: arena memory propose CLI

**Files:**
- Modify: `arena/cli.py`
- Create: `tests/test_cli_memory_propose.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli_memory_propose.py`:

```python
# tests/test_cli_memory_propose.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _bootstrap_review(runner: CliRunner) -> None:
    """Bootstrap a scoreboard with a research-proxy chain + a review row
    so memory propose has an artifact to read. Review row lands at exp_0005."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )


def test_memory_propose_happy_path(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arena memory propose against a review row writes a schema-valid
    memory_update.json + emits memory_proposal_created trace event +
    creates NO scoreboard row."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    result = runner.invoke(
        app,
        ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"],
    )
    assert result.exit_code == 0, result.output

    proposal_path = fixture_workspace / "memory" / "proposals" / "mem_0001.json"
    assert proposal_path.exists()
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert payload["proposal_id"] == "mem_0001"
    assert payload["namespace"] == "research"
    assert payload["review_status"] == "proposed"


def test_memory_propose_inserts_no_scoreboard_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-only action: memory propose must NOT inflate
    COUNT(*) (preserves PR5's provider_calls invariant)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        before = store._require_conn().execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE competition_slug = ?",
            ("tabular_binary_v1",),
        ).fetchone()["n"]
    finally:
        store.close()

    runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        after = store._require_conn().execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE competition_slug = ?",
            ("tabular_binary_v1",),
        ).fetchone()["n"]
    finally:
        store.close()
    assert after == before


def test_memory_propose_no_op_for_review_with_no_required_fixes(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default stub review has decision=accept, required_fixes=[];
    memory propose still emits a schema-valid no-op observation."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    result = runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )
    assert result.exit_code == 0
    payload = json.loads(
        (fixture_workspace / "memory" / "proposals" / "mem_0001.json").read_text(encoding="utf-8")
    )
    assert "no actionable findings" in payload["claim"].lower()


def test_memory_propose_missing_review_experiment(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--review <exp_id> must exist."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_9999"]
    )
    assert result.exit_code != 0
    assert "exp_9999" in result.output or "not found" in result.output.lower()


def test_memory_propose_rejects_schema_invalid_review(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A research_review.json that is syntactically valid JSON but
    fails schema validation (missing a required field) must surface as
    a clean typer.BadParameter, not an unhandled ValidationError.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    # Locate the review row's research_review.json and overwrite it
    # with a payload missing the required `decision` field.
    rev_workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0005"
    rr_path = rev_workspace / "research_review.json"
    assert rr_path.exists()
    rr_path.write_text(
        json.dumps(
            {
                "schema_version": "research_review.v1",
                "review_id": "rr_0001",
                "competition_slug": "tabular_binary_v1",
                "subject_id": "exp_0004",
                # decision intentionally missing
                "summary": "10+ char summary",
                "strengths": [],
                "weaknesses": [],
                "required_fixes": [],
                "follow_up_recommendations": [],
                "risk_level": "low",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )
    assert result.exit_code != 0
    assert "schema-invalid" in result.output.lower() or "decision" in result.output


def test_memory_propose_id_is_monotonic(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First call mints mem_0001; second call (against the same review)
    mints mem_0002. Filesystem-scan based; no race condition in this
    test since CliRunner is sequential."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )
    runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )
    proposals_dir = fixture_workspace / "memory" / "proposals"
    files = sorted(p.name for p in proposals_dir.iterdir())
    assert files == ["mem_0001.json", "mem_0002.json"]


def test_memory_propose_trace_event_attaches_to_review_run_not_latest(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cross-run linkage: arena memory propose has no
    scoreboard row, so the memory_proposal_created trace event is the
    only durable linkage to the review row. The event's run_id MUST be
    the review row's run, not _latest_run_id().

    Bootstrap a review under run_A. Start a second `arena init-fixture`
    + research-proxy under run_B. Run `arena memory propose
    --review <exp from run_A>` — the trace event MUST land under
    traces/run_A/, not traces/run_B/.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        run_a = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE experiment_id = ?",
            ("exp_0005",),
        ).fetchone()["run_id"]
    finally:
        store.close()

    # Second run.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        run_b = store._require_conn().execute(
            "SELECT run_id FROM experiments WHERE experiment_id = ?",
            ("exp_0006",),
        ).fetchone()["run_id"]
    finally:
        store.close()
    assert run_a != run_b

    # Memory propose against the run_A review — event must land under run_A.
    result = runner.invoke(
        app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"]
    )
    assert result.exit_code == 0, result.output

    found_in_a = False
    found_in_b = False
    traces_root = fixture_workspace / "traces"
    for jsonl in traces_root.rglob("events.jsonl"):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt.get("event_type") != "memory_proposal_created":
                continue
            if evt["run_id"] == run_a:
                found_in_a = True
            elif evt["run_id"] == run_b:
                found_in_b = True
    assert found_in_a, "memory_proposal_created not found under review's run"
    assert not found_in_b, (
        "memory_proposal_created leaked into latest run's trace"
    )
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_memory_propose.py -v
```

Expected: 7 failures — `arena memory propose` doesn't exist yet.

- [ ] **Step 3: Add `memory` Typer subapp + `propose` subcommand to `arena/cli.py`**

In `arena/cli.py`, add at the top of the file (after the existing `arena/research_proxy/*` imports):

```python
from jsonschema import ValidationError

from arena.memory.proposal import (
    get_next_proposal_id,
    synthesize_memory_proposal,
    validate_memory_update,
)
```

(If `from jsonschema import ValidationError` is already imported elsewhere in `arena/cli.py`, do NOT add it again — keep imports unique.)

Then add a Typer subapp BEFORE the existing subcommands (after `app = typer.Typer(...)` at line ~46):

```python
memory_app = typer.Typer(help="Memory proposal commands.")
app.add_typer(memory_app, name="memory")
```

Now add the subcommand at the end of the file (after `arena review`):

```python
@memory_app.command("propose")
def memory_propose(
    competition_slug: str,
    review: str = typer.Option(
        ...,
        "--review",
        help="experiment_id of the review row whose research_review.json "
        "drives the synthesized memory proposal.",
    ),
) -> None:
    """Synthesize a memory_update.json proposal from a review row.

    Deterministic-controller action: NO provider invocation, NO
    scoreboard row, provider_calls unchanged. Output is a durable
    artifact at memory/proposals/mem_NNNN.json plus a
    memory_proposal_created trace event whose payload uses ONLY keys
    permitted by schemas/event.schema.json.
    """
    store = _store()

    # Resolve the review row + its run_id FIRST. The trace event's
    # run_id MUST be the review row's own run, not _latest_run_id().
    # Memory proposals don't create scoreboard rows, so the
    # memory_proposal_created trace event is the durable linkage between
    # the proposal artifact and the review experiment that drove it.
    # Mirrors the same fix applied to `arena review` for cross-run
    # linkage.
    review_row = store._require_conn().execute(
        "SELECT run_id, artifact_paths FROM experiments "
        "WHERE competition_slug = ? AND experiment_id = ?",
        (competition_slug, review),
    ).fetchone()
    if review_row is None:
        raise typer.BadParameter(
            f"experiment {review} not found for {competition_slug}"
        )
    run_id = review_row["run_id"]
    if not run_id:
        raise typer.BadParameter(
            f"experiment {review} has no run_id (corrupt scoreboard?)"
        )
    review_paths: list[str] = json.loads(review_row["artifact_paths"])
    if "<step:review>" not in review_paths:
        raise typer.BadParameter(
            f"experiment {review} is not a review row "
            "(no <step:review> token in artifact_paths)"
        )
    research_review_path = next(
        (p for p in review_paths if p.endswith("research_review.json")),
        None,
    )
    if research_review_path is None:
        raise typer.BadParameter(
            f"review row {review} has no research_review.json in "
            "artifact_paths (corrupt scoreboard?)"
        )

    try:
        review_payload = json.loads(
            Path(research_review_path).read_text(encoding="utf-8")
        )
        validate_schema("research_review", review_payload)
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        raise typer.BadParameter(
            f"failed to read research_review.json at "
            f"{research_review_path}: {exc}"
        ) from exc
    except ValidationError as exc:
        # Schema-invalid research_review.json: surface as a clean
        # BadParameter rather than letting the ValidationError escape
        # as an unhandled exception.
        raise typer.BadParameter(
            f"research_review.json at {research_review_path} is "
            f"schema-invalid: {exc.message}"
        ) from exc

    proposals_dir = Path("memory/proposals")
    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = get_next_proposal_id(proposals_dir)
    proposal = synthesize_memory_proposal(review_payload, proposal_id=proposal_id)
    validate_memory_update(proposal)
    proposal_path = proposals_dir / f"{proposal_id}.json"
    proposal_path.write_text(
        json.dumps(proposal, indent=2), encoding="utf-8"
    )

    # Emit trace event with ONLY event.schema.json-permitted keys.
    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)
    trace_store.emit(
        event_type="memory_proposal_created",
        severity="info",
        payload={
            "message": f"memory proposal {proposal_id} synthesized from review {review}",
            "phase": Phase.MEMORY_PROPOSAL_CREATED.value,
            "proposal_id": proposal_id,
            "memory_update_id": proposal_id,
            "experiment_id": review,
            "review_id": review_payload["review_id"],
            "path": str(proposal_path),
            "paths": [research_review_path],
        },
    )
    console.print(
        f"[green]memory proposal[/green] {proposal_id} "
        f"({proposal['operation']} in {proposal['namespace']}) "
        f"written to {proposal_path}"
    )
```

- [ ] **Step 4: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_cli_memory_propose.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 330 passed (was 323, +7 = 5 happy-path/no-row/etc. + 1 schema-invalid review regression + 1 cross-run trace-event regression). All checks clean.

- [ ] **Step 6: Commit**

```bash
git add arena/cli.py tests/test_cli_memory_propose.py
git commit -m "$(cat <<'EOF'
feat(cli,memory): arena memory propose subcommand for §6.2 step 10

`arena memory propose <slug> --review <review_exp_id>` is a
deterministic-controller action: it reads the review row's
research_review.json from artifact_paths, synthesizes a schema-valid
memory_update.json via arena.memory.proposal.synthesize_memory_proposal,
writes it to memory/proposals/mem_NNNN.json, and emits a
memory_proposal_created trace event.

NO scoreboard row is inserted (controller-only; preserves PR5's
COUNT(*) == provider_calls invariant). NO provider call. The artifact
file + trace event together form the durable audit record.

Trace event payload uses ONLY event.schema.json-permitted keys:
message, phase=MEMORY_PROPOSAL_CREATED, proposal_id, memory_update_id
(same value), experiment_id (=review_exp_id), review_id (from the
review payload), path (to mem_NNNN.json), paths (to the source
research_review.json).

5 tests cover happy path, no-scoreboard-row invariant, no-op fallback
for empty review, missing review experiment, monotonic proposal IDs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Self-improvement scan + proposal + champion_challenger

**Files:**
- Create: `arena/self_improvement/__init__.py`
- Create: `arena/self_improvement/scan.py`
- Create: `arena/self_improvement/proposal.py`
- Create: `arena/self_improvement/champion_challenger.py`
- Create: `tests/test_self_improvement_scan.py`
- Create: `tests/test_self_improvement_proposal.py`
- Create: `tests/test_self_improvement_champion_challenger.py`

- [ ] **Step 1: Write failing scan tests**

Create `tests/test_self_improvement_scan.py`:

```python
# tests/test_self_improvement_scan.py
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore
from arena.self_improvement.scan import scan_runs


def _bootstrap_clean_run(runner: CliRunner) -> None:
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )


def test_scan_clean_scoreboard_returns_no_findings(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scoreboard with only completed rows and score >= calibration
    baseline produces zero findings."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert findings == []


def test_scan_detects_blocked_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked row in the scoreboard surfaces as a Finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Force a post-invoke BudgetExceeded so research-proxy persists a
    # blocked row at exp_0001 (the question step).
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "blocked_row" for f in findings)
    blocked = next(f for f in findings if f.kind == "blocked_row")
    assert any("exp_0001" in r for r in blocked.evidence_refs)


def test_scan_detects_score_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose max(score) is below the calibration baseline (0.5)
    surfaces a score_regression finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    # Manually downgrade the implementation row's score to simulate
    # regression.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        store._require_conn().execute(
            "UPDATE experiments SET score = 0.42 WHERE experiment_id = ?",
            ("exp_0004",),
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "score_regression" for f in findings)


def test_scan_detects_invalid_submission(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row with valid_submission=False (fixture-success-rate
    regression) surfaces as an invalid_submission finding (§7.3 'lower
    fixture success rate than champion')."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        store._require_conn().execute(
            "UPDATE experiments SET valid_submission = 0 "
            "WHERE experiment_id = ?",
            ("exp_0004",),
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "invalid_submission" for f in findings)


def test_scan_detects_wall_clock_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When non-calibration rows' aggregated wall_seconds exceeds the
    calibration champion's by >20% AND there's no score improvement,
    scan_runs surfaces a wall_clock_regression finding (§7.3
    'wall-clock increase over 20% without score/safety improvement').

    The PR1 calibration row exists at exp_0001; we plant a non-zero
    wall_seconds on it as the champion baseline, then inflate one
    research-proxy row's wall_seconds to trip the threshold."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(
        app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"]
    )
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        # Champion (calibration): 1 row, wall_seconds=1.0, score=0.5.
        store._require_conn().execute(
            "UPDATE experiments SET wall_seconds = 1.0 "
            "WHERE experiment_id = 'exp_0001'"
        )
        # Challenger: 4 rows, summed wall_seconds=2.0 (>1.20 * 1.0 and
        # score not improved over champion's 0.5 — research-proxy impl
        # row's score is also 0.5).
        store._require_conn().execute(
            "UPDATE experiments SET wall_seconds = 0.5 "
            "WHERE experiment_id IN ('exp_0002','exp_0003','exp_0004','exp_0005')"
        )
        store._require_conn().commit()
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "wall_clock_regression" for f in findings), [
        f.kind for f in findings
    ]


def test_scan_detects_provider_calls_regression(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When non-calibration row count exceeds the calibration row count
    by >20% AND there's no score improvement, scan_runs surfaces a
    provider_calls_regression finding."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(
        app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"]
    )
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    # Champion: 1 calibration row. Challenger: 4 research-proxy rows
    # = 4× the champion = >20% increase. Score at exp_0004 is the
    # calibration baseline 0.5, so no improvement.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "provider_calls_regression" for f in findings), [
        f.kind for f in findings
    ]


def test_scan_treats_missing_trace_as_failed_replay(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose task_id has no events.jsonl on disk MUST surface a
    failed_replay finding. The chain cannot be replayed, so per §7.3
    this is a freeze trigger. Regression for the original 'no trace =
    OK to skip' bug.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean_run(runner)

    # Locate exp_0004's trace file and delete it.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        impl = store._require_conn().execute(
            "SELECT run_id, task_id FROM experiments WHERE experiment_id = ?",
            ("exp_0004",),
        ).fetchone()
    finally:
        store.close()

    canonical = (
        fixture_workspace
        / "traces"
        / impl["run_id"]
        / impl["task_id"]
        / "events.jsonl"
    )
    if canonical.exists():
        canonical.unlink()
    nested = (
        fixture_workspace
        / "runs"
        / impl["run_id"]
        / "traces"
        / impl["task_id"]
        / "events.jsonl"
    )
    if nested.exists():
        nested.unlink()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        findings = scan_runs(
            "tabular_binary_v1",
            store=store,
            runs_root=fixture_workspace / "runs",
            baselines_root=fixture_workspace / "runs" / ".baselines",
        )
    finally:
        store.close()
    assert any(f.kind == "failed_replay" for f in findings), [
        f.kind for f in findings
    ]
```

- [ ] **Step 2: Write failing proposal tests**

Create `tests/test_self_improvement_proposal.py`:

```python
# tests/test_self_improvement_proposal.py
from __future__ import annotations

from pathlib import Path

from arena.self_improvement.proposal import (
    get_next_sip_id,
    make_self_improvement_proposal,
    validate_self_improvement_proposal,
)
from arena.self_improvement.scan import Finding


def test_make_self_improvement_proposal_is_schema_valid() -> None:
    finding = Finding(
        kind="blocked_row",
        severity="medium",
        problem="Task task_0001 was blocked by OutputCharsBreaker.",
        evidence_refs=["scoreboard:exp_0001", "trace:run_x/task_0001"],
    )
    proposal = make_self_improvement_proposal(finding, proposal_id="sip_0001")
    validate_self_improvement_proposal(proposal)
    assert proposal["proposal_id"] == "sip_0001"
    assert proposal["requires_human_approval"] is True


def test_proposal_carries_evidence_refs() -> None:
    finding = Finding(
        kind="score_regression",
        severity="high",
        problem="exp_0004 score 0.42 below calibration baseline 0.5.",
        evidence_refs=["scoreboard:exp_0004"],
    )
    proposal = make_self_improvement_proposal(finding, proposal_id="sip_0002")
    assert "scoreboard:exp_0004" in proposal["evidence_refs"]


def test_get_next_sip_id_monotonic(tmp_path: Path) -> None:
    proposals_dir = tmp_path / "self_improvement" / "proposals"
    assert get_next_sip_id(proposals_dir) == "sip_0001"
    proposals_dir.mkdir(parents=True)
    (proposals_dir / "sip_0001.json").write_text("{}", encoding="utf-8")
    (proposals_dir / "sip_0007.json").write_text("{}", encoding="utf-8")
    assert get_next_sip_id(proposals_dir) == "sip_0008"
```

- [ ] **Step 3: Write failing champion_challenger tests**

Create `tests/test_self_improvement_champion_challenger.py`:

```python
# tests/test_self_improvement_champion_challenger.py
from __future__ import annotations

from arena.self_improvement.champion_challenger import (
    ComparisonResult,
    Metrics,
    compare_metrics,
)


def test_compare_metrics_returns_comparison_result() -> None:
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.6, wall_seconds=11.0, provider_calls=1, waste_events=0)
    result = compare_metrics(champion, challenger)
    assert isinstance(result, ComparisonResult)
    assert result.score_delta == 0.1
    assert result.regressed is False


def test_compare_metrics_flags_score_regression() -> None:
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.42, wall_seconds=10.0, provider_calls=1, waste_events=0)
    result = compare_metrics(champion, challenger)
    assert result.regressed is True
    assert "score" in result.reason.lower()


def test_compare_metrics_is_pure() -> None:
    """Calling compare_metrics twice with the same inputs returns equal
    ComparisonResults; no internal state."""
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    a = compare_metrics(champion, challenger)
    b = compare_metrics(champion, challenger)
    assert a == b
```

- [ ] **Step 4: Run failing tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_self_improvement_scan.py tests/test_self_improvement_proposal.py tests/test_self_improvement_champion_challenger.py -v
```

Expected: 13 ImportError (7 scan + 3 proposal + 3 champion_challenger).

- [ ] **Step 5: Implement `arena/self_improvement/__init__.py` + `scan.py`**

Create `arena/self_improvement/__init__.py`:

```python
from __future__ import annotations
```

Create `arena/self_improvement/scan.py`:

```python
# arena/self_improvement/scan.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from arena.scoreboard.store import ScoreboardStore
from arena.self_improvement.champion_challenger import (
    Metrics,
    compare_metrics,
)

# Phase-0 thresholds. These are deliberate Phase-0-stub defaults; PR7
# may make them configurable.
_CALIBRATION_BASELINE_SCORE = 0.5
_WASTE_EVENTS_THRESHOLD = 5
# Fixture success rate threshold: any single row with valid_submission
# explicitly False is a finding (Phase 0 has at most a handful of rows
# per slug, so a per-row check is appropriate).
_INVALID_SUBMISSION_FIRES_FINDING = True


@dataclass(frozen=True)
class Finding:
    """One self-improvement scan finding. Maps to one
    self_improvement_proposal.json artifact."""

    kind: str
    severity: str
    problem: str
    evidence_refs: list[str] = field(default_factory=list)


def scan_runs(
    slug: str,
    *,
    store: ScoreboardStore,
    runs_root: Path,
    baselines_root: Path,
) -> list[Finding]:
    """Scan all scoreboard rows + traces + baselines for `slug` and
    return findings.

    Phase 0 checks cover the §7.3 triggers that can be derived from
    durable state. Protected-file mutation and schema drift are out of
    scope until PR7's auto-apply flow exists.

    Triggers:
    - blocked_row: any status="blocked" row.
    - invalid_submission: any row with valid_submission explicitly False.
      (§7.3 "lower fixture success rate than champion".)
    - score_regression: max(score) < _CALIBRATION_BASELINE_SCORE.
    - waste_events_threshold: SUM(waste_events) > _WASTE_EVENTS_THRESHOLD.
      (§7.3 "more waste events".)
    - wall_clock_regression: aggregated wall_seconds across non-calibration
      rows exceeds the calibration champion's wall_seconds by >20% AND
      max(score) <= calibration's score (no improvement to justify the
      cost). (§7.3 "wall-clock increase over 20% without score/safety
      improvement".)
    - provider_calls_regression: aggregated provider_calls across
      non-calibration rows > 1.20 * calibration's provider_calls AND no
      score improvement. (§7.3 "provider call count increase over 20%
      without score/safety improvement".)
    - failed_replay: any row with a task_id whose
      traces/<run_id>/<task_id>/events.jsonl is MISSING or corrupt. A
      missing trace is treated as failed replay (the trace event chain
      cannot be reconstructed), not as replay-success.

    The +20% triggers use champion_challenger.compare_metrics so the
    comparison logic is a single library helper. In Phase-0 stub mode
    most stubs report zero wall_seconds, so these triggers fire only on
    test fixtures that synthesize non-zero values (or PR7's real
    adapters). Tests in tests/test_self_improvement_scan.py exercise
    the triggers via direct row inserts.
    """
    findings: list[Finding] = []

    rows = store._require_conn().execute(
        "SELECT experiment_id, task_id, run_id, status, score, "
        "valid_submission, waste_events, wall_seconds, "
        "experiment_type, provider "
        "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
        (slug,),
    ).fetchall()

    # 1. blocked rows
    for row in rows:
        if row["status"] == "blocked":
            findings.append(
                Finding(
                    kind="blocked_row",
                    severity="medium",
                    problem=(
                        f"experiment {row['experiment_id']} (task "
                        f"{row['task_id']}) blocked"
                    ),
                    evidence_refs=[
                        f"scoreboard:{row['experiment_id']}",
                        f"trace:{row['run_id']}/{row['task_id']}",
                    ],
                )
            )

    # 2. invalid submissions (fixture success rate below champion).
    if _INVALID_SUBMISSION_FIRES_FINDING:
        for row in rows:
            # valid_submission is stored as 0/1/None; only fire on
            # explicit False (0). None means "not applicable to this
            # row" (e.g. blocked-row branches that never produced a
            # submission).
            if row["valid_submission"] == 0:
                findings.append(
                    Finding(
                        kind="invalid_submission",
                        severity="high",
                        problem=(
                            f"experiment {row['experiment_id']} produced "
                            "valid_submission=False (fixture success rate "
                            "regression vs champion)"
                        ),
                        evidence_refs=[f"scoreboard:{row['experiment_id']}"],
                    )
                )

    # 3. score regression (only against rows that produced a score)
    scores = [row["score"] for row in rows if row["score"] is not None]
    if scores and max(scores) < _CALIBRATION_BASELINE_SCORE:
        worst = next(
            row for row in rows
            if row["score"] is not None and row["score"] == min(scores)
        )
        findings.append(
            Finding(
                kind="score_regression",
                severity="high",
                problem=(
                    f"max score {max(scores):.4f} below calibration baseline "
                    f"{_CALIBRATION_BASELINE_SCORE}"
                ),
                evidence_refs=[f"scoreboard:{worst['experiment_id']}"],
            )
        )

    # 4. waste events threshold
    total_waste = sum((row["waste_events"] or 0) for row in rows)
    if total_waste > _WASTE_EVENTS_THRESHOLD:
        findings.append(
            Finding(
                kind="waste_events_threshold",
                severity="medium",
                problem=(
                    f"total waste_events {total_waste} > threshold "
                    f"{_WASTE_EVENTS_THRESHOLD}"
                ),
                evidence_refs=[f"scoreboard:slug={slug}"],
            )
        )

    # 5+6. wall-clock and provider-call +20% regressions vs the
    # calibration champion. Champion = the calibration row(s) (PR1's
    # `arena run-next` writes experiment_type="calibration"); challenger
    # = aggregated non-calibration rows for this slug. Rely on
    # compare_metrics so the threshold logic is a single helper.
    cal_rows = [row for row in rows if row["experiment_type"] == "calibration"]
    challenger_rows = [
        row for row in rows if row["experiment_type"] != "calibration"
    ]
    if cal_rows and challenger_rows:
        champion = Metrics(
            score=max(
                (row["score"] for row in cal_rows if row["score"] is not None),
                default=_CALIBRATION_BASELINE_SCORE,
            ),
            wall_seconds=sum((row["wall_seconds"] or 0.0) for row in cal_rows),
            provider_calls=len(cal_rows),
            waste_events=sum((row["waste_events"] or 0) for row in cal_rows),
        )
        challenger = Metrics(
            score=max(
                (row["score"] for row in challenger_rows if row["score"] is not None),
                default=champion.score,
            ),
            wall_seconds=sum(
                (row["wall_seconds"] or 0.0) for row in challenger_rows
            ),
            provider_calls=len(challenger_rows),
            waste_events=sum(
                (row["waste_events"] or 0) for row in challenger_rows
            ),
        )
        comparison = compare_metrics(champion, challenger)
        # Map the comparison's regression reason into the corresponding
        # Finding.kind. compare_metrics returns "; "-joined reason
        # strings; we surface each as its own finding so freeze
        # evidence enumerates them clearly.
        if "wall_seconds" in comparison.reason:
            findings.append(
                Finding(
                    kind="wall_clock_regression",
                    severity="medium",
                    problem=(
                        f"wall-clock +{comparison.wall_seconds_delta:.1f}s "
                        ">20% over champion without score/safety improvement"
                    ),
                    evidence_refs=[f"scoreboard:slug={slug}"],
                )
            )
        if "provider_calls" in comparison.reason:
            findings.append(
                Finding(
                    kind="provider_calls_regression",
                    severity="medium",
                    problem=(
                        f"provider_calls +{comparison.provider_calls_delta} "
                        ">20% over champion without score/safety improvement"
                    ),
                    evidence_refs=[f"scoreboard:slug={slug}"],
                )
            )

    # 7. failed replay: a row with a task_id and NO trace, or a corrupt
    # trace. Missing means the chain cannot be replayed; per §7.3 this
    # is a freeze trigger. We try the canonical traces/<run_id>/<task_id>
    # path first, then runs/<run_id>/traces/<run_id>/<task_id> for
    # workspaces that wrote traces under the run dir.
    for row in rows:
        if not row["task_id"] or not row["run_id"]:
            continue
        canonical = Path("traces") / row["run_id"] / row["task_id"] / "events.jsonl"
        nested = (
            runs_root / row["run_id"] / "traces" / row["task_id"] / "events.jsonl"
        )
        target: Path | None
        if canonical.exists():
            target = canonical
        elif nested.exists():
            target = nested
        else:
            target = None
        if target is None:
            findings.append(
                Finding(
                    kind="failed_replay",
                    severity="high",
                    problem=(
                        f"missing trace for {row['experiment_id']} (task "
                        f"{row['task_id']}, run {row['run_id']}); replay cannot "
                        "be reconstructed"
                    ),
                    evidence_refs=[f"trace:{row['run_id']}/{row['task_id']}"],
                )
            )
            continue
        try:
            target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(
                Finding(
                    kind="failed_replay",
                    severity="high",
                    problem=f"corrupt trace at {target}",
                    evidence_refs=[f"trace:{row['run_id']}/{row['task_id']}"],
                )
            )

    return findings
```

- [ ] **Step 6: Implement `arena/self_improvement/proposal.py`**

Create `arena/self_improvement/proposal.py`:

```python
# arena/self_improvement/proposal.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from arena.schemas.validate import validate
from arena.self_improvement.scan import Finding

_SIP_ID_RE = re.compile(r"^sip_(\d+)\.json$")


def make_self_improvement_proposal(
    finding: Finding, *, proposal_id: str
) -> dict[str, Any]:
    """Build a schema-valid self_improvement_proposal.v1 from a Finding.

    Phase-0 stub: requires_human_approval is always True; protected_files_touched
    is empty (the proposal observes; PR7+ may propose code changes).
    """
    risk_level_map = {"low": "low", "medium": "medium", "high": "high", "critical": "critical"}
    risk_level = risk_level_map.get(finding.severity, "medium")
    return {
        "schema_version": "self_improvement_proposal.v1",
        "proposal_id": proposal_id,
        "problem": finding.problem,
        "evidence_refs": list(finding.evidence_refs) or [f"finding:{finding.kind}"],
        "proposed_change": (
            f"Investigate {finding.kind} surfaced by self-improvement scan; "
            "add a regression test in tests/ and a corresponding fix only "
            "after human review."
        ),
        "risk_level": risk_level,
        "protected_files_touched": [],
        "tests_to_add": [
            f"tests/test_regression_{finding.kind}.py — pin the failure mode"
        ],
        "rollback_plan": (
            "Revert the offending commit; the scan + sentinel keep the "
            "system frozen until a human-approved fix lands."
        ),
        "champion_challenger_plan": (
            "Compare ROC-AUC + wall_seconds + provider_calls between the "
            "champion (PR1 calibration) and the proposed challenger fix on "
            "the tabular_binary_v1 fixture. Reject if any regression."
        ),
        "requires_human_approval": True,
    }


def get_next_sip_id(
    proposals_dir: Path = Path("self_improvement/proposals"),
) -> str:
    """Mint the next sip_NNNN id by scanning `proposals_dir`. Returns
    sip_0001 for empty/missing directory."""
    if not proposals_dir.exists():
        return "sip_0001"
    max_n = 0
    for entry in proposals_dir.iterdir():
        m = _SIP_ID_RE.match(entry.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"sip_{max_n + 1:04d}"


def validate_self_improvement_proposal(payload: dict[str, Any]) -> None:
    """Validate against schemas/self_improvement_proposal.schema.json."""
    validate("self_improvement_proposal", payload)
```

- [ ] **Step 7: Implement `arena/self_improvement/champion_challenger.py`**

Create `arena/self_improvement/champion_challenger.py`:

```python
# arena/self_improvement/champion_challenger.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    """Comparable metrics tuple. Phase-0 stub uses ROC-AUC score +
    coarse cost/safety counters. PR7+ may add input/output_chars."""

    score: float
    wall_seconds: float
    provider_calls: int
    waste_events: int


@dataclass(frozen=True)
class ComparisonResult:
    """Output of compare_metrics. score_delta = challenger.score -
    champion.score; regressed=True if any §7.3 trigger condition fires
    (score down, wall +20%, provider_calls +20%, more waste)."""

    score_delta: float
    wall_seconds_delta: float
    provider_calls_delta: int
    waste_events_delta: int
    regressed: bool
    reason: str


def compare_metrics(champion: Metrics, challenger: Metrics) -> ComparisonResult:
    """Compare a challenger's metrics against the champion. Pure
    function: same inputs → same output. No I/O.

    Regression triggers (§7.3 subset Phase-0 can compute deterministically
    from in-memory metrics):
    - challenger.score < champion.score
    - challenger.wall_seconds > 1.20 * champion.wall_seconds without score gain
    - challenger.provider_calls > 1.20 * champion.provider_calls without score gain
    - challenger.waste_events > champion.waste_events
    """
    score_delta = challenger.score - champion.score
    wall_delta = challenger.wall_seconds - champion.wall_seconds
    pc_delta = challenger.provider_calls - champion.provider_calls
    waste_delta = challenger.waste_events - champion.waste_events

    reasons: list[str] = []
    if challenger.score < champion.score:
        reasons.append(
            f"score regression: {challenger.score:.4f} < {champion.score:.4f}"
        )
    if (
        champion.wall_seconds > 0
        and challenger.wall_seconds > 1.20 * champion.wall_seconds
        and score_delta <= 0
    ):
        reasons.append(
            f"wall_seconds +{wall_delta:.1f}s (>20%) without score improvement"
        )
    if (
        champion.provider_calls > 0
        and challenger.provider_calls > 1.20 * champion.provider_calls
        and score_delta <= 0
    ):
        reasons.append(
            f"provider_calls +{pc_delta} (>20%) without score improvement"
        )
    if challenger.waste_events > champion.waste_events:
        reasons.append(
            f"waste_events +{waste_delta} (regression in safety surface)"
        )

    return ComparisonResult(
        score_delta=score_delta,
        wall_seconds_delta=wall_delta,
        provider_calls_delta=pc_delta,
        waste_events_delta=waste_delta,
        regressed=bool(reasons),
        reason="; ".join(reasons) if reasons else "no regression",
    )
```

- [ ] **Step 8: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_self_improvement_scan.py tests/test_self_improvement_proposal.py tests/test_self_improvement_champion_challenger.py -v
```

Expected: 13 passed (7 scan + 3 proposal + 3 champion_challenger).

- [ ] **Step 9: Run full suite + lint + mypy**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
```

Expected: 343 passed (was 330, +13 = 7 scan + 3 proposal + 3 champion_challenger). All checks clean.

- [ ] **Step 10: Commit**

```bash
git add arena/self_improvement/__init__.py arena/self_improvement/scan.py \
        arena/self_improvement/proposal.py \
        arena/self_improvement/champion_challenger.py \
        tests/test_self_improvement_scan.py \
        tests/test_self_improvement_proposal.py \
        tests/test_self_improvement_champion_challenger.py
git commit -m "$(cat <<'EOF'
feat(self_improvement): scan + proposal + champion_challenger libraries

arena/self_improvement/scan.py:
- Finding frozen dataclass (kind, severity, problem, evidence_refs).
- scan_runs(slug, *, store, runs_root, baselines_root) walks scoreboard
  + trace events. Phase-0 checks: blocked_row, score_regression vs
  calibration baseline 0.5, waste_events_threshold, failed_replay.
  Protected-file mutation + schema drift are out of scope until PR7.

arena/self_improvement/proposal.py:
- make_self_improvement_proposal(finding, *, proposal_id) synthesizes
  a schema-valid self_improvement_proposal.v1. requires_human_approval
  is always True for Phase 0; protected_files_touched is empty.
- get_next_sip_id(proposals_dir) mints sip_NNNN by filesystem scan.
- validate_self_improvement_proposal wraps arena.schemas.validate.

arena/self_improvement/champion_challenger.py:
- Metrics + ComparisonResult frozen dataclasses.
- compare_metrics(champion, challenger) is a pure helper checking the
  §7.3 subset that can be computed from metrics alone (score
  regression, wall +20% no-improvement, provider_calls +20%
  no-improvement, more waste). No I/O, no hidden-label coupling.
  Library-only; PR6's freeze evaluator + PR7's apply gate consume it.

9 tests cover scan happy/blocked/regression paths, proposal schema
validity, monotonic IDs, and pure-function comparison.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Self-improvement freeze + arena self-improve scan CLI

**Files:**
- Create: `arena/self_improvement/freeze.py`
- Create: `tests/test_self_improvement_freeze.py`
- Create: `tests/test_cli_self_improve_scan.py`
- Modify: `arena/cli.py`

- [ ] **Step 1: Write failing freeze tests**

Create `tests/test_self_improvement_freeze.py`:

```python
# tests/test_self_improvement_freeze.py
from __future__ import annotations

import json
import re
from pathlib import Path

from arena.self_improvement.freeze import (
    apply_freeze,
    evaluate_freeze,
    is_frozen,
)
from arena.self_improvement.scan import Finding


def test_evaluate_freeze_clean_findings() -> None:
    decision = evaluate_freeze([])
    assert decision.frozen is False


def test_evaluate_freeze_fires_on_any_finding() -> None:
    findings = [
        Finding(
            kind="score_regression",
            severity="high",
            problem="x",
            evidence_refs=["scoreboard:exp_0004"],
        )
    ]
    decision = evaluate_freeze(findings)
    assert decision.frozen is True
    assert any(t["kind"] == "score_regression" for t in decision.triggers)


def test_apply_freeze_writes_sentinel(tmp_path: Path) -> None:
    """apply_freeze writes a Markdown body with a fenced JSON metadata
    block, AND nothing else."""
    findings = [
        Finding(
            kind="blocked_row",
            severity="medium",
            problem="task_0001 blocked",
            evidence_refs=["scoreboard:exp_0001"],
        )
    ]
    decision = evaluate_freeze(findings)
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    apply_freeze(decision, sentinel_path=sentinel, competition_slug="tabular_binary_v1")
    assert sentinel.exists()
    content = sentinel.read_text(encoding="utf-8")
    assert content.startswith("# Self-Improvement Frozen")
    # JSON metadata block — extract via fenced code block boundary.
    m = re.search(r"```json\n(.+?)\n```", content, re.DOTALL)
    assert m is not None
    metadata = json.loads(m.group(1))
    assert metadata["frozen"] is True
    assert metadata["competition_slug"] == "tabular_binary_v1"
    assert any(t["kind"] == "blocked_row" for t in metadata["triggers"])


def test_is_frozen_after_apply(tmp_path: Path) -> None:
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    assert is_frozen(sentinel_path=sentinel) is False
    findings = [
        Finding(
            kind="score_regression", severity="high", problem="x", evidence_refs=["e"]
        )
    ]
    decision = evaluate_freeze(findings)
    apply_freeze(decision, sentinel_path=sentinel, competition_slug="x")
    assert is_frozen(sentinel_path=sentinel) is True


def test_unfreeze_via_sentinel_deletion(tmp_path: Path) -> None:
    """Deleting the sentinel marks the system unfrozen (operator action;
    no built-in unfreeze command in PR6)."""
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    findings = [
        Finding(
            kind="score_regression", severity="high", problem="x", evidence_refs=["e"]
        )
    ]
    apply_freeze(
        evaluate_freeze(findings),
        sentinel_path=sentinel,
        competition_slug="x",
    )
    assert is_frozen(sentinel_path=sentinel) is True
    sentinel.unlink()
    assert is_frozen(sentinel_path=sentinel) is False
```

- [ ] **Step 2: Write failing CLI tests**

Create `tests/test_cli_self_improve_scan.py`:

```python
# tests/test_cli_self_improve_scan.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _bootstrap_clean(runner: CliRunner) -> None:
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )


def test_self_improve_scan_clean_run_emits_no_proposals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean scoreboard produces zero proposals + no sentinel."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    result = runner.invoke(
        app, ["self-improve", "scan", "tabular_binary_v1"]
    )
    assert result.exit_code == 0, result.output
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    assert (not proposals_dir.exists()) or (
        not list(proposals_dir.iterdir())
    )
    assert not (fixture_workspace / "SELF_IMPROVEMENT_FROZEN.md").exists()


def test_self_improve_scan_fires_freeze_on_blocked_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked row in the scoreboard triggers a finding + sentinel +
    proposal artifact."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    result = runner.invoke(
        app, ["self-improve", "scan", "tabular_binary_v1"]
    )
    assert result.exit_code == 0
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    assert proposals_dir.exists()
    assert any(p.suffix == ".json" for p in proposals_dir.iterdir())
    sentinel = fixture_workspace / "SELF_IMPROVEMENT_FROZEN.md"
    assert sentinel.exists()


def test_self_improve_scan_inserts_no_scoreboard_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-only: COUNT(*) of experiments must be unchanged."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        before = store._require_conn().execute(
            "SELECT COUNT(*) AS n FROM experiments"
        ).fetchone()["n"]
    finally:
        store.close()
    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        after = store._require_conn().execute(
            "SELECT COUNT(*) AS n FROM experiments"
        ).fetchone()["n"]
    finally:
        store.close()
    assert before == after


def test_self_improve_scan_emits_trace_event_with_allowed_keys(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trace event payload uses ONLY keys permitted by event.schema.json
    (additionalProperties: false). Otherwise TraceStore.emit would
    reject."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    result = runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    assert result.exit_code == 0

    # Find the events.jsonl(s) and confirm the scan-completed event is
    # present and validates against the schema.
    traces_root = fixture_workspace / "traces"
    found = False
    for jsonl in traces_root.rglob("events.jsonl"):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt["event_type"] == "self_improvement_scan_completed":
                # Allowed keys per event.schema.json payload set.
                allowed = {
                    "message",
                    "phase",
                    "status",
                    "reason",
                    "paths",
                    "evidence",
                    "path",
                }
                assert set(evt["payload"].keys()) <= allowed, evt["payload"]
                found = True
    assert found, "self_improvement_scan_completed not found in any trace"


def test_self_improve_scan_idempotent_no_duplicate_proposals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running scan twice against the same blocked-row state produces
    the same set of proposals — no duplicates."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    after_first = sorted(p.name for p in proposals_dir.iterdir())
    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    after_second = sorted(p.name for p in proposals_dir.iterdir())
    assert after_first == after_second
```

- [ ] **Step 3: Run failing tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_self_improvement_freeze.py tests/test_cli_self_improve_scan.py -v
```

Expected: 10 failures.

- [ ] **Step 4: Implement `arena/self_improvement/freeze.py`**

Create `arena/self_improvement/freeze.py`:

```python
# arena/self_improvement/freeze.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.self_improvement.scan import Finding


@dataclass(frozen=True)
class FreezeDecision:
    """Output of evaluate_freeze. `triggers` is a list of dicts shaped
    for the sentinel's JSON metadata block."""

    frozen: bool
    triggers: list[dict[str, Any]] = field(default_factory=list)


def evaluate_freeze(findings: list[Finding]) -> FreezeDecision:
    """Return frozen=True iff any finding is present.

    Phase-0 policy: ANY finding from scan_runs triggers freeze. PR7+
    may add severity-based filtering. Each trigger dict contains
    kind/severity/problem + evidence_refs."""
    if not findings:
        return FreezeDecision(frozen=False, triggers=[])
    triggers = [
        {
            "kind": f.kind,
            "severity": f.severity,
            "problem": f.problem,
            "evidence_refs": list(f.evidence_refs),
        }
        for f in findings
    ]
    return FreezeDecision(frozen=True, triggers=triggers)


def apply_freeze(
    decision: FreezeDecision,
    *,
    sentinel_path: Path = Path("SELF_IMPROVEMENT_FROZEN.md"),
    competition_slug: str = "",
) -> None:
    """Write the freeze sentinel atomically. Markdown body + fenced JSON
    metadata block.

    No-op if `decision.frozen` is False.
    """
    if not decision.frozen:
        return
    triggered_at = datetime.now(UTC).isoformat(timespec="seconds")
    metadata = {
        "frozen": True,
        "triggered_at": triggered_at,
        "competition_slug": competition_slug,
        "triggers": decision.triggers,
    }
    evidence_lines = []
    for trigger in decision.triggers:
        for ref in trigger["evidence_refs"]:
            evidence_lines.append(f"- {trigger['kind']}: {ref}")

    body = (
        "# Self-Improvement Frozen\n"
        "\n"
        "```json\n"
        f"{json.dumps(metadata, indent=2)}\n"
        "```\n"
        "\n"
        "## Evidence\n"
        "\n"
        f"{chr(10).join(evidence_lines) if evidence_lines else '- (no evidence refs)'}\n"
        "\n"
        "## Unfreeze\n"
        "\n"
        "Human review required. Delete this file after addressing the "
        "triggers above.\n"
    )
    # Atomic write: write to a temp file then rename.
    tmp = sentinel_path.with_suffix(sentinel_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(sentinel_path)


def is_frozen(
    sentinel_path: Path = Path("SELF_IMPROVEMENT_FROZEN.md"),
) -> bool:
    """Return True iff the sentinel file exists. Source of truth."""
    return sentinel_path.exists()
```

- [ ] **Step 5: Add `self_improve` Typer subapp + `scan` subcommand to `arena/cli.py`**

In `arena/cli.py`, add at the top of the file (after the existing `arena/memory/*` imports):

```python
from arena.self_improvement.freeze import apply_freeze, evaluate_freeze
from arena.self_improvement.proposal import (
    get_next_sip_id,
    make_self_improvement_proposal,
    validate_self_improvement_proposal,
)
from arena.self_improvement.scan import scan_runs
```

Then add a Typer subapp (after the existing `memory_app` registration):

```python
self_improve_app = typer.Typer(help="Self-improvement scan + freeze commands.")
app.add_typer(self_improve_app, name="self-improve")
```

Add the subcommand at the end of the file (after `memory propose`):

```python
@self_improve_app.command("scan")
def self_improve_scan(competition_slug: str) -> None:
    """Scan all scoreboard rows + traces + baselines for `<slug>` and
    emit self_improvement_proposal.json artifacts for each finding.

    Deterministic-controller action: NO provider invocation, NO
    scoreboard row, provider_calls unchanged. If any finding fires,
    writes SELF_IMPROVEMENT_FROZEN.md sentinel at the repo root.

    Idempotent: re-running against unchanged scoreboard state does not
    duplicate proposals (content-hash dedup via (kind, sorted
    evidence_refs)).
    """
    run_id = _latest_run_id()
    if run_id is None:
        raise typer.BadParameter(
            f"no run for {competition_slug}; "
            f"run `arena init-fixture {competition_slug}` first"
        )
    store = _store()
    findings = scan_runs(
        competition_slug,
        store=store,
        runs_root=RUNS_ROOT,
        baselines_root=RUNS_ROOT / ".baselines",
    )

    proposals_dir = Path("self_improvement/proposals")
    proposals_dir.mkdir(parents=True, exist_ok=True)

    # Idempotency: hash existing proposals' (problem, sorted
    # evidence_refs) and skip new findings that match.
    existing_hashes: set[str] = set()
    for existing in proposals_dir.iterdir():
        if not existing.name.startswith("sip_"):
            continue
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        h = _finding_content_hash(
            data.get("problem", ""), sorted(data.get("evidence_refs") or [])
        )
        existing_hashes.add(h)

    new_proposal_paths: list[str] = []
    for finding in findings:
        h = _finding_content_hash(finding.problem, sorted(finding.evidence_refs))
        if h in existing_hashes:
            continue
        sip_id = get_next_sip_id(proposals_dir)
        proposal = make_self_improvement_proposal(finding, proposal_id=sip_id)
        validate_self_improvement_proposal(proposal)
        sip_path = proposals_dir / f"{sip_id}.json"
        sip_path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
        new_proposal_paths.append(str(sip_path))
        existing_hashes.add(h)  # avoid duplicate within this scan

    decision = evaluate_freeze(findings)
    sentinel_path = Path("SELF_IMPROVEMENT_FROZEN.md")
    apply_freeze(
        decision,
        sentinel_path=sentinel_path,
        competition_slug=competition_slug,
    )

    status = "frozen" if decision.frozen else ("findings" if findings else "clean")
    evidence_strs: list[str] = []
    for f in findings:
        evidence_strs.extend(f.evidence_refs)

    trace_store = TraceStore(run_id=run_id, root=TRACES_ROOT)
    payload: dict[str, Any] = {
        "message": (
            f"self-improvement scan completed for {competition_slug}: "
            f"{len(findings)} finding(s)"
        ),
        "phase": Phase.SELF_IMPROVEMENT_SCAN_COMPLETED.value,
        "status": status,
        "reason": (
            f"findings_count={len(findings)}; "
            f"freeze_triggered={'true' if decision.frozen else 'false'}"
        ),
        "paths": new_proposal_paths,
        "evidence": evidence_strs,
    }
    if decision.frozen:
        payload["path"] = str(sentinel_path)
    trace_store.emit(
        event_type="self_improvement_scan_completed",
        severity="warning" if decision.frozen else "info",
        payload=payload,
    )

    console.print(
        f"[bold]self-improve scan[/bold] {competition_slug}: "
        f"{len(findings)} finding(s); status={status}"
    )


def _finding_content_hash(problem: str, evidence_refs: list[str]) -> str:
    """Stable content hash for idempotency: same problem + same
    evidence refs → same hash → no duplicate proposal."""
    import hashlib

    h = hashlib.sha256()
    h.update(problem.encode("utf-8"))
    for ref in evidence_refs:
        h.update(b"\x00")
        h.update(ref.encode("utf-8"))
    return h.hexdigest()
```

- [ ] **Step 6: Run tests; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_self_improvement_freeze.py tests/test_cli_self_improve_scan.py -v
```

Expected: 10 passed (5 freeze + 5 CLI).

- [ ] **Step 7: Run full suite + lint + mypy + acceptance scripts**

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

Expected: 353 passed (was 343, +10). All checks clean. All 6 acceptance scripts green (still 6 in this commit; Task 7 removes one).

- [ ] **Step 8: Commit**

```bash
git add arena/self_improvement/freeze.py \
        tests/test_self_improvement_freeze.py \
        tests/test_cli_self_improve_scan.py \
        arena/cli.py
git commit -m "$(cat <<'EOF'
feat(cli,self_improvement): freeze evaluator + arena self-improve scan

arena/self_improvement/freeze.py:
- FreezeDecision frozen dataclass; evaluate_freeze(findings) returns
  frozen=True iff any finding present (Phase-0 policy: any finding =>
  freeze). apply_freeze(decision, *, sentinel_path, competition_slug)
  writes SELF_IMPROVEMENT_FROZEN.md atomically (Markdown body + fenced
  JSON metadata block; no separate JSON sidecar). is_frozen reads the
  sentinel.

arena/cli.py:
- New `arena self-improve scan <slug>` subcommand (Typer subapp).
  Reads scoreboard + traces + baselines via scan_runs; for each
  finding, synthesizes a self_improvement_proposal.json under
  self_improvement/proposals/sip_NNNN.json. Idempotent via
  content-hash dedup on (problem, sorted(evidence_refs)).
- If any finding fires, writes SELF_IMPROVEMENT_FROZEN.md sentinel.
- Emits self_improvement_scan_completed trace event with payload
  using ONLY event.schema.json-permitted keys: message, phase=
  SELF_IMPROVEMENT_SCAN_COMPLETED, status (clean|findings|frozen),
  reason ("findings_count=N; freeze_triggered=...|..."), paths
  (proposal files), evidence (refs), and path
  (SELF_IMPROVEMENT_FROZEN.md, only when frozen).
- NO scoreboard row inserted; provider_calls unchanged.

10 tests cover freeze decision logic, sentinel format (Markdown +
fenced JSON), is_frozen invariant, CLI happy path, freeze-on-blocked,
no-row invariant, allowed-payload-keys, idempotency.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Replace validate_memory_examples.py with proper test suite + update docs

**Files:**
- Delete: `scripts/validate_memory_examples.py`
- Create: `tests/test_memory_proposal_examples.py`
- Modify: `README.md` (and any other root-level `*.md` that contains "6 CI scripts"; this repo has no `CLAUDE.md`). Skip if grep finds no match. Historical plans under `docs/superpowers/plans/` are write-once and MUST NOT be edited.

- [ ] **Step 1: Write the test file that replaces the script**

Create `tests/test_memory_proposal_examples.py`:

```python
# tests/test_memory_proposal_examples.py
"""Replaces scripts/validate_memory_examples.py.

Covers:
- All 4 `operation` enum paths: add, modify, deprecate, remove.
- The schema's `prior_claim` conditional (required on
  modify/deprecate/remove; optional on add).
- Contradiction detection: claim != prior_claim on modify
  (semantic check via arena.memory.validator.check_evidence).
"""
from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.memory.validator import check_evidence
from arena.schemas.validate import validate


def _base_proposal(operation: str, **overrides) -> dict:
    base = {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": operation,
        "claim": "A non-trivial claim string.",
        "delta": "A non-trivial delta string.",
        "evidence": [
            {
                "type": "trace",
                "ref": "rr_0001",
                "quote_or_summary": "summary here",
            }
        ],
        "confidence": "medium",
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": "low",
        "review_status": "proposed",
    }
    base.update(overrides)
    return base


def test_add_operation_passes_schema_without_prior_claim() -> None:
    """The schema's allOf branch makes prior_claim optional on add."""
    proposal = _base_proposal("add")
    validate("memory_update", proposal)


def test_add_operation_passes_schema_with_null_prior_claim() -> None:
    """add allows prior_claim=null (the second allOf branch)."""
    proposal = _base_proposal("add", prior_claim=None)
    validate("memory_update", proposal)


def test_modify_operation_requires_prior_claim() -> None:
    """The schema's allOf branch requires prior_claim (string,
    minLength=5) on modify."""
    proposal = _base_proposal("modify")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_modify_operation_passes_with_distinct_prior_claim() -> None:
    proposal = _base_proposal(
        "modify",
        claim="The new claim.",
        prior_claim="The old claim.",
    )
    validate("memory_update", proposal)
    assert check_evidence(proposal) == []


def test_deprecate_operation_requires_prior_claim() -> None:
    proposal = _base_proposal("deprecate")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_remove_operation_requires_prior_claim() -> None:
    proposal = _base_proposal("remove")  # no prior_claim
    with pytest.raises(ValidationError):
        validate("memory_update", proposal)


def test_modify_with_identical_claim_fails_semantic_validator() -> None:
    """check_evidence flags modify with claim==prior_claim (no-op).
    Schema accepts this; the semantic validator catches it."""
    proposal = _base_proposal(
        "modify",
        claim="Same claim",
        prior_claim="Same claim",
    )
    validate("memory_update", proposal)  # schema-valid
    issues = check_evidence(proposal)
    assert any(
        "claim" in i.lower() and "prior_claim" in i.lower() for i in issues
    )
```

- [ ] **Step 2: Run the new test; confirm pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_memory_proposal_examples.py -v
```

Expected: 7 passed (covers all 4 operations + 2 conditional paths + contradiction detection).

- [ ] **Step 3: Delete the old script**

```bash
git rm scripts/validate_memory_examples.py
```

- [ ] **Step 4: Update root-level docs to remove "6 CI scripts" phrasing**

This repo has `README.md` at the root (NOT `CLAUDE.md`). The "6 CI scripts" phrase appears in historical plan documents under `docs/superpowers/plans/` (PR3, PR4, PR5) — those are write-once historical records and MUST NOT be edited. Only edit live, root-level documentation.

Search for live references:

```bash
.venv/Scripts/python.exe -c "import subprocess, pathlib; [print(p) for p in pathlib.Path('.').glob('*.md')]"
grep -n "6 CI scripts\|all 6 CI scripts\|6 acceptance scripts" README.md 2>/dev/null
```

If `README.md` (or any other `*.md` file at the repo root) contains the phrase, replace it inline. Use this template for the wording:

> Old: "all 6 CI scripts green: ... validate_memory_examples ..."
>
> New: "all external acceptance scripts green: validate_schemas.py, validate_prompt_delimiters.py, fixture_smoke.py, static_sandbox_policy_check.py, check_migrations.py — plus pytest (which now covers the memory_update examples that scripts/validate_memory_examples.py used to inline)"

If `README.md` has no such reference (the grep returns no output), no doc edit is needed; proceed directly to Step 5. Do NOT create a new `CLAUDE.md` just to write the phrasing into; do NOT modify the historical plan files under `docs/superpowers/plans/` even though they contain the old phrase.

Track which root-level docs you actually changed; the Step 6 commit should `git add` only those, plus `tests/test_memory_proposal_examples.py`. The deletion of `scripts/validate_memory_examples.py` was already staged by `git rm` in Step 3.

- [ ] **Step 5: Run full suite + lint + mypy + the (now 5) acceptance scripts**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy arena
.venv/Scripts/python.exe scripts/validate_schemas.py
.venv/Scripts/python.exe scripts/validate_prompt_delimiters.py
.venv/Scripts/python.exe scripts/fixture_smoke.py
.venv/Scripts/python.exe scripts/static_sandbox_policy_check.py
.venv/Scripts/python.exe scripts/check_migrations.py
```

Expected: 360 passed (was 353, +7). All 5 remaining acceptance scripts green. ruff/format/mypy clean.

(Note: `scripts/validate_memory_examples.py` no longer exists.)

- [ ] **Step 6: Commit**

```bash
# Stage the new test file. Stage any root-level *.md files you actually
# edited in Step 4 (e.g., `git add README.md`). The deletion of
# scripts/validate_memory_examples.py is already staged from `git rm`
# in Step 3. Do NOT `git add CLAUDE.md` — that file does not exist in
# this repo.
git add tests/test_memory_proposal_examples.py
# Optionally:
#   git add README.md           # only if Step 4 modified it
git commit -m "$(cat <<'EOF'
test(memory): replace validate_memory_examples.py with proper test suite

Removes scripts/validate_memory_examples.py (a one-off inline-payload
script) and replaces it with tests/test_memory_proposal_examples.py.

The new test suite covers:
- All 4 memory_update.operation enum paths: add, modify, deprecate,
  remove.
- The schema's prior_claim conditional: required (string, minLength=5)
  on modify/deprecate/remove; optional/null on add.
- Contradiction detection: arena.memory.validator.check_evidence
  flags modify with claim == prior_claim as a no-op even though the
  schema accepts it.

7 tests; the original script's single inline assertion is now
exercised against every operation path, with both schema- and
semantic-validation coverage.

If README.md (or any other root-level *.md) referenced "6 CI scripts",
it was updated in Step 4 to list the acceptance scripts explicitly so
the count doesn't drift on every script add/remove. Historical PR plans
under docs/superpowers/plans/ were intentionally left as-is. Memory
example coverage is now under pytest, so the external acceptance-scripts
list shrinks to 5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## PR6 acceptance recap

After Task 7, the following must all be true on a clean clone:

```bash
pip install '.[dev]'
pytest --cov=arena -q
ruff check . && ruff format --check .
mypy arena
python scripts/validate_schemas.py
python scripts/validate_prompt_delimiters.py
python scripts/fixture_smoke.py
python scripts/static_sandbox_policy_check.py
python scripts/check_migrations.py

# Existing PR1-PR5 paths still work:
arena init-fixture tabular_binary_v1
arena plan tabular_binary_v1
arena run-next tabular_binary_v1 --provider stub_codex   # exits 0
arena evaluate tabular_binary_v1 --latest                # score=0.500000

# Existing PR5 path still works:
arena init-fixture tabular_binary_v1
arena research-proxy tabular_binary_v1 --provider stub_claude   # exits 0

# New PR6 paths:
arena review tabular_binary_v1 --provider stub_claude --experiment exp_0004   # exits 0
arena memory propose tabular_binary_v1 --review exp_0005                       # exits 0
arena self-improve scan tabular_binary_v1                                       # exits 0
```

PR6 acceptance is met when:

1. `arena review tabular_binary_v1 --provider stub_claude --experiment exp_0004` succeeds against a research-proxy implementation row; persists 1 scoreboard row with `<step:review>` token + valid research_review.json artifact.
2. `arena memory propose tabular_binary_v1 --review exp_0005` succeeds; writes `memory/proposals/mem_0001.json` (schema-valid); emits `memory_proposal_created` trace event with payload using only `event.schema.json`-permitted keys; creates NO scoreboard row.
3. `arena self-improve scan tabular_binary_v1` against a clean scoreboard exits 0 with zero proposals + no sentinel. Same command against a scoreboard with a blocked row produces ≥1 `self_improvement/proposals/sip_NNNN.json` + writes `SELF_IMPROVEMENT_FROZEN.md`. NO scoreboard row.
4. `tests/test_memory_proposal_examples.py` covers all 4 `operation` paths + contradiction detection. `scripts/validate_memory_examples.py` is gone; any root-level live docs (`README.md`, etc.) that referenced "6 CI scripts" have been updated. Historical plan files under `docs/superpowers/plans/` are intentionally left as-is.
5. Full suite green (360+ tests); ruff/format/mypy clean; 5 external acceptance scripts green.
6. PR5 invariants still hold: re-running `arena research-proxy` against a clean fixture still produces 4 rows; `provider_calls == COUNT(*)`; PR4 reproducibility checks fire.

This unblocks PR7 (Real Codex/Claude + close-the-loop), which composes the three PR6 commands into the full 10-step §6.2 acceptance test.

---

## Self-review

**Spec coverage** (against `docs/superpowers/specs/2026-05-02-pr6-reviews-memory-si-freeze-design.md`):

| Spec section | Task |
|---|---|
| §3.1 `arena review` | Task 2 (packet builder + CLI + 7 regression tests) |
| §3.2 `arena memory propose` (controller-only, no row, no-op fallback) | Task 4 (CLI) + Task 3 (synthesizer with no-op branch) |
| §3.3 `arena self-improve scan` (controller-only, content-hash idempotency, sentinel) | Task 5 (scan + proposal + champion_challenger) + Task 6 (freeze + CLI) |
| §3.3 freeze sentinel format (Markdown + fenced JSON) | Task 6 Step 4 (`apply_freeze` body construction) + test_self_improvement_freeze.py |
| §4.1 11 new modules | Tasks 1–6 cover all 11 |
| §4.2 stub_claude review extension | Task 1 |
| §4.2 arena/cli.py extensions | Tasks 2, 4, 6 |
| §4.4 delete `scripts/validate_memory_examples.py` | Task 7 |
| §4.5 doc updates ("6 CI scripts" → 5 + tests) | Task 7 Step 4 |
| §5 schemas (no changes needed) | confirmed; no task |
| §6 Phase enum (no changes needed) | confirmed; no task |
| §8.1 PR5 invariants preserved | enforced in Task 2's `_persist_review_row` + Tasks 4 + 6's no-row-inserted tests |
| §8.2 new invariants (no scoreboard rows for memory/SI; idempotent scan; freeze sentinel; namespace=research; diff is read-only) | tests in Tasks 3, 4, 5, 6 |
| §10 risk register (esp. trace-event payload drift) | Task 4 + Task 6 emit events with allowed key sets only; `test_self_improve_scan_emits_trace_event_with_allowed_keys` mechanically pins this |

No gaps.

**Placeholder scan:** No TBD/TODO/"implement later"/"similar to". Every step has actual code or an exact command + expected output.

**Type consistency:**

- `make_review_packet(*, competition_slug, run_id, experiment_id, task_id, review_id, subject_experiment_id, fusion_proposal_path, submission_path)` — used identically in Task 2 packet tests + the CLI Task 2 Step 7.
- `synthesize_memory_proposal(review_payload, *, proposal_id, namespace="research")` — Task 3 defines it; Task 4 calls it.
- `get_next_proposal_id(proposals_dir)` / `get_next_sip_id(proposals_dir)` — both follow the same filesystem-scan + monotonic-counter pattern.
- `Finding(kind, severity, problem, evidence_refs)` — defined in Task 5 `arena/self_improvement/scan.py`; consumed by Task 5 `proposal.py` + Task 6 `freeze.py`.
- `evaluate_freeze(findings) -> FreezeDecision` and `apply_freeze(decision, *, sentinel_path, competition_slug)` — both signatures consistent across Task 6's freeze module + the CLI subcommand.
- `Metrics(score, wall_seconds, provider_calls, waste_events)` and `ComparisonResult(score_delta, wall_seconds_delta, provider_calls_delta, waste_events_delta, regressed, reason)` — both used identically in champion_challenger tests + the freeze module's library helper boundary.
- Trace event `memory_proposal_created` payload (Task 4) and `self_improvement_scan_completed` payload (Task 6) BOTH match the spec's exact key sets and `event.schema.json`'s allowed keys.
- `experiment_type="research_proxy"` consistent in Task 2's `_persist_review_row` (PR5 invariant preserved).
- `<step:review>` token is the FIRST element of `artifact_paths` in Task 2's row insertion (matches PR5 round-4 schema-enum convention).
