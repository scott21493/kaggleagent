from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def main() -> None:
    schema = json.loads(Path("schemas/memory_update.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid_modify = {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": "modify",
        "claim": "Research proxy ideas must include smallest proxy tests.",
        "prior_claim": "Research proxy ideas may be freeform.",
        "delta": "Replace freeform ideas with typed proxy-test requirements.",
        "evidence": [
            {
                "type": "file",
                "ref": "schemas/fusion_proposal.schema.json",
                "quote_or_summary": "smallest_proxy_test is required",
            }
        ],
        "confidence": "high",
        "expiry_or_revisit": "After Phase 0 fixture close.",
        "risk": "low",
        "review_status": "proposed",
    }
    validator.validate(valid_modify)
    invalid_modify = dict(valid_modify)
    invalid_modify["prior_claim"] = None
    errors = list(validator.iter_errors(invalid_modify))
    if not errors:
        raise SystemExit("memory_update modify without prior_claim unexpectedly validated")
    print("memory proposal conditional validation passed")


if __name__ == "__main__":
    main()
