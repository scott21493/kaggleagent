# tests/test_cli_memory_propose.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _bootstrap_review(runner: CliRunner) -> None:
    """Bootstrap a scoreboard with a research-proxy chain + a review row
    so memory propose has an artifact to read. Review row lands at exp_0005."""
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    runner.invoke(
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


def test_memory_propose_happy_path(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arena memory propose against a review row writes a schema-valid
    memory_update.json + emits memory_proposal_created trace event +
    creates NO scoreboard row."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    result = runner.invoke(
        app,
        ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"],
    )
    assert result.exit_code == 0, result.output

    proposal_path = fixture_workspace / "memory" / "proposals" / "mem_0001.json"
    assert proposal_path.exists()
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert payload["proposal_id"] == "mem_0001"
    assert payload["namespace"] == "research"
    assert payload["review_status"] == "proposed"


def test_memory_propose_inserts_no_scoreboard_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-only action: memory propose must NOT inflate
    COUNT(*) (preserves PR5's provider_calls invariant)."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        before = (
            store._require_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM experiments WHERE competition_slug = ?",
                ("tabular_binary_v1",),
            )
            .fetchone()["n"]
        )
    finally:
        store.close()

    runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])

    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        after = (
            store._require_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM experiments WHERE competition_slug = ?",
                ("tabular_binary_v1",),
            )
            .fetchone()["n"]
        )
    finally:
        store.close()
    assert after == before


def test_memory_propose_falls_through_to_follow_up_recommendations_when_required_fixes_empty(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default stub review has required_fixes=[] but a NON-EMPTY
    follow_up_recommendations list. memory propose's synthesizer
    treats that as actionable (per Task 3's
    test_synthesize_falls_through_to_follow_up_recommendations_when_required_fixes_empty
    contract) and lifts recs[0] into the claim. The proposal must
    still be schema-valid and review_status=proposed."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    result = runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    assert result.exit_code == 0
    payload = json.loads(
        (fixture_workspace / "memory" / "proposals" / "mem_0001.json").read_text(encoding="utf-8")
    )
    # Default stub review's first follow_up_recommendation references
    # real Codex; the synthesizer's fall-through path lifts that text
    # verbatim into payload["claim"].
    assert "real codex" in payload["claim"].lower()
    assert payload["review_status"] == "proposed"


def test_memory_propose_emits_no_op_when_review_has_neither_fixes_nor_recommendations(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When BOTH required_fixes AND follow_up_recommendations are
    empty, the synthesizer's no-op-observation branch fires:
    claim="No actionable findings from review {review_id}.", confidence
    and risk both "low". The default stub review has non-empty
    follow_up_recommendations, so we overwrite the artifact on disk to
    truly empty both lists before running memory propose. Pins the no-op
    CLI path that's separate from the actionable fall-through branch.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    # Overwrite the review artifact: empty BOTH actionable lists.
    rr_path = (
        fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0005" / "research_review.json"
    )
    review_payload = json.loads(rr_path.read_text(encoding="utf-8"))
    review_payload["required_fixes"] = []
    review_payload["follow_up_recommendations"] = []
    rr_path.write_text(json.dumps(review_payload), encoding="utf-8")

    result = runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    assert result.exit_code == 0, result.output
    proposal = json.loads(
        (fixture_workspace / "memory" / "proposals" / "mem_0001.json").read_text(encoding="utf-8")
    )
    assert "no actionable findings" in proposal["claim"].lower()
    assert proposal["confidence"] == "low"
    assert proposal["risk"] == "low"


def test_memory_propose_missing_review_experiment(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--review <exp_id> must exist."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    result = runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_9999"])
    assert result.exit_code != 0
    assert "exp_9999" in result.output or "not found" in result.output.lower()


def test_memory_propose_rejects_schema_invalid_review(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A research_review.json that is syntactically valid JSON but
    fails schema validation (missing a required field) must surface as
    a clean typer.BadParameter, not an unhandled ValidationError.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)

    # Locate the review row's research_review.json and overwrite it
    # with a payload missing the required `decision` field.
    rev_workspace = fixture_workspace / "worktrees" / "tabular_binary_v1" / "exp_0005"
    rr_path = rev_workspace / "research_review.json"
    assert rr_path.exists()
    rr_path.write_text(
        json.dumps(
            {
                "schema_version": "research_review.v1",
                "review_id": "rr_0001",
                "competition_slug": "tabular_binary_v1",
                "subject_id": "exp_0004",
                # decision intentionally missing
                "summary": "10+ char summary",
                "strengths": [],
                "weaknesses": [],
                "required_fixes": [],
                "follow_up_recommendations": [],
                "risk_level": "low",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    assert result.exit_code != 0
    # Tighten to AND so the test detects a regression where exc.message
    # stops surfacing the missing-field name. The CLI wraps the schema
    # error as `... is schema-invalid: <exc.message>`; both the wrapper
    # phrase AND the missing field name (`decision`) must appear.
    assert "schema-invalid" in result.output.lower()
    assert "decision" in result.output


def test_memory_propose_id_is_monotonic(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First call mints mem_0001; second call (against the same review)
    mints mem_0002. Filesystem-scan based; no race condition in this
    test since CliRunner is sequential."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    proposals_dir = fixture_workspace / "memory" / "proposals"
    files = sorted(p.name for p in proposals_dir.iterdir())
    assert files == ["mem_0001.json", "mem_0002.json"]


def test_memory_propose_trace_event_attaches_to_review_run_not_latest(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cross-run linkage: arena memory propose has no
    scoreboard row, so the memory_proposal_created trace event is the
    only durable linkage to the review row. The event's run_id MUST be
    the review row's run, not _latest_run_id().

    Bootstrap a review under run_A. Start a second `arena init-fixture`
    + research-proxy under run_B. Run `arena memory propose
    --review <exp from run_A>` — the trace event MUST land under
    traces/run_A/, not traces/run_B/.
    """
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_review(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        run_a = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE experiment_id = ?",
                ("exp_0005",),
            )
            .fetchone()["run_id"]
        )
    finally:
        store.close()

    # Second run.
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        run_b = (
            store._require_conn()
            .execute(
                "SELECT run_id FROM experiments WHERE experiment_id = ?",
                ("exp_0006",),
            )
            .fetchone()["run_id"]
        )
    finally:
        store.close()
    assert run_a != run_b

    # Memory propose against the run_A review — event must land under run_A.
    result = runner.invoke(app, ["memory", "propose", "tabular_binary_v1", "--review", "exp_0005"])
    assert result.exit_code == 0, result.output

    # Memory propose has no task_id, so the trace event lands in
    # <run>/run.jsonl (TraceStore's run-level layout), not under a
    # task subdir. Walk all *.jsonl trace files so we discover both
    # run-level and task-level events.
    found_in_a = False
    found_in_b = False
    traces_root = fixture_workspace / "traces"
    for jsonl in traces_root.rglob("*.jsonl"):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt.get("event_type") != "memory_proposal_created":
                continue
            if evt["run_id"] == run_a:
                found_in_a = True
            elif evt["run_id"] == run_b:
                found_in_b = True
    assert found_in_a, "memory_proposal_created not found under review's run"
    assert not found_in_b, "memory_proposal_created leaked into latest run's trace"
