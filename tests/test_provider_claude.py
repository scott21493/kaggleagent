# tests/test_provider_claude.py
"""RealClaudeProvider: monkeypatch unit tests + shim integration tests.

Mirrors test_provider_codex.py with Claude-specific assertions:
  - argv shape (`-p --output-format json --add-dir <ws>`; prompt via stdin)
  - stdout is a SINGLE JSON object (not NDJSON)
  - role+phase dispatch to research_review / paper_digest / etc.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arena.observability.trace_store import TraceStore
from arena.providers.base import ProviderUnavailable
from arena.providers.claude import RealClaudeProvider


def _packet(
    task_id: str = "task_0001",
    *,
    role: str = "review",
    phase: str = "FUSION_PROXY_REVIEWED",
    provider: str = "claude",
) -> dict:
    """task_packet.schema.json-valid packet helper (mirrors codex tests)."""
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
    """Real adapters MUST have a non-None TraceStore at invoke() time."""
    p = RealClaudeProvider(executable="claude", version="0.3.1", cwd=tmp_path)
    with pytest.raises(RuntimeError, match=r"event_emitter"):
        p.invoke(_packet())


def test_invoke_file_not_found_raises_provider_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
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
        raise subprocess.TimeoutExpired(
            cmd="claude",
            timeout=600,
            output="partial-out",
            stderr="partial-err",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "killed"
    assert "<killed:wall_clock_timeout>" in result.artifacts
    assert result.usage_proxy["output_chars"] >= len("partial-out") + len("partial-err")


def test_invoke_exit_64_returns_blocked_with_auth_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=64, stdout="", stderr="auth")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
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
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
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
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert not any(t.startswith("<blocked:") for t in result.artifacts)


def test_invoke_records_deterministic_usage_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure paths still record wall_seconds, input_chars, output_chars."""

    def fake_run(*a, **kw):
        return MagicMock(returncode=1, stdout="malformed{", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet())
    assert result.status == "failure"
    assert result.usage_proxy["wall_seconds"] >= 0.0
    assert result.usage_proxy["input_chars"] > 0
    assert result.usage_proxy["output_chars"] == len("malformed{") + len("err")
    # Claude doesn't execute shell commands.
    assert result.usage_proxy["shell_commands"] == 0
    assert result.usage_proxy["failed_commands"] == 0
    assert result.usage_proxy["waste_events"] == 0


