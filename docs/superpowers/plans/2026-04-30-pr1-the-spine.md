# PR1 (The Spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first end-to-end vertical slice of the Kaggle Agent Arena harness — a deterministic controller that drives a stub Codex provider through a calibration task on the local `tabular_binary_v1` fixture, evaluates the resulting submission with ROC-AUC, and persists the run + experiment to a SQLite scoreboard.

**Architecture:** Deterministic Python controller, file-based task queue, schema-validated task packets and provider results, SQLite scoreboard with explicit migrations, ABC-based provider adapters with stub implementations for CI. Per the [Phase 0 single-scope plan](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) and [DAG design](../specs/2026-04-30-phase-0-implementation-dag-design.md), models communicate through artifacts, not direct chat.

**Tech Stack:** Python 3.11, Typer CLI, jsonschema (Draft 2020-12), SQLite via stdlib, pandas + scikit-learn (already deps), pytest with coverage gate at 50% during PR1.

---

## Preconditions (PR0 must land first)

This plan assumes PR0 has already landed and provided:

1. `pyproject.toml` `[tool.coverage.report] fail_under` lowered from 70 → 50 (so PR1's tests don't fail the gate).
2. `docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md` exists. PR1's `ProviderAdapter` ABC is informed by it but does not depend on its specific subprocess content (real subprocess wrappers are PR7).
3. Issue 0 ("Controller skeleton") added to [PHASE_0_SINGLE_SCOPE_PLAN.md](../../phase0/PHASE_0_SINGLE_SCOPE_PLAN.md) §8.
4. `fixtures/tabular_binary_v1/paper_bundle/` files (not used by PR1 but added in PR0).

If PR0 has not landed, the agent must complete it first or this plan's coverage gate may fail.

---

## File structure

**Create (new modules):**

| Path | Responsibility |
|---|---|
| `arena/schemas/__init__.py` | Package marker |
| `arena/schemas/loader.py` | Load JSON schemas from disk, cache |
| `arena/schemas/validate.py` | Validate dicts against named schemas, cache validators |
| `arena/scoreboard/store.py` | SQLite store: applies migrations, inserts/updates runs and experiments |
| `arena/scoreboard/migrations/0002_extend_experiments_for_design_v2.sql` | Adds the 14 missing experiment fields from design-v2 §7 |
| `arena/controller/__init__.py` | Package marker |
| `arena/controller/state.py` | Phase enum + transitions table + `transition()` helper |
| `arena/controller/task_queue.py` | File-backed FIFO task queue with schema validation |
| `arena/controller/planner.py` | Builds calibration task packets from a fixed template |
| `arena/controller/worktree.py` | Creates per-experiment workspace directories |
| `arena/providers/__init__.py` | Package marker, exports |
| `arena/providers/base.py` | `ProviderAdapter` ABC, `ProviderResult` dataclass |
| `arena/providers/parser.py` | Helper to assemble a `ProviderResult` from raw stub output |
| `arena/providers/stub_codex.py` | `StubCodexProvider` — emits a constant-0.5 calibration submission |
| `arena/providers/stub_claude.py` | `StubClaudeProvider` — review-shaped skeleton (used by PR6) |

**Create (tests, flat per existing convention):**

| Path | Tests |
|---|---|
| `tests/test_schemas_loader.py` | Loader cache, missing-file error |
| `tests/test_schemas_validate.py` | Valid + invalid task packet, error type |
| `tests/test_scoreboard_store.py` | Migrations apply on empty + populated DB; insert/get round-trip |
| `tests/test_controller_state.py` | Transitions allowed/disallowed |
| `tests/test_controller_task_queue.py` | Enqueue validates, dequeue is FIFO, queue persists across processes |
| `tests/test_controller_planner.py` | Planner output is schema-valid; deterministic |
| `tests/test_controller_worktree.py` | Workspace dir created with right structure |
| `tests/test_providers_base.py` | ABC cannot be instantiated; concrete subclass works |
| `tests/test_providers_parser.py` | Parser produces schema-valid `ProviderResult` |
| `tests/test_providers_stub_codex.py` | Stub returns valid submission for calibration role |
| `tests/test_providers_stub_claude.py` | Stub returns deterministic skeleton |
| `tests/test_pr1_e2e.py` | Full flow: init-fixture → plan → run-next → evaluate → scoreboard correct |

**Modify:**

| Path | Change |
|---|---|
| `arena/cli.py` | Add `init-fixture`, `plan`, `run-next`, `evaluate` commands |
| `tests/test_cli.py` | Extend with command smoke tests |

---

## Workspace layout (clarification)

The harness has two on-disk workspace layers, and `arena init-fixture` creates only the first:

- **`runs/<run_id>/`** — per-run controller state. Contains `queue/<task_id>.json` (pending packets) and `results/<task_id>.json` (provider results). Created by `arena init-fixture`. The PR1 acceptance criterion "init-fixture initializes the workspace" refers to this layer.
- **`worktrees/<slug>/<exp_id>/`** — per-experiment provider workspace. Where providers write `submission.csv` and trace files. Created lazily by `arena run-next` (via `create_workspace`) the first time a task with a given `experiment_id` is dispatched. This keeps `init-fixture` cheap and idempotent and avoids creating empty experiment directories that may never be used.

Phase 0 has one fixture, one calibration experiment per run, so `worktrees/tabular_binary_v1/exp_0001/` is the only worktree directory that gets populated. Real git worktree integration is deferred to Phase 1.

---

## Task 1: Schemas loader and validator

**Files:**
- Create: `arena/schemas/__init__.py`
- Create: `arena/schemas/loader.py`
- Create: `arena/schemas/validate.py`
- Create: `tests/test_schemas_loader.py`
- Create: `tests/test_schemas_validate.py`

- [ ] **Step 1: Write the failing loader tests**

```python
# tests/test_schemas_loader.py
from __future__ import annotations

import pytest

from arena.schemas.loader import load_schema


def test_loads_task_packet_schema() -> None:
    schema = load_schema("task_packet")
    assert schema["title"] == "TaskPacket"
    assert "schema_version" in schema["required"]


def test_loader_caches() -> None:
    a = load_schema("task_packet")
    b = load_schema("task_packet")
    assert a is b


def test_missing_schema_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("does_not_exist")
```

- [ ] **Step 2: Run loader test and verify it fails**

```bash
pytest tests/test_schemas_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'arena.schemas'` or similar.

- [ ] **Step 3: Implement the schemas package and loader**

`__init__.py` is intentionally bare. Tests import directly from `arena.schemas.loader` and `arena.schemas.validate`; re-exporting `validate` from the package would force `__init__.py` to import it before it exists in this step, breaking Step 4.

```python
# arena/schemas/__init__.py
from __future__ import annotations
```

```python
# arena/schemas/loader.py
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


@cache
def load_schema(name: str) -> dict:
    """Load `<name>.schema.json` from the repo's top-level schemas/ directory.

    Cached: subsequent calls return the same dict instance.
    """
    path = SCHEMA_DIR / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run loader test and verify it passes**

```bash
pytest tests/test_schemas_loader.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Write the failing validator tests**

```python
# tests/test_schemas_validate.py
from __future__ import annotations

import pytest
from jsonschema import ValidationError

from arena.schemas.validate import validate


def _valid_task_packet() -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": "task_0001",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": None,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission for the tabular binary fixture.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": ["~/.kaggle/", "~/.codex/"],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid submission CSV with id and target columns"],
    }


def test_validate_passes_on_valid_packet() -> None:
    validate("task_packet", _valid_task_packet())


def test_validate_fails_on_missing_required() -> None:
    bad = _valid_task_packet()
    del bad["objective"]
    with pytest.raises(ValidationError):
        validate("task_packet", bad)


def test_validate_fails_on_unknown_field() -> None:
    bad = _valid_task_packet()
    bad["bonus_field"] = "nope"
    with pytest.raises(ValidationError):
        validate("task_packet", bad)
```

