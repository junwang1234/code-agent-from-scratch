from __future__ import annotations

from ..models import Action, ApprovalRequest, TaskResult
from ..presentation.runtime_reporter import RuntimeReporter
from ..tools import ToolExecutor, build_default_tool_registry
from ..tools.core import ApprovalRequiredError
from ..tools.registry import ToolRegistry
from .action_normalizer import ProposalNormalizer
from .action_outcomes import CommandObservationOutcome, ToolOutcomeAdapter, WriteObservationOutcome
from .execution_commands import ExecutionCommand, action_from_command, command_from_action
from .memory_manager import AgentMemory
from .result_composer import compose_response


class ActionExecutionFailed(Exception):
    def __init__(
        self,
        *,
        failure_kind: str,
        message: str,
        raw_output: list[str] | None = None,
        highlights: list[str] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.message = message
        self.raw_output = list(raw_output or [])
        self.highlights = list(highlights or self.raw_output[:6])
        self.retryable = retryable


class ApprovalRequired(Exception):
    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__(request.reason or "Explicit approval is required.")
        self.request = request


class ActionExecutor:
    def __init__(self, repo_path, reporter: RuntimeReporter | None = None, registry: ToolRegistry | None = None, normalizer: ProposalNormalizer | None = None) -> None:
        self.reporter = reporter
        self.tool_executor = ToolExecutor(repo_path, registry=registry or build_default_tool_registry())
        self.normalizer = normalizer or ProposalNormalizer()
        self.outcome_adapter = ToolOutcomeAdapter()

    def normalize(self, memory: AgentMemory, action: Action, remaining_steps: int) -> Action:
        return self.normalizer.normalize(memory, action, remaining_steps)

    def normalize_command(self, memory: AgentMemory, command: ExecutionCommand, remaining_steps: int) -> ExecutionCommand:
        normalized_action = self.normalize(memory, action_from_command(command), remaining_steps)
        return command_from_action(normalized_action)

    def execute(self, memory: AgentMemory, action: Action) -> TaskResult | None:
        return self.execute_command(memory, command_from_action(action))

    def execute_command(self, memory: AgentMemory, command: ExecutionCommand, *, apply_updates: bool = True) -> TaskResult | None:
        action = action_from_command(command)
        if apply_updates:
            memory.apply_action_updates(action)
        if action.kind == "finish":
            memory.apply_finish(action)
            return compose_response(memory)
        if not action.tool_name:
            raise ValueError("Tool action is missing tool_name.")
        try:
            outcome = self.tool_executor.execute(action.tool_name, action.tool_input)
        except ApprovalRequiredError as exc:
            raise ApprovalRequired(exc.request) from exc
        executable_outcome = self.outcome_adapter.adapt(outcome)
        result = executable_outcome.apply(memory)
        self._report_outcome(memory, executable_outcome)
        return result

    def _report_outcome(self, memory: AgentMemory, outcome) -> None:
        if self.reporter is None:
            return
        if memory.state.observations:
            self.reporter.report_result(memory.state.observations[-1].result_summary)
        if isinstance(outcome, WriteObservationOutcome):
            self.reporter.report_diff(outcome.write_result)
        if isinstance(outcome, CommandObservationOutcome) and outcome.result.exit_code != 0:
            pass
        self.reporter.report_step_completion(memory.state)
