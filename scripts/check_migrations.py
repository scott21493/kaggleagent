from __future__ import annotations

# SCAFFOLDING: greps SQL files for "CREATE TABLE" only. Replace when arena/scoreboard/store.py
# lands — must apply migrations to a temp SQLite DB and verify idempotency on empty + populated
# DBs as required by docs/security/SECURITY_COST_REPRODUCIBILITY_SPEC.md §6.6.

from pathlib import Path


def main() -> None:
    migrations = sorted(Path('arena/scoreboard/migrations').glob('*.sql'))
    if not migrations:
        raise SystemExit('no scoreboard migrations found')
    for path in migrations:
        text = path.read_text(encoding='utf-8').strip()
        if 'CREATE TABLE' not in text.upper():
            raise SystemExit(f'migration missing CREATE TABLE: {path}')
    print(f'ok migrations {len(migrations)}')


if __name__ == '__main__':
    main()
