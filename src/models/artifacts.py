from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Observation:
    tool: str
    tool_input: str
    result_summary: str
    highlights: list[str] = field(default_factory=list)
    raw_output: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ActionExecutionError:
    step_id: str
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    failure_kind: str = "tool_error"
    message: str = ""
    highlights: list[str] = field(default_factory=list)
    raw_output: list[str] = field(default_factory=list)
    attempt_index: int = 1
    retryable: bool = False


@dataclass(slots=True)
class EvidenceItem:
    claim: str
    files: list[str]
    confidence: str


@dataclass(slots=True)
class FileSnippet:
    path: str
    start_line: int
    end_line: int
    excerpt: str


@dataclass(slots=True)
class ReadRange:
    start_line: int
    end_line: int


@dataclass(slots=True)
class WriteResult:
    path: str
    old_content: str
    new_content: str


@dataclass(slots=True)
class FileContext:
    path: str
    read_ranges: list[ReadRange] = field(default_factory=list)
    excerpts: list[FileSnippet] = field(default_factory=list)
    symbols_seen: list[str] = field(default_factory=list)
    last_summary: str = ""
    patch_ready: bool = False
    last_read_step_id: str | None = None


@dataclass(slots=True)
class RepoMapEntry:
    path: str
    note: str


@dataclass(slots=True)
class FactItem:
    statement: str
    files: list[str] = field(default_factory=list)
    confidence: str = "medium"
    status: str = "confirmed"
    source: str = "local"


@dataclass(slots=True)
class SuccessCriterionStatus:
    criterion: str
    status: str
    note: str = ""
