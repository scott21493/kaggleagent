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


def test_render_diff_modify_only_edits_within_namespace_section(
    tmp_path: Path,
) -> None:
    """A research/ modify proposal whose prior_claim text ALSO appears
    in the invariants/ section earlier in the wiki must edit ONLY the
    research section's line. A naive substring-on-the-whole-wiki
    search would silently edit the invariants line because it appears
    first. P2 regression — caught by reviewer.
    """
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "# Memory Wiki\n\ninvariants/\n  Shared claim text.\nresearch/\n  Shared claim text.\n",
        encoding="utf-8",
    )
    proposal = _proposal()
    proposal["operation"] = "modify"
    proposal["claim"] = "New claim text."
    proposal["prior_claim"] = "Shared claim text."
    out = render_diff(proposal, wiki_path=wiki)

    # Locate the unified-diff "-" line (the one being removed). The
    # context line IMMEDIATELY ABOVE the change in the diff must be
    # `research/`, NOT `invariants/`.
    diff_lines = out.splitlines()
    minus_idx = next(
        i
        for i, line in enumerate(diff_lines)
        if line.startswith("-") and not line.startswith("---") and "Shared claim text" in line
    )
    # Walk backwards through context lines (prefix " ") to find the most
    # recent namespace heading.
    last_namespace = None
    for line in reversed(diff_lines[:minus_idx]):
        if line.startswith(" "):
            value = line[1:].rstrip()
            if value.endswith("/") and "/" not in value[:-1]:
                last_namespace = value
                break
    assert last_namespace == "research/", (
        f"modify edited the wrong namespace section "
        f"(found context heading {last_namespace!r}, expected 'research/'); "
        f"full diff:\n{out}"
    )


def test_render_diff_modify_with_prior_claim_only_in_other_namespace_is_noop(
    tmp_path: Path,
) -> None:
    """If prior_claim appears ONLY in a different namespace, the
    proposal's target namespace has nothing to edit, so render_diff
    must produce an empty diff (no false-positive cross-namespace
    edit). Same P2 class.
    """
    wiki = tmp_path / "wiki.md"
    wiki.write_text(
        "invariants/\n  Inv-only claim.\nresearch/\n  Different.\n",
        encoding="utf-8",
    )
    proposal = _proposal()
    proposal["operation"] = "modify"
    proposal["claim"] = "Should not appear."
    proposal["prior_claim"] = "Inv-only claim."  # only in invariants/
    out = render_diff(proposal, wiki_path=wiki)
    # Empty diff: the research/ section has no line containing the
    # prior_claim, so after_lines == before_lines.
    assert out == "", f"Expected empty diff; got:\n{out}"


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