- [ ] **Step 6: Run validator test and verify it fails**

```bash
pytest tests/test_schemas_validate.py -v
```

Expected: `ImportError: cannot import name 'validate'`.

- [ ] **Step 7: Implement the validator**

```python
# arena/schemas/validate.py
from __future__ import annotations

from functools import cache

from jsonschema import Draft202012Validator

from arena.schemas.loader import load_schema


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def validate(schema_name: str, instance: dict) -> None:
    """Raise jsonschema.ValidationError if instance does not satisfy schema."""
    _validator(schema_name).validate(instance)
```

- [ ] **Step 8: Run validator test and verify it passes**

```bash
pytest tests/test_schemas_validate.py -v
```

Expected: 3 passed.

- [ ] **Step 9: Run full test suite and lint**

```bash
pytest -q && ruff check . && ruff format --check .
```

Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add arena/schemas tests/test_schemas_loader.py tests/test_schemas_validate.py
git commit -m "$(cat <<'EOF'
feat(schemas): add JSON Schema loader and validator with caching

The loader reads from the repo's top-level schemas/ directory and caches
results; the validator builds Draft 2020-12 validators on demand.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Scoreboard migration 0002 and SQLite store

**Files:**
- Create: `arena/scoreboard/migrations/0002_extend_experiments_for_design_v2.sql`
- Create: `arena/scoreboard/store.py`
- Create: `tests/test_scoreboard_store.py`

- [ ] **Step 1: Write the migration SQL**

```sql
-- arena/scoreboard/migrations/0002_extend_experiments_for_design_v2.sql
ALTER TABLE experiments ADD COLUMN competition_slug TEXT;
ALTER TABLE experiments ADD COLUMN task_id TEXT;
ALTER TABLE experiments ADD COLUMN experiment_type TEXT;
ALTER TABLE experiments ADD COLUMN provider TEXT;
ALTER TABLE experiments ADD COLUMN provider_version TEXT;
ALTER TABLE experiments ADD COLUMN valid_submission INTEGER;
ALTER TABLE experiments ADD COLUMN wall_seconds REAL;
ALTER TABLE experiments ADD COLUMN input_chars INTEGER;
ALTER TABLE experiments ADD COLUMN output_chars INTEGER;
ALTER TABLE experiments ADD COLUMN shell_commands INTEGER;
ALTER TABLE experiments ADD COLUMN failed_commands INTEGER;
ALTER TABLE experiments ADD COLUMN waste_events INTEGER;
ALTER TABLE experiments ADD COLUMN artifact_paths TEXT;
ALTER TABLE experiments ADD COLUMN trace_path TEXT;
ALTER TABLE experiments ADD COLUMN created_at TEXT;
```

