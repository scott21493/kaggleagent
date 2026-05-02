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
