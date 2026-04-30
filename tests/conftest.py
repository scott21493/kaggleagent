from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def fixture_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the bundled tabular_binary_v1 fixture to tmp_path/fixtures/ and chdir.

    Returns the tmp_path so tests can read it for assertions. Used by every
    CLI test that needs the fixture present. shutil.copytree picks up
    paper_bundle/ automatically, so adding files to the manifest in future
    PRs doesn't require updating each test individually.
    """
    src = Path(__file__).resolve().parent.parent / "fixtures" / "tabular_binary_v1"
    dst = tmp_path / "fixtures" / "tabular_binary_v1"
    shutil.copytree(src, dst)
    monkeypatch.chdir(tmp_path)
    return tmp_path
