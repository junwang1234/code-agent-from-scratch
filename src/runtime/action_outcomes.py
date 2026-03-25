from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import TaskResult
from ..tools.core import CommandToolResult, HeadFileToolResult, ReadFileRangeToolResult, ShellToolResult, TreeToolResult, WriteToolResult
from ..tools.shell import format_shell_query
from .validation.failures import VALIDATION_TOOL_NAMES, validation_failure_kind
from .memory_manager import AgentMemory
from .tool_outcomes import (
    apply_command_outcome,
    apply_file_range_outcome,
    apply_head_file_outcome,
    apply_shell_outcome,
    apply_tree_outcome,
    apply_write_outcome,
)


class ExecutableOutcome(ABC):
    @abstractmethod
    def apply(self, memory: AgentMemory) -> TaskResult | None:
        raise NotImplementedError


@dataclass(slots=True)
class TreeObservationOutcome(ExecutableOutcome):
    tree: list[str]
    depth: int

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_tree_outcome(memory, tree=self.tree, depth=self.depth)
        return None


@dataclass(slots=True)
class HeadFileObservationOutcome(ExecutableOutcome):
    result: HeadFileToolResult

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_head_file_outcome(memory, result=self.result)
        return None


@dataclass(slots=True)
class FileRangeObservationOutcome(ExecutableOutcome):
    result: ReadFileRangeToolResult

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_file_range_outcome(memory, result=self.result)
        return None


@dataclass(slots=True)
class ShellObservationOutcome(ExecutableOutcome):
    observation_tool: str
    result: object
    empty_results_retryable: bool = False
    empty_results_message: str = ""

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_shell_outcome(memory, observation_tool=self.observation_tool, result=self.result)
        if self.empty_results_retryable and self.result.exit_code != 0:
            from .action_execution import ActionExecutionFailed

            raise ActionExecutionFailed(
                failure_kind="no_results",
                message=self.empty_results_message,
                raw_output=self.result.output,
                retryable=True,
            )
        return None


@dataclass(slots=True)
class WriteObservationOutcome(ExecutableOutcome):
    tool_name: str
    write_result: object
    summary: str

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_write_outcome(memory, tool_name=self.tool_name, write_result=self.write_result, summary=self.summary)
        return None


@dataclass(slots=True)
class CommandObservationOutcome(ExecutableOutcome):
    tool_name: str
    result: object
    discovery_state: object | None = None

    def apply(self, memory: AgentMemory) -> TaskResult | None:
        apply_command_outcome(memory, tool_name=self.tool_name, result=self.result, discovery_state=self.discovery_state)
        if self.result.exit_code != 0:
            from .action_execution import ActionExecutionFailed

            failure_kind = "nonzero_exit"
            retryable = False
            if self.tool_name in VALIDATION_TOOL_NAMES:
                failure_message = "\n".join(self.result.output[:12]) or f"{format_shell_query(self.result.command, self.result.args)} exited with code {self.result.exit_code}."
                failure_kind = validation_failure_kind(self.tool_name, failure_message)
                retryable = failure_kind == "timeout"
            raise ActionExecutionFailed(
                failure_kind=failure_kind,
                message=f"{format_shell_query(self.result.command, self.result.args)} exited with code {self.result.exit_code}.",
                raw_output=self.result.output,
                retryable=retryable,
            )
        return None


class ToolOutcomeAdapter:
    def adapt(self, outcome) -> ExecutableOutcome:
        if isinstance(outcome, TreeToolResult):
            return TreeObservationOutcome(outcome.tree, outcome.depth)
        if isinstance(outcome, HeadFileToolResult):
            return HeadFileObservationOutcome(outcome)
        if isinstance(outcome, ReadFileRangeToolResult):
            return FileRangeObservationOutcome(outcome)
        if isinstance(outcome, ShellToolResult):
            return ShellObservationOutcome(
                outcome.observation_tool,
                outcome.result,
                empty_results_retryable=outcome.empty_results_retryable,
                empty_results_message=outcome.empty_results_message,
            )
        if isinstance(outcome, WriteToolResult):
            return WriteObservationOutcome(outcome.tool_name, outcome.write_result, outcome.summary)
        if isinstance(outcome, CommandToolResult):
            return CommandObservationOutcome(outcome.tool_name, outcome.result, discovery_state=outcome.discovery_state)
        raise ValueError(f"Unsupported tool result: {type(outcome).__name__}")
