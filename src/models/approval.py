from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ApprovedCommandScope:
    argv: list[str]
    working_dir: str = "."
    match_type: str = "exact"
    execution_mode: str = "approved_bash"


@dataclass(slots=True)
class InstallSuggestion:
    argv: list[str]
    working_dir: str = "."
    verify_argv: list[str] = field(default_factory=list)
    source: str = "curated"


@dataclass(slots=True)
class ApprovalRequest:
    tool_name: str
    argv: list[str]
    working_dir: str = "."
    reason: str = ""
    risk_category: str = "repo_command"
    install_suggestion: InstallSuggestion | None = None


__all__ = [
    "ApprovalRequest",
    "ApprovedCommandScope",
    "InstallSuggestion",
]
