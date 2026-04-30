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
