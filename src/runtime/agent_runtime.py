from __future__ import annotations

from copy import deepcopy
import time

from ..models import RunOutcome, Task, TaskResult
from ..planning.base import BasePlanner
from ..presentation.runtime_reporter import RuntimeReporter
from .action_execution import ActionExecutionFailed as _ActionExecutionFailed
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
    ) -> None:
        self.step_budget = max(4, step_budget)
        self.planner = planner
        self.reporter = reporter
        self.trace_enabled = trace_enabled
        self.event_sink = event_sink

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
        return AgentMemory.create(task, plan)

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
        except _ActionExecutionFailed as exc:
            self._handle_action_failure(memory, action, exc)
            return None
        except (ValueError, RuntimeError) as exc:
            self._handle_action_failure(memory, action, _classify_action_exception(action, exc))
            return None

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
