from __future__ import annotations

from dataclasses import dataclass, field

from .artifacts import EvidenceItem, FactItem, RepoMapEntry, SuccessCriterionStatus


@dataclass(slots=True)
class TaskResult:
    result_kind: str
    primary_text: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    repo_map: list[RepoMapEntry] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suggested_next_questions: list[str] = field(default_factory=list)
    success_criteria: list[SuccessCriterionStatus] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return self.primary_text

    @property
    def summary(self) -> str:
        return self.primary_text


@dataclass(slots=True)
class TurnArtifacts:
    facts: list[FactItem] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    validation_runs: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunOutcome:
    result: TaskResult
    artifacts: TurnArtifacts
