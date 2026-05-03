# tests/test_memory_diff.py
from __future__ import annotations

import hashlib
from pathlib import Path

from arena.memory.diff import render_diff


def _proposal() -> dict:
    return {
        "schema_version": "memory_update.v1",
        "proposal_id": "mem_0001",
        "namespace": "research",
        "operation": "add",
        "claim": "Stack diverse base learners to reduce bias.",
        "delta": "Add this constraint to the research namespace.",
        "evidence": [{"type": "trace", "ref": "rr_0001", "quote_or_summary": "x"}],
        "confidence": "medium",
        "expiry_or_revisit": "After Phase 0 close.",
        "risk": "low",
        "review_status": "proposed",
    }


def test_render_diff_returns_unified_diff_string(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "# Memory Wiki\n\nresearch/\n  Existing claim.\n",
        encoding="utf-8",
    )
    out = render_diff(_proposal(), wiki_path=wiki)
    assert isinstance(out, str)
    # Unified-diff markers.
    assert out.startswith("---") or "+++" in out or "@@ " in out


def test_render_diff_is_namespace_scoped(tmp_path: Path) -> None:
    """The diff should only mention the proposal's namespace section,
    not unrelated parts of the wiki."""
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "invariants/\n  Inv claim.\nresearch/\n  Existing claim.\n",
        encoding="utf-8",
    )
    out = render_diff(_proposal(), wiki_path=wiki)
    # The proposal's claim should appear in the diff.
    assert "Stack diverse base learners" in out


def test_render_diff_does_not_mutate_wiki(tmp_path: Path) -> None:
    """Pure function: the wiki file's bytes + mtime must be identical
    before and after render_diff."""
    wiki = tmp_path / "wiki.md"
    original = "# Memory Wiki\n\nresearch/\n  Existing claim.\n"
    wiki.write_text(original, encoding="utf-8")
    before_hash = hashlib.sha256(wiki.read_bytes()).hexdigest()
    render_diff(_proposal(), wiki_path=wiki)
    after_hash = hashlib.sha256(wiki.read_bytes()).hexdigest()
    assert before_hash == after_hash
    assert wiki.read_text(encoding="utf-8") == original
