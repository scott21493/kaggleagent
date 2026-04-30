from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from arena.fixture.evaluator import evaluate_fixture_submission
from arena.fixture.manifest import validate_fixture_manifest


def test_submission_rejects_wrong_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    labels = tmp_path / "labels.csv"
    pd.DataFrame({"id": [1, 2], "prediction": [0.1, 0.9]}).to_csv(bad, index=False)
    pd.DataFrame({"id": [1, 2], "target": [0, 1]}).to_csv(labels, index=False)
    result = evaluate_fixture_submission(bad, labels)
    assert not result.valid_submission
    assert result.error is not None
    assert "expected columns" in result.error


def test_submission_rejects_id_order_mismatch(tmp_path: Path) -> None:
    sub = tmp_path / "sub.csv"
    labels = tmp_path / "labels.csv"
    pd.DataFrame({"id": [1, 2], "target": [0.1, 0.9]}).to_csv(sub, index=False)
    pd.DataFrame({"id": [2, 1], "target": [1, 0]}).to_csv(labels, index=False)
    result = evaluate_fixture_submission(sub, labels)
    assert not result.valid_submission
    assert result.error == "id_order_mismatch"


def test_submission_rejects_out_of_range_predictions(tmp_path: Path) -> None:
    sub = tmp_path / "sub.csv"
    labels = tmp_path / "labels.csv"
    pd.DataFrame({"id": [1, 2], "target": [-0.1, 1.2]}).to_csv(sub, index=False)
    pd.DataFrame({"id": [1, 2], "target": [0, 1]}).to_csv(labels, index=False)
    result = evaluate_fixture_submission(sub, labels)
    assert not result.valid_submission
    assert result.error == "predictions_outside_0_1"


def test_manifest_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_fixture_manifest(tmp_path)
