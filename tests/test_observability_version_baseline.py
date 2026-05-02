# tests/test_observability_version_baseline.py
from __future__ import annotations

from pathlib import Path

from arena.fixture.manifest import compute_fixture_set_digest
from arena.observability.version_baseline import record_fixture_hash, record_provider_version


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


def test_fixture_set_digest_changes_when_a_listed_file_changes(tmp_path: Path) -> None:
    """Digest reflects file CONTENTS, not just manifest bytes."""
    import hashlib

    (tmp_path / "train.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "test.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    train_hash = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    test_hash = hashlib.sha256(b"a,b\n3,4\n").hexdigest()
    (tmp_path / "fixture_manifest.yaml").write_text(
        f"files:\n  train.csv: {train_hash}\n  test.csv: {test_hash}\n",
        encoding="utf-8",
    )

    digest_before = compute_fixture_set_digest(tmp_path)
    # Mutate the file content WITHOUT updating the manifest.
    (tmp_path / "train.csv").write_text("a,b\n1,99\n", encoding="utf-8")
    digest_after = compute_fixture_set_digest(tmp_path)
    assert digest_before != digest_after


def test_fixture_set_digest_unchanged_when_manifest_comment_added(tmp_path: Path) -> None:
    """Comment-only changes to the manifest YAML don't affect the digest —
    comments don't change which files are listed or their contents."""
    import hashlib

    (tmp_path / "train.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    train_hash = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    (tmp_path / "fixture_manifest.yaml").write_text(
        f"files:\n  train.csv: {train_hash}\n",
        encoding="utf-8",
    )

    digest_before = compute_fixture_set_digest(tmp_path)
    # Add a YAML comment.
    (tmp_path / "fixture_manifest.yaml").write_text(
        f"# this is a comment\nfiles:\n  train.csv: {train_hash}\n",
        encoding="utf-8",
    )
    digest_after = compute_fixture_set_digest(tmp_path)
    assert digest_before == digest_after


def test_record_fixture_hash_first_call_returns_new(tmp_path: Path) -> None:
    is_new, drifted_from = record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    assert is_new is True
    assert drifted_from is None


def test_record_fixture_hash_same_digest_no_drift(tmp_path: Path) -> None:
    record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    is_new, drifted_from = record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    assert is_new is False
    assert drifted_from is None


def test_record_fixture_hash_different_digest_flags_drift(tmp_path: Path) -> None:
    record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    is_new, drifted_from = record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="cafef00d",
        root=tmp_path,
    )
    assert is_new is False
    assert drifted_from == "deadbeef"


def test_record_fixture_hash_baselines_are_per_slug(tmp_path: Path) -> None:
    record_fixture_hash(
        competition_slug="tabular_binary_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    is_new, _ = record_fixture_hash(
        competition_slug="image_classification_v1",
        fixture_hash="deadbeef",
        root=tmp_path,
    )
    assert is_new is True  # different slug = fresh baseline
