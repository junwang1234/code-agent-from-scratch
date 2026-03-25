from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .repo_filesystem import IGNORED_DIRS


ALLOWED_COMMANDS = {"rg", "find"}
ALLOWED_RG_FLAGS = {"-n", "-i", "-C", "-A", "-B", "-m", "--glob", "--files"}
RG_VALUE_FLAGS = {"-C", "-A", "-B", "-m", "--glob"}
ALLOWED_FIND_FLAGS = {"-maxdepth", "-type", "-name"}
FIND_VALUE_FLAGS = {"-maxdepth", "-type", "-name"}
DISALLOWED_ARG_TOKENS = {";", "&&", "||", "`", "$(", ">", ">>", "<"}

ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "python3.10",
    "python3.11",
    "python3.12",
    "go",
    "cargo",
    "npm",
    "pnpm",
    "yarn",
    "mvn",
    "gradle",
    "./gradlew",
    "./mvnw",
    "pytest",
    "ruff",
    "black",
    ".venv/bin/python",
    "venv/bin/python",
    ".venv/Scripts/python.exe",
    "venv/Scripts/python.exe",
}
ALLOWED_PYTHON_MODULES = {"unittest", "pytest", "ruff", "black", "venv", "pip"}
ALLOWED_PYTEST_FLAGS = {"-q", "-v", "-vv", "-x", "--maxfail", "-k"}
PYTEST_VALUE_FLAGS = {"--maxfail", "-k"}
ALLOWED_RUFF_SUBCOMMANDS = {"check", "format"}
ALLOWED_RUFF_FLAGS = {"--fix", "--diff", "--check"}
ALLOWED_BLACK_FLAGS = {"--check", "--diff", "--quiet"}
ALLOWED_PIP_INSTALL_FLAGS = {"-r", "--requirement", "-e", "--editable"}
WRAPPER_EXECUTABLES = {"./gradlew", "./mvnw"}
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn"}
BUILD_TOOL_EXECUTABLES = {"mvn", "gradle", *WRAPPER_EXECUTABLES}


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
    execution_mode: str = "bounded"


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
            return self._run_rg_fallback(args)
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

    def _run_rg_fallback(self, args: list[str]) -> tuple[list[str], int]:
        if "--files" in args:
            output = self._run_rg_files_fallback(args)
            return output, 0
        output = self._run_rg_search_fallback(args)
        return output, 0 if output else 1

    def _run_rg_files_fallback(self, args: list[str]) -> list[str]:
        paths = [arg for arg in args if not arg.startswith("-")]
        if not paths:
            paths = ["."]
        files = self._collect_search_files(paths)
        return [path.as_posix() for path in files]

    def _run_rg_search_fallback(self, args: list[str]) -> list[str]:
        ignore_case = False
        glob_pattern: str | None = None
        pattern: str | None = None
        paths: list[str] = []
        expect_value_for: str | None = None
        end_of_flags = False

        for arg in args:
            if expect_value_for is not None:
                if expect_value_for == "--glob":
                    glob_pattern = arg
                elif expect_value_for not in {"-C", "-A", "-B", "-m"}:
                    raise ValueError(f"Unsupported rg fallback flag: {expect_value_for}")
                expect_value_for = None
                continue
            if not end_of_flags and arg == "--":
                end_of_flags = True
                continue
            if not end_of_flags and arg.startswith("-"):
                if arg == "-i":
                    ignore_case = True
                    continue
                if arg in RG_VALUE_FLAGS:
                    expect_value_for = arg
                    continue
                if arg == "-n":
                    continue
                raise ValueError(f"Unsupported rg fallback flag: {arg}")
            if pattern is None:
                pattern = arg
            else:
                paths.append(arg)

        if expect_value_for is not None:
            raise ValueError(f"Missing value for rg fallback flag: {expect_value_for}")
        if pattern is None:
            raise ValueError("rg requires a pattern argument.")

        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
        output: list[str] = []
        for rel_path in self._collect_search_files(paths or ["."]):
            if glob_pattern and not fnmatch.fnmatch(rel_path.as_posix(), glob_pattern):
                continue
            file_path = self.repo_path / rel_path
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    output.append(f"{rel_path.as_posix()}:{line_number}:{line}")
        return output

    def _collect_search_files(self, paths: list[str]) -> list[Path]:
        collected: list[Path] = []
        seen: set[str] = set()
        for path_arg in paths:
            resolved = (self.repo_path / path_arg).resolve()
            if self.repo_path not in resolved.parents and resolved != self.repo_path:
                raise ValueError(f"Path escapes repository root: {path_arg}")
            if resolved.is_file():
                rel_file = resolved.relative_to(self.repo_path)
                rel_key = rel_file.as_posix()
                if rel_key not in seen:
                    seen.add(rel_key)
                    collected.append(rel_file)
                continue
            if not resolved.exists() or not resolved.is_dir():
                continue
            for item in sorted(resolved.rglob("*")):
                if any(part in IGNORED_DIRS for part in item.relative_to(self.repo_path).parts):
                    continue
                if not item.is_file():
                    continue
                rel_file = item.relative_to(self.repo_path)
                rel_key = rel_file.as_posix()
                if rel_key in seen:
                    continue
                seen.add(rel_key)
                collected.append(rel_file)
        return collected


