from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Action, FactItem, SuccessCriterionStatus


@dataclass(slots=True)
class ExecutionCommand:
    step_id: str
    reason: str
    completed_step_ids: list[str] = field(default_factory=list)
    criterion_updates: list[SuccessCriterionStatus] = field(default_factory=list)
    fact_updates: list[FactItem] = field(default_factory=list)


@dataclass(slots=True)
class ToolExecutionCommand(ExecutionCommand):
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FinishExecutionCommand(ExecutionCommand):
    answer: str = ""
    evidence: list = field(default_factory=list)
    repo_map: list = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suggested_next_questions: list[str] = field(default_factory=list)


def command_from_action(action: Action) -> ExecutionCommand:
    if action.kind == "finish":
        return FinishExecutionCommand(
            step_id=action.step_id,
            reason=action.reason,
            completed_step_ids=action.completed_step_ids,
            criterion_updates=action.criterion_updates,
            fact_updates=action.fact_updates,
            answer=action.answer,
            evidence=action.evidence,
            repo_map=action.repo_map,
            unknowns=action.unknowns,
            suggested_next_questions=action.suggested_next_questions,
        )
    return ToolExecutionCommand(
        step_id=action.step_id,
        reason=action.reason,
        completed_step_ids=action.completed_step_ids,
        criterion_updates=action.criterion_updates,
        fact_updates=action.fact_updates,
        tool_name=action.tool_name or "",
        tool_input=action.tool_input,
    )


def action_from_command(command: ExecutionCommand) -> Action:
    if isinstance(command, FinishExecutionCommand):
        return Action.finish_action(
            step_id=command.step_id,
            reason=command.reason,
            answer=command.answer,
            evidence=command.evidence,
            repo_map=command.repo_map,
            unknowns=command.unknowns,
            suggested_next_questions=command.suggested_next_questions,
            completed_step_ids=command.completed_step_ids,
            criterion_updates=command.criterion_updates,
            fact_updates=command.fact_updates,
        )
    if isinstance(command, ToolExecutionCommand):
        return Action.tool(
            step_id=command.step_id,
            reason=command.reason,
            tool_name=command.tool_name,
            tool_input=command.tool_input,
            completed_step_ids=command.completed_step_ids,
            criterion_updates=command.criterion_updates,
            fact_updates=command.fact_updates,
        )
    raise TypeError(f"Unsupported execution command: {type(command).__name__}")
