from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ALLOWED_COMMANDS = {"rg", "find"}
ALLOWED_RG_FLAGS = {"-n", "-i", "-C", "-A", "-B", "-m", "--glob", "--files"}
RG_VALUE_FLAGS = {"-C", "-A", "-B", "-m", "--glob"}
ALLOWED_FIND_FLAGS = {"-maxdepth", "-type", "-name"}
FIND_VALUE_FLAGS = {"-maxdepth", "-type", "-name"}
DISALLOWED_ARG_TOKENS = {";", "&&", "||", "`", "$(", ">", ">>", "<"}

ALLOWED_EXECUTABLES = {"python", "python3", "pytest", "ruff", "black", ".venv/bin/python"}
ALLOWED_PYTHON_MODULES = {"unittest", "pytest", "ruff", "black"}
ALLOWED_PYTEST_FLAGS = {"-q", "-v", "-vv", "-x", "--maxfail", "-k"}
PYTEST_VALUE_FLAGS = {"--maxfail", "-k"}
ALLOWED_RUFF_SUBCOMMANDS = {"check", "format"}
ALLOWED_RUFF_FLAGS = {"--fix", "--diff", "--check"}
ALLOWED_BLACK_FLAGS = {"--check", "--diff", "--quiet"}


@dataclass(slots=True)
class ShellQueryResult:
    command: str
    args: list[str]
    output: list[str]
    truncated: bool
    exit_code: int


@dataclass(slots=True)
class CommandResult:
    command: str
    args: list[str]
    output: list[str]
    truncated: bool
    exit_code: int


class ShellQueryRunner:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()

    def run(self, command: str, args: list[str]) -> ShellQueryResult:
        safe_command, safe_args = self._validate(command, args)
        if safe_command == "rg":
            output, exit_code = self._run_rg(safe_args)
        elif safe_command == "find":
            output, exit_code = self._run_find(safe_args)
        else:
            raise ValueError(f"Unsupported shell query command: {safe_command}")
        return ShellQueryResult(command=safe_command, args=safe_args, output=output, truncated=False, exit_code=exit_code)

    def _validate(self, command: str, args: list[str]) -> tuple[str, list[str]]:
        safe_command = command.strip()
        if safe_command not in ALLOWED_COMMANDS:
            raise ValueError(f"Unsupported shell query command: {safe_command}")
        if not args:
            raise ValueError("shell_query args may not be empty.")
        return (safe_command, self._validate_rg_args(args)) if safe_command == "rg" else (safe_command, self._validate_find_args(args))

    def _validate_rg_args(self, args: list[str]) -> list[str]:
        safe_args: list[str] = []
        pattern_seen = False
        files_mode = False
        expect_flag_value = False
        end_of_flags = False
        for raw_arg in args:
            arg = self._normalize_arg(raw_arg)
            if expect_flag_value:
                safe_args.append(arg)
                expect_flag_value = False
                continue
            if arg == "--" and not files_mode:
                safe_args.append(arg)
                end_of_flags = True
                continue
            if end_of_flags:
                if not pattern_seen and not files_mode:
                    safe_args.append(arg)
                    pattern_seen = True
                    continue
                safe_args.append(self._validate_repo_relative_path(arg))
                continue
            if arg.startswith("-"):
                if arg not in ALLOWED_RG_FLAGS:
                    raise ValueError(f"Unsupported rg flag: {arg}")
                safe_args.append(arg)
                if arg == "--files":
                    files_mode = True
                if arg in RG_VALUE_FLAGS:
                    expect_flag_value = True
                continue
            if not pattern_seen and not files_mode:
                safe_args.append(arg)
                pattern_seen = True
                continue
            safe_args.append(self._validate_repo_relative_path(arg))
        if expect_flag_value:
            raise ValueError("Missing value for rg flag.")
        if not pattern_seen and not files_mode:
            raise ValueError("rg requires a pattern argument.")
        return safe_args

    def _validate_find_args(self, args: list[str]) -> list[str]:
        safe_args: list[str] = []
        saw_path = False
        expect_flag_value = False
        last_flag = ""
        for raw_arg in args:
            arg = self._normalize_arg(raw_arg)
            if expect_flag_value:
                if last_flag == "-type" and arg not in {"f", "d"}:
                    raise ValueError(f"Unsupported find -type value: {arg}")
                if last_flag == "-maxdepth":
                    try:
                        depth = int(arg)
                    except ValueError as exc:
                        raise ValueError(f"Invalid find -maxdepth value: {arg}") from exc
                    if depth < 0 or depth > 6:
                        raise ValueError(f"find -maxdepth out of range: {arg}")
                safe_args.append(arg)
                expect_flag_value = False
                continue
            if arg.startswith("-"):
                if arg not in ALLOWED_FIND_FLAGS:
                    raise ValueError(f"Unsupported find flag: {arg}")
                safe_args.append(arg)
                if arg in FIND_VALUE_FLAGS:
                    last_flag = arg
                    expect_flag_value = True
                continue
            safe_args.append(self._validate_repo_relative_path(arg))
            saw_path = True
        if expect_flag_value:
            raise ValueError("Missing value for find flag.")
        if not saw_path:
            raise ValueError("find requires at least one repo-relative start path.")
        return safe_args

    def _normalize_arg(self, raw_arg: str) -> str:
        arg = raw_arg.strip()
        if not arg:
            raise ValueError("shell_query args may not contain empty values.")
        if any(token in arg for token in DISALLOWED_ARG_TOKENS):
            raise ValueError(f"Disallowed shell token in argument: {arg}")
        return arg

    def _validate_repo_relative_path(self, path_arg: str) -> str:
        candidate = (self.repo_path / path_arg).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            raise ValueError(f"Path escapes repository root: {path_arg}")
        return candidate.relative_to(self.repo_path).as_posix()

    def _run_rg(self, args: list[str]) -> tuple[list[str], int]:
        if not shutil.which("rg"):
            raise RuntimeError("rg is required for shell queries but is not installed.")
        result = subprocess.run(["rg", *args], cwd=self.repo_path, capture_output=True, text=True, check=False)
        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stderr.strip() or "rg failed.")
        return [line for line in result.stdout.splitlines() if line.strip()], result.returncode

    def _run_find(self, args: list[str]) -> tuple[list[str], int]:
        if not shutil.which("find"):
            raise RuntimeError("find is required for shell queries but is not installed.")
        result = subprocess.run(["find", *args], cwd=self.repo_path, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "find failed.")
        return [self._normalize_find_output(line) for line in result.stdout.splitlines() if line.strip()], result.returncode

    def _normalize_find_output(self, raw_path: str) -> str:
        cleaned = raw_path.strip()
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if not cleaned:
            return "."
        resolved = (self.repo_path / cleaned).resolve()
        if resolved.is_dir() and cleaned != ".":
            return cleaned.rstrip("/") + "/"
        return cleaned