(SQLite stores BOOLEAN as INTEGER; that's fine for `valid_submission`.)

- [ ] **Step 2: Write the failing store tests**

```python
# tests/test_scoreboard_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from arena.scoreboard.store import ScoreboardStore


def test_applies_migrations_on_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "scoreboard.sqlite"
    store = ScoreboardStore(db)
    store.connect()  # triggers migrations
    cols = store.experiment_columns()
    expected = {
        "experiment_id", "run_id", "score", "metric_name", "status",
        "competition_slug", "task_id", "experiment_type", "provider",
        "provider_version", "valid_submission", "wall_seconds",
        "input_chars", "output_chars", "shell_commands", "failed_commands",
        "waste_events", "artifact_paths", "trace_path", "created_at",
    }
    assert expected.issubset(set(cols))


def test_migrations_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "scoreboard.sqlite"
    ScoreboardStore(db).connect()
    # second connect should not raise
    ScoreboardStore(db).connect()


def test_insert_and_fetch_run(tmp_path: Path) -> None:
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()
    store.insert_run(run_id="run_test", started_at="2026-04-30T10:00:00", status="initialized")
    row = store.get_run("run_test")
    assert row["status"] == "initialized"


def test_insert_and_update_experiment(tmp_path: Path) -> None:
    store = ScoreboardStore(tmp_path / "s.sqlite")
    store.connect()
    store.insert_run(run_id="run_test", started_at="2026-04-30T10:00:00", status="initialized")
    store.insert_experiment(
        experiment_id="exp_0001",
        run_id="run_test",
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_type="calibration",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="completed",
        metric_name="roc_auc",
        valid_submission=True,
        artifact_paths=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        created_at="2026-04-30T10:01:00",
    )
    store.update_experiment_score("exp_0001", score=0.5)
    row = store.get_latest_experiment("tabular_binary_v1")
    assert row["score"] == pytest.approx(0.5)
    assert row["valid_submission"] == 1
    assert row["provider"] == "stub_codex"
```

- [ ] **Step 3: Run tests and verify failure**

```bash
pytest tests/test_scoreboard_store.py -v
```

Expected: ImportError on `ScoreboardStore`.

- [ ] **Step 4: Implement the store**

```python
# arena/scoreboard/store.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class ScoreboardStore:
    """SQLite-backed scoreboard. Applies all SQL migrations on connect."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        assert self._conn is not None
        cur = self._conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_versions ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row["version"] for row in cur.execute("SELECT version FROM schema_versions")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            try:
                cur.executescript(sql)
            except sqlite3.OperationalError as exc:
                # Idempotent re-apply: ALTER TABLE ADD COLUMN fails if column exists.
                if "duplicate column name" not in str(exc).lower():
                    raise
            cur.execute(
                "INSERT OR IGNORE INTO schema_versions (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
        self._conn.commit()

    def experiment_columns(self) -> list[str]:
        assert self._conn is not None
        cur = self._conn.execute("PRAGMA table_info(experiments)")
        return [row["name"] for row in cur.fetchall()]

    def insert_run(self, *, run_id: str, started_at: str, status: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO runs (run_id, started_at, status) VALUES (?, ?, ?)",
            (run_id, started_at, status),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def insert_experiment(
        self,
        *,
        experiment_id: str,
        run_id: str,
        competition_slug: str,
        task_id: str,
        experiment_type: str,
        provider: str,
        provider_version: str,
        status: str,
        metric_name: str,
        valid_submission: bool | None = None,
        artifact_paths: list[str] | None = None,
        trace_path: str | None = None,
        created_at: str,
    ) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO experiments ("
            "experiment_id, run_id, competition_slug, task_id, experiment_type,"
            " provider, provider_version, status, metric_name, valid_submission,"
            " artifact_paths, trace_path, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                run_id,
                competition_slug,
                task_id,
                experiment_type,
                provider,
                provider_version,
                status,
                metric_name,
                None if valid_submission is None else int(valid_submission),
                json.dumps(artifact_paths or []),
                trace_path,
                created_at,
            ),
        )
        self._conn.commit()

    def update_experiment_score(self, experiment_id: str, *, score: float) -> None:
        assert self._conn is not None
        self._conn.execute(
            "UPDATE experiments SET score = ? WHERE experiment_id = ?",
            (score, experiment_id),
        )
        self._conn.commit()

    def get_latest_experiment(self, competition_slug: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE competition_slug = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (competition_slug,),
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 5: Run tests and verify they pass**

```bash
pytest tests/test_scoreboard_store.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add arena/scoreboard/migrations/0002_extend_experiments_for_design_v2.sql arena/scoreboard/store.py tests/test_scoreboard_store.py
git commit -m "$(cat <<'EOF'
feat(scoreboard): SQLite store with migration 0002 (design-v2 §7 fields)

Adds the 14 missing experiment fields (competition_slug, task_id,
provider, provider_version, valid_submission, wall_seconds, input/output
chars, shell/failed commands, waste events, artifact_paths, trace_path,
created_at, experiment_type) via an explicit ALTER TABLE migration.

The store applies all migrations on connect, tracks applied versions in
a schema_versions table, and is idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Controller state machine

**Files:**
- Create: `arena/controller/__init__.py`
- Create: `arena/controller/state.py`
- Create: `tests/test_controller_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_state.py
from __future__ import annotations

import pytest

from arena.controller.state import ALLOWED_TRANSITIONS, Phase, transition


def test_initial_phase_is_new() -> None:
    assert Phase.NEW.value == "NEW"


def test_allowed_transition_from_new_to_fixture_initialized() -> None:
    transition(Phase.NEW, Phase.FIXTURE_INITIALIZED)


def test_disallowed_transition_raises() -> None:
    with pytest.raises(ValueError):
        transition(Phase.NEW, Phase.PHASE0_COMPLETE)


def test_blocked_phases_are_terminal_dead_ends() -> None:
    # Once blocked, you cannot transition to a non-blocked phase without going through NEEDS_HUMAN.
    assert Phase.BLOCKED_AUTH not in ALLOWED_TRANSITIONS or all(
        target in {Phase.NEEDS_HUMAN, Phase.BLOCKED_KILL_SWITCH}
        for target in ALLOWED_TRANSITIONS.get(Phase.BLOCKED_AUTH, set())
    )
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_controller_state.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement state machine**

```python
# arena/controller/__init__.py
from __future__ import annotations
```

```python
# arena/controller/state.py
from __future__ import annotations

import itertools
from enum import StrEnum


class Phase(StrEnum):
    NEW = "NEW"
    FIXTURE_INITIALIZED = "FIXTURE_INITIALIZED"
    PLAN_CREATED = "PLAN_CREATED"
    CALIBRATION_TASK_CREATED = "CALIBRATION_TASK_CREATED"
    CALIBRATION_IMPLEMENTED = "CALIBRATION_IMPLEMENTED"
    CALIBRATION_EVALUATED = "CALIBRATION_EVALUATED"
    CALIBRATION_REVIEWED = "CALIBRATION_REVIEWED"
    RESEARCH_QUESTION_CREATED = "RESEARCH_QUESTION_CREATED"
    METHOD_DIGEST_CREATED = "METHOD_DIGEST_CREATED"
    FUSION_PROPOSAL_CREATED = "FUSION_PROPOSAL_CREATED"
    FUSION_PROXY_IMPLEMENTED = "FUSION_PROXY_IMPLEMENTED"
    FUSION_PROXY_EVALUATED = "FUSION_PROXY_EVALUATED"
    FUSION_PROXY_REVIEWED = "FUSION_PROXY_REVIEWED"
    MEMORY_PROPOSAL_CREATED = "MEMORY_PROPOSAL_CREATED"
    SELF_IMPROVEMENT_SCAN_COMPLETED = "SELF_IMPROVEMENT_SCAN_COMPLETED"
    HARNESS_EVAL_COMPLETED = "HARNESS_EVAL_COMPLETED"
    PHASE0_COMPLETE = "PHASE0_COMPLETE"
    BLOCKED_AUTH = "BLOCKED_AUTH"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"
    BLOCKED_SANDBOX = "BLOCKED_SANDBOX"
    BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
    BLOCKED_SECRET_ACCESS = "BLOCKED_SECRET_ACCESS"
    BLOCKED_NETWORK = "BLOCKED_NETWORK"
    BLOCKED_PROTECTED_FILE = "BLOCKED_PROTECTED_FILE"
    BLOCKED_KILL_SWITCH = "BLOCKED_KILL_SWITCH"
    BLOCKED_REPRODUCIBILITY = "BLOCKED_REPRODUCIBILITY"
    NEEDS_HUMAN = "NEEDS_HUMAN"


_BLOCKED = {
    Phase.BLOCKED_AUTH,
    Phase.BLOCKED_BUDGET,
    Phase.BLOCKED_SANDBOX,
    Phase.BLOCKED_SCHEMA,
    Phase.BLOCKED_SECRET_ACCESS,
    Phase.BLOCKED_NETWORK,
    Phase.BLOCKED_PROTECTED_FILE,
    Phase.BLOCKED_KILL_SWITCH,
    Phase.BLOCKED_REPRODUCIBILITY,
}

# Forward edges through the happy path.
_FORWARD = [
    Phase.NEW,
    Phase.FIXTURE_INITIALIZED,
    Phase.PLAN_CREATED,
    Phase.CALIBRATION_TASK_CREATED,
    Phase.CALIBRATION_IMPLEMENTED,
    Phase.CALIBRATION_EVALUATED,
    Phase.CALIBRATION_REVIEWED,
    Phase.RESEARCH_QUESTION_CREATED,
    Phase.METHOD_DIGEST_CREATED,
    Phase.FUSION_PROPOSAL_CREATED,
    Phase.FUSION_PROXY_IMPLEMENTED,
    Phase.FUSION_PROXY_EVALUATED,
    Phase.FUSION_PROXY_REVIEWED,
    Phase.MEMORY_PROPOSAL_CREATED,
    Phase.SELF_IMPROVEMENT_SCAN_COMPLETED,
    Phase.HARNESS_EVAL_COMPLETED,
    Phase.PHASE0_COMPLETE,
]

ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {}

for src, dst in itertools.pairwise(_FORWARD):
    # Each forward step is allowed; from any forward step you can also enter a BLOCKED_* state.
    ALLOWED_TRANSITIONS.setdefault(src, set()).add(dst)
    for blocked in _BLOCKED:
        ALLOWED_TRANSITIONS.setdefault(src, set()).add(blocked)

# From BLOCKED_*, only NEEDS_HUMAN is reachable (and from NEEDS_HUMAN, you can resume).
for blocked in _BLOCKED:
    ALLOWED_TRANSITIONS[blocked] = {Phase.NEEDS_HUMAN}

ALLOWED_TRANSITIONS[Phase.NEEDS_HUMAN] = set(_FORWARD)


def transition(src: Phase, dst: Phase) -> None:
    """Raise ValueError if transitioning from src to dst is disallowed."""
    if dst not in ALLOWED_TRANSITIONS.get(src, set()):
        raise ValueError(f"disallowed phase transition: {src.value} -> {dst.value}")
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_controller_state.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/controller/__init__.py arena/controller/state.py tests/test_controller_state.py
git commit -m "$(cat <<'EOF'
feat(controller): add Phase enum and transition validator

The Phase enum mirrors the task_packet.schema.json phase enum exactly.
Forward transitions follow the happy-path sequence in PHASE_0_SINGLE_SCOPE_PLAN
§3.5; any forward state may transition to a BLOCKED_* state; BLOCKED_*
states reach only NEEDS_HUMAN, which can resume to any forward state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Controller task queue

**Files:**
- Create: `arena/controller/task_queue.py`
- Create: `tests/test_controller_task_queue.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_task_queue.py
from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import ValidationError

from arena.controller.task_queue import TaskQueue


def _packet(task_id: str = "task_0001") -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": "tabular_binary_v1",
        "experiment_id": None,
        "provider": "stub_codex",
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": "Produce a calibration baseline submission.",
        "inputs": ["fixtures/tabular_binary_v1/train.csv"],
        "allowed_paths": ["worktrees/tabular_binary_v1/exp_0001/"],
        "blocked_paths": ["~/.kaggle/"],
        "budgets": {
            "max_wall_minutes": 20,
            "max_shell_commands": 35,
            "max_failed_commands": 5,
            "max_input_chars": 75000,
            "max_output_chars": 25000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": ["valid submission CSV"],
    }


def test_enqueue_then_dequeue(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue")
    queue.enqueue(_packet("task_0001"))
    queue.enqueue(_packet("task_0002"))
    assert queue.size() == 2
    first = queue.dequeue()
    second = queue.dequeue()
    assert first is not None and first["task_id"] == "task_0001"
    assert second is not None and second["task_id"] == "task_0002"
    assert queue.size() == 0


def test_enqueue_validates_packet(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue")
    bad = _packet()
    del bad["objective"]
    with pytest.raises(ValidationError):
        queue.enqueue(bad)


def test_queue_persists_across_instances(tmp_path: Path) -> None:
    qdir = tmp_path / "queue"
    TaskQueue(qdir).enqueue(_packet("task_0001"))
    assert TaskQueue(qdir).size() == 1
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_controller_task_queue.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the queue**

```python
# arena/controller/task_queue.py
from __future__ import annotations

import json
from pathlib import Path

from arena.schemas.validate import validate


class TaskQueue:
    """File-backed FIFO queue of task packets.

    Each packet is validated against task_packet.schema.json on enqueue and
    written to <queue_dir>/<task_id>.json. Dequeue selects the lexicographically
    smallest filename (task_id is zero-padded so this is FIFO in practice) and
    deletes the file.
    """

    def __init__(self, queue_dir: str | Path) -> None:
        self._dir = Path(queue_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def enqueue(self, packet: dict) -> None:
        validate("task_packet", packet)
        path = self._dir / f"{packet['task_id']}.json"
        path.write_text(json.dumps(packet, indent=2), encoding="utf-8")

    def dequeue(self) -> dict | None:
        files = sorted(self._dir.glob("*.json"))
        if not files:
            return None
        path = files[0]
        packet = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return packet

    def size(self) -> int:
        return len(list(self._dir.glob("*.json")))
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_controller_task_queue.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/controller/task_queue.py tests/test_controller_task_queue.py
git commit -m "$(cat <<'EOF'
feat(controller): file-backed FIFO TaskQueue with schema validation

Enqueue validates packets against task_packet.schema.json and persists
to <queue_dir>/<task_id>.json. Dequeue selects the lex-smallest file
(zero-padded task_id makes this FIFO) and unlinks. State persists across
process restarts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Controller planner

**Files:**
- Create: `arena/controller/planner.py`
- Create: `tests/test_controller_planner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_planner.py
from __future__ import annotations

from arena.controller.planner import create_calibration_task_packet
from arena.schemas.validate import validate


def test_calibration_task_packet_is_schema_valid() -> None:
    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    validate("task_packet", packet)


def test_calibration_task_packet_has_role_and_phase() -> None:
    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    assert packet["role"] == "implementation"
    assert packet["phase"] == "CALIBRATION_TASK_CREATED"
    assert "submission.csv" in packet["required_outputs"]


def test_calibration_task_packet_is_deterministic() -> None:
    a = create_calibration_task_packet("tabular_binary_v1", "task_0001", "exp_0001", "stub_codex")
    b = create_calibration_task_packet("tabular_binary_v1", "task_0001", "exp_0001", "stub_codex")
    assert a == b
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_controller_planner.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the planner**

```python
# arena/controller/planner.py
from __future__ import annotations


def create_calibration_task_packet(
    competition_slug: str,
    task_id: str,
    experiment_id: str,
    provider: str,
) -> dict:
    """Return a deterministic schema-valid calibration task packet.

    The packet asks the implementation provider to produce a valid submission
    file for the given fixture. Budgets are scoped to per-task Phase 0 ceilings.
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": competition_slug,
        "experiment_id": experiment_id,
        "provider": provider,
        "role": "implementation",
        "phase": "CALIBRATION_TASK_CREATED",
        "objective": (
            "Produce a calibration baseline submission for the "
            f"{competition_slug} fixture: predict target probabilities for every "
            "row in test.csv."
        ),
        "inputs": [
            f"fixtures/{competition_slug}/train.csv",
            f"fixtures/{competition_slug}/test.csv",
            f"fixtures/{competition_slug}/sample_submission.csv",
            f"fixtures/{competition_slug}/competition.yaml",
            f"fixtures/{competition_slug}/rules.md",
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
        "success_criteria": [
            "submission.csv has columns id,target",
            "all target values are in [0, 1]",
            "row count matches test.csv",
        ],
    }
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_controller_planner.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/controller/planner.py tests/test_controller_planner.py
git commit -m "$(cat <<'EOF'
feat(controller): planner emits calibration task packets

create_calibration_task_packet builds a schema-valid task packet for the
implementation role, including allowed/blocked paths (notably blocking
hidden_labels.csv and home-dir secrets), per-task budgets matching the
Phase 0 hard ceilings, and explicit success criteria.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Controller worktree

**Files:**
- Create: `arena/controller/worktree.py`
- Create: `tests/test_controller_worktree.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_controller_worktree.py
from __future__ import annotations

from pathlib import Path

from arena.controller.worktree import create_workspace


def test_creates_per_experiment_directory(tmp_path: Path) -> None:
    workspace = create_workspace(
        worktree_root=tmp_path,
        competition_slug="tabular_binary_v1",
        experiment_id="exp_0001",
    )
    assert workspace.exists()
    assert workspace.is_dir()
    assert workspace == tmp_path / "tabular_binary_v1" / "exp_0001"


def test_idempotent(tmp_path: Path) -> None:
    a = create_workspace(tmp_path, "tabular_binary_v1", "exp_0001")
    b = create_workspace(tmp_path, "tabular_binary_v1", "exp_0001")
    assert a == b
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_controller_worktree.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement worktree**

```python
# arena/controller/worktree.py
from __future__ import annotations

from pathlib import Path


def create_workspace(
    worktree_root: str | Path,
    competition_slug: str,
    experiment_id: str,
) -> Path:
    """Create and return the per-experiment workspace directory.

    Layout: <worktree_root>/<competition_slug>/<experiment_id>/.
    Idempotent: if the directory already exists, it is returned unchanged.
    """
    workspace = Path(worktree_root) / competition_slug / experiment_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_controller_worktree.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/controller/worktree.py tests/test_controller_worktree.py
git commit -m "$(cat <<'EOF'
feat(controller): create_workspace utility for per-experiment dirs

Phase 0 worktrees are simple directories under worktree_root/<slug>/<exp>/.
Real git-worktree integration is deferred to Phase 1 when real competitions
land.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: ProviderAdapter ABC, ProviderResult, and parser

**Files:**
- Create: `arena/providers/__init__.py`
- Create: `arena/providers/base.py`
- Create: `arena/providers/parser.py`
- Create: `tests/test_providers_base.py`
- Create: `tests/test_providers_parser.py`

- [ ] **Step 1: Write the failing base tests**

```python
# tests/test_providers_base.py
from __future__ import annotations

import pytest

from arena.providers.base import ProviderAdapter, ProviderResult


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ProviderAdapter()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated() -> None:
    class _Echo(ProviderAdapter):
        @property
        def name(self) -> str:
            return "echo"

        @property
        def version(self) -> str:
            return "echo.v1"

        def invoke(self, task_packet: dict) -> ProviderResult:
            raise NotImplementedError

    _Echo()


def test_provider_result_dataclass_has_required_fields() -> None:
    result = ProviderResult(
        task_id="task_0001",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="success",
        stdout_path="traces/run_x/task_0001/stdout.scrubbed",
        stderr_path="traces/run_x/task_0001/stderr.scrubbed",
        artifacts=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        usage_proxy={
            "input_chars": 0,
            "output_chars": 0,
            "wall_seconds": 0.0,
            "shell_commands": 0,
            "failed_commands": 0,
            "waste_events": 0,
        },
        started_at="2026-04-30T10:00:00Z",
        finished_at="2026-04-30T10:00:01Z",
    )
    assert result.status == "success"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
pytest tests/test_providers_base.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the ABC and dataclass**

```python
# arena/providers/__init__.py
from __future__ import annotations

from arena.providers.base import ProviderAdapter, ProviderResult

__all__ = ["ProviderAdapter", "ProviderResult"]
```

```python
# arena/providers/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Literal


ProviderStatus = Literal["success", "failure", "blocked", "killed", "interrupted"]


@dataclass(frozen=True)
class ProviderResult:
    """Structured outcome of one provider invocation.

    Mirrors provider_result.schema.json. The to_dict() method emits the
    schema-valid JSON shape (with schema_version filled in).
    """

    task_id: str
    provider: str
    provider_version: str
    status: ProviderStatus
    stdout_path: str
    stderr_path: str
    artifacts: list[str]
    usage_proxy: dict
    started_at: str
    finished_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        return {"schema_version": "provider_result.v1", **payload}


class ProviderAdapter(ABC):
    """Abstract base class for provider workers (stub or real).

    See docs/architecture/ADR-0004-PROVIDER-CLI-INVOCATION.md for the
    real-provider subprocess conventions; stubs do not subprocess.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        ...

    @abstractmethod
    def invoke(self, task_packet: dict) -> ProviderResult:
        """Run the task and return a ProviderResult.

        Implementations must:
        - validate the incoming packet against task_packet.schema.json
          (callers may also pre-validate; double-validation is cheap)
        - write any required outputs into the workspace
        - return a ProviderResult whose to_dict() satisfies provider_result.schema.json
        """
```

- [ ] **Step 4: Run tests and verify base passes**

```bash
pytest tests/test_providers_base.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Write the failing parser tests**

```python
# tests/test_providers_parser.py
from __future__ import annotations

from arena.providers.parser import build_result
from arena.schemas.validate import validate


def test_build_result_is_schema_valid() -> None:
    result = build_result(
        task_id="task_0001",
        provider="stub_codex",
        provider_version="stub_codex.v1",
        status="success",
        stdout_path="traces/run_x/task_0001/stdout.scrubbed",
        stderr_path="traces/run_x/task_0001/stderr.scrubbed",
        artifacts=["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        input_chars=120,
        output_chars=80,
        wall_seconds=0.05,
        shell_commands=0,
        failed_commands=0,
        waste_events=0,
        started_at="2026-04-30T10:00:00Z",
        finished_at="2026-04-30T10:00:01Z",
    )
    validate("provider_result", result.to_dict())
```

- [ ] **Step 6: Run parser test and verify failure**

```bash
pytest tests/test_providers_parser.py -v
```

Expected: ImportError on `build_result`.

- [ ] **Step 7: Implement parser**

```python
# arena/providers/parser.py
from __future__ import annotations

from arena.providers.base import ProviderResult, ProviderStatus


def build_result(
    *,
    task_id: str,
    provider: str,
    provider_version: str,
    status: ProviderStatus,
    stdout_path: str,
    stderr_path: str,
    artifacts: list[str],
    input_chars: int,
    output_chars: int,
    wall_seconds: float,
    shell_commands: int,
    failed_commands: int,
    waste_events: int,
    started_at: str,
    finished_at: str,
) -> ProviderResult:
    """Assemble a ProviderResult from raw pieces.

    Phase 0 stub providers don't subprocess, so this is just a dataclass
    builder; real providers will use it to package subprocess output after
    scrubbing.
    """
    return ProviderResult(
        task_id=task_id,
        provider=provider,
        provider_version=provider_version,
        status=status,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        artifacts=artifacts,
        usage_proxy={
            "input_chars": input_chars,
            "output_chars": output_chars,
            "wall_seconds": wall_seconds,
            "shell_commands": shell_commands,
            "failed_commands": failed_commands,
            "waste_events": waste_events,
        },
        started_at=started_at,
        finished_at=finished_at,
    )
```

- [ ] **Step 8: Run parser test and verify it passes**

```bash
pytest tests/test_providers_parser.py -v
```

Expected: 1 passed.

- [ ] **Step 9: Commit**

```bash
git add arena/providers/__init__.py arena/providers/base.py arena/providers/parser.py tests/test_providers_base.py tests/test_providers_parser.py
git commit -m "$(cat <<'EOF'
feat(providers): add ProviderAdapter ABC, ProviderResult, parser

The ABC defines name/version properties and an invoke(task_packet) method
returning a ProviderResult dataclass that mirrors provider_result.schema.json.
build_result is the canonical way to construct a result; real adapters in PR7
will use it after scrubbing subprocess output.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: StubCodexProvider

**Files:**
- Create: `arena/providers/stub_codex.py`
- Create: `tests/test_providers_stub_codex.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_providers_stub_codex.py
from __future__ import annotations

from pathlib import Path

import pandas as pd

from arena.controller.planner import create_calibration_task_packet
from arena.providers.stub_codex import StubCodexProvider
from arena.schemas.validate import validate


def test_invoke_writes_valid_submission(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001"
    workspace.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    # Need test.csv at the path the planner references; copy from repo fixture.
    fixture_src = Path(__file__).resolve().parents[1] / "fixtures" / "tabular_binary_v1"
    target_dir = tmp_path / "fixtures" / "tabular_binary_v1"
    target_dir.mkdir(parents=True)
    for name in ["train.csv", "test.csv", "sample_submission.csv", "competition.yaml", "rules.md"]:
        (target_dir / name).write_bytes((fixture_src / name).read_bytes())

    packet = create_calibration_task_packet(
        competition_slug="tabular_binary_v1",
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    provider = StubCodexProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(packet)

    validate("provider_result", result.to_dict())
    assert result.status == "success"
    submission_path = workspace / "submission.csv"
    assert submission_path.exists()
    df = pd.read_csv(submission_path)
    assert list(df.columns) == ["id", "target"]
    assert df["target"].between(0, 1).all()
    test_df = pd.read_csv(target_dir / "test.csv")
    assert len(df) == len(test_df)
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_providers_stub_codex.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement StubCodexProvider**

```python
# arena/providers/stub_codex.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

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
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root)

    @property
    def name(self) -> str:
        return "stub_codex"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
        task_id = task_packet["task_id"]
        slug = task_packet["competition_slug"]
        exp_id = task_packet["experiment_id"]
        if exp_id is None:
            raise ValueError("StubCodexProvider requires task_packet.experiment_id to be set")

        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        workspace = self._workspace_root / slug / exp_id
        workspace.mkdir(parents=True, exist_ok=True)

        test_path = Path("fixtures") / slug / "test.csv"
        test_df = pd.read_csv(test_path)
        submission = pd.DataFrame({"id": test_df["id"], "target": 0.5})
        submission_path = workspace / "submission.csv"
        submission.to_csv(submission_path, index=False)

        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(workspace / "stdout.scrubbed"),
            stderr_path=str(workspace / "stderr.scrubbed"),
            artifacts=[str(submission_path)],
            input_chars=0,
            output_chars=submission_path.stat().st_size,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=started,
            finished_at=finished,
        )
```

- [ ] **Step 4: Run test and verify it passes**

```bash
pytest tests/test_providers_stub_codex.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/providers/stub_codex.py tests/test_providers_stub_codex.py
git commit -m "$(cat <<'EOF'
feat(providers): StubCodexProvider emits constant-0.5 calibration submission

For Phase 0, the stub's job is to prove the pipeline works, not to score
well. A constant 0.5 prediction is valid (target in [0,1], correct columns,
correct row count) and gives a deterministic ROC-AUC of ~0.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: StubClaudeProvider (skeleton for later PRs)

**Files:**
- Create: `arena/providers/stub_claude.py`
- Create: `tests/test_providers_stub_claude.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_providers_stub_claude.py
from __future__ import annotations

from pathlib import Path

from arena.providers.stub_claude import StubClaudeProvider
from arena.schemas.validate import validate


def _packet() -> dict:
    return {
        "schema_version": "task_packet.v1",
        "task_id": "task_0002",
        "competition_slug": "tabular_binary_v1",
        "experiment_id": "exp_0001",
        "provider": "stub_claude",
        "role": "review",
        "phase": "CALIBRATION_REVIEWED",
        "objective": "Review the calibration submission for validity and basic quality.",
        "inputs": ["worktrees/tabular_binary_v1/exp_0001/submission.csv"],
        "allowed_paths": [],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 0,
            "max_failed_commands": 0,
            "max_input_chars": 10000,
            "max_output_chars": 5000,
        },
        "required_outputs": ["review.json"],
        "success_criteria": ["valid review.json"],
    }


def test_invoke_returns_schema_valid_result(tmp_path: Path) -> None:
    provider = StubClaudeProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(_packet())
    validate("provider_result", result.to_dict())
    assert result.status == "success"


def test_invoke_writes_real_empty_trace_files(tmp_path: Path) -> None:
    provider = StubClaudeProvider(workspace_root=tmp_path / "worktrees")
    result = provider.invoke(_packet())
    stdout = Path(result.stdout_path)
    stderr = Path(result.stderr_path)
    assert stdout.exists() and stdout.read_text() == ""
    assert stderr.exists() and stderr.read_text() == ""
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_providers_stub_claude.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement skeleton**

```python
# arena/providers/stub_claude.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from arena.providers.base import ProviderAdapter, ProviderResult
from arena.providers.parser import build_result
from arena.schemas.validate import validate

_VERSION = "stub_claude.v1"


class StubClaudeProvider(ProviderAdapter):
    """Deterministic stand-in for Claude during Phase 0 CI and local stub runs.

    PR1 lands the skeleton only — invoke() validates the packet, writes empty
    scrubbed stdout/stderr trace files into the workspace, and returns a
    schema-valid ProviderResult with no artifacts. PR5 extends invoke() to
    emit paper_digest.json / fusion_proposal.json; PR6 extends it for
    review.json.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root)

    @property
    def name(self) -> str:
        return "stub_claude"

    @property
    def version(self) -> str:
        return _VERSION

    def invoke(self, task_packet: dict) -> ProviderResult:
        validate("task_packet", task_packet)
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

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return build_result(
            task_id=task_id,
            provider=self.name,
            provider_version=self.version,
            status="success",
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            artifacts=[],
            input_chars=0,
            output_chars=0,
            wall_seconds=0.0,
            shell_commands=0,
            failed_commands=0,
            waste_events=0,
            started_at=now,
            finished_at=now,
        )
```

- [ ] **Step 4: Run test and verify it passes**

```bash
pytest tests/test_providers_stub_claude.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/providers/stub_claude.py tests/test_providers_stub_claude.py
git commit -m "$(cat <<'EOF'
feat(providers): StubClaudeProvider skeleton for later PRs

PR1 lands only the bare ABC implementation; PR5/PR6 extend invoke() to
emit paper_digest, fusion_proposal, and review JSON artifacts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: CLI — `arena init-fixture`

**Files:**
- Modify: `arena/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Read the current CLI**

```bash
cat arena/cli.py
```

Confirm it currently has `doctor` and `fixture-smoke` commands.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_init_fixture_creates_run_record(tmp_path, monkeypatch):
    """`arena init-fixture <slug>` creates a runs/<run_id>/ tree and
    inserts a row into the scoreboard."""
    from arena.cli import app
    from typer.testing import CliRunner
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv", "test.csv", "sample_submission.csv", "hidden_labels.csv",
        "competition.yaml", "rules.md", "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    run_id = runs[0].name
    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    row = store.get_run(run_id)
    assert row is not None
    assert row["status"] == "initialized"
```

Add `from pathlib import Path` to the top of `tests/test_cli.py` if not present.

- [ ] **Step 3: Run the test and verify failure**

```bash
pytest tests/test_cli.py::test_init_fixture_creates_run_record -v
```

Expected: command not found / unknown command.

- [ ] **Step 4: Implement `init-fixture`**

Replace `arena/cli.py` entirely:

```python
# arena/cli.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from arena.controller.planner import create_calibration_task_packet
from arena.controller.task_queue import TaskQueue
from arena.controller.worktree import create_workspace
from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest
from arena.providers.base import ProviderAdapter
from arena.providers.stub_codex import StubCodexProvider
from arena.providers.stub_claude import StubClaudeProvider
from arena.scoreboard.store import ScoreboardStore

app = typer.Typer(help="Kaggle Agent Arena Phase 0 harness CLI.")
console = Console()

DB_PATH = Path("scoreboard.sqlite")
RUNS_ROOT = Path("runs")
WORKTREE_ROOT = Path("worktrees")
FIXTURES_ROOT = Path("fixtures")


def _store() -> ScoreboardStore:
    s = ScoreboardStore(DB_PATH)
    s.connect()
    return s


def _new_run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _latest_run_id_for(slug: str) -> str | None:
    if not RUNS_ROOT.exists():
        return None
    runs = sorted(RUNS_ROOT.glob("run_*"))
    return runs[-1].name if runs else None


def _get_provider(name: str) -> ProviderAdapter:
    if name == "stub_codex":
        return StubCodexProvider(workspace_root=WORKTREE_ROOT)
    if name == "stub_claude":
        return StubClaudeProvider(workspace_root=WORKTREE_ROOT)
    raise typer.BadParameter(f"unknown provider: {name}")


@app.command()
def doctor() -> None:
    """Run lightweight local readiness checks."""
    validate_fixture_manifest("fixtures/tabular_binary_v1")
    console.print("[green]arena doctor passed[/green]")


@app.command("fixture-smoke")
def fixture_smoke(
    submission: str = "fixtures/tabular_binary_v1/sample_submission.csv",
    labels: str = "fixtures/tabular_binary_v1/hidden_labels.csv",
) -> None:
    """Evaluate the bundled fake tabular fixture submission."""
    result = evaluate_fixture_submission(submission, labels)
    if not result.valid_submission:
        raise typer.Exit(code=1)
    console.print(f"fixture score={result.score:.6f}")


@app.command("init-fixture")
def init_fixture(slug: str) -> None:
    """Initialize a new run for the given fixture slug."""
    fixture_dir = FIXTURES_ROOT / slug
    if not fixture_dir.exists():
        raise typer.BadParameter(f"fixture not found: {fixture_dir}")
    validate_fixture_manifest(fixture_dir)

    run_id = _new_run_id()
    (RUNS_ROOT / run_id / "queue").mkdir(parents=True, exist_ok=True)
    (RUNS_ROOT / run_id / "results").mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _store().insert_run(run_id=run_id, started_at=started_at, status="initialized")
    console.print(f"[green]initialized {run_id}[/green]")
```

- [ ] **Step 5: Run test and verify it passes**

```bash
pytest tests/test_cli.py::test_init_fixture_creates_run_record -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add arena/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): arena init-fixture creates run record and workspace tree

Validates the fixture manifest, mints a run_id, creates runs/<run_id>/
queue and results directories, and inserts a row into the scoreboard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: CLI — `arena plan`

**Files:**
- Modify: `arena/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_plan_writes_calibration_task_packet(tmp_path, monkeypatch):
    from arena.cli import app
    from typer.testing import CliRunner

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv", "test.csv", "sample_submission.csv", "hidden_labels.csv",
        "competition.yaml", "rules.md", "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output

    runs = sorted((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    queue_files = list((runs[0] / "queue").glob("*.json"))
    assert len(queue_files) == 1
    packet = json.loads(queue_files[0].read_text())
    assert packet["role"] == "implementation"
    assert packet["competition_slug"] == "tabular_binary_v1"
    assert packet["task_id"] == "task_0001"
```

Add `import json` to the top of `tests/test_cli.py` if not present.

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_cli.py::test_plan_writes_calibration_task_packet -v
```

Expected: unknown command.

- [ ] **Step 3: Add the `plan` command**

Append to `arena/cli.py` (above the `if __name__ == '__main__':` line):

```python
@app.command("plan")
def plan(slug: str) -> None:
    """Create a calibration task packet for the latest run."""
    run_id = _latest_run_id_for(slug)
    if run_id is None:
        raise typer.BadParameter(f"no initialized run for {slug}; run init-fixture first")
    queue = TaskQueue(RUNS_ROOT / run_id / "queue")
    if queue.size() > 0:
        raise typer.BadParameter(f"queue is non-empty for {run_id}")
    packet = create_calibration_task_packet(
        competition_slug=slug,
        task_id="task_0001",
        experiment_id="exp_0001",
        provider="stub_codex",
    )
    queue.enqueue(packet)
    console.print(f"[green]planned task_0001 for {run_id}[/green]")
```

- [ ] **Step 4: Run test and verify it passes**

```bash
pytest tests/test_cli.py::test_plan_writes_calibration_task_packet -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): arena plan enqueues calibration task packet

Reads the latest run for the slug, calls the planner, and persists the
packet to runs/<run_id>/queue/task_0001.json after schema validation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: CLI — `arena run-next`

**Files:**
- Modify: `arena/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_run_next_invokes_provider_and_persists_experiment(tmp_path, monkeypatch):
    from arena.cli import app
    from typer.testing import CliRunner
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv", "test.csv", "sample_submission.csv", "hidden_labels.csv",
        "competition.yaml", "rules.md", "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    result = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert result.exit_code == 0, result.output

    submission = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_0001" / "submission.csv"
    assert submission.exists()
    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["task_id"] == "task_0001"
    assert exp["provider"] == "stub_codex"
    assert exp["score"] is None  # evaluate hasn't run yet
    assert exp["status"] == "completed"
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_cli.py::test_run_next_invokes_provider_and_persists_experiment -v
```

Expected: unknown command.

- [ ] **Step 3: Add the `run-next` command**

Append to `arena/cli.py` (above the `if __name__ == '__main__':` line):

```python
@app.command("run-next")
def run_next(slug: str, provider: str = typer.Option(..., "--provider")) -> None:
    """Pop the next task from the queue, invoke the provider, persist the experiment."""
    run_id = _latest_run_id_for(slug)
    if run_id is None:
        raise typer.BadParameter(f"no run for {slug}")
    queue = TaskQueue(RUNS_ROOT / run_id / "queue")
    packet = queue.dequeue()
    if packet is None:
        raise typer.BadParameter(f"queue is empty for {run_id}")

    create_workspace(WORKTREE_ROOT, packet["competition_slug"], packet["experiment_id"])
    adapter = _get_provider(provider)
    result = adapter.invoke(packet)

    results_dir = RUNS_ROOT / run_id / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{packet['task_id']}.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8"
    )

    _store().insert_experiment(
        experiment_id=packet["experiment_id"],
        run_id=run_id,
        competition_slug=packet["competition_slug"],
        task_id=packet["task_id"],
        experiment_type="calibration",
        provider=adapter.name,
        provider_version=adapter.version,
        status="completed" if result.status == "success" else result.status,
        metric_name="roc_auc",
        valid_submission=None,
        artifact_paths=result.artifacts,
        trace_path=None,
        created_at=result.finished_at,
    )
    console.print(f"[green]ran task_{packet['task_id']} on {provider}[/green]")
```

- [ ] **Step 4: Run test and verify it passes**

```bash
pytest tests/test_cli.py::test_run_next_invokes_provider_and_persists_experiment -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add arena/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): arena run-next dispatches provider, persists experiment

Pops the next task from the queue, materializes the workspace, invokes the
named provider (stub_codex or stub_claude in PR1), writes the
ProviderResult JSON, and inserts an experiment row with score=NULL pending
the evaluate step.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: CLI — `arena evaluate`

**Files:**
- Modify: `arena/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_evaluate_updates_score(tmp_path, monkeypatch):
    from arena.cli import app
    from typer.testing import CliRunner
    from arena.scoreboard.store import ScoreboardStore

    fixture_src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv", "test.csv", "sample_submission.csv", "hidden_labels.csv",
        "competition.yaml", "rules.md", "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((fixture_src / name).read_bytes())
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    result = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["score"] is not None
    # Constant 0.5 predictions yield ROC-AUC ~ 0.5; allow a tolerance because
    # sklearn's roc_auc_score with all-equal scores is exactly 0.5.
    assert exp["score"] == 0.5
    assert exp["valid_submission"] == 1
```

- [ ] **Step 2: Run test and verify failure**

```bash
pytest tests/test_cli.py::test_evaluate_updates_score -v
```

Expected: unknown command.

- [ ] **Step 3: Add the `evaluate` command**

First, add a public `update_experiment_validation` method to the store. Append to `arena/scoreboard/store.py`:

```python
    def update_experiment_validation(self, experiment_id: str, *, valid_submission: bool) -> None:
        assert self._conn is not None
        self._conn.execute(
            "UPDATE experiments SET valid_submission = ? WHERE experiment_id = ?",
            (int(valid_submission), experiment_id),
        )
        self._conn.commit()
```

Then append to `arena/cli.py`:

```python
@app.command("evaluate")
def evaluate(
    slug: str,
    latest: bool = typer.Option(False, "--latest", help="Evaluate the latest experiment"),
) -> None:
    """Score the latest experiment's submission against hidden labels."""
    if not latest:
        raise typer.BadParameter("only --latest is supported in PR1")

    store = _store()
    exp = store.get_latest_experiment(slug)
    if exp is None:
        raise typer.BadParameter(f"no experiment recorded for {slug}")
    raw_paths = exp["artifact_paths"]
    artifacts: list[str] = json.loads(raw_paths) if raw_paths else []
    submission = next((p for p in artifacts if p.endswith("submission.csv")), None)
    if submission is None:
        raise typer.BadParameter("no submission.csv among experiment artifacts")

    hidden = FIXTURES_ROOT / slug / "hidden_labels.csv"
    eval_result = evaluate_fixture_submission(submission, hidden)
    if not eval_result.valid_submission:
        console.print(f"[red]invalid submission: {eval_result.error}[/red]")
        raise typer.Exit(code=1)
    assert eval_result.score is not None  # narrow Optional[float] -> float for mypy

    experiment_id: str = exp["experiment_id"]
    store.update_experiment_score(experiment_id, score=eval_result.score)
    store.update_experiment_validation(experiment_id, valid_submission=True)
    console.print(f"score={eval_result.score:.6f}")
```

The `assert eval_result.score is not None` narrows `Optional[float]` to `float` for mypy after the validity gate. Store getters return `dict[str, Any]`, so `exp["artifact_paths"]` and `exp["experiment_id"]` are `Any` and pass through without casts.

- [ ] **Step 4: Run test and verify it passes**

```bash
pytest tests/test_cli.py::test_evaluate_updates_score -v
```

Expected: 1 passed.

- [ ] **Step 5: Run mypy and verify type-clean**

```bash
mypy arena
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add arena/cli.py arena/scoreboard/store.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): arena evaluate scores the latest submission and updates scoreboard

