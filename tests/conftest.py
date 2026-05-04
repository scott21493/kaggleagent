from __future__ import annotations

import shutil
import stat
import sys
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


@pytest.fixture
def shim_codex_executable(tmp_path: Path) -> Path:
    """Write a Python script that pretends to be `codex exec --json`.

    The shim's behavior is controlled by env vars:
      - ARENA_SHIM_EXIT_CODE: integer exit code (default 0)
      - ARENA_SHIM_STDOUT: NDJSON event stream to emit on stdout
      - ARENA_SHIM_STDERR: stderr text to emit
      - ARENA_SHIM_PROMPT_FILE_VAR: env var name to write the prompt
        path into (so tests can verify --prompt-file argv handling)

    Returns the absolute path to the executable. On Windows, a .cmd
    wrapper points at python invoking the script."""
    script = tmp_path / "fake_codex.py"
    script.write_text(
        """#!/usr/bin/env python
import os, sys
exit_code = int(os.environ.get("ARENA_SHIM_EXIT_CODE", "0"))
sys.stdout.write(os.environ.get("ARENA_SHIM_STDOUT", ""))
sys.stderr.write(os.environ.get("ARENA_SHIM_STDERR", ""))
# Record the --prompt-file argv slot if requested:
var = os.environ.get("ARENA_SHIM_PROMPT_FILE_VAR")
if var:
    for i, a in enumerate(sys.argv):
        if a == "--prompt-file" and i + 1 < len(sys.argv):
            with open(os.environ.get("ARENA_SHIM_RECORD_PATH", os.devnull), "w") as f:
                f.write(sys.argv[i + 1])
            break
sys.exit(exit_code)
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if sys.platform == "win32":
        cmd = tmp_path / "codex.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    # POSIX: rename to "codex" so argv[0] looks right
    posix = tmp_path / "codex"
    script.rename(posix)
    posix.chmod(posix.stat().st_mode | stat.S_IXUSR)
    return posix


@pytest.fixture
def shim_claude_executable(tmp_path: Path) -> Path:
    """Same shape as shim_codex_executable but named claude / claude.cmd.
    Emits single JSON (not NDJSON) per claude -p contract."""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        """#!/usr/bin/env python
import os, sys
sys.stdout.write(os.environ.get("ARENA_SHIM_STDOUT", ""))
sys.stderr.write(os.environ.get("ARENA_SHIM_STDERR", ""))
sys.exit(int(os.environ.get("ARENA_SHIM_EXIT_CODE", "0")))
""",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        cmd = tmp_path / "claude.cmd"
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    posix = tmp_path / "claude"
    script.rename(posix)
    posix.chmod(posix.stat().st_mode | stat.S_IXUSR)
    return posix
