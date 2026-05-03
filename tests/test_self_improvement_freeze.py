# tests/test_self_improvement_freeze.py
from __future__ import annotations

import json
import re
from pathlib import Path

from arena.self_improvement.freeze import (
    apply_freeze,
    evaluate_freeze,
    is_frozen,
)
from arena.self_improvement.scan import Finding


def test_evaluate_freeze_clean_findings() -> None:
    decision = evaluate_freeze([])
    assert decision.frozen is False


def test_evaluate_freeze_fires_on_any_finding() -> None:
    findings = [
        Finding(
            kind="score_regression",
            severity="high",
            problem="x",
            evidence_refs=["scoreboard:exp_0004"],
        )
    ]
    decision = evaluate_freeze(findings)
    assert decision.frozen is True
    assert any(t["kind"] == "score_regression" for t in decision.triggers)


def test_apply_freeze_writes_sentinel(tmp_path: Path) -> None:
    """apply_freeze writes a Markdown body with a fenced JSON metadata
    block, AND nothing else."""
    findings = [
        Finding(
            kind="blocked_row",
            severity="medium",
            problem="task_0001 blocked",
            evidence_refs=["scoreboard:exp_0001"],
        )
    ]
    decision = evaluate_freeze(findings)
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    apply_freeze(decision, sentinel_path=sentinel, competition_slug="tabular_binary_v1")
    assert sentinel.exists()
    content = sentinel.read_text(encoding="utf-8")
    assert content.startswith("# Self-Improvement Frozen")
    # JSON metadata block — extract via fenced code block boundary.
    m = re.search(r"```json\n(.+?)\n```", content, re.DOTALL)
    assert m is not None
    metadata = json.loads(m.group(1))
    assert metadata["frozen"] is True
    assert metadata["competition_slug"] == "tabular_binary_v1"
    assert any(t["kind"] == "blocked_row" for t in metadata["triggers"])


def test_is_frozen_after_apply(tmp_path: Path) -> None:
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    assert is_frozen(sentinel_path=sentinel) is False
    findings = [Finding(kind="score_regression", severity="high", problem="x", evidence_refs=["e"])]
    decision = evaluate_freeze(findings)
    apply_freeze(decision, sentinel_path=sentinel, competition_slug="x")
    assert is_frozen(sentinel_path=sentinel) is True


def test_unfreeze_via_sentinel_deletion(tmp_path: Path) -> None:
    """Deleting the sentinel marks the system unfrozen (operator action;
    no built-in unfreeze command in PR6)."""
    sentinel = tmp_path / "SELF_IMPROVEMENT_FROZEN.md"
    findings = [Finding(kind="score_regression", severity="high", problem="x", evidence_refs=["e"])]
    apply_freeze(
        evaluate_freeze(findings),
        sentinel_path=sentinel,
        competition_slug="x",
    )
    assert is_frozen(sentinel_path=sentinel) is True
    sentinel.unlink()
    assert is_frozen(sentinel_path=sentinel) is False
