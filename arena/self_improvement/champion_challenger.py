# arena/self_improvement/champion_challenger.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    """Comparable metrics tuple. Phase-0 stub uses ROC-AUC score +
    coarse cost/safety counters. PR7+ may add input/output_chars."""

    score: float
    wall_seconds: float
    provider_calls: int
    waste_events: int


@dataclass(frozen=True)
class ComparisonResult:
    """Output of compare_metrics. score_delta = challenger.score -
    champion.score; regressed=True if any §7.3 trigger condition fires
    (score down, wall +20%, provider_calls +20%, more waste)."""

    score_delta: float
    wall_seconds_delta: float
    provider_calls_delta: int
    waste_events_delta: int
    regressed: bool
    reason: str


def compare_metrics(champion: Metrics, challenger: Metrics) -> ComparisonResult:
    """Compare a challenger's metrics against the champion. Pure
    function: same inputs -> same output. No I/O.

    Regression triggers (§7.3 subset Phase-0 can compute deterministically
    from in-memory metrics):
    - challenger.score < champion.score
    - challenger.wall_seconds > 1.20 * champion.wall_seconds without score gain
    - challenger.provider_calls > 1.20 * champion.provider_calls without score gain
    - challenger.waste_events > champion.waste_events
    """
    score_delta = challenger.score - champion.score
    wall_delta = challenger.wall_seconds - champion.wall_seconds
    pc_delta = challenger.provider_calls - champion.provider_calls
    waste_delta = challenger.waste_events - champion.waste_events

    reasons: list[str] = []
    if challenger.score < champion.score:
        reasons.append(f"score regression: {challenger.score:.4f} < {champion.score:.4f}")
    # `score_delta <= 0` means "no score gain to justify the cost".
    # IEEE-754 noise here (e.g., -1e-17 from sum-of-folds arithmetic)
    # biases toward firing the freeze, which is the safe direction:
    # spurious freeze on truly-equal scores is far less harmful than
    # missed freeze on a genuine cost regression. The `> 0` guards on
    # champion.wall_seconds / champion.provider_calls below skip the
    # ratio check when the champion is unmeasured (Phase-0 stubs report
    # 0.0 wall_seconds), preventing trivial regressions like
    # "0.001 > 1.20 * 0.0 = 0".
    if (
        champion.wall_seconds > 0
        and challenger.wall_seconds > 1.20 * champion.wall_seconds
        and score_delta <= 0
    ):
        reasons.append(f"wall_seconds +{wall_delta:.1f}s (>20%) without score improvement")
    if (
        champion.provider_calls > 0
        and challenger.provider_calls > 1.20 * champion.provider_calls
        and score_delta <= 0
    ):
        reasons.append(f"provider_calls +{pc_delta} (>20%) without score improvement")
    if challenger.waste_events > champion.waste_events:
        reasons.append(f"waste_events +{waste_delta} (regression in safety surface)")

    return ComparisonResult(
        score_delta=score_delta,
        wall_seconds_delta=wall_delta,
        provider_calls_delta=pc_delta,
        waste_events_delta=waste_delta,
        regressed=bool(reasons),
        reason="; ".join(reasons) if reasons else "no regression",
    )
