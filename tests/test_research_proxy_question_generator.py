# tests/test_research_proxy_question_generator.py
from __future__ import annotations

import pytest

from arena.research_proxy.question_generator import (
    generate_research_question,
    make_research_question_packet,
)
from arena.schemas.validate import validate


def test_generate_research_question_returns_schema_valid() -> None:
    question = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
    )
    validate("research_question", question)
    assert question["question_id"] == "rq_0001"
    assert question["competition_slug"] == "tabular_binary_v1"
    assert len(question["expected_mechanisms"]) >= 1


def test_generate_research_question_id_pattern() -> None:
    """question_id must match ^rq_[0-9]{4,}$ per schema."""
    question = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="rq_9999",
        source_refs=["fixtures/method_note.md"],
    )
    validate("research_question", question)
    # Reject malformed id at construction time.
    from jsonschema import ValidationError

    bad = generate_research_question(
        competition_slug="tabular_binary_v1",
        question_id="not_an_rq_id",
        source_refs=["fixtures/method_note.md"],
    )
    with pytest.raises(ValidationError):
        validate("research_question", bad)


def test_make_research_question_packet_is_schema_valid_task_packet() -> None:
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_2026_05_02_001",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
    )
    validate("task_packet", packet)
    assert packet["role"] == "research_proxy"
    assert packet["phase"] == "RESEARCH_QUESTION_CREATED"
    assert packet["competition_slug"] == "tabular_binary_v1"
    assert packet["experiment_id"] == "exp_0001"


def test_make_research_question_packet_includes_method_notes_in_inputs() -> None:
    """The method notes the question references should be in the packet's
    inputs list so the planner/sandbox sees them as readable inputs."""
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_x",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=[
            "fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
            "fixtures/tabular_binary_v1/paper_bundle/method_note_002.md",
        ],
    )
    for ref in packet.get("inputs", []):
        # All inputs are workspace-relative paths.
        assert not ref.startswith("/")
    assert any("method_note_001.md" in p for p in packet["inputs"])
    assert any("method_note_002.md" in p for p in packet["inputs"])
