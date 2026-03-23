from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .artifacts import EvidenceItem, FactItem, RepoMapEntry, SuccessCriterionStatus


@dataclass(slots=True)
class ToolCall:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryUpdates:
    completed_step_ids: list[str] = field(default_factory=list)
    criterion_updates: list[SuccessCriterionStatus] = field(default_factory=list)
    fact_updates: list[FactItem] = field(default_factory=list)


@dataclass(slots=True)
class FinishPayload:
    answer: str = ""
    evidence: list[EvidenceItem] = field(default_factory=list)
    repo_map: list[RepoMapEntry] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suggested_next_questions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Action:
    kind: str
    step_id: str
    reason: str
    tool_call: ToolCall | None = None
    updates: MemoryUpdates = field(default_factory=MemoryUpdates)
    finish: FinishPayload | None = None

    @classmethod
    def tool(
        cls,
        *,
        step_id: str,
        reason: str,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        completed_step_ids: list[str] | None = None,
        criterion_updates: list[SuccessCriterionStatus] | None = None,
        fact_updates: list[FactItem] | None = None,
    ) -> "Action":
        return cls(
            kind="tool",
            step_id=step_id,
            reason=reason,
            tool_call=ToolCall(tool_name=tool_name, payload=tool_input or {}),
            updates=MemoryUpdates(
                completed_step_ids=completed_step_ids or [],
                criterion_updates=criterion_updates or [],
                fact_updates=fact_updates or [],
            ),
        )

    @classmethod
    def finish_action(
        cls,
        *,
        step_id: str,
        reason: str,
        answer: str = "",
        evidence: list[EvidenceItem] | None = None,
        repo_map: list[RepoMapEntry] | None = None,
        unknowns: list[str] | None = None,
        suggested_next_questions: list[str] | None = None,
        completed_step_ids: list[str] | None = None,
        criterion_updates: list[SuccessCriterionStatus] | None = None,
        fact_updates: list[FactItem] | None = None,
    ) -> "Action":
        return cls(
            kind="finish",
            step_id=step_id,
            reason=reason,
            updates=MemoryUpdates(
                completed_step_ids=completed_step_ids or [],
                criterion_updates=criterion_updates or [],
                fact_updates=fact_updates or [],
            ),
            finish=FinishPayload(
                answer=answer,
                evidence=evidence or [],
                repo_map=repo_map or [],
                unknowns=unknowns or [],
                suggested_next_questions=suggested_next_questions or [],
            ),
        )

    @property
    def tool_name(self) -> str | None:
        return self.tool_call.tool_name if self.tool_call else None

    @tool_name.setter
    def tool_name(self, value: str | None) -> None:
        if value is None:
            self.tool_call = None
            return
        if self.tool_call is None:
            self.tool_call = ToolCall(tool_name=value, payload={})
            return
        self.tool_call.tool_name = value

    @property
    def tool_input(self) -> dict[str, Any]:
        return self.tool_call.payload if self.tool_call else {}

    @tool_input.setter
    def tool_input(self, value: dict[str, Any]) -> None:
        if self.tool_call is None:
            self.tool_call = ToolCall(tool_name="", payload=dict(value))
            return
        self.tool_call.payload = dict(value)

    @property
    def completed_step_ids(self) -> list[str]:
        return self.updates.completed_step_ids

    @completed_step_ids.setter
    def completed_step_ids(self, value: list[str]) -> None:
        self.updates.completed_step_ids = value

    @property
    def criterion_updates(self) -> list[SuccessCriterionStatus]:
        return self.updates.criterion_updates

    @criterion_updates.setter
    def criterion_updates(self, value: list[SuccessCriterionStatus]) -> None:
        self.updates.criterion_updates = value

    @property
    def fact_updates(self) -> list[FactItem]:
        return self.updates.fact_updates

    @fact_updates.setter
    def fact_updates(self, value: list[FactItem]) -> None:
        self.updates.fact_updates = value

    @property
    def answer(self) -> str:
        return self.finish.answer if self.finish else ""

    @answer.setter
    def answer(self, value: str) -> None:
        if self.finish is None:
            self.finish = FinishPayload(answer=value)
            return
        self.finish.answer = value

    @property
    def evidence(self) -> list[EvidenceItem]:
        return self.finish.evidence if self.finish else []

    @evidence.setter
    def evidence(self, value: list[EvidenceItem]) -> None:
        if self.finish is None:
            self.finish = FinishPayload(evidence=value)
            return
        self.finish.evidence = value

    @property
    def repo_map(self) -> list[RepoMapEntry]:
        return self.finish.repo_map if self.finish else []

    @repo_map.setter
    def repo_map(self, value: list[RepoMapEntry]) -> None:
        if self.finish is None:
            self.finish = FinishPayload(repo_map=value)
            return
        self.finish.repo_map = value

    @property
    def unknowns(self) -> list[str]:
        return self.finish.unknowns if self.finish else []

    @unknowns.setter
    def unknowns(self, value: list[str]) -> None:
        if self.finish is None:
            self.finish = FinishPayload(unknowns=value)
            return
        self.finish.unknowns = value

    @property
    def suggested_next_questions(self) -> list[str]:
        return self.finish.suggested_next_questions if self.finish else []

    @suggested_next_questions.setter
    def suggested_next_questions(self, value: list[str]) -> None:
        if self.finish is None:
            self.finish = FinishPayload(suggested_next_questions=value)
            return
        self.finish.suggested_next_questions = value
