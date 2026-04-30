from __future__ import annotations

import re
from pathlib import Path

PROMPT_DIR = Path("prompts")
UNTRUSTED_VARIABLES = {
    "paper_context",
    "kaggle_discussion_context",
    "github_readme_context",
    "public_notebook_context",
    "web_context",
    "log_context",
}
VAR_RE_TEMPLATE = r"{{\s*%s\s*}}"
BLOCK_RE = re.compile(r"<UNTRUSTED_SOURCE\b[^>]*>(.*?)</UNTRUSTED_SOURCE>", re.DOTALL)


def variable_positions(text: str, variable: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(VAR_RE_TEMPLATE % re.escape(variable), text)]


def block_spans(text: str) -> list[tuple[int, int]]:
    return [m.span(1) for m in BLOCK_RE.finditer(text)]


def inside_any_block(span: tuple[int, int], blocks: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(block_start <= start and end <= block_end for block_start, block_end in blocks)


def main() -> None:
    failures: list[str] = []
    for path in sorted(PROMPT_DIR.glob("*.j2")):
        text = path.read_text(encoding="utf-8")
        spans = block_spans(text)
        for variable in UNTRUSTED_VARIABLES:
            for pos in variable_positions(text, variable):
                if not spans:
                    failures.append(
                        f"{path}: {variable} appears without any UNTRUSTED_SOURCE block"
                    )
                elif not inside_any_block(pos, spans):
                    failures.append(f"{path}: {variable} appears outside UNTRUSTED_SOURCE block")
        if "<UNTRUSTED_SOURCE" in text and "</UNTRUSTED_SOURCE>" not in text:
            failures.append(f"{path}: opening UNTRUSTED_SOURCE without closing tag")
        print(f"ok prompt {path}")
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
