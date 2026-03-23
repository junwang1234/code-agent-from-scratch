from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Plan:
    goal: str
    question_type: str
    steps: list[str]
    search_terms: list[str]
    file_hints: list[str]
    hypotheses: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    unknowns_to_resolve: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanStep:
    id: str
    purpose: str
    allowed_tools: list[str]
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StructuredPlan:
    goal: str
    question_type: str
    steps: list[PlanStep]
    success_criteria: list[str]
    constraints: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    file_hints: list[str] = field(default_factory=list)
    unknowns_to_resolve: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionStep:
    step: PlanStep

    @property
    def id(self) -> str:
        return self.step.id

    @property
    def purpose(self) -> str:
        return self.step.purpose

    @property
    def allowed_tools(self) -> list[str]:
        return self.step.allowed_tools

    @property
    def depends_on(self) -> list[str]:
        return self.step.depends_on

    @property
    def status(self) -> str:
        return self.step.status

    def mark_completed(self) -> None:
        self.step.status = "completed"

    def mark_in_progress(self) -> None:
        if self.step.status == "pending":
            self.step.status = "in_progress"


@dataclass(slots=True)
class ExecutionPlan:
    plan: StructuredPlan

    @property
    def goal(self) -> str:
        return self.plan.goal

    @property
    def steps(self) -> list[ExecutionStep]:
        return [ExecutionStep(step) for step in self.plan.steps]

    def active_step(self) -> ExecutionStep:
        step = next((step for step in self.plan.steps if step.status != "completed"), self.plan.steps[-1])
        return ExecutionStep(step)
