from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class RuntimeEvent:
    event_type: str
    timestamp: float
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class RuntimeEventSink(Protocol):
    def record(self, event_type: str, **payload: Any) -> None: ...


class InMemoryRuntimeEventLog:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def record(self, event_type: str, **payload: Any) -> None:
        self.events.append(RuntimeEvent(event_type=event_type, timestamp=time.time(), payload=payload))


class JsonlRuntimeEventLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event_type: str, **payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = RuntimeEvent(event_type=event_type, timestamp=time.time(), payload=payload)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")
