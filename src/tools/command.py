from __future__ import annotations

from typing import Any

from .core import CommandToolResult, Tool, ToolExecutionContext


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run a validated local command from the allowlist."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["command", "args"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        command = str(payload.get("command") or "")
        args = [str(item) for item in payload.get("args") or []]
        return CommandToolResult(tool_name=self.name, result=context.command_runner.run(command, args))


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Run validated test commands using unittest or pytest."
    input_schema = {
        "type": "object",
        "properties": {
            "runner": {"type": "string", "enum": ["unittest", "pytest"]},
            "targets": {"type": "array", "items": {"type": "string"}},
            "extra_args": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["runner"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        runner = str(payload.get("runner") or "")
        targets = [str(item) for item in payload.get("targets") or []]
        extra_args = [str(item) for item in payload.get("extra_args") or []]
        return CommandToolResult(tool_name=self.name, result=context.command_runner.run_tests(runner, targets, extra_args))


class FormatCodeTool(Tool):
    name = "format_code"
    description = "Run a validated formatter over repo-relative paths."
    input_schema = {
        "type": "object",
        "properties": {
            "formatter": {"type": "string", "enum": ["ruff", "black"]},
            "paths": {"type": "array", "items": {"type": "string"}},
            "check_only": {"type": "boolean"},
        },
        "required": ["formatter"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        formatter = str(payload.get("formatter") or "")
        paths = [str(item) for item in payload.get("paths") or []]
        check_only = bool(payload.get("check_only") or False)
        return CommandToolResult(
            tool_name=self.name,
            result=context.command_runner.format_code(formatter, paths, check_only=check_only),
        )
