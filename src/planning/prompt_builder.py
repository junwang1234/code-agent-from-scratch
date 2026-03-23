from __future__ import annotations

import json

from ..models import Task
from ..runtime.memory_manager import AgentMemory
from ..runtime.memory_manager import build_incremental_prompt_state, build_snapshot_prompt_state
from .prompt_refresh_strategy import PromptRefreshStrategy


class PlanningPromptBuilder:
    def __init__(self, available_tools: list[dict], refresh_strategy: PromptRefreshStrategy | None = None) -> None:
        self.available_tools = available_tools
        self.refresh_strategy = refresh_strategy or PromptRefreshStrategy()

    def build_plan_prompt(self, task: Task) -> str:
        return (
            "You are the planning component of a bounded repository agent.\n"
            "You cannot inspect the repository directly. Produce only a structured plan that the local executor can follow with validated tools.\n"
            "The task may require pure investigation, code changes, or both. Decide the shape from the user request instead of forcing a predeclared mode.\n"
            f"User request: {task.question}\n"
            "Available tools:\n"
            + json.dumps(self.available_tools, indent=2)
            + "\nKeep the plan to at most 6 steps. If the task likely changes code, include inspection before writing and include a validation step. "
            + "If the task is explain-only, prefer low-bandwidth exploration first. Include concrete success criteria and any useful constraints, search terms, file hints, and unknowns to resolve."
        )

    def build_action_prompt(self, memory: AgentMemory, remaining_steps: int) -> tuple[str, str]:
        use_snapshot_state = self.refresh_strategy.should_send_snapshot_prompt_state(memory.state)
        prompt_state_kind = "snapshot" if use_snapshot_state else "incremental"
        prompt_state = (
            build_snapshot_prompt_state(memory, remaining_steps)
            if use_snapshot_state
            else build_incremental_prompt_state(memory, remaining_steps)
        )
        if use_snapshot_state:
            prompt_state["available_tools"] = self.available_tools
            instructions = (
                "You are the policy engine for a bounded repository agent.\n"
                "Do not inspect or edit the repository directly. Choose exactly one validated local tool call or finish.\n"
                "Let the user request and current evidence determine whether this run is exploratory or edit-oriented. Do not assume a fixed mode.\n"
                "Read or search relevant files before writing. Validate after code changes when feasible. If enough evidence exists, finish.\n"
                "Avoid duplicate reads, repeated broad searches, and early finish with weak evidence.\n"
                "If the last tool call failed, use the structured failure context to decide the next action.\n"
                "Do not repeat the identical failing action unless the error suggests a bounded correction.\n"
                "Prefer adjusting parameters, inspecting inputs, or choosing a prerequisite tool before retrying.\n"
            )
            state_instruction = "Use the snapshot state below as the current execution snapshot.\nCurrent state:\n"
        else:
            instructions = (
                "Resumed repository-agent session.\n"
                "Choose exactly one next tool action or finish using only the latest delta below.\n"
                "If the latest action failed, reason from that failure instead of blindly repeating it.\n"
            )
            state_instruction = "Latest delta:\n"
        return instructions + state_instruction + json.dumps(prompt_state, indent=2), prompt_state_kind
