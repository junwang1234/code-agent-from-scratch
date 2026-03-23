from __future__ import annotations

from pathlib import Path

from ..runtime.memory_manager import AgentMemory
from ..models import Action, ActionExecutionError
from ..tools.shell import format_shell_query
from .file_context_helpers import repair_redundant_read


def classify_action_exception(action: Action, exc: Exception) -> ActionExecutionFailed:
    from .action_execution import ActionExecutionFailed

    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    failure_kind = "tool_error"
    retryable = False
    if "timed out" in lowered:
        failure_kind = "timeout"
        retryable = True
    elif "not found" in lowered or "does not exist" in lowered or "path escapes" in lowered:
        failure_kind = "invalid_input"
    elif "unsupported" in lowered or "missing value" in lowered:
        failure_kind = "invalid_input"
    elif "failed" in lowered:
        failure_kind = "tool_error"
    if action.tool_name in {"rg_probe", "rg_search", "search_code"} and failure_kind == "tool_error":
        retryable = True
    return ActionExecutionFailed(failure_kind=failure_kind, message=message, raw_output=[message], retryable=retryable)


def action_fingerprint(action: Action) -> str:
    return f"{action.tool_name}:{normalize_retry_payload(action.tool_input)}"


def failure_fingerprint(failure: ActionExecutionError) -> str:
    return f"{failure.tool_name}:{normalize_retry_payload(failure.tool_input)}"


def normalize_retry_payload(payload: dict) -> str:
    parts: list[str] = []
    for key in sorted(payload):
        value = payload[key]
        if value in (None, "", []):
            continue
        rendered = ",".join(str(item) for item in value) if isinstance(value, list) else str(value)
        parts.append(f"{key}={rendered}")
    return "|".join(parts)


def can_finish(memory: AgentMemory, action: Action, remaining_steps: int) -> bool:
    state = memory.state
    if remaining_steps <= 1:
        return True
    if state.changed_files:
        has_summary = bool(action.answer.strip())
        has_validation = bool(state.validation_runs)
        has_failure = bool(state.failures or action.unknowns)
        return has_summary and (has_validation or has_failure)
    met_count = sum(1 for item in state.success_criteria if item.status == "met")
    proposed_met_count = sum(1 for item in action.criterion_updates if item.status == "met")
    has_inspection = bool(state.inspected_files) or any(
        observation.tool in {"list_tree", "head_file", "rg_probe", "rg_search", "rg_files", "find_paths", "list_files", "read_file_range", "search_code"}
        for observation in state.observations
    )
    enough_evidence = len(state.evidence) >= 1 or bool(action.evidence)
    return has_inspection and (enough_evidence or met_count + proposed_met_count >= 2)


def fallback_tool_action(memory: AgentMemory) -> Action:
    state = memory.state
    if is_editish_run(memory):
        return fallback_edit_tool_action(memory)
    pending_steps = [step for step in state.plan.steps if step.status != "completed"]
    step_id = pending_steps[0].id if pending_steps else state.plan.steps[-1].id
    if not any(observation.tool == "list_tree" for observation in state.observations):
        return Action.tool(step_id=step_id, reason="Need a shallow repo map before finishing.", tool_name="list_tree", tool_input={"depth": 2})
    if not state.inspected_files:
        for candidate in ("SKILL.md", "INDEX.md", "OPERATORS.md", "README.md"):
            candidate_path = state.task.repo_path / candidate
            if candidate_path.exists():
                return Action.tool(step_id=step_id, reason="Need a low-bandwidth probe of a top-level document before finishing.", tool_name="rg_probe", tool_input={"pattern": "^#|^##|scripts/|python|workflow|entrypoint", "paths": [candidate]})
        return Action.tool(step_id=step_id, reason="Need shell-first discovery of representative files before finishing.", tool_name="find_paths", tool_input={"paths": ["."], "max_depth": 2, "file_type": "f", "name_glob": "*"})
    return Action.tool(step_id=step_id, reason="Need one more grounded shell-first probe before finishing.", tool_name="rg_probe", tool_input={"pattern": "^(def |class |async def |if __name__ ==|cmd_|main|run_|phase)", "paths": sorted(state.inspected_files)[:2]})


