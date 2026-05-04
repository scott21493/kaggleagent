from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into non-empty trimmed statements on `;`.

    Phase 0 migrations are ALTER TABLE / CREATE TABLE only — no triggers,
    no string literals containing `;`. Naive split is sufficient.
    """
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


class ScoreboardStore:
    """SQLite-backed scoreboard. Applies all SQL migrations on connect.

    Each `*.sql` file in `migrations/` is run statement-by-statement in a
    single transaction. Per-statement `duplicate column name` errors are
    tolerated so a partially-applied migration can be safely re-run. The
    `schema_versions` row is only written after every statement in the file
    has run (or been tolerated), so a partial application never falsely
    marks the migration applied.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._apply_migrations()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ScoreboardStore.connect() must be called before use")
        return self._conn

    def _apply_migrations(self) -> None:
        conn = self._require_conn()
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_versions ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row["version"] for row in cur.execute("SELECT version FROM schema_versions")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in applied:
                continue
            for stmt in _split_sql_statements(path.read_text(encoding="utf-8")):
                try:
                    cur.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            cur.execute(
                "INSERT OR IGNORE INTO schema_versions (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
        conn.commit()

    def experiment_columns(self) -> list[str]:
        conn = self._require_conn()
        cur = conn.execute("PRAGMA table_info(experiments)")
        return [row["name"] for row in cur.fetchall()]

    def insert_run(self, *, run_id: str, started_at: str, status: str) -> None:
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO runs (run_id, started_at, status) VALUES (?, ?, ?)",
            (run_id, started_at, status),
        )
        conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
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
        # New in PR2:
        input_chars: int = 0,
        output_chars: int = 0,
        wall_seconds: float = 0.0,
        shell_commands: int = 0,
        failed_commands: int = 0,
        waste_events: int = 0,
    ) -> None:
        """Insert a new experiment row.

        `artifact_paths` is JSON-encoded into the TEXT column; readers must
        `json.loads()` the result. `created_at` should be ISO-8601 so that
        `get_latest_experiment` orders correctly. Usage-proxy fields default
        to zero for callers (e.g. tests) that don't care about budget data.
        """
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO experiments ("
            "experiment_id, run_id, competition_slug, task_id, experiment_type,"
            " provider, provider_version, status, metric_name, valid_submission,"
            " artifact_paths, trace_path, created_at,"
            " input_chars, output_chars, wall_seconds,"
            " shell_commands, failed_commands, waste_events"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                input_chars,
                output_chars,
                wall_seconds,
                shell_commands,
                failed_commands,
                waste_events,
            ),
        )
        conn.commit()

    def update_experiment_score(self, experiment_id: str, *, score: float) -> None:
        conn = self._require_conn()
        conn.execute(
            "UPDATE experiments SET score = ? WHERE experiment_id = ?",
            (score, experiment_id),
        )
        conn.commit()

    def update_experiment_validation(self, experiment_id: str, *, valid_submission: bool) -> None:
        conn = self._require_conn()
        conn.execute(
            "UPDATE experiments SET valid_submission = ? WHERE experiment_id = ?",
            (int(valid_submission), experiment_id),
        )
        conn.commit()

    def get_latest_experiment(self, competition_slug: str) -> dict[str, Any] | None:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT * FROM experiments WHERE competition_slug = ? "
            "ORDER BY created_at DESC, experiment_id DESC LIMIT 1",
            (competition_slug,),
        ).fetchone()
        return dict(row) if row else None

    def get_run_usage_totals(self, competition_slug: str, run_id: str) -> dict[str, Any]:
        """Sum the usage_proxy fields across all experiments belonging to
        `run_id` for `competition_slug`. Used to seed
        `BudgetGovernor.accumulators` and drive `arena budget status`.

        Filtering by run_id is important: a fresh `arena init-fixture`
        starts a new run with zero accumulators, even if the same slug had
        prior runs in the scoreboard. Without the run_id filter, ceilings
        would carry over and `run-next` could trip caps prematurely.

        Returns 0/0.0 for every field when there are no experiments yet.
        """
        # NOTE: This SQL uses LIKE prefix matching ('codex%', 'claude%')
        # while BudgetGovernor._is_codex/_is_claude (governor.py) uses
        # substring matching ("codex" in name). For the PR2 stub providers
        # (stub_codex, stub_claude) both produce identical counts, but a
        # future provider name like "mock_codex_v2" would be classified
        # differently by the two paths. A future provider-family
        # registry should unify both call sites — see governor.py
        # TODO(PR8+).
        conn = self._require_conn()
        row = conn.execute(
            "SELECT "
            " COALESCE(SUM(CASE WHEN provider LIKE 'stub_codex%' OR provider LIKE 'codex%' THEN 1 ELSE 0 END), 0) AS codex_calls,"
            " COALESCE(SUM(CASE WHEN provider LIKE 'stub_claude%' OR provider LIKE 'claude%' THEN 1 ELSE 0 END), 0) AS claude_calls,"
            " COALESCE(COUNT(*), 0) AS provider_calls,"
            " COALESCE(SUM(input_chars), 0) AS input_chars,"
            " COALESCE(SUM(output_chars), 0) AS output_chars,"
            " COALESCE(SUM(wall_seconds), 0.0) AS wall_seconds,"
            " COALESCE(SUM(waste_events), 0) AS waste_events"
            " FROM experiments WHERE competition_slug = ? AND run_id = ?",
            (competition_slug, run_id),
        ).fetchone()
        return (
            dict(row)
            if row
            else {
                "provider_calls": 0,
                "codex_calls": 0,
                "claude_calls": 0,
                "input_chars": 0,
                "output_chars": 0,
                "wall_seconds": 0.0,
                "waste_events": 0,
            }
        )

    def get_next_experiment_id(self, competition_slug: str) -> str:
        """Return the next available experiment_id for `competition_slug`,
        in the form `exp_NNNN`. Scans existing experiments for the slug,
        finds the maximum trailing-digit suffix, increments by 1.

        Used by `arena plan` so a second `init-fixture` for the same slug
        produces a fresh exp_id rather than colliding with an existing
        primary key. Returns `exp_0001` on an empty scoreboard.
        """
        import re

        conn = self._require_conn()
        rows = conn.execute(
            "SELECT experiment_id FROM experiments WHERE competition_slug = ?",
            (competition_slug,),
        ).fetchall()
        max_n = 0
        pattern = re.compile(r"^exp_(\d+)$")
        for row in rows:
            m = pattern.match(row["experiment_id"])
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"exp_{max_n + 1:04d}"
