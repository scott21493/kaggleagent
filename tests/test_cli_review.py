# tests/test_cli_review.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _run_research_proxy_first(runner: CliRunner) -> None:
    """Bootstrap a research-proxy run so we have an implementation row
    to review."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"],
    )
    assert result.exit_code == 0, result.output


def test_arena_review_happy_path(fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """arena review against a research-proxy impl row succeeds, persists
    a row with <step:review> token, emits valid research_review.json."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    # The impl row from research-proxy is exp_0004.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output

    # New review experiment row exists at exp_0005.
    rev_workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0005"
    assert (rev_workspace / "research_review.json").exists()

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rows = (
            store._require_conn()
            .execute(
                "SELECT experiment_id, experiment_type, status, artifact_paths "
                "FROM experiments WHERE competition_slug = ? ORDER BY experiment_id",
                ("tabular_binary_v1",),
            )
            .fetchall()
        )
        # 4 research-proxy rows + 1 review row.
        assert len(rows) == 5
        rev_row = rows[-1]
        assert rev_row["experiment_id"] == "exp_0005"
        assert rev_row["experiment_type"] == "research_proxy"
        assert rev_row["status"] == "completed"
        paths = json.loads(rev_row["artifact_paths"])
        assert paths[0] == "<step:review>"
        assert any(p.endswith("research_review.json") for p in paths)
    finally:
        store.close()


def test_arena_review_missing_impl_experiment(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--experiment <exp_id> must exist in the scoreboard."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_9999",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "exp_9999" in result.output


def test_arena_review_impl_row_missing_fusion_id_token(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewing a non-research-proxy row (e.g., calibration) must fail
    cleanly: calibration rows lack the <fusion_id:...> token."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["plan", "tabular_binary_v1"])
    runner.invoke(app, ["run-next", "tabular_binary_v1", "--provider", "stub_codex"])
    # exp_0001 is the calibration row — no fusion_id token.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0001",
        ],
    )
    assert result.exit_code != 0
    assert "fusion" in result.output.lower()


def test_arena_review_blocks_on_fixture_digest_drift(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR4 reproducibility: arena review runs the same precheck as
    arena research-proxy. Mutating train.csv after the baseline is
    recorded must halt with BLOCKED_REPRODUCIBILITY before any
    provider invocation."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    train = fixture_workspace / "fixtures" / "tabular_binary_v1" / "train.csv"
    train.write_text("id,x1,x2,target\n0,0,0,0\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2
    assert "fixture digest drift" in result.output.lower()


def test_arena_review_tags_provider_version_drift(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When stub_claude's version changes after the baseline is
    recorded, the review row carries <PROVIDER_VERSION_CHANGED:from=...>
    in its artifact_paths."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    monkeypatch.setattr("arena.providers.stub_claude._VERSION", "stub_claude.v2")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        row = (
            store._require_conn()
            .execute(
                "SELECT artifact_paths FROM experiments WHERE experiment_id = ?",
                ("exp_0005",),
            )
            .fetchone()
        )
        paths = json.loads(row["artifact_paths"])
        assert any(p.startswith("<PROVIDER_VERSION_CHANGED:from=stub_claude.v1>") for p in paths), (
            paths
        )
    finally:
        store.close()


def test_arena_review_blocks_on_kill_switch(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARENA_KILL_SWITCH halts arena review at check_can_invoke; no
    scoreboard row inserted."""
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)
    monkeypatch.setenv("ARENA_KILL_SWITCH", "1")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2
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
        # Only the 4 research-proxy rows; no review row inserted.
        assert len(rows) == 4
    finally:
        store.close()


def test_arena_review_attaches_to_impl_rows_run_not_latest(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cross-run linkage: stub fusion_id is deterministic
    (fusion_0001), so a second `arena init-fixture` + new research-proxy
    creates a new run with the SAME fusion_id. `arena review --experiment
    <impl from first run>` must attach to the FIRST run, not the latest,
    AND must locate the fusion row from the FIRST run."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)
    # Capture the first run's id from exp_0004.
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        first_run = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE experiment_id = ?",
                ("exp_0004",),
            )
            .fetchone()["run_id"]
        )
    finally:
        store.close()

    # Start a second run; produces exp_0005..exp_0008 with a different
    # run_id. _new_run_id uses microsecond precision so consecutive
    # init-fixture calls always yield distinct run_ids without sleeping.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])

    # Review the FIRST run's impl row. The new review row should be
    # attached to first_run, not the latest run.
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 0, result.output
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        rev_row = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE experiment_id = ?",
                ("exp_0009",),
            )
            .fetchone()
        )
        assert rev_row is not None
        assert rev_row["run_id"] == first_run
    finally:
        store.close()


def test_arena_review_persists_post_invoke_budget_blocked_row_with_usage(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-invoke BudgetExceeded (run-level output_chars cap=1) must
    persist a blocked row WITH usage_proxy threaded through. Mirrors
    the equivalent research-proxy regression."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _run_research_proxy_first(runner)

    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    result = runner.invoke(
        app,
        [
            "review",
            "tabular_binary_v1",
            "--provider",
            "stub_claude",
            "--experiment",
            "exp_0004",
        ],
    )
    assert result.exit_code == 2

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        row = (
            store._require_conn()
            .execute(
                "SELECT status, output_chars, artifact_paths "
                "FROM experiments WHERE experiment_id = ?",
                ("exp_0005",),
            )
            .fetchone()
        )
        assert row is not None
        assert row["status"] == "blocked"
        assert row["output_chars"] > 0
        paths = json.loads(row["artifact_paths"])
        assert paths[0] == "<step:review>"
    finally:
        store.close()
