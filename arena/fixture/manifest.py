from __future__ import annotations

import hashlib
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
