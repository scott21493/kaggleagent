"""End-to-end proof of the §6.2 ten-step research-proxy loop.

Source: docs/phase0/PHASE_0_SINGLE_SCOPE_PLAN.md §6.2.

PR5 covered steps 1-8 (research-proxy fan-out: question, digest,
fusion proposal, scoring, impl). PR6 covered steps 9-10 (review,
memory proposal). This test stitches them into a single sequential
flow under stub providers.

Per design Q5 / brainstorming: ONE test function, intermediate
assertions with §6.2 step-labeled messages, all CLI invocations via
CliRunner (test does NOT share orchestration helpers with
arena eval-harness — it drives the public CLI independently)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.schemas.validate import validate as validate_schema
from arena.scoreboard.store import ScoreboardStore


def test_full_research_proxy_loop_under_stubs(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()

    # Step 0: bootstrap (init-fixture + plan)
    r = runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    assert r.exit_code == 0, f"§6.2 step 0: init-fixture exit={r.exit_code}; out={r.output}"

    r = runner.invoke(app, ["plan", "tabular_binary_v1"])
    assert r.exit_code == 0, f"§6.2 step 0: plan exit={r.exit_code}"

    # Step 1: calibration via run-next produces a calibration row
    r = runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    assert r.exit_code == 0, "§6.2 step 1: calibration run-next failed"

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        cal = (
            store._require_conn()
            .execute(
                "SELECT experiment_id, status FROM experiments "
                "WHERE experiment_type='calibration' "
                "ORDER BY experiment_id DESC LIMIT 1"
            )
            .fetchone()
        )
    finally:
        store.close()
    assert cal is not None, "§6.2 step 1: no calibration row in scoreboard"
    assert cal["status"] == "completed", f"§6.2 step 1: calibration status={cal['status']}"

    # Steps 2-7: research-proxy creates question, digest, fusion proposal, fusion scoring, impl
    r = runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    assert r.exit_code == 0, f"§6.2 steps 2-7: research-proxy exit={r.exit_code}; out={r.output}"

    # Verify the four §6.2 step-tagged rows exist (question, digest, fusion, impl)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_id, artifact_paths FROM experiments "
                "WHERE competition_slug='tabular_binary_v1' AND experiment_type='research_proxy' "
                "ORDER BY experiment_id ASC"
            )
            .fetchall()
        )
    finally:
        store.close()

    # JSON-decode artifact_paths BEFORE substring check (PR6 Task 2 false-positive guard)
    by_step: dict[str, str] = {}
    for row in rows:
        paths = json.loads(row["artifact_paths"])
        for step_token in (
            "<step:question>",
            "<step:digest>",
            "<step:fusion>",
            "<step:implementation>",
        ):
            if step_token in paths:
                by_step[step_token] = row["experiment_id"]

    assert "<step:question>" in by_step, "§6.2 step 2: research_question row missing"
    assert "<step:digest>" in by_step, "§6.2 step 3: paper_digest row missing"
    assert "<step:fusion>" in by_step, "§6.2 step 5: fusion_proposal row missing"
    assert "<step:implementation>" in by_step, "§6.2 step 7: implementation row missing"
    impl_exp_id = by_step["<step:implementation>"]

    # Step 8: evaluate the proxy implementation via fixture scoring
    r = runner.invoke(app, ["evaluate", "tabular_binary_v1", "--latest"])
    assert r.exit_code == 0, f"§6.2 step 8: evaluate --latest exit={r.exit_code}"

    # Step 9: Claude review of the implementation
    r = runner.invoke(
        app,
        ["review", "tabular_binary_v1", "--provider", "stub_claude", "--experiment", impl_exp_id],
    )
    assert r.exit_code == 0, f"§6.2 step 9: review exit={r.exit_code}; out={r.output}"

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        review_row = (
            store._require_conn()
            .execute(
                "SELECT experiment_id, artifact_paths FROM experiments "
                "WHERE artifact_paths LIKE ? "
                "ORDER BY experiment_id DESC LIMIT 1",
                ('%"<step:review>"%',),
            )
            .fetchone()
        )
    finally:
        store.close()
    assert review_row is not None, "§6.2 step 9: review row missing"
    review_paths = json.loads(review_row["artifact_paths"])
    assert "<step:review>" in review_paths, "§6.2 step 9: review token missing in artifact_paths"
    review_exp_id = review_row["experiment_id"]

    # Step 10: memory proposal synthesis from the review
    r = runner.invoke(
        app,
        ["memory", "propose", "tabular_binary_v1", "--review", review_exp_id],
    )
    assert r.exit_code == 0, f"§6.2 step 10: memory propose exit={r.exit_code}; out={r.output}"

    proposals = list((fixture_workspace / "memory" / "proposals").glob("mem_*.json"))
    assert len(proposals) == 1, f"§6.2 step 10: expected 1 memory proposal, found {len(proposals)}"
    payload = json.loads(proposals[0].read_text(encoding="utf-8"))
    validate_schema("memory_update", payload)
    assert payload["namespace"] == "research", "§6.2 step 10: namespace must be 'research'"
