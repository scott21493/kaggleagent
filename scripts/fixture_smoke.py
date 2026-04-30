from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest


def main() -> None:
    validate_fixture_manifest("fixtures/tabular_binary_v1")
    result = evaluate_fixture_submission(
        "fixtures/tabular_binary_v1/sample_submission.csv",
        "fixtures/tabular_binary_v1/hidden_labels.csv",
    )
    if not result.valid_submission:
        raise SystemExit(result.error or "fixture sample submission invalid")
    print(f"fixture smoke score={result.score:.6f}")


if __name__ == "__main__":
    main()
