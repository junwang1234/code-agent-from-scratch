from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class StructuredCall:
    prompt: str
    schema: dict
    call_kind: str


class LLMProvider(Protocol):
    source_name: str

    def generate_plan(self, call: StructuredCall) -> dict: ...

    def generate_action(self, call: StructuredCall) -> dict: ...

    def get_session_id(self) -> str | None: ...

    def set_session_id(self, session_id: str | None) -> None: ...