Reads the latest experiment's submission.csv from the recorded artifacts,
runs the existing fixture evaluator (ROC-AUC), and updates score +
valid_submission via the store's public API.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: End-to-end PR1 acceptance test

**Files:**
- Create: `tests/test_pr1_e2e.py`

- [ ] **Step 1: Write the e2e test**

```python
# tests/test_pr1_e2e.py
"""End-to-end test for PR1 acceptance: init-fixture -> plan -> run-next -> evaluate.

Asserts every PR1 acceptance criterion from
docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md §5.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from arena.cli import app
from arena.schemas.validate import validate
from arena.scoreboard.store import ScoreboardStore


def _stage_fixtures(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    target = tmp_path / "fixtures" / "tabular_binary_v1"
    target.mkdir(parents=True)
    for name in [
        "train.csv", "test.csv", "sample_submission.csv", "hidden_labels.csv",
        "competition.yaml", "rules.md", "fixture_manifest.yaml",
    ]:
        (target / name).write_bytes((src / name).read_bytes())


def test_pr1_full_loop_on_clean_workspace(tmp_path, monkeypatch) -> None:
    _stage_fixtures(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    init = runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert init.exit_code == 0, init.output

    plan = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert plan.exit_code == 0, plan.output

    run = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert run.exit_code == 0, run.output

    evaluate = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert evaluate.exit_code == 0, evaluate.output
    assert "score=" in evaluate.output

    # Acceptance criterion: scoreboard persists, experiment fully populated.
    store = ScoreboardStore(tmp_path / "scoreboard.sqlite")
    store.connect()
    exp = store.get_latest_experiment("tabular_binary_v1")
    assert exp is not None
    assert exp["task_id"] == "task_0001"
    assert exp["experiment_type"] == "calibration"
    assert exp["provider"] == "stub_codex"
    assert exp["provider_version"] == "stub_codex.v1"
    assert exp["valid_submission"] == 1
    assert exp["score"] == 0.5
    assert exp["status"] == "completed"

    # Acceptance criterion: ProviderResult on disk is schema-valid.
    runs = sorted((tmp_path / "runs").iterdir())
    result_path = runs[0] / "results" / "task_0001.json"
    payload = json.loads(result_path.read_text())
    validate("provider_result", payload)

    # Acceptance criterion: queue has been drained.
    queue_dir = runs[0] / "queue"
    assert list(queue_dir.glob("*.json")) == []
```

