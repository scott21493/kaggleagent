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
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
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
            "SELECT * FROM experiments WHERE competition_slug = ? ORDER BY created_at DESC LIMIT 1",
            (competition_slug,),
        ).fetchone()
        return dict(row) if row else None
