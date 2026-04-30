from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena.scoreboard.store import ScoreboardStore

# Required columns after all migrations apply, sourced from
# docs/architecture/KAGGLE_AGENT_ARENA_DESIGN_V2.md §7.
_EXPECTED_EXPERIMENT_COLUMNS = {
    "experiment_id",
    "run_id",
    "score",
    "metric_name",
    "status",
    "competition_slug",
    "task_id",
    "experiment_type",
    "provider",
    "provider_version",
    "valid_submission",
    "wall_seconds",
    "input_chars",
    "output_chars",
    "shell_commands",
    "failed_commands",
    "waste_events",
    "artifact_paths",
    "trace_path",
    "created_at",
}


def main() -> None:
    """Apply migrations on a fresh SQLite DB and verify idempotent reconnect.

    Replaces the prior CREATE-TABLE-only grep, which rejected legitimate
    ALTER-only migrations. Implements the durability check from
    docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §6.6.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "scoreboard.sqlite"

        # Phase 1: apply on empty DB; verify experiments has every design-v2 §7 column.
        store = ScoreboardStore(db)
        store.connect()
        cols_first = set(store.experiment_columns())
        missing = _EXPECTED_EXPERIMENT_COLUMNS - cols_first
        if missing:
            store.close()
            raise SystemExit(
                f"experiments table missing columns after fresh apply: {sorted(missing)}"
            )
        store.close()

        # Phase 2: re-apply on populated DB; must not raise and column set must be stable.
        store2 = ScoreboardStore(db)
        store2.connect()
        cols_second = set(store2.experiment_columns())
        if cols_first != cols_second:
            added = cols_second - cols_first
            removed = cols_first - cols_second
            store2.close()
            raise SystemExit(
                f"non-idempotent reconnect: added={sorted(added)}, removed={sorted(removed)}"
            )
        store2.close()

    print(
        "ok migrations: applied cleanly on empty DB + idempotent on populated DB "
        f"({len(_EXPECTED_EXPERIMENT_COLUMNS)} expected columns present)"
    )


if __name__ == "__main__":
    main()