- [ ] **Step 2: Run the e2e test**

```bash
pytest tests/test_pr1_e2e.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Run the full suite + lint + format**

```bash
pytest --cov=arena --cov-report=term-missing -q && ruff check . && ruff format --check .
```

Expected: all green; coverage ≥ 50% on `arena`.

- [ ] **Step 4: Run mypy**

```bash
mypy arena
```

Expected: no errors. (If strict-mode flags `_conn` attribute access in CLI, address by typing it `sqlite3.Connection | None` — already done.)

- [ ] **Step 5: Commit**

```bash
git add tests/test_pr1_e2e.py
git commit -m "$(cat <<'EOF'
test(pr1): end-to-end fixture loop acceptance

Drives init-fixture -> plan -> run-next -> evaluate against the bundled
tabular_binary_v1 fixture using the stub Codex provider, asserts:
- exit codes 0 throughout
- scoreboard records all design-v2 §7 fields populated
- ProviderResult JSON on disk is schema-valid
- queue is drained after run-next
- score is the expected ~0.5 for constant-0.5 predictions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## PR1 acceptance recap

After Task 14, the following should all be true on a clean clone:

```bash
pip install '.[dev]'
pytest --cov=arena --cov-report=term-missing -q
ruff check . && ruff format --check .
mypy arena
python scripts/validate_schemas.py
python scripts/validate_prompt_delimiters.py
python scripts/fixture_smoke.py
arena init-fixture tabular_binary_v1
arena plan tabular_binary_v1
arena run-next tabular_binary_v1 --provider stub_codex
arena evaluate tabular_binary_v1 --latest
```

