# tests/test_self_improvement_champion_challenger.py
from __future__ import annotations

import pytest

from arena.self_improvement.champion_challenger import (
    ComparisonResult,
    Metrics,
    compare_metrics,
)


def test_compare_metrics_returns_comparison_result() -> None:
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.6, wall_seconds=11.0, provider_calls=1, waste_events=0)
    result = compare_metrics(champion, challenger)
    assert isinstance(result, ComparisonResult)
    # Use pytest.approx — `0.6 - 0.5` is `0.09999999999999998` in
    # IEEE-754 float arithmetic, so exact equality fails. The default
    # rel/abs tolerance (1e-6/1e-12) is well below any real ROC-AUC
    # signal, so this is the right comparison for production code that
    # subtracts floats without rounding.
    assert result.score_delta == pytest.approx(0.1)
    assert result.regressed is False


def test_compare_metrics_flags_score_regression() -> None:
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.42, wall_seconds=10.0, provider_calls=1, waste_events=0)
    result = compare_metrics(champion, challenger)
    assert result.regressed is True
    assert "score" in result.reason.lower()


def test_compare_metrics_is_pure() -> None:
    """Calling compare_metrics twice with the same inputs returns equal
    ComparisonResults; no internal state."""
    champion = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    challenger = Metrics(score=0.5, wall_seconds=10.0, provider_calls=1, waste_events=0)
    a = compare_metrics(champion, challenger)
    b = compare_metrics(champion, challenger)
    assert a == b
