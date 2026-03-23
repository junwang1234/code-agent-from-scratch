from __future__ import annotations

from typing import Any

from .core import ShellToolResult, Tool, ToolExecutionContext


def _build_rg_search_args(pattern: str, paths: list[str]) -> list[str]:
    return ["-n", pattern, *paths]


class RgProbeTool(Tool):
    name = "rg_probe"
    description = "Run a narrow validated ripgrep probe over repo-relative paths."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pattern", "paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        pattern = str(payload.get("pattern") or "")
        paths = [str(item) for item in payload.get("paths") or []]
        result = context.shell_runner.run("rg", _build_rg_search_args(pattern, paths))
        return ShellToolResult(
            observation_tool=self.name,
            result=result,
            empty_results_retryable=True,
            empty_results_message="rg_probe returned no matching lines.",
        )


class RgSearchTool(Tool):
    name = "rg_search"
    description = "Run a validated ripgrep content search over repo-relative paths."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pattern", "paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        pattern = str(payload.get("pattern") or "")
        paths = [str(item) for item in payload.get("paths") or []]
        result = context.shell_runner.run("rg", _build_rg_search_args(pattern, paths))
        return ShellToolResult(
            observation_tool=self.name,
            result=result,
            empty_results_retryable=True,
            empty_results_message="rg_search returned no matching lines.",
        )


class RgFilesTool(Tool):
    name = "rg_files"
    description = "Run a validated ripgrep file listing over repo-relative paths."
    input_schema = {
        "type": "object",
        "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
        "required": ["paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        paths = [str(item) for item in payload.get("paths") or []]
        return ShellToolResult(observation_tool=self.name, result=context.shell_runner.run("rg", ["--files", *paths]))


class FindPathsTool(Tool):
    name = "find_paths"
    description = "Run a validated find-style path listing over repo-relative roots."
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 6},
            "file_type": {"type": "string", "enum": ["f", "d"]},
            "name_glob": {"type": "string"},
        },
        "required": ["paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        paths = [str(item) for item in payload.get("paths") or []]
        args = list(paths)
        max_depth = payload.get("max_depth")
        file_type = payload.get("file_type")
        name_glob = payload.get("name_glob")
        if max_depth is not None:
            args.extend(["-maxdepth", str(max_depth)])
        if file_type:
            args.extend(["-type", str(file_type)])
        if name_glob:
            args.extend(["-name", str(name_glob)])
        return ShellToolResult(observation_tool=self.name, result=context.shell_runner.run("find", args))


class ListFilesTool(Tool):
    name = "list_files"
    description = "List bounded repo-relative paths for planning or validation targeting."
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 6},
            "file_type": {"type": "string", "enum": ["f", "d"]},
            "name_glob": {"type": "string"},
        },
        "required": ["paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        paths = [str(item) for item in payload.get("paths") or ["."]]
        args = list(paths)
        max_depth = payload.get("max_depth")
        file_type = payload.get("file_type")
        name_glob = payload.get("name_glob")
        if max_depth is not None:
            args.extend(["-maxdepth", str(max_depth)])
        if file_type:
            args.extend(["-type", str(file_type)])
        if name_glob:
            args.extend(["-name", str(name_glob)])
        return ShellToolResult(observation_tool=self.name, result=context.shell_runner.run("find", args))


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Run a validated ripgrep search over repo-relative paths."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pattern", "paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ShellToolResult:
        pattern = str(payload.get("pattern") or "")
        paths = [str(item) for item in payload.get("paths") or ["."]]
        result = context.shell_runner.run("rg", _build_rg_search_args(pattern, paths))
        return ShellToolResult(
            observation_tool=self.name,
            result=result,
            empty_results_retryable=True,
            empty_results_message="search_code returned no matching lines.",
        )