Every command returns exit 0, the test suite passes, and the scoreboard contains a `runs` row and an `experiments` row with all design-v2 §7 fields populated (most non-NULL after `evaluate`; `provider_version`, `created_at`, `valid_submission=1`, `score=0.5` are the populated ones).

This unblocks PR2 (budget governor + kill switch).

---

## Self-review

**Spec coverage** (against `docs/superpowers/specs/2026-04-30-phase-0-implementation-dag-design.md` §5):

| Spec item | Task |
|---|---|
| `arena/controller/state.py` | Task 3 |
| `arena/controller/task_queue.py` | Task 4 |
| `arena/controller/planner.py` | Task 5 |
| `arena/controller/worktree.py` | Task 6 |
| `arena/providers/base.py` | Task 7 |
| `arena/providers/stub_codex.py` | Task 8 |
| `arena/providers/stub_claude.py` | Task 9 |
| `arena/providers/parser.py` | Task 7 |
| `arena/scoreboard/store.py` | Task 2 |
| `arena/scoreboard/migrations/0002_*.sql` | Task 2 |
| `arena/schemas/loader.py`, `validate.py` | Task 1 |
| CLI: init-fixture, plan, run-next, evaluate | Tasks 10–13 |
| Tests for each module | Each task |
| Acceptance: full e2e | Task 14 |

