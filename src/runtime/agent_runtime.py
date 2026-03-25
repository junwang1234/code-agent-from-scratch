from __future__ import annotations

from copy import deepcopy
import time
from typing import Callable

from ..models import ApprovalRequest, ApprovedCommandScope, RunOutcome, Task, TaskResult
from ..planning.base import BasePlanner
from ..presentation.runtime_reporter import RuntimeReporter
from ..tools.shell import render_argv_as_shell_command
from .action_execution import ActionExecutionFailed as _ActionExecutionFailed
from .action_execution import ApprovalRequired as _ApprovalRequired
from .action_execution import ActionExecutor
from .execution_commands import action_from_command, command_from_action
from .turn_artifacts import build_turn_artifacts
from .events import RuntimeEventSink
from .memory_manager import AgentMemory
from .action_repair import classify_action_exception as _classify_action_exception
from .result_composer import compose_response


class AgentRuntime:
    def __init__(
        self,
        *,
        planner: BasePlanner,
        step_budget: int = 6,
        reporter: RuntimeReporter | None = None,
        trace_enabled: bool = False,
        event_sink: RuntimeEventSink | None = None,
        approval_handler: Callable[[ApprovalRequest], bool] | None = None,
    ) -> None:
        self.step_budget = max(4, step_budget)
        self.planner = planner
        self.reporter = reporter
        self.trace_enabled = trace_enabled
        self.event_sink = event_sink
        self.approval_handler = approval_handler
        self.approved_command_scopes: list[ApprovedCommandScope] = []

    def set_approval_handler(self, handler: Callable[[ApprovalRequest], bool] | None) -> None:
        self.approval_handler = handler

    def set_approved_command_scopes(self, scopes: list[ApprovedCommandScope]) -> None:
        self.approved_command_scopes = [
            ApprovedCommandScope(
                argv=list(item.argv),
                working_dir=item.working_dir,
                match_type=item.match_type,
                execution_mode=item.execution_mode,
            )
            for item in scopes
        ]

    def run(self, task: Task) -> TaskResult:
        return self.run_with_artifacts(task).result

    def run_with_artifacts(self, task: Task) -> RunOutcome:
        start_time = time.perf_counter()
        plan = self._start_run(task)
        memory = self._build_memory(task, plan)
        executor = ActionExecutor(task.repo_path, reporter=self.reporter)
        response = self._run_step_loop(memory, executor)
        if response is not None:
            return self._finish_run(memory, response, completed=True, start_time=start_time)
        return self._finish_budget_exhausted(memory, start_time=start_time)

    def _start_run(self, task: Task):
        self._record_event("run_started", question=task.question, repo_path=str(task.repo_path), step_budget=self.step_budget)
        if self.reporter is not None:
            self.reporter.report_run_start(task, self.step_budget, self.trace_enabled)
        plan = self.planner.make_plan(task)
        self._record_event("plan_created", goal=plan.goal, step_ids=[step.id for step in plan.steps])
        if self.reporter is not None:
            self.reporter.report_plan(plan)
        return plan

    def _build_memory(self, task: Task, plan) -> AgentMemory:
        memory = AgentMemory.create(task, plan)
        memory.state.approved_command_scopes = [
            ApprovedCommandScope(
                argv=list(item.argv),
                working_dir=item.working_dir,
                match_type=item.match_type,
                execution_mode=item.execution_mode,
            )
            for item in self.approved_command_scopes
        ]
        return memory

    def _run_step_loop(self, memory: AgentMemory, executor: ActionExecutor) -> TaskResult | None:
        for remaining_steps in range(self.step_budget, 0, -1):
            original_action, command, action = self._prepare_action(memory, executor, remaining_steps)
            self._record_selected_action(memory, original_action, action, remaining_steps)
            response = self._execute_step(memory, executor, command, action)
            if response is not None:
                return response
        return None

    def _prepare_action(self, memory: AgentMemory, executor: ActionExecutor, remaining_steps: int):
        proposed_action = self.planner.next_action(memory, remaining_steps)
        original_action = deepcopy(proposed_action)
        command = executor.normalize_command(memory, command_from_action(proposed_action), remaining_steps)
        action = action_from_command(command)
        return original_action, command, action

    def _record_selected_action(self, memory: AgentMemory, original_action, action, remaining_steps: int) -> None:
        self._record_event(
            "action_selected",
            step_id=action.step_id,
            kind=action.kind,
            tool_name=action.tool_name,
            remaining_steps=remaining_steps,
        )
        if self.reporter is None:
            return
        step = next((item for item in memory.plan.steps if item.id == action.step_id), None)
        self.reporter.report_action(step.purpose if step is not None else "Execute next planned work.", action)
        self.reporter.report_action_repaired(original_action, action)

    def _execute_step(self, memory: AgentMemory, executor: ActionExecutor, command, action) -> TaskResult | None:
        try:
            return executor.execute_command(memory, command)
        except _ApprovalRequired as exc:
            return self._handle_approval_request(memory, executor, command, action, exc.request)
        except _ActionExecutionFailed as exc:
            self._handle_action_failure(memory, action, exc)
            return None
        except (ValueError, RuntimeError) as exc:
            self._handle_action_failure(memory, action, _classify_action_exception(action, exc))
            return None

    def _handle_approval_request(self, memory: AgentMemory, executor: ActionExecutor, command, action, request: ApprovalRequest) -> TaskResult | None:
        try:
            if self._is_approved(request):
                return self._retry_with_approved_bash(memory, executor, command)
            approved = self.approval_handler(request) if self.approval_handler is not None else False
            if not approved:
                error = _ActionExecutionFailed(
                    failure_kind="approval_denied" if self.approval_handler is not None else "approval_required",
                    message=f"Approval denied for command: {render_argv_as_shell_command(request.argv)}",
                    raw_output=[render_argv_as_shell_command(request.argv)],
                    retryable=False,
                )
                self._handle_action_failure(memory, action, error)
                return None
            if request.install_suggestion is not None:
                install_result = executor.tool_executor.command_runner.run_approved_bash(
                    request.install_suggestion.argv,
                    working_dir=request.install_suggestion.working_dir,
                )
                if install_result.exit_code != 0:
                    raise _ActionExecutionFailed(
                        failure_kind="install_failed",
                        message=f"{render_argv_as_shell_command(request.install_suggestion.argv)} exited with code {install_result.exit_code}.",
                        raw_output=install_result.output,
                        retryable=False,
                    )
                if request.install_suggestion.verify_argv:
                    verify_result = executor.tool_executor.command_runner.run_approved_bash(request.install_suggestion.verify_argv)
                    if verify_result.exit_code != 0:
                        raise _ActionExecutionFailed(
                            failure_kind="install_failed",
                            message=f"Installed tool verification failed: {render_argv_as_shell_command(request.install_suggestion.verify_argv)}",
                            raw_output=verify_result.output,
                            retryable=False,
                        )
            self._remember_approved_scope(request)
            memory.state.approved_command_scopes = [
                ApprovedCommandScope(
                    argv=list(item.argv),
                    working_dir=item.working_dir,
                    match_type=item.match_type,
                    execution_mode=item.execution_mode,
                )
                for item in self.approved_command_scopes
            ]
            return self._retry_with_approved_bash(memory, executor, command)
        except _ActionExecutionFailed as exc:
            self._handle_action_failure(memory, action, exc)
            return None
        except (ValueError, RuntimeError) as exc:
            self._handle_action_failure(memory, action, _classify_action_exception(action, exc))
            return None

    def _retry_with_approved_bash(self, memory: AgentMemory, executor: ActionExecutor, command):
        approved_command = deepcopy(command)
        if hasattr(approved_command, "tool_input"):
            approved_command.tool_input = {**approved_command.tool_input, "_approved_bash": True}
        return executor.execute_command(memory, approved_command, apply_updates=False)

    def _is_approved(self, request: ApprovalRequest) -> bool:
        for scope in self.approved_command_scopes:
            if scope.working_dir != request.working_dir:
                continue
            if scope.match_type == "exact" and scope.argv == request.argv:
                return True
            if scope.match_type == "prefix" and request.argv[: len(scope.argv)] == scope.argv:
                return True
        return False

    def _remember_approved_scope(self, request: ApprovalRequest) -> None:
        if self._is_approved(request):
            return
        self.approved_command_scopes.append(
            ApprovedCommandScope(
                argv=list(request.argv),
                working_dir=request.working_dir,
                match_type="exact",
                execution_mode="approved_bash",
            )
        )

    def _handle_action_failure(self, memory: AgentMemory, action, error) -> None:
        self._record_event(
            "action_failed",
            step_id=action.step_id,
            tool_name=action.tool_name,
            failure_kind=error.failure_kind,
            message=error.message,
        )
        failure = memory.record_action_failure_from_error(action, error)
        if self.reporter is not None:
            self.reporter.report_result(f"{failure.tool_name} failed ({failure.failure_kind}) on attempt {failure.attempt_index}: {failure.message}")

    def _finish_run(self, memory: AgentMemory, response: TaskResult, *, completed: bool, start_time: float) -> RunOutcome:
        self._record_event("run_finished", result_kind=response.result_kind, completed=completed)
        if self.reporter is not None:
            self.reporter.report_finish(memory.state, response, elapsed_seconds=time.perf_counter() - start_time)
        return RunOutcome(result=response, artifacts=build_turn_artifacts(memory.state))

    def _finish_budget_exhausted(self, memory: AgentMemory, *, start_time: float) -> RunOutcome:
        memory.note_step_budget_exhausted()
        response = compose_response(memory)
        return self._finish_run(memory, response, completed=False, start_time=start_time)

    def _record_event(self, event_type: str, **payload) -> None:
        if self.event_sink is None:
            return
        self.event_sink.record(event_type, **payload)
