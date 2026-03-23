from __future__ import annotations


class PromptRefreshStrategy:
    def should_send_snapshot_prompt_state(self, memory) -> bool:
        active_step = next((step for step in memory.plan.steps if step.status != "completed"), memory.plan.steps[-1])
        if memory.prompt_turn_count == 0:
            return True
        if memory.last_prompt_step_id != active_step.id:
            return True
        if memory.incremental_turns_since_refresh >= 3:
            return True
        if len(memory.failures) > memory.last_prompt_failure_count:
            return True
        return False

    def record_action_prompt_use(self, memory, prompt_state_kind: str) -> None:
        active_step = next((step for step in memory.plan.steps if step.status != "completed"), memory.plan.steps[-1])
        memory.prompt_turn_count += 1
        memory.last_prompt_step_id = active_step.id
        memory.last_prompt_failure_count = len(memory.failures)
        if prompt_state_kind == "snapshot":
            memory.incremental_turns_since_refresh = 0
            return
        memory.incremental_turns_since_refresh += 1
