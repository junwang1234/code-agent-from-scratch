from __future__ import annotations

from typing import Any

from ..runtime.validation.failures import approval_blocker_for_command, approval_request_for_command, should_offer_approved_bash
from .core import ApprovalRequiredError, CommandToolResult, Tool, ToolExecutionContext


def _execute_validation_candidate(
    *,
    tool_name: str,
    context: ToolExecutionContext,
    payload: dict[str, Any],
    argv: list[str],
    working_dir: str,
    env_overrides: dict[str, str] | None = None,
    discovery_state=None,
) -> CommandToolResult:
    install_argv = [str(item) for item in payload.get("install_argv") or []]
    install_working_dir = str(payload.get("install_working_dir") or ".")
    verify_argv = [str(item) for item in payload.get("verify_argv") or []]
    if payload.get("_approved_bash"):
        return CommandToolResult(
            tool_name=tool_name,
            result=context.command_runner.run_approved_bash(argv, working_dir=working_dir, env_overrides=env_overrides),
            discovery_state=discovery_state,
        )
    blocker = approval_blocker_for_command(argv)
    if blocker is not None:
        raise ApprovalRequiredError(
            approval_request_for_command(
                tool_name,
                argv,
                working_dir=working_dir,
                reason=blocker,
                fallback_install_argv=install_argv,
                fallback_install_working_dir=install_working_dir,
                fallback_verify_argv=verify_argv,
            )
        )
    try:
        return CommandToolResult(
            tool_name=tool_name,
            result=context.command_runner.run_validation_command(argv, working_dir=working_dir, env_overrides=env_overrides),
            discovery_state=discovery_state,
        )
    except ValueError as exc:
        message = str(exc).strip() or exc.__class__.__name__
        if should_offer_approved_bash(argv, message):
            raise ApprovalRequiredError(
                approval_request_for_command(
                    tool_name,
                    argv,
                    working_dir=working_dir,
                    reason=f"Validated runner cannot execute this repo command directly: {' '.join(argv)}",
                    fallback_install_argv=install_argv,
                    fallback_install_working_dir=install_working_dir,
                    fallback_verify_argv=verify_argv,
                )
            ) from exc
        raise


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run a validated local command from the allowlist or a discovered lint/build argv."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "argv": {"type": "array", "items": {"type": "string"}},
            "install_argv": {"type": "array", "items": {"type": "string"}},
            "install_working_dir": {"type": "string"},
            "verify_argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            working_dir = str(payload.get("working_dir") or ".")
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=argv,
                working_dir=working_dir,
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
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=candidate.command.argv,
                working_dir=candidate.command.working_dir,
                env_overrides=candidate.command.env_overrides,
                discovery_state=discovery,
            )
        return _execute_validation_candidate(
            tool_name=self.name,
            context=context,
            payload=payload,
            argv=[command, *args] if command else args,
            working_dir=".",
        )


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
            "install_argv": {"type": "array", "items": {"type": "string"}},
            "install_working_dir": {"type": "string"},
            "verify_argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            working_dir = str(payload.get("working_dir") or ".")
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=argv,
                working_dir=working_dir,
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
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=candidate.command.argv,
                working_dir=candidate.command.working_dir,
                env_overrides=candidate.command.env_overrides,
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
            "install_argv": {"type": "array", "items": {"type": "string"}},
            "install_working_dir": {"type": "string"},
            "verify_argv": {"type": "array", "items": {"type": "string"}},
            "working_dir": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> CommandToolResult:
        argv = [str(item) for item in payload.get("argv") or []]
        if argv:
            working_dir = str(payload.get("working_dir") or ".")
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=argv,
                working_dir=working_dir,
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
            return _execute_validation_candidate(
                tool_name=self.name,
                context=context,
                payload=payload,
                argv=candidate.command.argv,
                working_dir=candidate.command.working_dir,
                env_overrides=candidate.command.env_overrides,
                discovery_state=discovery,
            )
        return CommandToolResult(
            tool_name=self.name,
            result=context.command_runner.format_code(formatter, paths, check_only=check_only),
        )
