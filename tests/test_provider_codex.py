# tests/test_provider_codex.py
"""RealCodexProvider: monkeypatch unit tests + shim integration tests.

Unit tests cover edge cases (timeouts, FileNotFoundError, exit-code
mapping). Shim tests exercise the real subprocess boundary including
argv construction, prompt-file routing, scrubber attachment, and
TraceStore.write_provider_streams.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderUnavailable
from arena.providers.codex import RealCodexProvider


def _packet(
    task_id: str = "task_0001",
    *,
    role: str = "implementation",
    phase: str = "CALIBRATION_TASK_CREATED",
    provider: str = "codex",
) -> dict:
    """task_packet.schema.json-valid packet helper.

    Required fields per schema (additionalProperties: false):
      schema_version, task_id, competition_slug, provider, role, phase,
      objective, inputs, allowed_paths, blocked_paths, budgets,
      required_outputs, success_criteria.
    `objective` has minLength=10. Budgets `required` is exactly 5
    fields: max_wall_minutes, max_shell_commands, max_failed_commands,
    max_input_chars, max_output_chars (additionalProperties: false on
    budgets too, so don't add extras).
    """
    return {
        "schema_version": "task_packet.v1",
        "task_id": task_id,
        "competition_slug": "tabular_binary_v1",
        "provider": provider,
        "role": role,
        "phase": phase,
        "objective": "real-adapter test packet",  # minLength=10
        "inputs": [],
        "allowed_paths": [],
        "blocked_paths": [],
        "budgets": {
            "max_wall_minutes": 5,
            "max_shell_commands": 100,
            "max_failed_commands": 10,
            "max_input_chars": 100_000,
            "max_output_chars": 100_000,
        },
        "required_outputs": ["submission.csv"],
        "success_criteria": [],
    }


def test_invoke_requires_event_emitter(tmp_path: Path) -> None:
    """Real adapters MUST have a non-None TraceStore at invoke() time —
    task packets do not carry run_id."""
    p = RealCodexProvider(executable="codex", version="0.4.2", cwd=tmp_path)
    with pytest.raises(RuntimeError, match=r"event_emitter"):
        p.invoke(_packet())


def test_invoke_file_not_found_raises_provider_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    with pytest.raises(ProviderUnavailable) as exc:
        p.invoke(_packet())
    assert exc.value.code == "not_found"


def test_invoke_timeout_returns_killed_with_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        # Include partial output to exercise the (e.stdout or "") path —
        # text=True on subprocess.run guarantees str on TimeoutExpired.
        raise subprocess.TimeoutExpired(
            cmd="codex",
            timeout=600,
            output="partial-out",
            stderr="partial-err",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "killed"
    assert "<killed:wall_clock_timeout>" in result.artifacts
    # Partial output flowed through scrubber into output_chars
    assert result.usage_proxy["output_chars"] >= len("partial-out") + len("partial-err")


def test_invoke_rejects_invalid_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter-level packet validation per ProviderAdapter contract.
    Schema-invalid packet must raise BEFORE any subprocess invocation
    or filesystem side effect."""
    from jsonschema import ValidationError

    invocations: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: invocations.append(a) or None)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    with pytest.raises(ValidationError):
        p.invoke({"schema_version": "task_packet.v1"})  # missing required fields
    # Subprocess.run must NOT have been called
    assert invocations == []
    # .arena_prompts/ must NOT have been created (validate-before-side-effect)
    assert not (tmp_path / ".arena_prompts").exists()


