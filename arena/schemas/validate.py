from __future__ import annotations

from functools import cache

from jsonschema import Draft202012Validator

from arena.schemas.loader import load_schema


@cache
def _validator(name: str) -> Draft202012Validator:
    """Return a cached Draft 2020-12 validator for `name`.

    Format checking is enabled (Draft202012Validator.FORMAT_CHECKER), so
    `format: "date-time"` and similar declared format constraints are
    actually enforced. Without this, format keywords are silently ignored
    — six schemas in the repo (event, experiment, provider_result,
    research_node, usage_snapshot) declare format: date-time and depend on
    this enforcement to reject malformed timestamps before durable writes.
    """
    return Draft202012Validator(
        load_schema(name),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def validate(schema_name: str, instance: dict) -> None:
    """Raise jsonschema.ValidationError if instance does not satisfy schema."""
    _validator(schema_name).validate(instance)
