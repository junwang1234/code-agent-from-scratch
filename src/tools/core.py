from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..models import ValidationDiscoveryState, WriteResult
from .repo_filesystem import RepoFilesystem
from .shell import CommandResult, SafeCommandRunner, ShellQueryResult, ShellQueryRunner


@dataclass(slots=True)
class ToolExecutionContext:
    repo_filesystem: RepoFilesystem
    shell_runner: ShellQueryRunner
    command_runner: SafeCommandRunner
    validation_service: Any


@dataclass(slots=True)
class TreeToolResult:
    tree: list[str]
    depth: int


@dataclass(slots=True)
class HeadFileToolResult:
    paths: list[str]
    lines: int
    excerpts: list["FileExcerpt"]


@dataclass(slots=True)
class ReadFileRangeToolResult:
    path: str
    start_line: int
    end_line: int
    excerpt: str


@dataclass(slots=True)
class FileExcerpt:
    path: str
    start_line: int
    end_line: int
    excerpt: str


@dataclass(slots=True)
class ShellToolResult:
    observation_tool: str
    result: ShellQueryResult
    empty_results_retryable: bool = False
    empty_results_message: str = ""


@dataclass(slots=True)
class WriteToolResult:
    tool_name: str
    write_result: WriteResult
    summary: str


@dataclass(slots=True)
class CommandToolResult:
    tool_name: str
    result: CommandResult
    discovery_state: ValidationDiscoveryState | None = None


ToolResult = TreeToolResult | HeadFileToolResult | ReadFileRangeToolResult | ShellToolResult | WriteToolResult | CommandToolResult


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
