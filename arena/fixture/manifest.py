from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_fixture_manifest(fixture_dir: str | Path) -> None:
    root = Path(fixture_dir)
    manifest_path = root / "fixture_manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing fixture manifest: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    for rel_path, expected_hash in manifest["files"].items():
        actual = sha256_file(root / rel_path)
        if actual != expected_hash:
            raise ValueError(
                f"fixture hash mismatch for {rel_path}: expected {expected_hash}, got {actual}"
            )


def compute_fixture_set_digest(fixture_dir: str | Path) -> str:
    """Compute a deterministic SHA-256 digest over the fixture set.

    The digest is sha256(json.dumps(sorted [(rel_path, actual_file_sha256), ...])).
    Catches:
    - File content changes (actual sha256 differs)
    - File additions/removals (manifest['files'] keys change)
    - Re-init with different content (each pair changes)

    Does NOT catch comment-only changes to fixture_manifest.yaml — that's
    correct, comments don't affect reproducibility.

    Raises FileNotFoundError if the manifest is missing.
    """
    root = Path(fixture_dir)
    manifest_path = root / "fixture_manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing fixture manifest: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    pairs = sorted((rel_path, sha256_file(root / rel_path)) for rel_path in manifest["files"])
    blob = json.dumps(pairs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
