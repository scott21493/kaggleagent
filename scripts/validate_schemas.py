from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path('schemas')


def main() -> None:
    failures: list[str] = []
    for path in sorted(SCHEMA_DIR.glob('*.schema.json')):
        try:
            schema = json.loads(path.read_text(encoding='utf-8'))
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # pragma: no cover - failure path is exercised in CI by exit code
            failures.append(f'{path}: {exc}')
        else:
            print(f'ok schema {path}')
    if failures:
        raise SystemExit('\n'.join(failures))


if __name__ == '__main__':
    main()
