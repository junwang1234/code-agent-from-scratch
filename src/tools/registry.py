from __future__ import annotations

from typing import Any

from .command import FormatCodeTool, RunCommandTool, RunTestsTool
from .file_tools import ApplyPatchTool, HeadFileTool, ListTreeTool, ReadFileRangeTool, WriteFileTool
from .search import (
    FindPathsTool,
    ListFilesTool,
    RgFilesTool,
    RgProbeTool,
    RgSearchTool,
    SearchCodeTool,
)
from .core import Tool


class FinishTool(Tool):
    name = "finish"
    description = "Return the final response when enough evidence or work exists."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, context, payload):  # type: ignore[override]
        raise NotImplementedError("finish is handled by the runtime, not dispatched as a tool.")


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [self._tools[name].spec() for name in self.names()]


def build_default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ApplyPatchTool(),
            FindPathsTool(),
            FinishTool(),
            FormatCodeTool(),
            HeadFileTool(),
            ListFilesTool(),
            ListTreeTool(),
            ReadFileRangeTool(),
            RgFilesTool(),
            RgProbeTool(),
            RgSearchTool(),
            RunCommandTool(),
            RunTestsTool(),
            SearchCodeTool(),
            WriteFileTool(),
        ]
    )
