# arena/observability/report.py
from __future__ import annotations

from arena.observability.replay import RunReplayView


def render_run_report(view: RunReplayView) -> str:
    """Render a markdown summary of a replayed run.

    Sections:
      - title with run_id
      - fixture_manifest_hash (if recorded)
      - per-task table (id, status, score, provider, provider_version, breakers)
      - breaker totals (only if any breakers fired)
    """
    lines: list[str] = []
    lines.append(f"# Run report: {view.run_id}")
    lines.append("")
    if view.fixture_manifest_hash:
        lines.append(f"- fixture_manifest_hash: `{view.fixture_manifest_hash}`")
    lines.append(f"- tasks: {len(view.tasks)}")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    lines.append("| task_id | status | score | metric | provider | version | breakers |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in view.tasks:
        # score=0.0 is a VALID score value (perfect-fail or trivial baseline),
        # so use explicit None check rather than truthy fallback. Other string
        # fields use `or ""` since empty string and None render identically.
        lines.append(
            f"| {t.task_id} | {t.status or ''} | {t.score if t.score is not None else ''} "
            f"| {t.metric_name or ''} | {t.provider or ''} | {t.provider_version or ''} "
            f"| {','.join(t.breakers)} |"
        )
    if view.breaker_counts:
        lines.append("")
        lines.append("## Breakers triggered")
        lines.append("")
        lines.append("| breaker | count |")
        lines.append("|---|---|")
        for breaker, count in sorted(view.breaker_counts.items()):
            lines.append(f"| {breaker} | {count} |")
    lines.append("")
    return "\n".join(lines)
