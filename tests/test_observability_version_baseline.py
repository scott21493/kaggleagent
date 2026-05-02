# tests/test_observability_version_baseline.py
from __future__ import annotations

from pathlib import Path

from arena.observability.version_baseline import record_provider_version


def test_first_call_records_baseline_returns_new_no_drift(tmp_path: Path) -> None:
    is_new, drifted_from = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    assert is_new is True
    assert drifted_from is None


def test_same_version_same_slug_returns_existing_no_drift(tmp_path: Path) -> None:
    record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    is_new, drifted_from = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    assert is_new is False
    assert drifted_from is None


def test_different_version_same_slug_returns_drift_with_old_version(tmp_path: Path) -> None:
    record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    is_new, drifted_from = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v2",
        root=tmp_path,
    )
    assert is_new is False
    assert drifted_from == "stub_codex.v1"


def test_baselines_are_per_slug(tmp_path: Path) -> None:
    """Baselines persist across run_ids for the same slug — drift across
    arena init-fixture cycles MUST be flagged."""
    record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    is_new, drifted_from = record_provider_version(
        competition_slug="image_classification_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    # Different slug = baseline starts fresh; drift is per-slug.
    assert is_new is True
    assert drifted_from is None


def test_baselines_are_per_provider(tmp_path: Path) -> None:
    record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    is_new, drifted_from = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_claude",
        version="stub_claude.v1",
        root=tmp_path,
    )
    # Different provider in same slug = first time for that provider.
    assert is_new is True
    assert drifted_from is None


def test_drift_is_sticky_across_subsequent_versions(tmp_path: Path) -> None:
    """The baseline is frozen at the first-ever recorded version. A
    chain v1 → v2 → v3 must keep returning drifted_from='v1' on every
    later call — the baseline never advances past the original value
    until a human deliberately resets it."""
    # Establish baseline at v1.
    record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v1",
        root=tmp_path,
    )
    # First drift: v2.
    is_new_2, drifted_from_2 = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v2",
        root=tmp_path,
    )
    assert is_new_2 is False
    assert drifted_from_2 == "stub_codex.v1"
    # Second drift: v3 — baseline still v1, NOT v2.
    is_new_3, drifted_from_3 = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v3",
        root=tmp_path,
    )
    assert is_new_3 is False
    assert drifted_from_3 == "stub_codex.v1"
    # Even calling v2 again now returns v1 as the drifted_from baseline.
    is_new_2_again, drifted_from_2_again = record_provider_version(
        competition_slug="tabular_binary_v1",
        provider="stub_codex",
        version="stub_codex.v2",
        root=tmp_path,
    )
    assert is_new_2_again is False
    assert drifted_from_2_again == "stub_codex.v1"
