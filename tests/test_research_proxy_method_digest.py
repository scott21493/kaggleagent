# tests/test_research_proxy_method_digest.py
from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import ValidationError

from arena.research_proxy.method_digest import (
    make_method_digest_packet,
    read_method_note,
    validate_paper_digest,
)
from arena.schemas.validate import validate


def test_read_method_note_returns_file_contents(tmp_path: Path) -> None:
    note = tmp_path / "method_note_test.md"
    note.write_text("# Test method note\n\nA mechanism description.", encoding="utf-8")
    assert read_method_note(note).startswith("# Test method note")


def test_read_method_note_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_method_note(tmp_path / "no_such_note.md")


def test_make_method_digest_packet_is_schema_valid_task_packet() -> None:
    packet = make_method_digest_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        digest_id="pd_0001",
        method_note_path="fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "METHOD_DIGEST_CREATED"
    assert "method_note_001.md" in packet["inputs"][0]


def test_validate_paper_digest_accepts_valid_payload() -> None:
    payload = {
        "schema_version": "paper_digest.v1",
        "digest_id": "pd_0001",
        "source_id": "fixtures/method_note_001.md",
        "title": "Method Note 001 — Monotonic GBDTs",
        "source_type": "local_method_note",
        "trusted_status": "trusted_fixture",
        "mechanisms": [
            {
                "name": "monotonic_gbdt",
                "description": "Gradient-boosted trees with monotone constraints.",
                "why_it_might_help": "Reduces variance on small training sets.",
            }
        ],
        "assumptions": ["assumption A"],
        "datasets_or_tasks": ["tabular_binary_v1"],
        "metrics": ["ROC-AUC"],
        "implementation_clues": ["LightGBM monotone_constraints"],
        "failure_modes": ["non-monotonic truth"],
        "applicability": {
            "competition_slug": "tabular_binary_v1",
            "fit": "high",
            "reason": "Fixture features look monotonic in the target.",
        },
        "citations": [{"ref": "method_note_001.md", "summary": "Local trusted method note."}],
    }
    validate_paper_digest(payload)  # no raise


def test_validate_paper_digest_rejects_missing_required_field() -> None:
    payload = {
        "schema_version": "paper_digest.v1",
        # missing digest_id (required)
        "source_id": "x",
        "title": "x",
        "source_type": "local_method_note",
        "trusted_status": "trusted_fixture",
        "mechanisms": [],  # also bad: minItems=1
        "assumptions": [],
        "datasets_or_tasks": [],
        "metrics": [],
        "implementation_clues": [],
        "failure_modes": [],
        "applicability": {},
        "citations": [],
    }
    with pytest.raises(ValidationError):
        validate_paper_digest(payload)
