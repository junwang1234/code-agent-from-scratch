from __future__ import annotations

from dataclasses import dataclass

from ..models import (
    Action,
    ActionExecutionError,
    FactItem,
    FileContext,
    FileSnippet,
    Observation,
    ReadRange,
    RepoMapEntry,
    SessionState,
    StructuredPlan,
    SuccessCriterionStatus,
    Task,
)


@dataclass(slots=True)
class KnowledgeStateView:
    facts: list[FactItem]
    evidence: list
    repo_map: list[RepoMapEntry]
    unknowns: list[str]
    open_questions: list[str]


@dataclass(slots=True)
class WorkspaceStateView:
    inspected_files: set[str]
    changed_files: set[str]
    validation_runs: list[str]
    edit_history: list[str]
    file_contexts: dict


@dataclass(slots=True)
class ExecutionStateView:
    current_step_id: str | None
    pending_actions: list[str]
    failures: list[str]
    action_failures: list[ActionExecutionError]
    retry_counts: dict[str, int]
    success_criteria: list[SuccessCriterionStatus]


@dataclass(slots=True)
class PolicyStateView:
    prompt_turn_count: int
    last_prompt_step_id: str | None
    incremental_turns_since_refresh: int
    last_prompt_failure_count: int


@dataclass(slots=True)
class SessionSnapshot:
    task: str
    goal: str
    current_step_id: str | None
    observations: list[Observation]
    knowledge: KnowledgeStateView
    workspace: WorkspaceStateView
    execution: ExecutionStateView
    policy: PolicyStateView


def _create_state(task: Task, plan: StructuredPlan) -> SessionState:
    return SessionState(
        task=task,
        plan=plan,
        success_criteria=[
            SuccessCriterionStatus(criterion=criterion, status="pending")
            for criterion in plan.success_criteria
        ],
    )


def create_memory(task: Task, plan: StructuredPlan) -> "AgentMemory":
    return AgentMemory(_create_state(task, plan))


