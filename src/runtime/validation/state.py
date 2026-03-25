from __future__ import annotations

from ...models.validation import DiscoveredCommand, ValidationDiscoveryState
from ...tools.shell import format_shell_query


def summarize_discovered_command(command: DiscoveredCommand | None) -> str:
    if command is None:
        return ""
    rendered = format_shell_query(command.command.argv[0], command.command.argv[1:])
    return f"{rendered} via {command.source}"


def summarize_discovery_state(state: ValidationDiscoveryState | None) -> dict | None:
    if state is None:
        return None
    return {
        "repo_fingerprint": state.repo_fingerprint,
        "selected_test": _serialize_command(state.selected_test),
        "selected_lint": _serialize_command(state.selected_lint),
        "selected_format": _serialize_command(state.selected_format),
        "blockers": list(state.blockers[:6]),
        "evidence": list(state.evidence[:6]),
    }


def collect_validation_blockers(state: ValidationDiscoveryState | None, *, limit: int = 3) -> list[str]:
    if state is None:
        return []
    blockers: list[str] = []
    for value in state.blockers:
        if value in blockers:
            continue
        blockers.append(value)
        if len(blockers) >= limit:
            break
    return blockers


def _serialize_command(command: DiscoveredCommand | None) -> dict | None:
    if command is None:
        return None
    return {
        "kind": command.kind,
        "argv": list(command.command.argv),
        "working_dir": command.command.working_dir,
        "source": command.source,
        "confidence": command.confidence,
        "evidence": list(command.evidence[:4]),
        "blockers": list(command.blockers[:4]),
        "rendered": format_shell_query(command.command.argv[0], command.command.argv[1:]),
    }