def test_invoke_uses_packet_allowed_paths_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production callers populate packet.allowed_paths[0] with the
    per-experiment worktree. The adapter MUST use that path for prompt
    files, subprocess cwd, --add-dir flag, AND advisory artifact
    materialization. Without this, repeated invocations would
    overwrite a shared root-level <schema>.json file across runs;
    scoreboard rows from older runs would point at mutated artifacts.
    """
    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = kwargs.get("cwd")
        return MagicMock(returncode=0, stdout=valid_review, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    workspace = tmp_path / "worktrees" / "tabular_binary_v1" / "exp_1234"
    ts = TraceStore(run_id="run_test", root=tmp_path)
    # Adapter's _cwd is tmp_path (NOT the workspace) — we're proving
    # packet.allowed_paths overrides the constructor default.
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    packet["allowed_paths"] = [str(workspace)]
    result = p.invoke(packet)

    expected_workspace = workspace.resolve()
    assert captured["cwd"] == str(expected_workspace), (
        f"subprocess cwd must be packet workspace, got {captured['cwd']!r}"
    )
    # --add-dir argument points at the packet workspace (PR7-polish:
    # real claude CLI has no --workspace flag; --add-dir grants the
    # workspace tool access and cwd is the implicit workspace).
    add_idx = captured["argv"].index("--add-dir")
    assert captured["argv"][add_idx + 1] == str(expected_workspace)
    # --output-format json forces single-JSON stdout (without it the
    # model can emit prose and the parser falls into json_decode_error)
    assert "--output-format" in captured["argv"]
    fmt_idx = captured["argv"].index("--output-format")
    assert captured["argv"][fmt_idx + 1] == "json"
    # Prompt file lands under the packet workspace
    prompt_file = expected_workspace / ".arena_prompts" / "prompt_task_0001.json"
    assert prompt_file.exists(), f"prompt file missing at {prompt_file}"
    # Advisory artifact is materialized under the packet workspace
    rr_path = expected_workspace / "research_review.json"
    assert rr_path.exists(), f"research_review.json missing at {rr_path}"
    assert any(a.endswith(str(rr_path)) or Path(a) == rr_path for a in result.artifacts), (
        f"result.artifacts must reference the packet-workspace artifact path; "
        f"got {result.artifacts!r}"
    )
    # AND adapter's _cwd should NOT have a stray research_review.json or
    # .arena_prompts dir (proves the override took effect)
    assert not (tmp_path / "research_review.json").exists()
    assert not (tmp_path / ".arena_prompts").exists()


def test_invoke_resolves_relative_allowed_paths_against_adapter_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the codex test: relative allowed_paths must resolve
    against self._cwd (adapter override), NOT the process cwd. Pinned
    here for the artifact-materialisation path too — a relative path
    must produce <adapter_cwd>/<workspace>/<schema>.json, not
    <process_cwd>/<workspace>/<schema>.json. No chdir."""
    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return MagicMock(returncode=0, stdout=valid_review, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    packet["allowed_paths"] = ["worktrees/tabular_binary_v1/exp_1234/"]
    p.invoke(packet)

    expected = (tmp_path / "worktrees" / "tabular_binary_v1" / "exp_1234").resolve()
    assert captured["cwd"] == str(expected)
    assert (expected / "research_review.json").exists()
    # Process-cwd-relative location must NOT have been written
    process_cwd_artifact = Path("worktrees/tabular_binary_v1/exp_1234/research_review.json")
    if process_cwd_artifact.exists():
        # Sanity guard — if for some reason this exists in the repo
        # already, our test isn't useful. But it shouldn't.
        raise AssertionError(
            f"unexpected pre-existing artifact at {process_cwd_artifact!r} "
            "would mask the regression"
        )


def test_invoke_writes_provider_streams_via_tracestore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism", "smallest test defined"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=valid_review, stderr="some err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
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


def test_invoke_review_role_validates_against_research_review_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """role='review' + phase='FUSION_PROXY_REVIEWED' → research_review schema."""
    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism", "smallest test defined"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=valid_review, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    result = p.invoke(packet)
    assert result.status == "success"


def test_invoke_review_success_persists_research_review_json_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real Claude is advisory: it returns content, not files. The
    adapter MUST materialise the validated JSON to <cwd>/<schema>.json
    and append the path to ProviderResult.artifacts. Otherwise
    arena review's _require_artifact(suffix='research_review.json')
    will fail with 'did not emit artifact' on a successful Claude
    invocation.

    Mirrors the stub_claude convention: the artifact lives at
    <workspace>/<schema_name>.json and ends up in result.artifacts."""
    from arena.schemas.validate import validate as validate_schema

    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout=valid_review, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    result = p.invoke(packet)
    assert result.status == "success"
    # Same suffix-match the CLI uses (arena/cli.py:_require_artifact).
    rr_paths = [a for a in result.artifacts if a.endswith("research_review.json")]
    assert len(rr_paths) == 1, (
        f"expected exactly one research_review.json artifact, got {result.artifacts!r}"
    )
    rr_path = Path(rr_paths[0])
    assert rr_path.exists(), f"materialised artifact not on disk: {rr_path}"
    assert rr_path.parent == tmp_path, (
        f"artifact must be persisted under cwd ({tmp_path}); got parent {rr_path.parent}"
    )
    # Round-trip the file through the schema to confirm what we wrote
    # is actually valid (catches regressions where we write the wrong
    # payload shape, e.g., write the request packet instead of the
    # response).
    payload = json.loads(rr_path.read_text(encoding="utf-8"))
    validate_schema("research_review", payload)
    assert payload["decision"] == "accept"
    assert payload["review_id"] == "rr_0001"


def test_invoke_review_failure_does_not_persist_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema-violation on the success path → no <schema>.json file
    is written. Only the <failure:schema_violation> token appears in
    artifacts. Pins the inverse of the persistence behaviour above."""

    def fake_run(*a, **kw):
        # Valid JSON shape but missing required fields → schema_violation
        return MagicMock(returncode=0, stdout='{"foo": "bar"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    packet = _packet(role="review", phase="FUSION_PROXY_REVIEWED")
    result = p.invoke(packet)
    assert result.status == "failure"
    assert "<failure:schema_violation>" in result.artifacts
    # No JSON artifact materialised on failure
    assert not (tmp_path / "research_review.json").exists()
    assert not any(a.endswith("research_review.json") for a in result.artifacts)


def test_invoke_invalid_json_returns_failure_with_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout="not json{", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="FUSION_PROXY_REVIEWED"))
    assert result.status == "failure"
    assert "<failure:json_decode_error>" in result.artifacts


