from __future__ import annotations

import json
from pathlib import Path

from arena.schemas.validate import validate


class TaskQueue:
    """File-backed FIFO queue of task packets.

    Each packet is validated against task_packet.schema.json on enqueue and
    written to <queue_dir>/<task_id>.json. Dequeue selects the lexicographically
    smallest filename (task_id is zero-padded so this is FIFO in practice) and
    deletes the file.
    """

    def __init__(self, queue_dir: str | Path) -> None:
        self._dir = Path(queue_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def enqueue(self, packet: dict) -> None:
        validate("task_packet", packet)
        path = self._dir / f"{packet['task_id']}.json"
        path.write_text(json.dumps(packet, indent=2), encoding="utf-8")

    def dequeue(self) -> dict | None:
        files = sorted(self._dir.glob("*.json"))
        if not files:
            return None
        path = files[0]
        packet: dict = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return packet

    def size(self) -> int:
        return len(list(self._dir.glob("*.json")))
