from __future__ import annotations

from ..runtime.memory_manager import AgentMemory
from ..models import Action, StructuredPlan, Task


class BasePlanner:
    def make_plan(self, task: Task) -> StructuredPlan:
        raise NotImplementedError

    def next_action(self, memory: AgentMemory, remaining_steps: int) -> Action:
        raise NotImplementedError

    def get_session_id(self) -> str | None:
        return None

    def set_session_id(self, session_id: str | None) -> None:
        return None