class AgentMemory:
    def __init__(self, state: SessionState) -> None:
        self.state = state

    @classmethod
    def create(cls, task, plan) -> "AgentMemory":
        memory = cls(_create_state(task, plan))
        memory.compact()
        return memory

    def compact(self) -> None:
        reduce_memory(self)

    @property
    def plan(self):
        return self.state.plan

    @property
    def task(self):
        return self.state.task

    @property
    def knowledge(self) -> KnowledgeStateView:
        return KnowledgeStateView(
            facts=self.state.facts,
            evidence=self.state.evidence,
            repo_map=self.state.repo_map,
            unknowns=self.state.unknowns,
            open_questions=self.state.open_questions,
        )

    @property
    def workspace(self) -> WorkspaceStateView:
        return WorkspaceStateView(
            inspected_files=self.state.inspected_files,
            changed_files=self.state.changed_files,
            validation_runs=self.state.validation_runs,
            edit_history=self.state.edit_history,
            file_contexts=self.state.file_contexts,
        )

    @property
    def execution(self) -> ExecutionStateView:
        return ExecutionStateView(
            current_step_id=self.state.current_step_id,
            pending_actions=self.state.pending_actions,
            failures=self.state.failures,
            action_failures=self.state.action_failures,
            retry_counts=self.state.retry_counts,
            success_criteria=self.state.success_criteria,
        )

    @property
    def policy(self) -> PolicyStateView:
        return PolicyStateView(
            prompt_turn_count=self.state.prompt_turn_count,
            last_prompt_step_id=self.state.last_prompt_step_id,
            incremental_turns_since_refresh=self.state.incremental_turns_since_refresh,
            last_prompt_failure_count=self.state.last_prompt_failure_count,
        )

    def snapshot_for_policy(self) -> SessionSnapshot:
        return SessionSnapshot(
            task=self.state.task.question,
            goal=self.state.plan.goal,
            current_step_id=self.state.current_step_id,
            observations=self.state.observations,
            knowledge=self.knowledge,
            workspace=self.workspace,
            execution=self.execution,
            policy=self.policy,
        )

    def apply_action_updates(self, action: Action) -> None:
        from ..runtime.turn_artifacts import merge_fact_updates

        self.state.current_step_id = action.step_id
        self.state.last_completed_step_ids = list(action.completed_step_ids)
        self.state.last_criterion_updates = list(action.criterion_updates)
        self.state.last_fact_updates = list(action.fact_updates)
        self.state.pending_actions.append(f"{action.kind}:{action.step_id}:{action.reason}")
        self.state.pending_actions = self.state.pending_actions[-8:]
        for step in self.state.plan.steps:
            if step.id in action.completed_step_ids:
                step.status = "completed"
        active_step = next((step for step in self.state.plan.steps if step.id == action.step_id), None)
        if active_step and active_step.status == "pending":
            active_step.status = "in_progress"
        for update in action.criterion_updates:
            existing = next((criterion for criterion in self.state.success_criteria if criterion.criterion == update.criterion), None)
            if existing is None:
                self.state.success_criteria.append(update)
            else:
                existing.status = update.status
                existing.note = update.note
        if action.fact_updates:
            self.state.facts = merge_fact_updates(self.state, action.fact_updates)
            reduce_memory(self)

    def apply_finish(self, action: Action) -> None:
        for step in self.state.plan.steps:
            if step.status != "completed":
                step.status = "completed"
        if action.answer:
            self.state.final_answer_override = action.answer
            self.state.unknowns = action.unknowns[:]
            self.state.evidence = action.evidence or self.state.evidence
            self.state.repo_map = action.repo_map or self.state.repo_map
            if action.suggested_next_questions:
                self.state.suggested_next_questions_override = action.suggested_next_questions[:]
        if action.criterion_updates:
            self.apply_action_updates(action)
        else:
            for criterion in self.state.success_criteria:
                if criterion.status == "pending":
                    criterion.status = "partial" if not self.state.evidence else "met"
                    criterion.note = "Completed during final synthesis."
        self.state.note_observation("finish", action.reason, action.answer[:120])

    def note_step_budget_exhausted(self) -> None:
        self.state.unknowns.append("The step budget was exhausted before the agent returned a finish action.")

    def add_repo_map_entries(self, entries: list[RepoMapEntry]) -> None:
        self.state.repo_map.extend(entries)

    def add_facts(self, facts: list[FactItem]) -> None:
        self.state.facts.extend(facts)

    def mark_inspected_files(self, paths: list[str]) -> None:
        for path in paths:
            if path:
                self.state.inspected_files.add(path.rstrip("/"))

    def record_observation(
        self,
        tool: str,
        tool_input: str,
        result_summary: str,
        highlights: list[str] | None = None,
        raw_output: list[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.state.note_observation(
            tool,
            tool_input,
            result_summary,
            highlights or [],
            raw_output=raw_output or [],
            metadata=metadata or {},
        )

    def record_tree_observation(self, *, depth: int, tree: list[str], summary: str, highlights: list[str], facts: list[FactItem]) -> None:
        self.record_observation("list_tree", f"depth={depth}", summary, highlights, raw_output=tree, metadata={"depth": depth, "line_count": len(tree)})
        self.add_facts(facts)
        entries = [RepoMapEntry(path=entry, note="directory" if entry.endswith("/") else "file") for entry in tree[:12]]
        self.add_repo_map_entries(entries)

    def record_file_context(
        self,
        *,
        path: str,
        start_line: int,
        end_line: int,
        excerpt: str,
        summary: str,
        step_id: str | None,
    ) -> None:
        from ..runtime.file_context_helpers import extract_symbols, is_patch_ready, merge_read_ranges, merge_snippets, merge_symbols

        context = self.state.file_contexts.get(path)
        if context is None:
            context = FileContext(path=path)
            self.state.file_contexts[path] = context
        context.read_ranges = merge_read_ranges(context.read_ranges + [ReadRange(start_line=start_line, end_line=end_line)])
        snippet = FileSnippet(path=path, start_line=start_line, end_line=end_line, excerpt=excerpt)
        context.excerpts = merge_snippets(context.excerpts + [snippet])
        context.symbols_seen = merge_symbols(context.symbols_seen, extract_symbols(excerpt))
        context.last_summary = summary
        context.patch_ready = is_patch_ready(path, context.read_ranges)
        context.last_read_step_id = step_id
        self.state.snippets = merge_snippets(self.state.snippets + [snippet])[-12:]

    def record_file_read(
        self,
        *,
        tool: str,
        tool_input: str,
        path: str,
        start_line: int,
        end_line: int,
        excerpt: str,
        summary: str,
        highlights: list[str],
        facts: list[FactItem],
        repo_note: str,
        raw_output: list[str],
    ) -> None:
        self.mark_inspected_files([path])
        self.record_file_context(path=path, start_line=start_line, end_line=end_line, excerpt=excerpt, summary=summary, step_id=self.state.current_step_id)
        self.add_facts(facts)
        self.add_repo_map_entries([RepoMapEntry(path=path, note=repo_note)])
        self.record_observation(
            tool,
            tool_input,
            summary,
            highlights[:6],
            raw_output=raw_output,
            metadata={"line_count": len(raw_output), "path": path, "start_line": start_line, "end_line": end_line},
        )

    def record_head_file_batch(
        self,
        *,
        tool_input: str,
        summaries: list[str],
        highlights: list[str],
        raw_output: list[str],
        repo_entries: list[RepoMapEntry],
        facts: list[FactItem],
        inspected_files: list[str],
    ) -> None:
        self.mark_inspected_files(inspected_files)
        self.add_facts(facts)
        self.add_repo_map_entries(repo_entries)
        self.record_observation(
            "head_file",
            tool_input,
            " ".join(summaries) if summaries else "head_file returned no readable file content.",
            highlights[:6],
            raw_output=raw_output,
            metadata={"line_count": len(raw_output)},
        )

    def record_shell_observation(
        self,
        *,
        observation_tool: str,
        tool_input: str,
        summary: str,
        highlights: list[str],
        raw_output: list[str],
        metadata: dict,
        inspected_files: list[str],
        facts: list[FactItem],
    ) -> None:
        self.record_observation(observation_tool, tool_input, summary, highlights, raw_output=raw_output, metadata=metadata)
        self.mark_inspected_files(inspected_files)
        self.add_facts(facts)

    def record_write(self, *, tool_name: str, path: str, summary: str) -> None:
        self.state.changed_files.add(path)
        self.state.edit_history.append(summary)
        self.state.edit_history = self.state.edit_history[-12:]
        self.record_observation(tool_name, path, summary, [summary], metadata={"changed_file": path})

    def record_command(self, *, tool_name: str, rendered: str, summary: str, highlights: list[str], raw_output: list[str], metadata: dict, validation_note: str, success: bool) -> None:
        self.record_observation(tool_name, rendered, summary, highlights, raw_output=raw_output, metadata=metadata)
        if success:
            self.state.validation_runs.append(validation_note)
            self.state.validation_runs = self.state.validation_runs[-12:]
            return
        failure = f"{rendered} failed with exit code {metadata.get('exit_code')}."
        if highlights:
            failure = failure + " " + highlights[0]
        self.state.failures.append(failure)
        self.state.failures = self.state.failures[-12:]
        self.state.validation_runs.append(validation_note)
        self.state.validation_runs = self.state.validation_runs[-12:]

    def record_action_failure(self, failure: ActionExecutionError) -> None:
        self.state.action_failures.append(failure)
        self.state.action_failures = self.state.action_failures[-8:]
        self.state.last_action_failure = failure
        failure_summary = f"{failure.tool_name} failed ({failure.failure_kind}) on attempt {failure.attempt_index}: {failure.message}"
        self.state.failures.append(failure_summary)
        self.state.failures = self.state.failures[-12:]

    def record_action_failure_from_error(self, action: Action, error) -> ActionExecutionError:
        from ..runtime.action_repair import action_fingerprint

        fingerprint = action_fingerprint(action)
        attempt_index = self.state.retry_counts.get(fingerprint, 0) + 1
        self.state.retry_counts[fingerprint] = attempt_index
        failure = ActionExecutionError(
            step_id=action.step_id,
            tool_name=action.tool_name or "",
            tool_input=dict(action.tool_input),
            failure_kind=error.failure_kind,
            message=error.message,
            highlights=error.highlights[:6],
            raw_output=error.raw_output[:12],
            attempt_index=attempt_index,
            retryable=error.retryable,
        )
        self.record_action_failure(failure)
        self.compact()
        return failure


def reduce_memory(memory: AgentMemory) -> None:
    _reduce_memory_state(memory.state)


def build_snapshot_prompt_state(memory: AgentMemory, remaining_steps: int) -> dict:
    state = memory.state
    active_step = next((step for step in state.plan.steps if step.status != "completed"), state.plan.steps[-1])
    file_contexts = _select_file_contexts(state)
    return {
        "task": state.task.question,
        "goal": state.plan.goal,
        "constraints": state.plan.constraints,
        "active_step": {
            "id": active_step.id,
            "purpose": active_step.purpose,
            "allowed_tools": active_step.allowed_tools,
            "depends_on": active_step.depends_on,
            "status": active_step.status,
        },
        "completed_steps": [step.id for step in state.plan.steps if step.status == "completed"],
        "archived_steps": state.archived_step_notes[-4:],
        "success_criteria": [
            {"criterion": item.criterion, "status": item.status, "note": item.note}
            for item in state.success_criteria
        ],
        "working_summary": state.working_summary,
        "facts": [
            {
                "statement": fact.statement,
                "files": fact.files,
                "confidence": fact.confidence,
                "status": fact.status,
                "source": fact.source,
            }
            for fact in state.facts[-8:]
        ],
        "open_questions": state.open_questions[:5],
        "recent_observations": [_serialize_observation(observation) for observation in _active_step_observations(state)[-4:]],
        "inspected_files": sorted(state.inspected_files)[-8:],
        "file_contexts": [_serialize_file_context(item) for item in file_contexts],
        "patch_ready_files": [item.path for item in file_contexts if item.patch_ready][:6],
        "changed_files": sorted(state.changed_files)[-8:],
        "edit_history": state.edit_history[-6:],
        "validation_runs": state.validation_runs[-4:],
        "failures": state.failures[-4:],
        "recent_action_failures": [_serialize_action_failure(item) for item in state.action_failures[-3:]],
        "last_failed_action": _serialize_action_failure(state.last_action_failure),
        "retry_counts": state.retry_counts,
        "remaining_steps": remaining_steps,
    }


def build_incremental_prompt_state(memory: AgentMemory, remaining_steps: int) -> dict:
    state = memory.state
    active_step = next((step for step in state.plan.steps if step.status != "completed"), state.plan.steps[-1])
    latest_observation = state.observations[-1] if state.observations else None
    return {
        "active_step": {
            "id": active_step.id,
            "purpose": active_step.purpose,
            "allowed_tools": active_step.allowed_tools,
            "depends_on": active_step.depends_on,
            "status": active_step.status,
        },
        "completed_steps_delta": state.last_completed_step_ids[-4:],
        "criterion_updates_delta": [
            {"criterion": item.criterion, "status": item.status, "note": item.note}
            for item in state.last_criterion_updates[-4:]
        ],
        "latest_observation": _serialize_observation(latest_observation) if latest_observation is not None else None,
        "latest_changed_files": sorted(state.changed_files)[-4:],
        "latest_edit_note": state.edit_history[-1] if state.edit_history else "",
        "latest_validation_run": state.validation_runs[-1] if state.validation_runs else "",
        "latest_failure": state.failures[-1] if state.failures else "",
        "latest_action_failure": _serialize_action_failure(state.last_action_failure),
        "remaining_steps": remaining_steps,
    }


def _reduce_memory_state(state: SessionState) -> None:
    _compact_completed_step_observations(state)
    state.facts = _dedupe_facts(state.facts)[-12:]
    state.open_questions = _dedupe_strings(_collect_open_questions(state))[:6]
    state.working_summary = _build_working_summary(state)


def _build_working_summary(state: SessionState) -> str:
    patch_ready = [item.path for item in _select_file_contexts(state) if item.patch_ready]
    if patch_ready:
        return "Patch-ready files: " + ", ".join(patch_ready[:3]) + "."
    recent_shell = next(
        (
            item
            for item in reversed(state.observations)
            if item.tool in {"list_tree", "head_file", "rg_probe", "rg_search", "rg_files", "find_paths", "list_files", "read_file_range", "search_code", "run_command", "run_tests", "format_code"} and item.raw_output
        ),
        None,
    )
    if recent_shell is not None:
        return f"{recent_shell.tool_input} returned {len(recent_shell.raw_output)} raw line(s)."
    if state.facts:
        return " ".join(fact.statement for fact in state.facts[-3:])
    if state.archived_step_notes:
        return state.archived_step_notes[-1]
    if state.observations:
        return state.observations[-1].result_summary
    return "No evidence has been collected yet."


def _collect_open_questions(state: SessionState) -> list[str]:
    questions = list(state.plan.unknowns_to_resolve)
    questions.extend(state.unknowns)
    questions.extend(item.criterion for item in state.success_criteria if item.status != "met")
    return questions


def _dedupe_facts(facts: list[FactItem]) -> list[FactItem]:
    ordered: list[FactItem] = []
    seen: set[str] = set()
    for fact in reversed(facts):
        if fact.statement in seen:
            continue
        seen.add(fact.statement)
        ordered.append(fact)
    return list(reversed(ordered))


def _dedupe_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _compact_completed_step_observations(state: SessionState) -> None:
    completed_step_ids = {step.id for step in state.plan.steps if step.status == "completed"}
    archived_notes = list(state.archived_step_notes)
    for observation in state.observations:
        step_id = observation.metadata.get("step_id")
        if not step_id or step_id not in completed_step_ids or observation.metadata.get("archived"):
            continue
        archived_notes.append(f"{step_id}: {observation.tool} -> {observation.result_summary}")
        observation.raw_output = []
        observation.metadata["archived"] = True
        observation.metadata["highlights_retained"] = min(len(observation.highlights), 2)
        observation.highlights = observation.highlights[:2]
    state.archived_step_notes = _dedupe_strings(archived_notes)[-8:]


def _active_step_observations(state: SessionState) -> list:
    active_step = next((step.id for step in state.plan.steps if step.status != "completed"), None)
    active = [obs for obs in state.observations if obs.metadata.get("step_id") == active_step and not obs.metadata.get("archived")]
    return active or [obs for obs in state.observations if not obs.metadata.get("archived")]


def _select_file_contexts(state: SessionState) -> list[FileContext]:
    contexts = list(state.file_contexts.values())
    contexts.sort(key=lambda item: (0 if item.patch_ready else 1, item.path))
    return contexts[:6]


def _serialize_file_context(context: FileContext) -> dict:
    return {
        "path": context.path,
        "read_ranges": [[item.start_line, item.end_line] for item in context.read_ranges[:6]],
        "symbols_seen": context.symbols_seen[:8],
        "summary": context.last_summary,
        "patch_ready": context.patch_ready,
        "excerpts": [
            {"start_line": item.start_line, "end_line": item.end_line, "excerpt": item.excerpt}
            for item in context.excerpts[:3]
        ],
    }


def _serialize_observation(observation) -> dict:
    return {
        "tool": observation.tool,
        "tool_input": observation.tool_input,
        "result_summary": observation.result_summary,
        "highlights": observation.highlights,
        "raw_output": observation.raw_output if observation.tool in {"list_tree", "head_file", "rg_probe", "rg_search", "rg_files", "find_paths", "list_files", "read_file_range", "search_code", "run_command", "run_tests", "format_code"} else [],
        "metadata": observation.metadata if observation.tool in {"list_tree", "head_file", "rg_probe", "rg_search", "rg_files", "find_paths", "list_files", "read_file_range", "search_code", "run_command", "run_tests", "format_code"} else {},
    }


def _serialize_action_failure(failure) -> dict | None:
    if failure is None:
        return None
    return {
        "step_id": failure.step_id,
        "tool_name": failure.tool_name,
        "tool_input": failure.tool_input,
        "failure_kind": failure.failure_kind,
        "message": failure.message,
        "highlights": failure.highlights,
        "raw_output": failure.raw_output,
        "attempt_index": failure.attempt_index,
        "retryable": failure.retryable,
    }
