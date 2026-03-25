from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ValidationCommand:
    kind: str
    argv: list[str]
    working_dir: str = "."
    env_overrides: dict[str, str] = field(default_factory=dict)
    expected_tools: list[str] = field(default_factory=list)
    timeout_sec: int = 30


@dataclass(slots=True)
class DiscoveredCommand:
    kind: str
    command: ValidationCommand
    source: str
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationDiscoveryState:
    repo_fingerprint: str
    selected_test: DiscoveredCommand | None = None
    selected_lint: DiscoveredCommand | None = None
    selected_format: DiscoveredCommand | None = None
    test_candidates: list[DiscoveredCommand] = field(default_factory=list)
    lint_candidates: list[DiscoveredCommand] = field(default_factory=list)
    format_candidates: list[DiscoveredCommand] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


__all__ = [
    "DiscoveredCommand",
    "ValidationCommand",
    "ValidationDiscoveryState",
]
