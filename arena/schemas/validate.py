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
