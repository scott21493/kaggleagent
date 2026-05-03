# tests/test_cli_self_improve_scan.py
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.scoreboard.store import ScoreboardStore


def _bootstrap_clean(runner: CliRunner) -> None:
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])


def test_self_improve_scan_clean_run_emits_no_proposals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean scoreboard produces zero proposals + no sentinel."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    result = runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    assert result.exit_code == 0, result.output
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    assert (not proposals_dir.exists()) or (not list(proposals_dir.iterdir()))
    assert not (fixture_workspace / "SELF_IMPROVEMENT_FROZEN.md").exists()


def test_self_improve_scan_fires_freeze_on_blocked_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked row in the scoreboard triggers a finding + sentinel +
    proposal artifact."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    result = runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    assert result.exit_code == 0
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    assert proposals_dir.exists()
    assert any(p.suffix == ".json" for p in proposals_dir.iterdir())
    sentinel = fixture_workspace / "SELF_IMPROVEMENT_FROZEN.md"
    assert sentinel.exists()


def test_self_improve_scan_inserts_no_scoreboard_row(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller-only: COUNT(*) of experiments must be unchanged."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        before = (
            store._require_conn().execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"]
        )
    finally:
        store.close()
    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    store = ScoreboardStore(fixture_workspace / "scoreboard.sqlite")
    store.connect()
    try:
        after = (
            store._require_conn().execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"]
        )
    finally:
        store.close()
    assert before == after


def test_self_improve_scan_emits_trace_event_with_allowed_keys(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trace event payload uses ONLY keys permitted by event.schema.json
    (additionalProperties: false). Otherwise TraceStore.emit would
    reject."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    _bootstrap_clean(runner)
    result = runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    assert result.exit_code == 0

    # Find the events.jsonl(s) and confirm the scan-completed event is
    # present and validates against the schema.
    traces_root = fixture_workspace / "traces"
    found = False
    for jsonl in traces_root.rglob("events.jsonl"):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evt = json.loads(line)
            if evt["event_type"] == "self_improvement_scan_completed":
                # Allowed keys per event.schema.json payload set.
                allowed = {
                    "message",
                    "phase",
                    "status",
                    "reason",
                    "paths",
                    "evidence",
                    "path",
                }
                assert set(evt["payload"].keys()) <= allowed, evt["payload"]
                found = True
    assert found, "self_improvement_scan_completed not found in any trace"


def test_self_improve_scan_idempotent_no_duplicate_proposals(
    fixture_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running scan twice against the same blocked-row state produces
    the same set of proposals — no duplicates."""
    monkeypatch.delenv("ARENA_KILL_SWITCH", raising=False)
    monkeypatch.delenv("ARENA_NETWORK_DOMAINS_ALLOWED", raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init-fixture", "tabular_binary_v1"])
    monkeypatch.setenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", "1")
    runner.invoke(app, ["research-proxy", "tabular_binary_v1", "--provider", "stub_claude"])
    monkeypatch.delenv("ARENA_PHASE0_OUTPUT_CHARS_CAP", raising=False)

    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    proposals_dir = fixture_workspace / "self_improvement" / "proposals"
    after_first = sorted(p.name for p in proposals_dir.iterdir())
    runner.invoke(app, ["self-improve", "scan", "tabular_binary_v1"])
    after_second = sorted(p.name for p in proposals_dir.iterdir())
    assert after_first == after_second
