from __future__ import annotations

from ..models import Action
from .action_repair import (
    action_fingerprint,
    can_finish,
    failure_fingerprint,
    fallback_tool_action,
    repair_tool_action,
    retry_alternative_action,
)
from .memory_manager import AgentMemory


class ProposalNormalizer:
    def normalize(self, memory: AgentMemory, action: Action, remaining_steps: int) -> Action:
        if action.kind == "finish" and action.tool_call is not None:
            action.kind = "tool"
            action.finish = None
            return _apply_retry_policy(memory, repair_tool_action(memory, action))

        if action.kind == "finish" and not can_finish(memory, action, remaining_steps):
            return fallback_tool_action(memory)
        return _apply_retry_policy(memory, repair_tool_action(memory, action))


def _apply_retry_policy(memory: AgentMemory, action: Action) -> Action:
    if action.kind != "tool":
        return action
    fingerprint = action_fingerprint(action)
    retry_count = memory.state.retry_counts.get(fingerprint, 0)
    last_failure = memory.state.last_action_failure
    if retry_count < 2 or last_failure is None:
        return action
    if failure_fingerprint(last_failure) != fingerprint:
        return action
    return retry_alternative_action(memory, action)
