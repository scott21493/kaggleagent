from __future__ import annotations

from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest


def test_fixture_manifest_validates() -> None:
    validate_fixture_manifest("fixtures/tabular_binary_v1")


def test_sample_submission_scores() -> None:
    result = evaluate_fixture_submission(
        "fixtures/tabular_binary_v1/sample_submission.csv",
        "fixtures/tabular_binary_v1/hidden_labels.csv",
    )
    assert result.valid_submission
    assert result.score is not None
