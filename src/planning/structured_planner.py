from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..runtime.memory_manager import AgentMemory
from ..models import (
    Action,
    EvidenceItem,
    FactItem,
    FinishPayload,
    MemoryUpdates,
    PlanStep,
    RepoMapEntry,
    StructuredPlan,
    SuccessCriterionStatus,
    Task,
    ToolCall,
)
from ..providers.codex_cli import CodexCliProvider
from ..providers.base import LLMProvider, StructuredCall
from ..tools import build_default_tool_registry
from .base import BasePlanner
from .prompt_builder import PlanningPromptBuilder


UNIFIED_TOOL_SPECS = build_default_tool_registry().specs()


@dataclass(slots=True)
class PlannerProposal:
    kind: str
    step_id: str
    reason: str
    source_name: str
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    completed_step_ids: list[str] = field(default_factory=list)
    criterion_updates: list[dict[str, Any]] = field(default_factory=list)
    fact_updates: list[dict[str, Any]] = field(default_factory=list)
    finish_payload: dict[str, Any] | None = None


class StructuredPlanner(BasePlanner):
    def __init__(self, provider: LLMProvider, prompt_builder: PlanningPromptBuilder | None = None) -> None:
        self.provider = provider
        self.prompt_builder = prompt_builder or PlanningPromptBuilder(UNIFIED_TOOL_SPECS)

    def make_plan(self, task: Task) -> StructuredPlan:
        payload = self._generate_plan(
            StructuredCall(
                prompt=self.prompt_builder.build_plan_prompt(task),
                schema=_plan_schema(),
                call_kind="plan",
            )
        )
        return _parse_plan_payload(payload)

    def next_action(self, memory: AgentMemory, remaining_steps: int) -> Action:
        prompt, prompt_state_kind = self.prompt_builder.build_action_prompt(memory, remaining_steps)
        payload = self._generate_action(
            StructuredCall(
                prompt=prompt,
                schema=_action_schema(),
                call_kind="action",
            )
        )
        self.prompt_builder.refresh_strategy.record_action_prompt_use(memory.state, prompt_state_kind)
        proposal = _parse_planner_proposal(payload, source_name=getattr(self.provider, "source_name", "planner"))
        return _proposal_to_action(memory, proposal)

    def _generate_plan(self, call: StructuredCall) -> dict:
        return self.provider.generate_plan(call)

    def _generate_action(self, call: StructuredCall) -> dict:
        return self.provider.generate_action(call)

    def get_session_id(self) -> str | None:
        return self.provider.get_session_id()

    def set_session_id(self, session_id: str | None) -> None:
        self.provider.set_session_id(session_id)


def build_planner(
    planner_kind: str,
    workdir: Path | None = None,
    trace_to_stderr: bool = False,
    timeout_sec: int = 60,
    trace_file: Path | None = None,
) -> BasePlanner:
    if planner_kind == "codex":
        return StructuredPlanner(
            provider=CodexCliProvider(
                workdir=workdir,
                trace_to_stderr=trace_to_stderr,
                timeout_sec=timeout_sec,
                trace_file=trace_file,
            )
        )
    raise ValueError(f"Unsupported planner: {planner_kind}")