def fallback_edit_tool_action(memory: AgentMemory) -> Action:
    state = memory.state
    pending_steps = [step for step in state.plan.steps if step.status != "completed"]
    step_id = pending_steps[0].id if pending_steps else state.plan.steps[-1].id
    if not state.inspected_files:
        if (state.task.repo_path / "README.md").exists():
            return Action.tool(step_id=step_id, reason="Need to inspect a likely top-level target before editing.", tool_name="read_file_range", tool_input={"path": "README.md", "start_line": 1, "end_line": 60})
        return Action.tool(step_id=step_id, reason="Need a bounded file list before editing.", tool_name="list_files", tool_input={"paths": ["."], "max_depth": 3, "file_type": "f", "name_glob": "*"})
    if state.changed_files and not state.validation_runs:
        return Action.tool(step_id=step_id, reason="Need validation after making code changes.", tool_name="run_tests", tool_input={"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]})
    target = next(iter(sorted(state.inspected_files)), None) or "README.md"
    return Action.tool(step_id=step_id, reason="Need one more targeted inspection before finishing the edit task.", tool_name="read_file_range", tool_input={"path": target, "start_line": 1, "end_line": 80})


def retry_alternative_action(memory: AgentMemory, action: Action) -> Action:
    state = memory.state
    if action.tool_name in {"apply_patch", "write_file"}:
        target = str(action.tool_input.get("path") or next(iter(sorted(state.inspected_files)), "README.md"))
        return Action.tool(step_id=action.step_id, reason=action.reason + " Switched away from a repeated failing edit to inspect the target again.", tool_name="read_file_range", tool_input={"path": target, "start_line": 1, "end_line": 80}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)
    if action.tool_name in {"run_tests", "run_command", "format_code"}:
        target = next(iter(sorted(state.changed_files)), None) or next(iter(sorted(state.inspected_files)), "README.md")
        return Action.tool(step_id=action.step_id, reason=action.reason + " Switched away from a repeated failing validation command to inspect the relevant file.", tool_name="read_file_range", tool_input={"path": target, "start_line": 1, "end_line": 80}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)
    if action.tool_name in {"rg_probe", "rg_search", "search_code"}:
        paths = [str(item) for item in action.tool_input.get("paths") or []]
        target_paths = paths[:2] or [next(iter(sorted(state.inspected_files)), "README.md")]
        if any(looks_like_doc_target(path) for path in target_paths):
            return Action.tool(step_id=action.step_id, reason=action.reason + " Switched away from a repeated failing search to inspect the target directly.", tool_name="head_file", tool_input={"paths": target_paths[:2], "lines": 40}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)
        return Action.tool(step_id=action.step_id, reason=action.reason + " Switched away from a repeated failing search to a narrower probe.", tool_name="rg_probe", tool_input={"pattern": default_probe_pattern_for_paths(target_paths), "paths": target_paths}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)
    return fallback_tool_action(memory)


def repair_tool_action(memory: AgentMemory, action: Action) -> Action:
    state = memory.state
    if action.kind != "tool":
        return action
    if is_editish_action(memory, action):
        return repair_edit_tool_action(memory, action)
    if action.tool_name in {"head_file", "rg_probe"}:
        paths = [str(item) for item in action.tool_input.get("paths") or []]
        if len(paths) > 3:
            action.tool_input = {**action.tool_input, "paths": paths[:3]}
            action.reason = action.reason + " Trimmed probe targets to the first 3 paths to keep the probe lightweight."
            return action
    if should_probe_before_expand(memory, action):
        return repair_to_probe_action(memory, action)
    if action.tool_name in {"head_file", "rg_probe", "rg_search", "rg_files", "find_paths"}:
        rendered = render_explicit_tool_action(action.tool_name, action.tool_input)
        if any(observation.tool == action.tool_name and observation.tool_input == rendered for observation in state.observations):
            replacement = pick_explicit_tool_replacement(memory, action.tool_name, action.tool_input)
            if replacement is not None:
                action.tool_name = replacement["tool_name"]
                action.tool_input = replacement["tool_input"]
                action.reason = action.reason + " Repaired duplicate shell-style extraction into a new bounded command."
                return action
        if action.tool_name == "rg_search" and not action.tool_input.get("paths"):
            target = next(iter(sorted(state.inspected_files)), None) or pick_script_target(memory) or "README.md"
            action.tool_input = {"pattern": "workflow|scripts/|python|uv run", "paths": [target]}
            action.reason = action.reason + f" Added a safe default rg_search against '{target}'."
            return action
    return action


def repair_edit_tool_action(memory: AgentMemory, action: Action) -> Action:
    state = memory.state
    if action.tool_name in {"write_file", "apply_patch"} and not state.inspected_files:
        action.tool_name = "list_files"
        action.tool_input = {"paths": ["."], "max_depth": 3, "file_type": "f", "name_glob": "*"}
        action.reason = action.reason + " Repaired early write into an inspection step because the agent must inspect relevant files before editing."
        return action
    if action.tool_name == "search_code" and not action.tool_input.get("paths"):
        action.tool_input = {**action.tool_input, "paths": ["."]}
        action.reason = action.reason + " Added a default repo root search path."
        return action
    if action.tool_name == "read_file_range":
        path = str(action.tool_input.get("path") or "")
        if not path and state.inspected_files:
            action.tool_input = {**action.tool_input, "path": sorted(state.inspected_files)[0]}
            action.reason = action.reason + " Repaired missing read target from inspected files."
            path = str(action.tool_input.get("path") or "")
        start_line = int(action.tool_input.get("start_line") or 1)
        end_line = int(action.tool_input.get("end_line") or min(start_line + 39, 80))
        repair = repair_redundant_read(state, path, start_line, end_line)
        if repair is not None:
            action.tool_input = repair["tool_input"]
            action.reason = action.reason + " " + repair["reason"]
        return action
    if action.tool_name == "run_tests":
        runner = str(action.tool_input.get("runner") or "")
        if not runner:
            action.tool_input = {**action.tool_input, "runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]}
            action.reason = action.reason + " Added a safe unittest discovery default."
        return action
    if action.tool_name == "format_code" and not action.tool_input.get("paths") and state.changed_files:
        action.tool_input = {**action.tool_input, "paths": sorted(state.changed_files)}
        action.reason = action.reason + " Added the changed files as formatter targets."
        return action
    if action.tool_name in {"write_file", "apply_patch"}:
        read_target = next(iter(sorted(state.inspected_files)), None)
        if read_target and not action.tool_input.get("path"):
            action.tool_input = {**action.tool_input, "path": read_target}
            action.reason = action.reason + " Repaired missing edit target from inspected files."
        return action
    return action


def is_editish_action(memory: AgentMemory, action: Action) -> bool:
    edit_tools = {"list_files", "read_file_range", "search_code", "write_file", "apply_patch", "run_command", "run_tests", "format_code"}
    if action.tool_name in edit_tools:
        return True
    return is_editish_run(memory)


def is_editish_run(memory: AgentMemory) -> bool:
    state = memory.state
    edit_tools = {"write_file", "apply_patch", "run_command", "run_tests", "format_code"}
    if state.changed_files or state.validation_runs:
        return True
    for step in state.plan.steps:
        if any(tool in edit_tools for tool in step.allowed_tools):
            return True
    return False


def pick_script_target(memory: AgentMemory) -> str | None:
    state = memory.state
    candidates: list[str] = []
    for observation in state.observations:
        for item in observation.highlights:
            if item.startswith("Representative nested files: "):
                listed = [entry.strip() for entry in item.removeprefix("Representative nested files: ").split(",")]
                for entry in listed:
                    if entry.startswith("scripts/"):
                        candidates.append(entry)
    for fact in state.facts:
        for path in fact.files:
            if path.startswith("scripts/") and not path.endswith("/"):
                candidates.append(path)
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    scored = sorted(ordered, key=script_priority)
    return scored[0] if scored else None


def script_priority(path: str) -> tuple[int, str]:
    name = Path(path).name.lower()
    if name == "__init__.py":
        return (9, name)
    for index, needle in enumerate(("agent", "main", "cli", "run", "entry", "app")):
        if needle in name:
            return (index, name)
    return (5, name)


def pick_explicit_tool_replacement(memory: AgentMemory, tool_name: str, tool_input: dict) -> dict | None:
    state = memory.state
    if tool_name == "rg_search":
        for target in candidate_files_for_repair(memory):
            candidate = {"tool_name": "rg_search", "tool_input": {"pattern": "^(def |class )|workflow|entrypoint|test_", "paths": [target]}}
            rendered = render_explicit_tool_action(candidate["tool_name"], candidate["tool_input"])
            if not any(observation.tool == candidate["tool_name"] and observation.tool_input == rendered for observation in state.observations):
                return candidate
        if (state.task.repo_path / "scripts").exists():
            candidate = {"tool_name": "find_paths", "tool_input": {"paths": ["scripts"], "max_depth": 2, "file_type": "f", "name_glob": "*.py"}}
            rendered = render_explicit_tool_action(candidate["tool_name"], candidate["tool_input"])
            if not any(observation.tool == candidate["tool_name"] and observation.tool_input == rendered for observation in state.observations):
                return candidate
    if tool_name == "find_paths":
        target = pick_script_target(memory) or next(iter(sorted(state.inspected_files)), None)
        if target:
            candidate = {"tool_name": "rg_probe", "tool_input": {"pattern": default_probe_pattern_for_path(target), "paths": [target]}}
            rendered = render_explicit_tool_action(candidate["tool_name"], candidate["tool_input"])
            if not any(observation.tool == candidate["tool_name"] and observation.tool_input == rendered for observation in state.observations):
                return candidate
    if tool_name == "rg_files":
        target = pick_script_target(memory) or "scripts"
        candidate = {"tool_name": "rg_probe", "tool_input": {"pattern": default_probe_pattern_for_path(target), "paths": [target]}}
        rendered = render_explicit_tool_action(candidate["tool_name"], candidate["tool_input"])
        if not any(observation.tool == candidate["tool_name"] and observation.tool_input == rendered for observation in state.observations):
            return candidate
    if tool_name in {"head_file", "rg_probe"}:
        for target in candidate_files_for_repair(memory):
            candidate = {"tool_name": "rg_search", "tool_input": {"pattern": "workflow|entrypoint|def |class |test_", "paths": [target]}}
            rendered = render_explicit_tool_action(candidate["tool_name"], candidate["tool_input"])
            if not any(observation.tool == candidate["tool_name"] and observation.tool_input == rendered for observation in state.observations):
                return candidate
    return None


def candidate_files_for_repair(memory: AgentMemory) -> list[str]:
    state = memory.state
    candidates: list[str] = []
    for observation in state.observations:
        if observation.tool in {"head_file", "rg_probe", "rg_search", "rg_files", "find_paths"}:
            for line in observation.raw_output:
                path = line.split(":", 1)[0].strip()
                if (path and "/" in path) or path.endswith(".md") or path.endswith(".py"):
                    candidates.append(path)
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def render_explicit_tool_action(tool_name: str, tool_input: dict) -> str:
    if tool_name == "rg_search":
        return f"rg_search pattern={tool_input.get('pattern')} paths={','.join(tool_input.get('paths') or [])}"
    if tool_name == "head_file":
        return f"head_file paths={','.join(tool_input.get('paths') or [])} lines={tool_input.get('lines')}"
    if tool_name == "rg_probe":
        return f"rg_probe pattern={tool_input.get('pattern')} paths={','.join(tool_input.get('paths') or [])}"
    if tool_name == "rg_files":
        return f"rg_files paths={','.join(tool_input.get('paths') or [])}"
    if tool_name == "find_paths":
        return f"find_paths paths={','.join(tool_input.get('paths') or [])} max_depth={tool_input.get('max_depth')} file_type={tool_input.get('file_type')} name_glob={tool_input.get('name_glob')}"
    if tool_name == "read_file_range":
        return f"read_file_range path={tool_input.get('path')} start_line={tool_input.get('start_line')} end_line={tool_input.get('end_line')}"
    return format_shell_query(str(tool_input.get('command') or 'rg'), [str(item) for item in tool_input.get('args') or []])


def extract_find_option(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    index = args.index(flag)
    if index + 1 >= len(args):
        return None
    return args[index + 1]


def looks_like_doc_target(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in {".md", ".markdown", ".rst", ".txt"} or Path(path).name.upper() == Path(path).name


def has_probe_for_paths(memory: AgentMemory, paths: list[str]) -> bool:
    targets = set(paths)
    for observation in memory.state.observations:
        if observation.tool not in {"head_file", "rg_probe"}:
            continue
        if any(target and target in observation.tool_input for target in targets):
            return True
    return False


def should_probe_before_expand(memory: AgentMemory, action: Action) -> bool:
    if action.tool_name != "rg_search":
        return False
    paths = [str(item) for item in action.tool_input.get("paths") or []]
    if not paths or has_probe_for_paths(memory, paths):
        return False
    return len(paths) > 1 or any(Path(path).suffix == "" for path in paths)


def repair_to_probe_action(memory: AgentMemory, action: Action) -> Action:
    paths = [str(item) for item in action.tool_input.get("paths") or []]
    if paths and any(looks_like_doc_target(path) for path in paths):
        return Action.tool(step_id=action.step_id, reason=action.reason + " Repaired broad search into a low-bandwidth probe before expansion.", tool_name="head_file", tool_input={"paths": paths[:2], "lines": 40}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)
    return Action.tool(step_id=action.step_id, reason=action.reason + " Repaired broad search into a low-bandwidth probe before expansion.", tool_name="rg_probe", tool_input={"pattern": default_probe_pattern_for_paths(paths[:2]), "paths": paths[:2]}, completed_step_ids=action.completed_step_ids, criterion_updates=action.criterion_updates, fact_updates=action.fact_updates)


def default_probe_pattern_for_path(path: str) -> str:
    if looks_like_doc_target(path):
        return "^#|^##|scripts/|python|workflow|entrypoint"
    return "^(def |class |async def |if __name__ ==|cmd_|main|run_|phase)"


def default_probe_pattern_for_paths(paths: list[str]) -> str:
    if paths and all(looks_like_doc_target(path) for path in paths):
        return "^#|^##|scripts/|python|workflow|entrypoint"
    return "^(def |class |async def |if __name__ ==|cmd_|main|run_|phase)"
