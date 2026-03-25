from __future__ import annotations

from pathlib import Path

from .core import ToolExecutionContext, ToolResult
from .registry import ToolRegistry, build_default_tool_registry
from .repo_filesystem import RepoFilesystem
from .shell import SafeCommandRunner, ShellQueryRunner
from ..runtime.validation import ValidationDiscoveryService


class ToolExecutor:
    def __init__(
        self,
        repo_path: Path,
        *,
        registry: ToolRegistry | None = None,
        repo_filesystem: RepoFilesystem | None = None,
        shell_runner: ShellQueryRunner | None = None,
        command_runner: SafeCommandRunner | None = None,
    ) -> None:
        self.registry = registry or build_default_tool_registry()
        self.repo_filesystem = repo_filesystem or RepoFilesystem(repo_path)
        self.shell_runner = shell_runner or ShellQueryRunner(repo_path)
        self.command_runner = command_runner or SafeCommandRunner(repo_path)
        self.validation_service = ValidationDiscoveryService()

    @property
    def context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            repo_filesystem=self.repo_filesystem,
            shell_runner=self.shell_runner,
            command_runner=self.command_runner,
            validation_service=self.validation_service,
        )

    def execute(self, tool_name: str, payload: dict) -> ToolResult:
        tool = self.registry.get(tool_name)
        return tool.execute(self.context, payload)

    def names(self) -> list[str]:
        return self.registry.names()

    def specs(self) -> list[dict]:
        return self.registry.specs()
