# arena/self_improvement/scan.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from arena.scoreboard.store import ScoreboardStore
from arena.self_improvement.champion_challenger import (
    Metrics,
    compare_metrics,
)

# Phase-0 thresholds. These are deliberate Phase-0-stub defaults; PR7
# may make them configurable.
_CALIBRATION_BASELINE_SCORE = 0.5
_WASTE_EVENTS_THRESHOLD = 5
# Fixture success rate threshold: any single row with valid_submission
# explicitly False is a finding (Phase 0 has at most a handful of rows
# per slug, so a per-row check is appropriate).
_INVALID_SUBMISSION_FIRES_FINDING = True


@dataclass(frozen=True)
class Finding:
    """One self-improvement scan finding. Maps to one
    self_improvement_proposal.json artifact."""

    kind: str
    severity: str
    problem: str
    evidence_refs: list[str] = field(default_factory=list)


def scan_runs(
    slug: str,
    *,
    store: ScoreboardStore,
    runs_root: Path,
    baselines_root: Path,
    traces_root: Path = Path("traces"),
) -> list[Finding]:
    """Scan all scoreboard rows + traces + baselines for `slug` and
    return findings.

    Phase 0 checks cover the §7.3 triggers that can be derived from
    durable state. Protected-file mutation and schema drift are out of
    scope until PR7's auto-apply flow exists.

    `baselines_root` is intentionally accepted but unused in Phase 0.
    Spec §3.3 step 2 reserves it for "fixture-digest + provider-version
    baseline" consumption — a future PR will wire it into the
    score-regression check (currently hard-coded to
    `_CALIBRATION_BASELINE_SCORE = 0.5`) and into a future
    drift_baseline trigger. Keeping the parameter in the signature
    avoids a churning CLI when that work lands. `runs_root` is similarly
    accepted; the failed_replay trigger consults
    `runs/<run_id>/traces/...` as a fallback path.

    Triggers:
    - blocked_row: any status="blocked" row.
    - invalid_submission: any row with valid_submission explicitly False.
      (§7.3 "lower fixture success rate than champion".)
    - score_regression: max(score) < _CALIBRATION_BASELINE_SCORE.
    - waste_events_threshold: SUM(waste_events) > _WASTE_EVENTS_THRESHOLD.
      (§7.3 "more waste events".)
    - wall_clock_regression: aggregated wall_seconds across non-calibration
      rows exceeds the calibration champion's wall_seconds by >20% AND
      max(score) <= calibration's score (no improvement to justify the
      cost). (§7.3 "wall-clock increase over 20% without score/safety
      improvement".)
    - provider_calls_regression: aggregated provider_calls across
      non-calibration rows > 1.20 * calibration's provider_calls AND no
      score improvement. (§7.3 "provider call count increase over 20%
      without score/safety improvement".)
    - failed_replay: any row with a task_id whose
      traces/<run_id>/<task_id>/events.jsonl is MISSING or corrupt. A
      missing trace is treated as failed replay (the trace event chain
      cannot be reconstructed), not as replay-success.

    The +20% triggers use champion_challenger.compare_metrics so the
    comparison logic is a single library helper. In Phase-0 stub mode
    most stubs report zero wall_seconds, so these triggers fire only on
    test fixtures that synthesize non-zero values (or production CLI
    adapters). Tests in tests/test_self_improvement_scan.py exercise
    the triggers via direct row inserts.
    """
    findings: list[Finding] = []

    rows = (
        store._require_conn()
        .execute(
            "SELECT experiment_id, task_id, run_id, status, score, "
            "valid_submission, waste_events, wall_seconds, "
            "experiment_type, provider "
            "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
            (slug,),
        )
        .fetchall()
    )

    # 1. blocked rows
    for row in rows:
        if row["status"] == "blocked":
            findings.append(
                Finding(
                    kind="blocked_row",
                    severity="medium",
                    problem=(f"experiment {row['experiment_id']} (task {row['task_id']}) blocked"),
                    evidence_refs=[
                        f"scoreboard:{row['experiment_id']}",
                        f"trace:{row['run_id']}/{row['task_id']}",
                    ],
                )
            )

    # 2. invalid submissions (fixture success rate below champion).
    if _INVALID_SUBMISSION_FIRES_FINDING:
        for row in rows:
            # valid_submission is stored as 0/1/None; only fire on
            # explicit False (0). None means "not applicable to this
            # row" (e.g. blocked-row branches that never produced a
            # submission).
            if row["valid_submission"] == 0:
                findings.append(
                    Finding(
                        kind="invalid_submission",
                        severity="high",
                        problem=(
                            f"experiment {row['experiment_id']} produced "
                            "valid_submission=False (fixture success rate "
                            "regression vs champion)"
                        ),
                        evidence_refs=[f"scoreboard:{row['experiment_id']}"],
                    )
                )

    # Split rows by experiment_type ONCE so the score-regression check
    # below and the +20% comparisons further down both use the same
    # champion/challenger partition. Champion = calibration row(s); any
    # other row is a challenger.
    cal_rows = [row for row in rows if row["experiment_type"] == "calibration"]
    challenger_rows = [row for row in rows if row["experiment_type"] != "calibration"]

    # 3. score regression: the challenger's BEST score must NOT be
    # below the champion. The previous max-across-all-rows check
    # masked regressions whenever a calibration row was present —
    # cal=0.5 + challenger=0.42 → max(all)=0.5 → no finding fired
    # (P1 from PR6 Task 5 review). Champion baseline is the calibration
    # row's max score if present, else _CALIBRATION_BASELINE_SCORE.
    champion_score = max(
        (row["score"] for row in cal_rows if row["score"] is not None),
        default=_CALIBRATION_BASELINE_SCORE,
    )
    challenger_scores = [row["score"] for row in challenger_rows if row["score"] is not None]
    if challenger_scores and max(challenger_scores) < champion_score:
        worst = next(
            row
            for row in challenger_rows
            if row["score"] is not None and row["score"] == min(challenger_scores)
        )
        findings.append(
            Finding(
                kind="score_regression",
                severity="high",
                problem=(
                    f"max challenger score {max(challenger_scores):.4f} below "
                    f"champion {champion_score:.4f}"
                ),
                evidence_refs=[f"scoreboard:{worst['experiment_id']}"],
            )
        )

    # 4. waste events threshold
    total_waste = sum((row["waste_events"] or 0) for row in rows)
    if total_waste > _WASTE_EVENTS_THRESHOLD:
        findings.append(
            Finding(
                kind="waste_events_threshold",
                severity="medium",
                problem=(f"total waste_events {total_waste} > threshold {_WASTE_EVENTS_THRESHOLD}"),
                evidence_refs=[f"scoreboard:slug={slug}"],
            )
        )

    # 5+6. wall-clock and provider-call +20% regressions vs the
    # calibration champion. Reuse the cal_rows / challenger_rows
    # partition computed above for the score-regression check. Rely on
    # compare_metrics so the threshold logic is a single helper.
    if cal_rows and challenger_rows:
        champion = Metrics(
            score=max(
                (row["score"] for row in cal_rows if row["score"] is not None),
                default=_CALIBRATION_BASELINE_SCORE,
            ),
            wall_seconds=sum((row["wall_seconds"] or 0.0) for row in cal_rows),
            provider_calls=len(cal_rows),
            waste_events=sum((row["waste_events"] or 0) for row in cal_rows),
        )
        challenger = Metrics(
            score=max(
                (row["score"] for row in challenger_rows if row["score"] is not None),
                default=champion.score,
            ),
            wall_seconds=sum((row["wall_seconds"] or 0.0) for row in challenger_rows),
            provider_calls=len(challenger_rows),
            waste_events=sum((row["waste_events"] or 0) for row in challenger_rows),
        )
        comparison = compare_metrics(champion, challenger)
        # Map the comparison's regression reason into the corresponding
        # Finding.kind. compare_metrics returns "; "-joined reason
        # strings; we surface each as its own finding so freeze
        # evidence enumerates them clearly.
        if "wall_seconds" in comparison.reason:
            findings.append(
                Finding(
                    kind="wall_clock_regression",
                    severity="medium",
                    problem=(
                        f"wall-clock +{comparison.wall_seconds_delta:.1f}s "
                        ">20% over champion without score/safety improvement"
                    ),
                    evidence_refs=[f"scoreboard:slug={slug}"],
                )
            )
        if "provider_calls" in comparison.reason:
            findings.append(
                Finding(
                    kind="provider_calls_regression",
                    severity="medium",
                    problem=(
                        f"provider_calls +{comparison.provider_calls_delta} "
                        ">20% over champion without score/safety improvement"
                    ),
                    evidence_refs=[f"scoreboard:slug={slug}"],
                )
            )

    # 7. failed replay: a row with a task_id and NO trace, or a corrupt
    # trace. Missing means the chain cannot be replayed; per §7.3 this
    # is a freeze trigger. We try the canonical traces/<run_id>/<task_id>
    # path first, then runs/<run_id>/traces/<run_id>/<task_id> for
    # workspaces that wrote traces under the run dir.
    for row in rows:
        if not row["task_id"] or not row["run_id"]:
            continue
        canonical = traces_root / row["run_id"] / row["task_id"] / "events.jsonl"
        nested = runs_root / row["run_id"] / "traces" / row["task_id"] / "events.jsonl"
        target: Path | None
        if canonical.exists():
            target = canonical
        elif nested.exists():
            target = nested
        else:
            target = None
        if target is None:
            findings.append(
                Finding(
                    kind="failed_replay",
                    severity="high",
                    problem=(
                        f"missing trace for {row['experiment_id']} (task "
                        f"{row['task_id']}, run {row['run_id']}); replay cannot "
                        "be reconstructed"
                    ),
                    evidence_refs=[f"trace:{row['run_id']}/{row['task_id']}"],
                )
            )
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(
                Finding(
                    kind="failed_replay",
                    severity="high",
                    problem=f"unreadable trace at {target}",
                    evidence_refs=[f"trace:{row['run_id']}/{row['task_id']}"],
                )
            )
            continue
        # A trace file that is UTF-8-decodable but contains
        # syntactically invalid JSONL is also a failed_replay: the
        # event chain cannot be reconstructed by replay tooling. Parse
        # each non-empty line and fire on the first decode error.
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                findings.append(
                    Finding(
                        kind="failed_replay",
                        severity="high",
                        problem=f"corrupt JSONL at {target}",
                        evidence_refs=[f"trace:{row['run_id']}/{row['task_id']}"],
                    )
                )
                break

    return findings
