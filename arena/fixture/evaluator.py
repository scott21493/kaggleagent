from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.metrics import roc_auc_score


@dataclass(frozen=True)
class FixtureEvaluationResult:
    valid_submission: bool
    score: float | None
    metric_name: str = 'roc_auc'
    error: str | None = None


def evaluate_fixture_submission(submission_csv: str | Path, hidden_labels_csv: str | Path) -> FixtureEvaluationResult:
    submission_path = Path(submission_csv)
    labels_path = Path(hidden_labels_csv)
    try:
        submission = pd.read_csv(submission_path)
        labels = pd.read_csv(labels_path)
    except Exception as exc:
        return FixtureEvaluationResult(False, None, error=f'csv_read_error: {exc}')

    expected_columns = ['id', 'target']
    if list(submission.columns) != expected_columns:
        return FixtureEvaluationResult(False, None, error=f'expected columns {expected_columns}, got {list(submission.columns)}')
    if list(labels.columns) != expected_columns:
        return FixtureEvaluationResult(False, None, error=f'hidden labels must have columns {expected_columns}')
    if len(submission) != len(labels):
        return FixtureEvaluationResult(False, None, error='row_count_mismatch')
    if submission['id'].tolist() != labels['id'].tolist():
        return FixtureEvaluationResult(False, None, error='id_order_mismatch')
    if submission['target'].isna().any():
        return FixtureEvaluationResult(False, None, error='nan_predictions')
    if not submission['target'].between(0, 1).all():
        return FixtureEvaluationResult(False, None, error='predictions_outside_0_1')
    try:
        score = float(roc_auc_score(labels['target'], submission['target']))
    except Exception as exc:
        return FixtureEvaluationResult(False, None, error=f'metric_error: {exc}')
    return FixtureEvaluationResult(True, score)
