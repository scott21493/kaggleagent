# tests/test_packet_builders_provider_threading.py
"""[P7] Regression coverage for the post-PR7 fix that threads `provider`
through the four research-proxy / review packet builders.

Pre-fix, all four builders hardcoded ``"provider": "stub_claude"`` in
the returned packet. PR7 widened the CLI gates to accept
``--provider claude`` but the packet's ``provider`` field still said
``"stub_claude"``, leaving an internal inconsistency that future
queue/run-next-style validation (``peeked["provider"] != adapter.name``)
would reject.

Each test asserts that:
  - default ``provider="stub_claude"`` is preserved (back-compat).
  - explicit ``provider="claude"`` propagates to the packet.
  - the schema-required ``provider`` field is the value the caller
    passed, so the queued packet matches the resolved real adapter.
"""

from __future__ import annotations

import pytest

from arena.research_proxy.fusion_proposal import make_fusion_proposal_packet
from arena.research_proxy.method_digest import make_method_digest_packet
from arena.research_proxy.question_generator import make_research_question_packet
from arena.review.packet import make_review_packet
from arena.schemas.validate import validate as validate_schema


@pytest.mark.parametrize("provider", ["stub_claude", "claude"])
def test_research_question_packet_threads_provider(provider: str) -> None:
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
        provider=provider,
    )
    assert packet["provider"] == provider
    validate_schema("task_packet", packet)


def test_research_question_packet_default_provider_is_stub_claude() -> None:
    packet = make_research_question_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0001",
        task_id="task_0001",
        question_id="rq_0001",
        source_refs=["fixtures/tabular_binary_v1/paper_bundle/method_note_001.md"],
    )
    assert packet["provider"] == "stub_claude"


@pytest.mark.parametrize("provider", ["stub_claude", "claude"])
def test_method_digest_packet_threads_provider(provider: str) -> None:
    packet = make_method_digest_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0002",
        task_id="task_0002",
        digest_id="pd_0001",
        method_note_path="fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
        provider=provider,
    )
    assert packet["provider"] == provider
    validate_schema("task_packet", packet)


def test_method_digest_packet_default_provider_is_stub_claude() -> None:
    packet = make_method_digest_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0002",
        task_id="task_0002",
        digest_id="pd_0001",
        method_note_path="fixtures/tabular_binary_v1/paper_bundle/method_note_001.md",
    )
    assert packet["provider"] == "stub_claude"


@pytest.mark.parametrize("provider", ["stub_claude", "claude"])
def test_fusion_proposal_packet_threads_provider(provider: str) -> None:
    packet = make_fusion_proposal_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0003",
        task_id="task_0003",
        fusion_id="fusion_0001",
        digest_path="worktrees/tabular_binary_v1/exp_0002/paper_digest.json",
        provider=provider,
    )
    assert packet["provider"] == provider
    validate_schema("task_packet", packet)


def test_fusion_proposal_packet_default_provider_is_stub_claude() -> None:
    packet = make_fusion_proposal_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0003",
        task_id="task_0003",
        fusion_id="fusion_0001",
        digest_path="worktrees/tabular_binary_v1/exp_0002/paper_digest.json",
    )
    assert packet["provider"] == "stub_claude"


@pytest.mark.parametrize("provider", ["stub_claude", "claude"])
def test_review_packet_threads_provider(provider: str) -> None:
    packet = make_review_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0006",
        task_id="task_0006",
        review_id="rr_0001",
        subject_experiment_id="exp_0005",
        fusion_proposal_path="worktrees/tabular_binary_v1/exp_0003/fusion_proposal.json",
        submission_path="worktrees/tabular_binary_v1/exp_0005/submission.csv",
        provider=provider,
    )
    assert packet["provider"] == provider
    validate_schema("task_packet", packet)


def test_review_packet_default_provider_is_stub_claude() -> None:
    packet = make_review_packet(
        competition_slug="tabular_binary_v1",
        run_id="run_test",
        experiment_id="exp_0006",
        task_id="task_0006",
        review_id="rr_0001",
        subject_experiment_id="exp_0005",
        fusion_proposal_path="worktrees/tabular_binary_v1/exp_0003/fusion_proposal.json",
        submission_path="worktrees/tabular_binary_v1/exp_0005/submission.csv",
    )
    assert packet["provider"] == "stub_claude"
