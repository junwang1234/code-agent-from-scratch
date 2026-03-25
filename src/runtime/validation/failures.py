from __future__ import annotations


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


def _matches_prefix(argv: list[str], prefix: tuple[str, ...]) -> bool:
    if len(argv) < len(prefix):
        return False
    return tuple(argv[: len(prefix)]) == prefix
