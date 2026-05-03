# arena/memory/diff.py
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

# A namespace heading is a column-0 line of the form "<lower_word>/"
# (e.g., "research/", "invariants/"). The unified memory wiki at
# docs/memory/UNIFIED_MEMORY_WIKI.md uses this convention. Markdown
# `##` headings also act as section terminators.
_NAMESPACE_HEADING_RE = re.compile(r"^[a-z][a-z0-9_]*/\s*$")
_MARKDOWN_HEADING_PREFIX = "#"


def _find_section_range(after_lines: list[str], section_marker: str) -> tuple[int, int] | None:
    """Find the [start, end) line range covered by the namespace
    `section_marker` (e.g. "research/") in `after_lines`.

    `start` is the line AFTER the heading (first content line of the
    section). `end` is the line index of the next namespace heading,
    or the next markdown `#` heading, or len(after_lines) if neither
    appears.

    Returns None if the namespace heading is not present.
    """
    section_start: int | None = None
    section_end = len(after_lines)
    for i, line in enumerate(after_lines):
        stripped = line.rstrip("\n").rstrip()
        if section_start is None:
            if stripped == section_marker.rstrip():
                section_start = i + 1  # First line AFTER the heading
            continue
        # We're inside the section; check end-of-section markers.
        if _NAMESPACE_HEADING_RE.match(stripped) and stripped != section_marker.rstrip():
            section_end = i
            break
        if stripped.startswith(_MARKDOWN_HEADING_PREFIX):
            section_end = i
            break
    if section_start is None:
        return None
    return section_start, section_end


def render_diff(
    proposal: dict[str, Any],
    wiki_path: Path = Path("docs/memory/UNIFIED_MEMORY_WIKI.md"),
) -> str:
    """Render a unified-diff-style string showing what `proposal` would
    change in the unified memory wiki, scoped to the proposal's
    namespace.

    Pure function: never mutates `wiki_path`. The caller is responsible
    for any actual merge — this only renders what the merge would look
    like.

    For PR6, the diff treats the proposal as an `add` to the namespace
    section: the synthesized "after" text inserts the claim under the
    namespace heading. modify/deprecate/remove are rendered analogously
    using `prior_claim` to locate the line to change. The line search
    is STRICTLY scoped to the proposal's namespace section — if the
    same `prior_claim` text appears in a different namespace earlier
    in the wiki, that line is NOT touched. The wiki itself is
    read-only; the caller decides whether to apply the diff.
    """
    namespace = proposal.get("namespace", "")
    claim = proposal.get("claim", "")
    operation = proposal.get("operation", "add")
    prior_claim = proposal.get("prior_claim") or ""
    proposal_id = proposal.get("proposal_id", "")

    wiki_text = wiki_path.read_text(encoding="utf-8")
    before_lines = wiki_text.splitlines(keepends=True)

    after_lines: list[str] = list(before_lines)
    section_marker = f"{namespace}/"
    section_range = _find_section_range(after_lines, section_marker)

    new_line = f"  [{proposal_id}] {claim}\n"
    if operation == "add":
        if section_range is not None:
            # Insert just after the namespace heading.
            after_lines.insert(section_range[0], new_line)
        else:
            # No section yet — append a fresh one at the end.
            after_lines.append(f"\n{section_marker}\n{new_line}")
    elif (
        operation in {"modify", "deprecate", "remove"} and prior_claim and section_range is not None
    ):
        # Find the prior_claim line within the namespace section ONLY
        # — never edit a line that happens to contain the same text in
        # another namespace section. If the namespace section doesn't
        # exist OR the prior_claim isn't in it, the diff is empty (no
        # mutation, no false-positive elsewhere).
        section_start, section_end = section_range
        for i in range(section_start, section_end):
            line = after_lines[i]
            if prior_claim in line:
                if operation == "remove":
                    after_lines[i] = ""
                elif operation == "deprecate":
                    after_lines[i] = f"  [DEPRECATED via {proposal_id}] {prior_claim}\n"
                else:  # modify
                    after_lines[i] = new_line
                break

    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=str(wiki_path),
        tofile=f"{wiki_path} (after {proposal_id})",
        n=3,
    )
    return "".join(diff)
