from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@cache
def load_schema(name: str) -> dict:
    """Load `<name>.schema.json` from the repo's top-level schemas/ directory.

    `name` must match `^[a-z][a-z0-9_]*$`. This rejects path-traversal
    attempts (`foo/../bar`), absolute paths, leading dots, backslashes, and
    other non-canonical inputs that could let a caller read schema files
    outside the schemas directory or grow the unbounded cache without bound.

    Cached: subsequent calls return the same dict instance.
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid schema name: {name!r}; must match {_NAME_RE.pattern}")
    path = SCHEMA_DIR / f"{name}.schema.json"
    schema: dict = json.loads(path.read_text(encoding="utf-8"))
    return schema