class SafeCommandRunner:
    def __init__(self, repo_path: Path, timeout_sec: int = 30, max_output_lines: int = 200) -> None:
        self.repo_path = repo_path.resolve()
        self.timeout_sec = timeout_sec
        self.max_output_lines = max_output_lines

    def run(self, command: str, args: list[str]) -> CommandResult:
        safe_command, safe_args = self._validate(command, args)
        executable = sys.executable if safe_command in {"python", "python3"} else safe_command
        result = subprocess.run([executable, *safe_args], cwd=self.repo_path, capture_output=True, text=True, check=False, timeout=self.timeout_sec)
        output = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        truncated = len(output) > self.max_output_lines
        if truncated:
            output = output[: self.max_output_lines]
        return CommandResult(command=safe_command, args=safe_args, output=output, truncated=truncated, exit_code=result.returncode)

    def run_tests(self, runner: str, targets: list[str] | None = None, extra_args: list[str] | None = None) -> CommandResult:
        safe_runner = runner.strip()
        targets = list(targets or [])
        extra_args = list(extra_args or [])
        if safe_runner == "unittest":
            args = ["-m", "unittest", *targets, *extra_args]
            return self.run("python", args)
        if safe_runner == "pytest":
            return self.run("pytest", [*targets, *extra_args])
        raise ValueError(f"Unsupported test runner: {runner}")

    def format_code(self, formatter: str, paths: list[str], check_only: bool = False) -> CommandResult:
        safe_formatter = formatter.strip()
        safe_paths = [self._validate_repo_relative_path(path) for path in paths]
        if safe_formatter == "ruff":
            args = ["format", *safe_paths]
            if check_only:
                args.insert(1, "--check")
            return self.run("ruff", args)
        if safe_formatter == "black":
            args = list(safe_paths)
            if check_only:
                args.insert(0, "--check")
            return self.run("black", args)
        raise ValueError(f"Unsupported formatter: {formatter}")

    def _validate(self, command: str, args: list[str]) -> tuple[str, list[str]]:
        safe_command = command.strip()
        if safe_command not in ALLOWED_EXECUTABLES:
            raise ValueError(f"Unsupported command: {safe_command}")
        if any(not item.strip() for item in args):
            raise ValueError("command args may not contain empty values.")
        if safe_command in {"python", "python3", ".venv/bin/python"}:
            return safe_command, self._validate_python_args(args)
        if safe_command == "pytest":
            return safe_command, self._validate_pytest_args(args)
        if safe_command == "ruff":
            return safe_command, self._validate_ruff_args(args)
        if safe_command == "black":
            return safe_command, self._validate_black_args(args)
        raise ValueError(f"Unsupported command: {safe_command}")

    def _validate_python_args(self, args: list[str]) -> list[str]:
        if len(args) < 2 or args[0] != "-m":
            raise ValueError("python commands must use -m with an allowed module.")
        module = args[1].strip()
        if module not in ALLOWED_PYTHON_MODULES:
            raise ValueError(f"Unsupported python module: {module}")
        remainder = args[2:]
        if module == "unittest":
            return ["-m", module, *self._validate_unittest_args(remainder)]
        if module == "pytest":
            return ["-m", module, *self._validate_pytest_args(remainder)]
        if module == "ruff":
            return ["-m", module, *self._validate_ruff_args(remainder)]
        if module == "black":
            return ["-m", module, *self._validate_black_args(remainder)]
        raise ValueError(f"Unsupported python module: {module}")

    def _validate_unittest_args(self, args: list[str]) -> list[str]:
        safe_args: list[str] = []
        index = 0
        while index < len(args):
            normalized = args[index].strip()
            if not normalized:
                raise ValueError("unittest args may not contain empty values.")
            if normalized in {"discover", "-v", "-q", "-f"}:
                safe_args.append(normalized)
                index += 1
                continue
            if normalized in {"-s", "-p", "-k"}:
                if index + 1 >= len(args):
                    raise ValueError(f"Missing value for unittest flag: {normalized}")
                value = args[index + 1].strip()
                if not value:
                    raise ValueError(f"Missing value for unittest flag: {normalized}")
                safe_args.append(normalized)
                if normalized == "-s":
                    safe_args.append(self._validate_repo_relative_path(value))
                else:
                    safe_args.append(value)
                index += 2
                continue
            if normalized.startswith("-"):
                safe_args.append(normalized)
                index += 1
                continue
            if "/" in normalized or normalized.startswith("."):
                safe_args.append(self._validate_repo_relative_path(normalized))
            else:
                safe_args.append(normalized)
            index += 1
        return safe_args

    def _validate_pytest_args(self, args: list[str]) -> list[str]:
        safe_args: list[str] = []
        expect_value_for: str | None = None
        for raw_arg in args:
            arg = raw_arg.strip()
            if not arg:
                raise ValueError("pytest args may not contain empty values.")
            if expect_value_for is not None:
                safe_args.append(arg)
                expect_value_for = None
                continue
            if arg.startswith("-"):
                if arg not in ALLOWED_PYTEST_FLAGS:
                    raise ValueError(f"Unsupported pytest flag: {arg}")
                safe_args.append(arg)
                if arg in PYTEST_VALUE_FLAGS:
                    expect_value_for = arg
                continue
            safe_args.append(self._validate_repo_relative_path(arg))
        if expect_value_for is not None:
            raise ValueError(f"Missing value for pytest flag: {expect_value_for}")
        return safe_args

    def _validate_ruff_args(self, args: list[str]) -> list[str]:
        if not args:
            raise ValueError("ruff requires a subcommand.")
        subcommand = args[0].strip()
        if subcommand not in ALLOWED_RUFF_SUBCOMMANDS:
            raise ValueError(f"Unsupported ruff subcommand: {subcommand}")
        safe_args = [subcommand]
        for raw_arg in args[1:]:
            arg = raw_arg.strip()
            if not arg:
                raise ValueError("ruff args may not contain empty values.")
            if arg.startswith("-"):
                if arg not in ALLOWED_RUFF_FLAGS:
                    raise ValueError(f"Unsupported ruff flag: {arg}")
                safe_args.append(arg)
                continue
            safe_args.append(self._validate_repo_relative_path(arg))
        return safe_args

    def _validate_black_args(self, args: list[str]) -> list[str]:
        safe_args: list[str] = []
        for raw_arg in args:
            arg = raw_arg.strip()
            if not arg:
                raise ValueError("black args may not contain empty values.")
            if arg.startswith("-"):
                if arg not in ALLOWED_BLACK_FLAGS:
                    raise ValueError(f"Unsupported black flag: {arg}")
                safe_args.append(arg)
                continue
            safe_args.append(self._validate_repo_relative_path(arg))
        return safe_args

    def _validate_repo_relative_path(self, path_arg: str) -> str:
        candidate = (self.repo_path / path_arg).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            raise ValueError(f"Path escapes repository root: {path_arg}")
        return candidate.relative_to(self.repo_path).as_posix()


def format_shell_query(command: str, args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in [command, *args])
