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


def test_render_diff_appends_fresh_namespace_when_section_absent(
    tmp_path: Path,
) -> None:
    """If the wiki lacks the proposal's namespace section, render_diff
    appends a fresh `<namespace>/` section at the end (instead of
    silently dropping the change). Pins the no-section branch in
    arena/memory/diff.py."""
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "# Memory Wiki\n\ninvariants/\n  Some invariant.\n",
        encoding="utf-8",
    )
    out = render_diff(_proposal(), wiki_path=wiki)
    # The diff must include a new `research/` section header AND the claim.
    assert "research/" in out
    assert "Stack diverse base learners" in out


def test_render_diff_modify_replaces_prior_claim_line(tmp_path: Path) -> None:
    """For operation=modify, render_diff locates the prior_claim
    substring and replaces that line with the new claim. Pins the
    modify branch (otherwise dead code in PR6 since the synthesizer
    only emits add)."""
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "# Memory Wiki\n\nresearch/\n  [mem_0001] Old claim.\n",
        encoding="utf-8",
    )
    proposal = _proposal()
    proposal["operation"] = "modify"
    proposal["claim"] = "New claim text."
    proposal["prior_claim"] = "Old claim."
    out = render_diff(proposal, wiki_path=wiki)
    assert "New claim text" in out
    # The diff must SHOW the old line being removed (with `-` prefix in unified diff).
    assert "-  [mem_0001] Old claim." in out
