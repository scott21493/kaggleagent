# arena/memory/diff.py
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


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
    using `prior_claim` to locate the line to change. The wiki itself
    is read-only; the caller decides whether to apply the diff.
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
    insertion_index = -1
    for i, line in enumerate(after_lines):
        if line.strip().startswith(section_marker):
            # Insert just after the section header.
            insertion_index = i + 1
            break

    new_line = f"  [{proposal_id}] {claim}\n"
    if operation == "add":
        if insertion_index >= 0:
            after_lines.insert(insertion_index, new_line)
        else:
            # No section yet — append a fresh one at the end.
            after_lines.append(f"\n{section_marker}\n{new_line}")
    elif operation in {"modify", "deprecate", "remove"} and prior_claim:
        # Find the prior_claim line within the namespace section and
        # replace / annotate it.
        for i, line in enumerate(after_lines):
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