def _plan_schema() -> dict:
    return {
        "type": "object",
        "required": [
            "goal",
            "question_type",
            "constraints",
            "hypotheses",
            "steps",
            "search_terms",
            "file_hints",
            "success_criteria",
            "unknowns_to_resolve",
        ],
        "properties": {
            "goal": {"type": "string"},
            "question_type": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "hypotheses": {"type": "array", "items": {"type": "string"}},
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "required": ["id", "purpose", "allowed_tools", "depends_on"],
                    "properties": {
                        "id": {"type": "string"},
                        "purpose": {"type": "string"},
                        "allowed_tools": {"type": "array", "items": {"type": "string", "enum": [tool["name"] for tool in UNIFIED_TOOL_SPECS]}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
            },
            "search_terms": {"type": "array", "items": {"type": "string"}},
            "file_hints": {"type": "array", "items": {"type": "string"}},
            "success_criteria": {"type": "array", "items": {"type": "string"}},
            "unknowns_to_resolve": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _action_schema() -> dict:
    payload_properties = _action_payload_properties()
    return {
        "type": "object",
        "required": ["kind", "step_id", "reason", "tool_call", "updates", "finish"],
        "properties": {
            "kind": {"type": "string", "enum": ["tool", "finish"]},
            "step_id": {"type": "string"},
            "reason": {"type": "string"},
            "tool_call": {
                "type": ["object", "null"],
                "required": ["tool_name", "payload"],
                "properties": {
                    "tool_name": {"type": "string", "enum": [tool["name"] for tool in UNIFIED_TOOL_SPECS if tool["name"] != "finish"]},
                    "payload": {"type": "object", "required": list(payload_properties), "properties": payload_properties, "additionalProperties": False},
                },
                "additionalProperties": False,
            },
            "updates": {
                "type": "object",
                "required": ["completed_step_ids", "criterion_updates", "fact_updates"],
                "properties": {
                    "completed_step_ids": {"type": "array", "items": {"type": "string"}},
                    "criterion_updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["criterion", "status", "note"],
                            "properties": {
                                "criterion": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "partial", "met"]},
                                "note": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "fact_updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["statement", "files", "confidence", "status"],
                            "properties": {
                                "statement": {"type": "string"},
                                "files": {"type": "array", "items": {"type": "string"}},
                                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                                "status": {"type": "string", "enum": ["candidate", "confirmed", "retracted"]},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            "finish": {
                "type": ["object", "null"],
                "required": ["answer", "evidence", "repo_map", "unknowns", "suggested_next_questions"],
                "properties": {
                    "answer": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["claim", "files", "confidence"],
                            "properties": {
                                "claim": {"type": "string"},
                                "files": {"type": "array", "items": {"type": "string"}},
                                "confidence": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "repo_map": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["path", "note"],
                            "properties": {"path": {"type": "string"}, "note": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    },
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                    "suggested_next_questions": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _action_payload_properties() -> dict:
    properties: dict = {}
    for tool in UNIFIED_TOOL_SPECS:
        schema = tool.get("input_schema", {})
        for key, value in schema.get("properties", {}).items():
            properties[key] = _nullable_schema(value)
    return properties


def _nullable_schema(schema: dict) -> dict:
    nullable = dict(schema)
    schema_type = nullable.get("type")
    if isinstance(schema_type, str):
        nullable["type"] = [schema_type, "null"]
    elif isinstance(schema_type, list):
        types = list(schema_type)
        if "null" not in types:
            types.append("null")
        nullable["type"] = types
    if "enum" in nullable and None not in nullable["enum"]:
        nullable["enum"] = [*nullable["enum"], None]
    return nullable


def _parse_plan_payload(payload: dict) -> StructuredPlan:
    steps = [
        PlanStep(id=step["id"], purpose=step["purpose"], allowed_tools=step["allowed_tools"], depends_on=step.get("depends_on", []))
        for step in payload["steps"]
    ]
    return StructuredPlan(
        goal=payload["goal"],
        question_type=payload["question_type"],
        steps=steps,
        success_criteria=payload["success_criteria"],
        constraints=payload.get("constraints", []),
        hypotheses=payload.get("hypotheses", []),
        search_terms=payload.get("search_terms", []),
        file_hints=payload.get("file_hints", []),
        unknowns_to_resolve=payload.get("unknowns_to_resolve", []),
    )

def _parse_planner_proposal(payload: dict, *, source_name: str) -> PlannerProposal:
    updates_payload = payload.get("updates") or {}
    finish_payload = payload.get("finish")
    tool_call_payload = payload.get("tool_call")
    tool_name = None
    tool_input: dict = {}
    if tool_call_payload:
        tool_name = tool_call_payload.get("tool_name") or tool_call_payload.get("name")
        tool_input = tool_call_payload.get("payload") or tool_call_payload.get("arguments") or {}
    return PlannerProposal(
        kind=payload["kind"],
        step_id=payload["step_id"],
        reason=payload["reason"],
        source_name=source_name,
        tool_name=tool_name,
        tool_input=tool_input,
        completed_step_ids=updates_payload.get("completed_step_ids", []),
        criterion_updates=updates_payload.get("criterion_updates", []),
        fact_updates=updates_payload.get("fact_updates", []),
        finish_payload=finish_payload,
    )


def _proposal_to_action(_memory: AgentMemory | None, proposal: PlannerProposal) -> Action:
    finish_payload = proposal.finish_payload
    tool_call = ToolCall(tool_name=proposal.tool_name or "", payload=proposal.tool_input) if proposal.tool_name else None
    return Action(
        kind=proposal.kind,
        step_id=proposal.step_id,
        reason=proposal.reason,
        tool_call=tool_call,
        updates=MemoryUpdates(
            completed_step_ids=proposal.completed_step_ids,
            criterion_updates=_parse_criterion_updates(proposal.criterion_updates),
            fact_updates=_parse_fact_updates(proposal.fact_updates, source_name=proposal.source_name),
        ),
        finish=(
            FinishPayload(
                answer=finish_payload.get("answer", ""),
                evidence=_parse_evidence_items(finish_payload.get("evidence", [])),
                repo_map=[RepoMapEntry(path=item["path"], note=item["note"]) for item in finish_payload.get("repo_map", [])],
                unknowns=finish_payload.get("unknowns", []),
                suggested_next_questions=finish_payload.get("suggested_next_questions", []),
            )
            if finish_payload
            else None
        ),
    )


def _parse_criterion_update_item(item: dict) -> SuccessCriterionStatus | None:
    criterion = item.get("criterion")
    status = item.get("status")
    if not criterion or not status:
        return None
    normalized = "met" if status == "completed" else status
    return SuccessCriterionStatus(criterion=criterion, status=normalized, note=item.get("note", ""))


def _parse_fact_update_item(item: dict, *, source_name: str) -> FactItem | None:
    statement = item.get("statement") or item.get("fact")
    if not statement:
        return None
    return FactItem(
        statement=statement,
        files=item.get("files", []),
        confidence=item.get("confidence", "medium"),
        status=item.get("status", "candidate"),
        source=source_name,
    )


def _parse_fact_updates(items: list[dict], *, source_name: str) -> list[FactItem]:
    parsed: list[FactItem] = []
    for item in items:
        fact = _parse_fact_update_item(item, source_name=source_name)
        if fact is not None:
            parsed.append(fact)
    return parsed


def _parse_criterion_updates(items: list[dict]) -> list[SuccessCriterionStatus]:
    parsed: list[SuccessCriterionStatus] = []
    for item in items:
        criterion = _parse_criterion_update_item(item)
        if criterion is not None:
            parsed.append(criterion)
    return parsed


def _parse_evidence_items(items: list[dict]) -> list[EvidenceItem]:
    parsed: list[EvidenceItem] = []
    for item in items:
        claim = item.get("claim")
        files = item.get("files")
        confidence = item.get("confidence")
        if claim and isinstance(files, list) and confidence:
            parsed.append(EvidenceItem(claim=claim, files=files, confidence=confidence))
            continue
        file_path = item.get("file")
        points = item.get("points")
        if file_path and isinstance(points, list) and points:
            parsed.append(EvidenceItem(claim=" ".join(str(point) for point in points), files=[file_path], confidence="medium"))
    return parsed
