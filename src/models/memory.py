from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .artifacts import ActionExecutionError, EvidenceItem, FactItem, FileContext, FileSnippet, Observation, RepoMapEntry, SuccessCriterionStatus
from .plan import StructuredPlan
from .task import Task
from .validation import ValidationDiscoveryState


@dataclass(slots=True)
class SessionState:
    task: Task
    plan: StructuredPlan
    current_step_id: str | None = None
    observations: list[Observation] = field(default_factory=list)
    archived_step_notes: list[str] = field(default_factory=list)
    facts: list[FactItem] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    working_summary: str = ""
    evidence: list[EvidenceItem] = field(default_factory=list)
    repo_map: list[RepoMapEntry] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    snippets: list[FileSnippet] = field(default_factory=list)
    inspected_files: set[str] = field(default_factory=set)
    file_contexts: dict[str, FileContext] = field(default_factory=dict)
    changed_files: set[str] = field(default_factory=set)
    edit_history: list[str] = field(default_factory=list)
    validation_runs: list[str] = field(default_factory=list)
    validation_discovery: ValidationDiscoveryState | None = None
    failures: list[str] = field(default_factory=list)
    action_failures: list[ActionExecutionError] = field(default_factory=list)
    last_action_failure: ActionExecutionError | None = None
    retry_counts: dict[str, int] = field(default_factory=dict)
    pending_actions: list[str] = field(default_factory=list)
    success_criteria: list[SuccessCriterionStatus] = field(default_factory=list)
    final_answer_override: str | None = None
    suggested_next_questions_override: list[str] = field(default_factory=list)
    last_completed_step_ids: list[str] = field(default_factory=list)
    last_criterion_updates: list[SuccessCriterionStatus] = field(default_factory=list)
    last_fact_updates: list[FactItem] = field(default_factory=list)
    prompt_turn_count: int = 0
    last_prompt_step_id: str | None = None
    incremental_turns_since_refresh: int = 0
    last_prompt_failure_count: int = 0

    def note_observation(
        self,
        tool: str,
        tool_input: str,
        result_summary: str,
        highlights: list[str] | None = None,
        raw_output: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.observations.append(
            Observation(
                tool=tool,
                tool_input=tool_input,
                result_summary=result_summary,
                highlights=highlights or [],
                raw_output=raw_output or [],
                metadata={
                    **({"step_id": self.current_step_id} if self.current_step_id else {}),
                    **(metadata or {}),
                },
            )
        )