class SafeCommandRunner:
    def __init__(self, repo_path: Path, timeout_sec: int = 30, max_output_lines: int = 200) -> None:
        self.repo_path = repo_path.resolve()
        self.timeout_sec = timeout_sec
        self.max_output_lines = max_output_lines

    def run(self, command: str, args: list[str]) -> CommandResult:
        safe_command, safe_args = self._validate(command, args)
        result = self._execute(safe_command, safe_args, working_dir=".")
        output = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        truncated = len(output) > self.max_output_lines
        if truncated:
            output = output[: self.max_output_lines]
        return CommandResult(command=safe_command, args=safe_args, output=output, truncated=truncated, exit_code=result.returncode, execution_mode="bounded")

    def run_argv(self, argv: list[str], *, working_dir: str = ".", env_overrides: dict[str, str] | None = None) -> CommandResult:
        if not argv:
            raise ValueError("command argv may not be empty.")
        safe_command, safe_args = self._validate(argv[0], argv[1:])
        result = self._execute(safe_command, safe_args, working_dir=working_dir, env_overrides=env_overrides)
        output = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        truncated = len(output) > self.max_output_lines
        if truncated:
            output = output[: self.max_output_lines]
        return CommandResult(command=safe_command, args=safe_args, output=output, truncated=truncated, exit_code=result.returncode, execution_mode="bounded")

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

    def run_validation_command(self, argv: list[str], *, working_dir: str = ".", env_overrides: dict[str, str] | None = None) -> CommandResult:
        return self.run_argv(argv, working_dir=working_dir, env_overrides=env_overrides)

    def run_approved_bash(self, argv: list[str], *, working_dir: str = ".", env_overrides: dict[str, str] | None = None) -> CommandResult:
        if not argv:
            raise ValueError("command argv may not be empty.")
        safe_argv = [item.strip() for item in argv]
        if any(not item for item in safe_argv):
            raise ValueError("command argv may not contain empty values.")
        cwd = self._resolve_working_dir(working_dir)
        env = {**os.environ, **env_overrides} if env_overrides else None
        rendered = render_argv_as_shell_command(safe_argv)
        result = subprocess.run(
            ["/bin/bash", "-lc", rendered],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_sec,
            env=env,
        )
        output = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        truncated = len(output) > self.max_output_lines
        if truncated:
            output = output[: self.max_output_lines]
        return CommandResult(
            command=safe_argv[0],
            args=safe_argv[1:],
            output=output,
            truncated=truncated,
            exit_code=result.returncode,
            execution_mode="approved_bash",
        )

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
        if safe_command in {
            "python",
            "python3",
            "python3.10",
            "python3.11",
            "python3.12",
            ".venv/bin/python",
            "venv/bin/python",
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
        }:
            return safe_command, self._validate_python_args(args)
        if safe_command == "go":
            return safe_command, self._validate_go_args(args)
        if safe_command == "cargo":
            return safe_command, self._validate_cargo_args(args)
        if safe_command in PACKAGE_MANAGERS:
            return safe_command, self._validate_package_manager_args(safe_command, args)
        if safe_command in BUILD_TOOL_EXECUTABLES:
            if safe_command in WRAPPER_EXECUTABLES:
                self._validate_wrapper_command(safe_command)
            return safe_command, self._validate_build_tool_args(args)
        if safe_command == "pytest":
            return safe_command, self._validate_pytest_args(args)
        if safe_command == "ruff":
            return safe_command, self._validate_ruff_args(args)
        if safe_command == "black":
            return safe_command, self._validate_black_args(args)
        raise ValueError(f"Unsupported command: {safe_command}")

    def _execute(self, safe_command: str, safe_args: list[str], *, working_dir: str, env_overrides: dict[str, str] | None = None):
        executable = sys.executable if safe_command in {"python", "python3"} else safe_command
        cwd = self._resolve_working_dir(working_dir)
        env = {**os.environ, **env_overrides} if env_overrides else None
        return subprocess.run(
            [executable, *safe_args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_sec,
            env=env,
        )

    def _validate_python_args(self, args: list[str]) -> list[str]:
        if args in (["--version"], ["-V"]):
            return list(args)
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
        if module == "venv":
            return ["-m", module, *self._validate_venv_args(remainder)]
        if module == "pip":
            return ["-m", module, *self._validate_pip_args(remainder)]
        raise ValueError(f"Unsupported python module: {module}")

    def _validate_go_args(self, args: list[str]) -> list[str]:
        if args == ["version"]:
            return ["version"]
        if args == ["mod", "download"]:
            return ["mod", "download"]
        if not args or args[0] != "test":
            raise ValueError("go commands must use 'version' or 'test'.")
        safe_args = ["test"]
        for arg in args[1:]:
            normalized = arg.strip()
            if not normalized:
                raise ValueError("go args may not contain empty values.")
            if normalized == "./...":
                safe_args.append(normalized)
                continue
            if normalized.startswith("-"):
                raise ValueError(f"Unsupported go test argument: {normalized}")
            safe_args.append(self._validate_repo_relative_path(normalized))
        return safe_args

    def _validate_cargo_args(self, args: list[str]) -> list[str]:
        if args == ["--version"]:
            return ["--version"]
        if args == ["test"]:
            return ["test"]
        if args == ["fetch"]:
            return ["fetch"]
        if args == ["fmt"]:
            return ["fmt"]
        if args == ["clippy"]:
            return ["clippy"]
        if args == ["clippy", "--all-targets", "--all-features", "--", "-D", "warnings"]:
            return list(args)
        raise ValueError("cargo commands must use '--version' or 'test'.")

    def _validate_package_manager_args(self, command: str, args: list[str]) -> list[str]:
        if args == ["--version"]:
            return ["--version"]
        if args == ["test"]:
            return ["test"]
        if args == ["install"]:
            return ["install"]
        if command == "yarn" and args in (["format"], ["lint"]):
            return list(args)
        if command in {"npm", "pnpm"} and args == ["run", "test"]:
            return ["run", "test"]
        if command in {"npm", "pnpm"} and args in (["run", "format"], ["run", "lint"]):
            return list(args)
        raise ValueError(f"Unsupported {command} arguments.")

    def _validate_build_tool_args(self, args: list[str]) -> list[str]:
        if args == ["--version"]:
            return ["--version"]
        if args == ["test"]:
            return ["test"]
        raise ValueError("Build tool commands must use '--version' or 'test'.")

    def _validate_wrapper_command(self, command: str) -> None:
        candidate = (self.repo_path / command).resolve()
        if self.repo_path not in candidate.parents:
            raise ValueError(f"Wrapper command escapes repository root: {command}")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"Wrapper command does not exist: {command}")

    def _validate_venv_args(self, args: list[str]) -> list[str]:
        if len(args) != 1:
            raise ValueError("venv bootstrap requires exactly one target directory.")
        target = args[0].strip()
        if not target:
            raise ValueError("venv target directory may not be empty.")
        return [self._validate_repo_relative_path(target)]

    def _validate_pip_args(self, args: list[str]) -> list[str]:
        if not args:
            raise ValueError("pip commands may not be empty.")
        subcommand = args[0].strip()
        if subcommand != "install":
            raise ValueError(f"Unsupported pip subcommand: {subcommand}")
        safe_args = [subcommand]
        index = 1
        while index < len(args):
            arg = args[index].strip()
            if not arg:
                raise ValueError("pip args may not contain empty values.")
            if arg in {"-r", "--requirement", "-e", "--editable"}:
                if index + 1 >= len(args):
                    raise ValueError(f"Missing value for pip flag: {arg}")
                value = args[index + 1].strip()
                if not value:
                    raise ValueError(f"Missing value for pip flag: {arg}")
                safe_args.append(arg)
                safe_args.append(self._validate_repo_relative_path(value))
                index += 2
                continue
            raise ValueError(f"Unsupported pip install argument: {arg}")
        return safe_args

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

    def _resolve_working_dir(self, working_dir: str) -> Path:
        normalized = working_dir.strip() or "."
        candidate = (self.repo_path / normalized).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            raise ValueError(f"Path escapes repository root: {working_dir}")
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(f"Working directory does not exist: {working_dir}")
        return candidate


def format_shell_query(command: str, args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in [command, *args])


def render_argv_as_shell_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)
