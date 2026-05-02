# tests/test_observability_report.py
from __future__ import annotations

from arena.observability.replay import RunReplayView, TaskSummary
from arena.observability.report import render_run_report


def test_report_includes_run_id_and_task_table() -> None:
    view = RunReplayView(
        run_id="run_2026_05_02",
        fixture_manifest_hash="abcdef",
        tasks=[
            TaskSummary(
                task_id="task_0001",
                first_event_id="evt_0002",
                status="success",
                score=0.5,
                metric_name="accuracy",
                provider="stub_codex",
                provider_version="stub_codex.v1",
            ),
        ],
        breaker_counts={},
    )
    md = render_run_report(view)
    assert "# Run report: run_2026_05_02" in md
    assert "task_0001" in md
    assert "0.5" in md
    assert "stub_codex.v1" in md
    assert "abcdef" in md


def test_report_renders_breaker_section_when_breakers_present() -> None:
    view = RunReplayView(
        run_id="run_x",
        fixture_manifest_hash=None,
        tasks=[],
        breaker_counts={"SecretAccessBreaker": 2, "ProtectedFileBreaker": 1},
    )
    md = render_run_report(view)
    assert "## Breakers triggered" in md
    assert "SecretAccessBreaker | 2" in md
    assert "ProtectedFileBreaker | 1" in md


def test_report_omits_breaker_section_when_clean() -> None:
    view = RunReplayView(
        run_id="run_x",
        fixture_manifest_hash="abc",
        tasks=[TaskSummary(task_id="t", first_event_id="evt_0001", status="success")],
        breaker_counts={},
    )
    md = render_run_report(view)
    assert "Breakers triggered" not in md