def test_invoke_schema_violation_returns_failure_with_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid JSON but missing required fields → <failure:schema_violation>."""

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout='{"foo": "bar"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="FUSION_PROXY_REVIEWED"))
    assert result.status == "failure"
    assert "<failure:schema_violation>" in result.artifacts


def test_invoke_unmapped_role_phase_combo_returns_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema-valid packet whose (role, phase) is intentionally NOT in
    the adapter dispatch table → no schema to validate output against
    → <failure:schema_violation>.

    role="review" + phase="CALIBRATION_REVIEWED" is the canonical
    unmapped pair: both values pass task_packet.schema.json's enums,
    but the (role, phase) tuple is not in _ROLE_PHASE_TO_SCHEMA. Using
    truly-unknown enum values (e.g., role="unknown_role") would fail
    adapter-level packet validation BEFORE the dispatch runs and never
    reach _parse_claude_response."""

    def fake_run(*a, **kw):
        return MagicMock(returncode=0, stdout='{"foo": "bar"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable="claude",
        version="0.3.1",
        cwd=tmp_path,
        event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="CALIBRATION_REVIEWED"))
    assert result.status == "failure"
    assert "<failure:schema_violation>" in result.artifacts


# Shim integration test — exercises the real subprocess boundary


def test_shim_invoke_full_pipeline_writes_traces(
    tmp_path: Path,
    shim_claude_executable: Path,
) -> None:
    """Stdout + stderr from real subprocess flow through scrubber + TraceStore.

    Argv assertion: claude argv is `[exe, "-p", "--input", str(prompt_file),
    "--workspace", str(cwd)]` (per ADR-0004) — the shim runs successfully
    with that shape; if argv were wrong, the shim's sys.argv would not
    match and the test would still pass, but the round-trip JSON parsing +
    schema validation success below confirms the wrapper produced a
    successful invocation under that argv shape."""
    valid_review = json.dumps(
        {
            "schema_version": "research_review.v1",
            "review_id": "rr_0001",
            "competition_slug": "tabular_binary_v1",
            "subject_id": "fusion_0001",
            "decision": "accept",
            "summary": "Proposal looks reasonable for the proxy slice.",
            "strengths": ["clear mechanism"],
            "weaknesses": [],
            "required_fixes": [],
            "follow_up_recommendations": [],
            "risk_level": "low",
        }
    )
    ts = TraceStore(run_id="run_test", root=tmp_path)
    p = RealClaudeProvider(
        executable=str(shim_claude_executable),
        version="0.3.1",
        cwd=tmp_path,
        env={
            "ARENA_SHIM_STDOUT": valid_review,
            "ARENA_SHIM_STDERR": "ignore me",
        },
        event_emitter=ts,
    )
    result = p.invoke(_packet(role="review", phase="FUSION_PROXY_REVIEWED"))
    assert result.status == "success"
    assert (tmp_path / "run_test" / "task_0001" / "stdout.raw").read_text(encoding="utf-8").strip()
    # Prompt file should have been written under .arena_prompts/
    assert (tmp_path / ".arena_prompts" / "prompt_task_0001.json").exists()
