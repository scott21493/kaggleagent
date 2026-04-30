from __future__ import annotations

import pytest

from arena.schemas.loader import load_schema


def test_loads_task_packet_schema() -> None:
    schema = load_schema("task_packet")
    assert schema["title"] == "TaskPacket"
    assert "schema_version" in schema["required"]


def test_loader_caches() -> None:
    a = load_schema("task_packet")
    b = load_schema("task_packet")
    assert a is b


def test_missing_schema_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("does_not_exist")
