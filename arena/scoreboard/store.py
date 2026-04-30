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
    ) -> None:
        """Insert a new experiment row.

        `artifact_paths` is JSON-encoded into the TEXT column; readers must
        `json.loads()` the result. `created_at` should be ISO-8601 so that
        `get_latest_experiment` orders correctly.
        """
        conn = self._require_conn()
        conn.execute(
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
        conn.commit()

    def update_experiment_score(self, experiment_id: str, *, score: float) -> None:
        conn = self._require_conn()
        conn.execute(
            "UPDATE experiments SET score = ? WHERE experiment_id = ?",
            (score, experiment_id),
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