def test_parse_codex_ndjson_emits_drift_warning_for_malformed_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If earlier NDJSON lines are malformed but the terminal event is
    valid, the parser surfaces n_malformed_lines so the adapter can
    append a <warn:n_malformed_ndjson_lines:N> token. Operator signal
    for parser drift; mirrors the auth-pattern maintenance loop."""
    stdout = '{not json\n{also bad\n{"event":"done","artifacts":["x.csv"],"usage":{}}\n'

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"  # terminal event was valid
    assert "x.csv" in result.artifacts
    assert "<warn:n_malformed_ndjson_lines:2>" in result.artifacts


def test_invoke_exit_64_returns_blocked_with_auth_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=64, stdout="", stderr="auth")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "blocked"
    assert "<blocked:AuthFailureBreaker>" in result.artifacts
    assert "<runbook:docs/phase0/runbooks/auth_expiry.md>" in result.artifacts


def test_invoke_exit_1_with_auth_stderr_upgrades_to_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="session expired, please log in")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "blocked"
    assert "<blocked:AuthFailureBreaker>" in result.artifacts


def test_invoke_exit_1_neutral_stays_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert not any(t.startswith("<blocked:") for t in result.artifacts)


def test_invoke_exit_0_missing_terminal_event_returns_failure_with_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NDJSON without a terminal usage/artifacts event."""

    def fake_run(*a, **kw):
        return MagicMock(
            returncode=0,
            stdout='{"event": "thinking"}\n{"event": "thinking"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert "<failure:missing_terminal_event>" in result.artifacts


def test_invoke_exit_0_with_terminal_event_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final NDJSON event has artifacts + usage."""
    terminal = json.dumps(
        {
            "event": "done",
            "artifacts": ["submission.csv"],
            "usage": {"shell_commands": 3, "failed_commands": 0, "waste_events": 0},
        }
    )

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=terminal + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    assert "submission.csv" in result.artifacts
    # UsageProxy is a TypedDict (not a dataclass) — use ["key"] access.
    assert result.usage_proxy["shell_commands"] == 3


def test_invoke_uses_packet_allowed_paths_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production callers (run-next, research-proxy) populate
    packet.allowed_paths[0] with the per-experiment worktree. The
    adapter MUST use that path for prompt files, subprocess cwd, and
    --workspace-write — NOT the static self._cwd. Otherwise codex runs
    from the repo root and PR3's packet-scoped write boundary breaks.
    """
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = kwargs.get("cwd")
        return MagicMock(
            returncode=0,
            stdout='{"event":"done","artifacts":[],"usage":{}}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    workspace = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_1234"
    ts = TraceStore(run_id="run_test", root=tmp_path)
    # Adapter's _cwd is intentionally tmp_path (NOT the workspace) —
    # we're proving the packet's allowed_paths overrides the
    # constructor default.
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet()
    packet["allowed_paths"] = [str(workspace)]
    p.invoke(packet)

    expected_workspace = workspace.resolve()
    assert captured["cwd"] == str(expected_workspace), (
        f"subprocess cwd must be packet workspace, got {captured['cwd']!r}"
    )
    # --workspace-write argument also points at the packet workspace
    ws_idx = captured["argv"].index("--workspace-write")
    assert captured["argv"][ws_idx + 1] == str(expected_workspace)
    # Prompt file landed under the packet workspace, NOT under tmp_path root
    prompt_file = expected_workspace / ".arena_prompts" / "prompt_task_0001.json"
    assert prompt_file.exists(), f"prompt file missing at {prompt_file}"
    # AND the adapter's _cwd should NOT have a stray .arena_prompts dir
    assert not (tmp_path / ".arena_prompts").exists(), (
        "adapter wrote prompt to its _cwd instead of the packet workspace — "
        "PR3 packet-scoped write boundary violated"
    )


def test_invoke_resolves_relative_allowed_paths_against_adapter_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production packets carry RELATIVE allowed_paths like
    'worktrees/<slug>/<exp_id>/'. The DI contract says self._cwd is
    the adapter's repo-root anchor and is overridable for tests.
    Path(...).resolve() on a bare relative path uses the PROCESS cwd,
    silently ignoring the adapter cwd override. The fix: resolve
    relative paths against self._cwd; absolute paths as-is.

    This test deliberately does NOT monkeypatch.chdir(tmp_path) — it
    proves the resolver works on the adapter's _cwd, not on whatever
    the pytest process CWD happens to be."""
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return MagicMock(
            returncode=0,
            stdout='{"event":"done","artifacts":[],"usage":{}}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet()
    # RELATIVE path — should resolve against adapter cwd (tmp_path),
    # NOT against the pytest process CWD (the repo root).
    packet["allowed_paths"] = ["worktrees/tabular_binary_v1/exp_1234/"]
    p.invoke(packet)

    expected = (tmp_path / "worktrees" / "tabular_binary_v1" / "exp_1234").resolve()
    assert captured["cwd"] == str(expected), (
        f"relative allowed_paths must resolve against adapter cwd, not process cwd; "
        f"got {captured['cwd']!r}; expected {str(expected)!r}"
    )
    # Prompt file ALSO landed under the resolved workspace
    assert (expected / ".arena_prompts" / "prompt_task_0001.json").exists()


def test_invoke_writes_provider_streams_via_tracestore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(
            returncode=0, stdout='{"event":"done","artifacts":[],"usage":{}}\n', stderr="some err"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    base = tmp_path / "run_test" / "task_0001"
    assert (base / "stdout.raw").exists()
    assert (base / "stderr.raw").exists()
    assert (base / "stdout.scrubbed").exists()
    assert (base / "stderr.scrubbed").exists()
    # ProviderResult paths reference SCRUBBED only:
    assert result.stdout_path.endswith("stdout.scrubbed")
    assert result.stderr_path.endswith("stderr.scrubbed")


def test_invoke_records_deterministic_usage_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure paths still record wall_seconds, input_chars, output_chars."""

    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="malformed{", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable="codex",
        version="0.4.2",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    # UsageProxy is a TypedDict — use ["key"] access.
    assert result.usage_proxy["wall_seconds"] >= 0.0
    assert result.usage_proxy["input_chars"] > 0
    assert result.usage_proxy["output_chars"] == len("malformed{") + len("err")


# Shim integration tests — exercise the real subprocess boundary


def test_shim_invoke_argv_is_correct(
    tmp_path: Path,
    shim_codex_executable: Path,
) -> None:
    """Real subprocess: codex shim sees the right argv structure."""
    record_path = tmp_path / "argv_record.txt"
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable=str(shim_codex_executable),
        version="0.4.2",
        cwd=tmp_path,
        env={
            "ARENA_SHIM_STDOUT": '{"event":"done","artifacts":[],"usage":{}}\n',
            "ARENA_SHIM_PROMPT_FILE_VAR": "1",
            "ARENA_SHIM_RECORD_PATH": str(record_path),
        },
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    # Shim recorded the prompt-file path; should point inside .arena_prompts/
    recorded = record_path.read_text(encoding="utf-8")
    assert ".arena_prompts" in recorded
    assert recorded.endswith("prompt_task_0001.json")


def test_shim_invoke_full_pipeline_writes_traces(
    tmp_path: Path,
    shim_codex_executable: Path,
) -> None:
    """Stdout + stderr from real subprocess flow through scrubber + TraceStore."""
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealCodexProvider(
        executable=str(shim_codex_executable),
        version="0.4.2",
        cwd=tmp_path,
        env={
            "ARENA_SHIM_STDOUT": '{"event":"done","artifacts":["x.csv"],"usage":{}}\n',
            "ARENA_SHIM_STDERR": "ignore me",
        },
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "success"
    assert "x.csv" in result.artifacts
    assert (tmp_path / "run_test" / "task_0001" / "stdout.raw").read_text(encoding="utf-8").strip()