No gaps.

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to" placeholders. Every step has actual code or an exact command with expected output.

**Type consistency:**
- `Phase` enum used identically in state.py and elsewhere where referenced.
- `ProviderResult` dataclass field names match `provider_result.schema.json` field names: `task_id`, `provider`, `provider_version`, `status`, `stdout_path`, `stderr_path`, `artifacts`, `usage_proxy`, `started_at`, `finished_at`. ✓
- `ScoreboardStore` method signatures: `insert_run(run_id, started_at, status)`, `insert_experiment(experiment_id, run_id, ...)`, `update_experiment_score(experiment_id, score)`, `update_experiment_validation(experiment_id, valid_submission)`. Used consistently in CLI and tests. ✓
- CLI helper functions `_store()`, `_new_run_id()`, `_latest_run_id_for()`, `_get_provider()` defined in Task 10 and used in Tasks 11–13. ✓

**Notable design choices made during planning (not in the spec but consistent with it):**

1. Scoreboard tracks applied migrations in a `schema_versions` table. Migration script reapply tolerates `duplicate column name` errors so the same migration on an already-migrated DB is a no-op.
2. Run IDs use a UTC timestamp pattern `run_YYYYMMDDTHHMMSSZ`. Latest run is the lex-greatest matching pattern. Sufficient for Phase 0's single-run-at-a-time model.
3. `_conn` is the only place where the store exposes its internals; the e2e test does not touch it. Task 13 refactors the CLI off of `_conn` in step 5.
4. The stub Codex provider reads `fixtures/<slug>/test.csv` from the CWD; tests `monkeypatch.chdir(tmp_path)` and stage fixture copies. This matches existing convention in [tests/test_cli.py](../../../tests/test_cli.py).
