from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _step_of(row) -> str | None:
    """Extract the step name from a research-proxy row's artifact_paths.

    All research-proxy rows persist with experiment_type='research_proxy'
    (the schema-allowed enum value). The per-step distinction lives in
    artifact_paths as a `<step:NAME>` token (mirrors PR4's
    `<PROVIDER_VERSION_CHANGED:from=...>` tag pattern). Returns the
    NAME — `question` / `digest` / `fusion` / `implementation` — or
    None if no step token is present. artifact_paths is stored as a
    JSON-encoded text column in SQLite, so decode if needed.
    """
    paths = row["artifact_paths"]
    if isinstance(paths, str):
        paths = json.loads(paths)
    for p in paths:
        if isinstance(p, str) and p.startswith("<step:") and p.endswith(">"):
            return p[len("<step:") : -1]
    return None


def test_research_proxy_runs_steps_1_through_8(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: arena research-proxy tabular_binary_v1 --provider stub_claude
    runs steps 1-8 against method_note_001.md and produces all 4 artifacts +
    four scoreboard rows (one per provider invocation)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 0, result.output
    assert "fusion_id=fusion_0001" in result.output
    assert "score=" in result.output

    # Artifacts land in separate per-step worktrees (exp_0001 through exp_0004).
    wt_root = fixture_workspace / "worktrees" / "tabular_binary_v1"
    assert (wt_root / "exp_0001" / "research_question.json").exists()
    assert (wt_root / "exp_0002" / "paper_digest.json").exists()
    assert (wt_root / "exp_0003" / "fusion_proposal.json").exists()
    assert (wt_root / "exp_0004" / "submission.csv").exists()

    # Four scoreboard rows: question, digest, fusion, implementation.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_id, experiment_type, status, score, artifact_paths "
                "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # All rows use the schema-allowed enum value; step name lives
        # in artifact_paths as a <step:NAME> token.
        assert all(r["experiment_type"] == "research_proxy" for r in rows)
        steps = [_step_of(r) for r in rows]
        assert steps == ["question", "digest", "fusion", "implementation"]
        # All four completed.
        assert all(r["status"] == "completed" for r in rows)
        # Fusion + implementation rows carry the fusion_id token.
        fusion_rows = [r for r in rows if _step_of(r) == "fusion"]
        assert len(fusion_rows) == 1
        assert any(
            "<fusion_id:fusion_0001>" in p for p in json.loads(fusion_rows[0]["artifact_paths"])
        )
        # Implementation row has the score.
        impl_rows = [r for r in rows if _step_of(r) == "implementation"]
        assert len(impl_rows) == 1
        assert impl_rows[0]["score"] is not None
    finally:
        store.close()


def test_research_proxy_halts_at_fusion_gate_below_min_score(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force MIN_FUSION_SCORE above the deterministic stub's score so the
    chain halts at step 6. Asserts rows 1-3 completed + NO row 4 (since
    stub_codex was never invoked, no provider_calls increment), and
    that submission.csv is NOT created (stub_codex was never invoked)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setattr("arena.research_proxy.fusion_scorer.MIN_FUSION_SCORE", 0.99)
    # Rebind the symbol the CLI imports.
    monkeypatch.setattr("arena.cli.MIN_FUSION_SCORE", 0.99)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 2
    assert "fusion gate failed" in result.output

    wt_root = fixture_workspace / "worktrees" / "tabular_binary_v1"
    # Steps 1-5 produced their artifacts (exp_0001 through exp_0003); step 7 did NOT.
    assert (wt_root / "exp_0003" / "fusion_proposal.json").exists()
    assert not any((wt_root / f"exp_{i:04d}" / "submission.csv").exists() for i in range(1, 6))

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_type, status, artifact_paths FROM experiments "
                "WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # Steps 1-3 + 5 produced their rows as completed. NO implementation
        # row exists because stub_codex was never invoked at the gate.
        # All rows use experiment_type='research_proxy'; step lives in
        # artifact_paths' <step:NAME> token.
        assert all(r["experiment_type"] == "research_proxy" for r in rows)
        steps_and_status = [(_step_of(r), r["status"]) for r in rows]
        assert ("question", "completed") in steps_and_status
        assert ("digest", "completed") in steps_and_status
        assert ("fusion", "completed") in steps_and_status
        # No implementation row (provider_calls must equal 3, not 4).
        assert not any(s == "implementation" for s, _ in steps_and_status)
    finally:
        store.close()


def test_research_proxy_rejects_unknown_provider(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR5 supports only stub_claude as the research provider."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_codex"],
    )
    assert result.exit_code != 0
    assert "unknown research provider" in result.output


def test_research_proxy_rejects_missing_method_note(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If method_note_001.md is missing, the CLI fails fast before any
    provider invocation or scoreboard write."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    method_note = (
        fixture_workspace / "fixtures" / "tabular_binary_v1" / "paper_bundle" / "method_note_001.md"
    )
    method_note.unlink()
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code != 0
    assert "method note missing" in result.output


def test_research_proxy_blocks_on_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_can_invoke must fire BEFORE the first wrap_invoke. Setting
    ARENA_KILL_SWITCH should halt research-proxy at step 1."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "kill switch active" in result.output.lower()


def test_research_proxy_blocks_on_pre_invoke_budget_cap(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider-call run-level cap fires via check_can_invoke. Setting the
    cap to 0 should halt research-proxy before step 2's first invoke."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    monkeypatch.setenv("ARENA_PHASE0_PROVIDER_CALL_CAP", "0")

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "budget exceeded" in result.output.lower() or "ProviderCallBreaker" in result.output


def test_research_proxy_does_not_collide_after_calibration(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration first creates exp_0001; research-proxy mints 4 more
    (exp_0002 through exp_0005) via get_next_experiment_id — one per
    provider invocation, with no primary-key collision."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    # Now research-proxy MUST get different experiment_ids (exp_0002 through exp_0005).
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_id FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        ids = [r["experiment_id"] for r in rows]
        assert "exp_0001" in ids  # calibration
        # research-proxy persists 4 rows; total >= 5 distinct IDs.
        assert len(set(ids)) >= 5
        # And the research-proxy rows are exp_0002 through exp_0005.
        assert {"exp_0002", "exp_0003", "exp_0004", "exp_0005"}.issubset(set(ids))
    finally:
        store.close()


def test_research_proxy_persists_usage_totals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each of the 4 per-invocation usage_proxy values must be stored on
    its own experiment row so arena budget status sees actual cost per step.
    Stubs report zero usage, but stub_codex's submission.csv contributes
    output_chars (the file size is read in build_result) — that's a useful
    smoke check for the implementation row."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code == 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_type, artifact_paths, output_chars, input_chars, "
                "wall_seconds, shell_commands, failed_commands, waste_events "
                "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        assert len(rows) == 4
        # All 4 rows have non-negative usage fields.
        for row in rows:
            assert row["output_chars"] >= 0
            assert row["input_chars"] >= 0
            assert row["wall_seconds"] >= 0.0
            assert row["shell_commands"] >= 0
            assert row["failed_commands"] >= 0
            assert row["waste_events"] >= 0
        # Implementation row (stub_codex) MUST contribute non-zero
        # output_chars from the submission.csv file size (build_result
        # calls submission_path.stat().st_size). A zero here would
        # indicate the usage_proxy round-trip from ProviderResult ->
        # insert_experiment is broken.
        impl_row = next(r for r in rows if _step_of(r) == "implementation")
        assert impl_row["output_chars"] > 0
    finally:
        store.close()


def test_research_proxy_respects_run_level_provider_call_cap_after_calibration(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration consumes one provider call; cap research-proxy at the
    new total to verify the governor seeds from get_run_usage_totals."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])

    # Calibration consumed 1 provider call. Cap research-proxy at 1 so its
    # very first invoke fails check_can_invoke (would-be call count = 2).
    monkeypatch.setenv("ARENA_PHASE0_PROVIDER_CALL_CAP", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    output_lower = result.output.lower()
    assert "budget exceeded" in output_lower or "provider" in output_lower


def test_research_proxy_does_not_persist_row_on_pre_invoke_provider_call_cap(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the run-level provider-call cap allows N invocations and
    research-proxy needs N+1, the (N+1)th call halts in check_can_invoke
    BEFORE wrap_invoke runs. No provider invocation happened for that
    step, so no scoreboard row should be inserted — otherwise COUNT(*)
    would inflate get_run_usage_totals.provider_calls and the next run's
    seeded budget would be wrong."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Cap at 2 provider calls. Steps 2 (question) + 4 (digest) succeed;
    # step 5 (fusion) fails check_can_invoke (third call would-be).
    monkeypatch.setenv("ARENA_PHASE0_PROVIDER_CALL_CAP", "2")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "budget exceeded" in result.output.lower() or "ProviderCallBreaker" in result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_type, status, artifact_paths FROM experiments "
                "WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # Exactly TWO rows: the two completed steps. No row for the
        # pre-invoke-blocked third step. provider_calls = COUNT(*) = 2,
        # matching the cap. All rows use experiment_type='research_proxy';
        # step name lives in artifact_paths' <step:NAME> token.
        assert len(rows) == 2
        assert all(r["experiment_type"] == "research_proxy" for r in rows)
        steps_and_status = [(_step_of(r), r["status"]) for r in rows]
        assert ("question", "completed") in steps_and_status
        assert ("digest", "completed") in steps_and_status
        assert not any(s == "fusion" for s, _ in steps_and_status)
        # Verify the seeded-budget invariant: get_run_usage_totals must
        # report provider_calls == 2, NOT 3.
        run_row = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE competition_slug = ? LIMIT 1",
                ("tabular_binary_v1",),
            )
            .fetchone()
        )
        run_id = run_row["run_id"]
        totals = store.get_run_usage_totals("tabular_binary_v1", run_id)
        assert totals["provider_calls"] == 2
    finally:
        store.close()


def test_research_proxy_does_not_persist_row_on_first_call_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill switch active before research-proxy starts. The first
    check_can_invoke (step 2) raises KillSwitchActive — no provider call
    happens, so no scoreboard row is inserted. provider_calls must
    remain 0."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "kill switch" in result.output.lower()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_id FROM experiments WHERE competition_slug = ?",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # No invocations succeeded, so no rows.
        assert len(rows) == 0
    finally:
        store.close()


def test_research_proxy_does_not_persist_row_on_fusion_gate_block(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When score_fusion_proposal returns below MIN_FUSION_SCORE, the
    chain halts before stub_codex runs. The 3 completed rows (question,
    digest, fusion) reflect the work that actually happened; no fourth
    row is inserted because no provider call occurred for the
    implementation step. provider_calls must equal 3, not 4."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    # Force the gate to fail by raising MIN_FUSION_SCORE above what the
    # stub_claude payload can score. The default stub fusion proposal
    # produces a known deterministic score; setting the threshold to
    # 0.99 guarantees the gate fails (score is in [0, 1] by construction
    # in fusion_scorer). MIN_FUSION_SCORE is a module-level constant
    # (not an env var); patch BOTH import sites — the source in
    # fusion_scorer.py AND the cli.py re-import — because cli.py imports
    # the symbol directly (`from arena.research_proxy.fusion_scorer
    # import MIN_FUSION_SCORE`) and pytest's setattr only affects the
    # bound name in each module's namespace.
    monkeypatch.setattr("arena.research_proxy.fusion_scorer.MIN_FUSION_SCORE", 0.99)
    monkeypatch.setattr("arena.cli.MIN_FUSION_SCORE", 0.99)
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0
    assert "fusion gate failed" in result.output.lower()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_type, status, artifact_paths FROM experiments "
                "WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # Three rows: question, digest, fusion (all completed). No
        # implementation row. All rows use experiment_type='research_proxy';
        # step name lives in artifact_paths' <step:NAME> token.
        assert len(rows) == 3
        assert all(r["experiment_type"] == "research_proxy" for r in rows)
        steps_and_status = [(_step_of(r), r["status"]) for r in rows]
        assert ("question", "completed") in steps_and_status
        assert ("digest", "completed") in steps_and_status
        assert ("fusion", "completed") in steps_and_status
        assert not any(s == "implementation" for s, _ in steps_and_status)
        # Seeded-budget invariant.
        run_row = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE competition_slug = ? LIMIT 1",
                ("tabular_binary_v1",),
            )
            .fetchone()
        )
        totals = store.get_run_usage_totals("tabular_binary_v1", run_row["run_id"])
        assert totals["provider_calls"] == 3
    finally:
        store.close()


def test_research_proxy_persists_post_invoke_budget_blocked_row_with_usage(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-invoke BudgetExceeded (run-level output_chars cap) — the
    provider returned a result, then record_post_invoke detected the
    over-cap usage and raised BudgetExceeded with usage_proxy populated.
    The blocked row MUST persist that usage_proxy so arena budget status
    and the next-run seeded accumulators reflect what was consumed.
    Reproduces the PR2 bug class for the research-proxy chain.

    Note: arena/budget/policy.py exposes only run-level char caps
    (ARENA_PHASE0_OUTPUT_CHARS_CAP). Setting it to 1 trips post-invoke
    after the first stub_claude call (which writes
    research_question.json — file size > 1 byte, build_result reports
    that as output_chars). record_post_invoke raises BudgetExceeded
    with usage_proxy attached AFTER invoke completed —
    invocation_started is True, so a blocked row IS persisted, with the
    consumed usage attached."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)

    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])

    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    result = runner.invoke(
        app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"]
    )
    assert result.exit_code != 0

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_type, status, artifact_paths, output_chars "
                "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # Exactly one row: the question step, status=blocked, with
        # usage_proxy persisted (output_chars > 0 reflects the consumed
        # usage from the exception's usage_proxy). experiment_type is
        # the schema enum value; step lives in the <step:NAME> token.
        assert len(rows) == 1
        assert rows[0]["experiment_type"] == "research_proxy"
        assert _step_of(rows[0]) == "question"
        assert rows[0]["status"] == "blocked"
        assert rows[0]["output_chars"] > 0
    finally:
        store.close()
