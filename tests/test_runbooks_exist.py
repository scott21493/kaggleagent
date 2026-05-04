"""Cheap existence/header coverage for the three Phase-0 runbooks.

Catches accidental delete/rename. Not a full doc-validation test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "docs" / "phase0" / "runbooks"


@pytest.mark.parametrize(
    "filename, title",
    [
        ("auth_expiry.md", "Auth"),
        ("reboot.md", "Reboot"),
        ("cli_regression.md", "CLI"),
    ],
)
def test_runbook_exists_and_has_title(filename: str, title: str) -> None:
    p = RUNBOOK_DIR / filename
    assert p.exists(), f"runbook missing: {p}"
    text = p.read_text(encoding="utf-8")
    # First non-empty line should be a level-1 header containing the title:
    first = next((line for line in text.splitlines() if line.strip()), "")
    assert first.startswith("# "), f"runbook {filename} missing top-level header"
    assert title.lower() in first.lower(), f"runbook {filename} header missing {title!r}"


def test_auth_expiry_runbook_documents_maintenance_loop() -> None:
    text = (RUNBOOK_DIR / "auth_expiry.md").read_text(encoding="utf-8")
    assert "Maintenance" in text or "maintenance" in text
    assert "AUTH_EXPIRY_PATTERNS" in text or "auth.py" in text


def test_reboot_runbook_does_not_invent_arena_resume() -> None:
    text = (RUNBOOK_DIR / "reboot.md").read_text(encoding="utf-8")
    assert "arena resume" not in text, (
        "reboot runbook must not reference a nonexistent `arena resume` command"
    )
