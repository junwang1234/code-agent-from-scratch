from __future__ import annotations

import shutil

from ...models import ApprovalRequest, InstallSuggestion


VALIDATION_TOOL_NAMES = {"run_tests", "run_command", "format_code"}

SETUP_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "pip", "install"),
    ("python3", "-m", "pip", "install"),
    ("python3.10", "-m", "pip", "install"),
    ("python3.11", "-m", "pip", "install"),
    ("python3.12", "-m", "pip", "install"),
    (".venv/bin/python", "-m", "pip", "install"),
    ("venv/bin/python", "-m", "pip", "install"),
    (".venv/Scripts/python.exe", "-m", "pip", "install"),
    ("venv/Scripts/python.exe", "-m", "pip", "install"),
    ("python", "-m", "venv"),
    ("python3", "-m", "venv"),
    ("python3.10", "-m", "venv"),
    ("python3.11", "-m", "venv"),
    ("python3.12", "-m", "venv"),
    ("npm", "install"),
    ("pnpm", "install"),
    ("yarn", "install"),
    ("cargo", "fetch"),
    ("go", "mod", "download"),
)


def normalize_validation_failure(message: str, *, fallback_kind: str = "env_setup_failure") -> str:
    lowered = message.strip().lower()
    if not lowered:
        return fallback_kind
    if "no validation command selected" in lowered:
        return "no_discovered_command"
    if "explicit approval required" in lowered or "requires explicit approval" in lowered:
        return "env_setup_failure"
    if "unsupported" in lowered or "path escapes" in lowered or "missing value" in lowered:
        return "invalid_command"
    if "may not be empty" in lowered or "requires a pattern" in lowered:
        return "invalid_command"
    if "no module named" in lowered or "modulenotfounderror" in lowered:
        return "missing_dependency"
    if "node_modules is not installed" in lowered or "virtualenv is not available" in lowered:
        return "env_setup_failure"
    if "repo-local python interpreter was not detected" in lowered:
        return "missing_toolchain"
    if "wrapper command does not exist" in lowered:
        return "missing_toolchain"
    if "command not found" in lowered or "not recognized as an internal or external command" in lowered:
        return "missing_toolchain"
    if "not installed" in lowered or "executable file not found" in lowered:
        return "missing_toolchain"
    if "timed out" in lowered:
        return "timeout"
    if "assertionerror" in lowered or "failed (" in lowered:
        return "test_failure"
    return fallback_kind


def validation_failure_kind(tool_name: str, message: str) -> str:
    fallback_kind = "test_failure" if tool_name == "run_tests" else "env_setup_failure"
    return normalize_validation_failure(message, fallback_kind=fallback_kind)


def validation_failure_retryable(tool_name: str, message: str) -> bool:
    return validation_failure_kind(tool_name, message) == "timeout"


def approval_blocker_for_command(argv: list[str]) -> str | None:
    if not argv:
        return None
    for prefix in SETUP_COMMAND_PREFIXES:
        if _matches_prefix(argv, prefix):
            return f"Explicit approval required before running setup/install command: {' '.join(argv)}"
    return None


def approval_request_for_command(
    tool_name: str,
    argv: list[str],
    *,
    working_dir: str = ".",
    reason: str,
    fallback_install_argv: list[str] | None = None,
    fallback_install_working_dir: str = ".",
    fallback_verify_argv: list[str] | None = None,
) -> ApprovalRequest:
    executable = argv[0].strip() if argv else ""
    missing_tool = bool(executable) and "/" not in executable and not executable.startswith(".") and shutil.which(executable) is None
    install_suggestion = None
    if install_suggestion is None and missing_tool and fallback_install_argv:
        install_suggestion = InstallSuggestion(
            argv=list(fallback_install_argv),
            working_dir=fallback_install_working_dir,
            verify_argv=list(fallback_verify_argv or [executable, "--version"]),
            source="agent_proposed",
        )
    return ApprovalRequest(
        tool_name=tool_name,
        argv=list(argv),
        working_dir=working_dir,
        reason=reason,
        risk_category="install_and_retry" if install_suggestion is not None else "approved_bash",
        install_suggestion=install_suggestion,
    )


def should_offer_approved_bash(argv: list[str], message: str) -> bool:
    lowered = message.strip().lower()
    if not argv:
        return False
    if "path escapes" in lowered or "may not contain empty values" in lowered:
        return False
    if "unsupported command:" in lowered:
        return True
    return any(
        token in lowered
        for token in (
            "unsupported python module",
            "unsupported pip subcommand",
            "unsupported pytest flag",
            "unsupported ruff subcommand",
            "unsupported ruff flag",
            "unsupported black flag",
            "unsupported cargo",
            "unsupported go",
            "unsupported npm",
            "unsupported pnpm",
            "unsupported yarn",
            "must use",
        )
    )


def _matches_prefix(argv: list[str], prefix: tuple[str, ...]) -> bool:
    if len(argv) < len(prefix):
        return False
    return tuple(argv[: len(prefix)]) == prefix
