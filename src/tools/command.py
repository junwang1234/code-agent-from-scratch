from __future__ import annotations

from typing import Any

from ..runtime.validation.failures import approval_blocker_for_command
from .core import CommandToolResult, Tool, ToolExecutionContext


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run a validated local command from the allowlist or a discovered lint/build argv."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            blocker = approval_blocker_for_command(argv)
            if blocker is not None:
                raise ValueError(blocker)
            working_dir = str(payload.get("working_dir") or ".")
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(argv, working_dir=working_dir),
            )
        command = str(payload.get("command") or "")
        args = [str(item) for item in payload.get("args") or []]
        if not command and not args:
            discovery = context.validation_service.discover(context.repo_filesystem.repo_path)
            candidate = discovery.selected_lint or (discovery.lint_candidates[0] if discovery.lint_candidates else None)
            if candidate is None:
                raise ValueError("No validation command selected for lint/build execution.")
            if candidate.blockers:
                raise ValueError(candidate.blockers[0])
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(
                    candidate.command.argv,
                    working_dir=candidate.command.working_dir,
                    env_overrides=candidate.command.env_overrides,
                ),
                discovery_state=discovery,
            )
        blocker = approval_blocker_for_command([command, *args] if command else args)
        if blocker is not None:
            raise ValueError(blocker)
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
            "argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            blocker = approval_blocker_for_command(argv)
            if blocker is not None:
                raise ValueError(blocker)
            working_dir = str(payload.get("working_dir") or ".")
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(argv, working_dir=working_dir),
            )
        runner = str(payload.get("runner") or "")
        targets = [str(item) for item in payload.get("targets") or []]
        extra_args = [str(item) for item in payload.get("extra_args") or []]
        if not runner:
            discovery = context.validation_service.discover(context.repo_filesystem.repo_path)
            candidate = discovery.selected_test or (discovery.test_candidates[0] if discovery.test_candidates else None)
            if candidate is None:
                raise ValueError("No validation command selected for test execution.")
            if candidate.blockers:
                raise ValueError(candidate.blockers[0])
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(
                    candidate.command.argv,
                    working_dir=candidate.command.working_dir,
                    env_overrides=candidate.command.env_overrides,
                ),
                discovery_state=discovery,
            )
        return CommandToolResult(tool_name=self.name, result=context.command_runner.run_tests(runner, targets, extra_args))


class FormatCodeTool(Tool):
    name = "format_code"
    description = "Run a validated formatter over repo-relative paths or a discovered formatter argv."
    input_schema = {
        "type": "object",
        "properties": {
            "formatter": {"type": "string", "enum": ["ruff", "black"]},
            "paths": {"type": "array", "items": {"type": "string"}},
            "check_only": {"type": "boolean"},
            "argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            blocker = approval_blocker_for_command(argv)
            if blocker is not None:
                raise ValueError(blocker)
            working_dir = str(payload.get("working_dir") or ".")
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(argv, working_dir=working_dir),
            )
        formatter = str(payload.get("formatter") or "")
        paths = [str(item) for item in payload.get("paths") or []]
        check_only = bool(payload.get("check_only") or False)
        if not formatter and not paths:
            discovery = context.validation_service.discover(context.repo_filesystem.repo_path)
            candidate = discovery.selected_format or (discovery.format_candidates[0] if discovery.format_candidates else None)
            if candidate is None:
                raise ValueError("No validation command selected for formatting.")
            if candidate.blockers:
                raise ValueError(candidate.blockers[0])
            return CommandToolResult(
                tool_name=self.name,
                result=context.command_runner.run_validation_command(
                    candidate.command.argv,
                    working_dir=candidate.command.working_dir,
                    env_overrides=candidate.command.env_overrides,
                ),
                discovery_state=discovery,
            )
        return CommandToolResult(
            tool_name=self.name,
            result=context.command_runner.format_code(formatter, paths, check_only=check_only),
        )
