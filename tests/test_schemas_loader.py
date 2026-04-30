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


@pytest.mark.parametrize(
    "bad_name",
    [
        "foo/../task_packet",  # path traversal via parent dir
        "../task_packet",  # relative escape
        "/task_packet",  # absolute-style
        "task_packet/",  # trailing slash
        "task packet",  # whitespace
        "Task_Packet",  # uppercase
        "task.packet",  # dot
        "",  # empty
    ],
)
def test_rejects_invalid_schema_names(bad_name: str) -> None:
    with pytest.raises(ValueError, match="invalid schema name"):
        load_schema(bad_name)
