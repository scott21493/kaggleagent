from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena.budget.kill_switch import KILL_SWITCH_ENV, KILL_SWITCH_FILE, Breaker, KillSwitch


def test_breaker_enum_has_ten_names() -> None:
    expected = {
        "ProviderCallBreaker",
        "WallClockBreaker",
        "ShellCommandBreaker",
        "RepeatedFailureBreaker",
        "WasteEventBreaker",
        "SecretAccessBreaker",
        "NetworkEgressBreaker",
        "ProtectedFileBreaker",
        "SchemaViolationBreaker",
        "AuthFailureBreaker",
    }
    assert {b.value for b in Breaker} == expected


def test_breaker_enum_values_match_event_schema() -> None:
    """The breaker enum on disk in event.schema.json must equal Breaker.values
    in BOTH locations: the top-level payload.breaker definition AND the
    conditional allOf[0].then.payload.breaker. Both must stay synchronized
    so breaker_triggered events validate consistently across paths.

    Length-checked to catch duplicate-entry regressions (JSON Schema requires
    unique enum items but not all validators enforce it)."""
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    enum_values = {b.value for b in Breaker}

    # Top-level payload.breaker.
    primary = schema["properties"]["payload"]["properties"]["breaker"]["enum"]
    assert len(primary) == len(set(primary)), "schema breaker enum has duplicates (primary)"
    assert set(primary) == enum_values

    # Conditional allOf[0].then.payload.breaker — must equal primary.
    conditional = schema["allOf"][0]["then"]["properties"]["payload"]["properties"]["breaker"][
        "enum"
    ]
    assert len(conditional) == len(set(conditional)), (
        "schema breaker enum has duplicates (conditional)"
    )
    assert set(conditional) == enum_values


def test_inactive_when_no_file_and_no_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert KillSwitch.is_active() is False


def test_active_when_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_FILE.touch()
    assert KillSwitch.is_active() is True


def test_active_when_env_var_set_to_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    monkeypatch.chdir(tmp_path)
    assert KillSwitch.is_active() is True


def test_inactive_when_env_var_set_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KILL_SWITCH_ENV, "0")
    monkeypatch.chdir(tmp_path)
    assert KillSwitch.is_active() is False


def test_activate_creates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert KillSwitch.is_active() is False
    KillSwitch.activate()
    assert KillSwitch.is_active() is True


def test_deactivate_removes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    KillSwitch.activate()
    KillSwitch.deactivate()
    assert KillSwitch.is_active() is False


def test_deactivate_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    KillSwitch.deactivate()  # never activated; must not raise
    KillSwitch.deactivate()
    assert KillSwitch.is_active() is False


def test_env_var_takes_precedence_over_file_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 0 semantics: ARENA_KILL_SWITCH=1 wins over file presence.
    deactivate() removes the file but cannot clear the env var; an
    operator-set env override is intentional and only the operator can
    clear it. Locks in the chosen precedence so a future refactor cannot
    silently flip it."""
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    monkeypatch.chdir(tmp_path)
    KillSwitch.activate()
    assert KillSwitch.is_active() is True

    # deactivate() removes the file but the env var still reads as active.
    KillSwitch.deactivate()
    assert KillSwitch.is_active() is True
    assert not KILL_SWITCH_FILE.exists()
