from __future__ import annotations

from ..models import RepoMapEntry, SessionState, TaskResult
from .memory_manager import AgentMemory
from .validation.state import collect_validation_blockers, summarize_discovered_command


EDITISH_TOOLS = {"write_file", "apply_patch", "run_command", "run_tests", "format_code"}


def compose_response(memory: AgentMemory) -> TaskResult:
    state = memory.state
    if _is_edit_run(state):
        return _compose_edit_result(state)
    return _compose_understanding_result(state)


def _compose_understanding_result(memory: SessionState) -> TaskResult:
    answer = _build_answer(memory)
    if step_budget_exhausted(memory.unknowns):
        answer = "Incomplete run: the step budget was exhausted before the agent returned a final answer.\n\n" + answer
    repo_map = _dedupe_repo_map(memory.repo_map)
    evidence = memory.evidence[:6]
    unknowns = memory.unknowns or [
        "The agent stayed within bounded file reads and may have missed behavior outside the inspected files."
    ]
    suggested_next_questions = memory.suggested_next_questions_override or _suggest_next_questions()
    return TaskResult(
        result_kind="answer",
        primary_text=answer,
        evidence=evidence,
        repo_map=repo_map[:8],
        unknowns=unknowns[:5],
        suggested_next_questions=suggested_next_questions[:5],
        success_criteria=memory.success_criteria[:5],
    )


def _compose_edit_result(memory: SessionState) -> TaskResult:
    summary = memory.final_answer_override or (memory.edit_history[-1] if memory.edit_history else "Edit task finished without a final summary.")
    if step_budget_exhausted(memory.unknowns):
        progress = summary
        summary = "Incomplete run: the step budget was exhausted before the agent returned a finish action."
        if progress:
            summary += f" Last recorded progress: {progress}"
    changed_files = sorted(memory.changed_files)[:12]
    validation = _compose_validation_summary(memory)
    risks = _compose_validation_risks(memory)
    return TaskResult(result_kind="edit", primary_text=summary, changed_files=changed_files, validation=validation, risks=risks)


def _build_answer(memory: SessionState) -> str:
    if memory.final_answer_override:
        return memory.final_answer_override
    supporting_files = _key_files_from_evidence(memory.evidence, limit=3)
    if supporting_files:
        return f"{memory.plan.goal} The strongest supporting files currently are " + ", ".join(supporting_files) + "."
    return f"{memory.plan.goal} The current answer remains partial because the bounded exploration gathered limited evidence."


def _dedupe_repo_map(entries: list[RepoMapEntry]) -> list[RepoMapEntry]:
    deduped: list[RepoMapEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            continue
        seen.add(entry.path)
        deduped.append(entry)
    return deduped


def _suggest_next_questions() -> list[str]:
    return [
        "Which file is the main runtime entrypoint?",
        "How does data flow between the major modules?",
        "Which tests best demonstrate the main behavior?",
    ]


def _key_files_from_evidence(evidence: list, limit: int = 4) -> list[str]:
    files: list[str] = []
    for item in evidence:
        for path in item.files:
            if path not in files:
                files.append(path)
            if len(files) >= limit:
                return files
    return files


def step_budget_exhausted(unknowns: list[str]) -> bool:
    return any("step budget was exhausted" in unknown.lower() for unknown in unknowns)


def _is_edit_run(memory: SessionState) -> bool:
    if memory.changed_files or memory.validation_runs:
        return True
    for step in memory.plan.steps:
        if any(tool in EDITISH_TOOLS for tool in step.allowed_tools):
            return True
    return False


def _compose_validation_summary(memory: SessionState) -> list[str]:
    items: list[str] = []
    discovery = memory.validation_discovery
    if discovery is not None:
        selected_test = summarize_discovered_command(discovery.selected_test)
        selected_lint = summarize_discovered_command(discovery.selected_lint)
        selected_format = summarize_discovered_command(discovery.selected_format)
        if selected_test:
            items.append(f"Selected test command: {selected_test}.")
        if selected_lint:
            items.append(f"Selected lint command: {selected_lint}.")
        if selected_format:
            items.append(f"Selected format command: {selected_format}.")
    items.extend(memory.validation_runs[:8])
    return items[:8] or ["No validation runs were recorded."]


def _compose_validation_risks(memory: SessionState) -> list[str]:
    items: list[str] = []
    blockers = collect_validation_blockers(memory.validation_discovery)
    if blockers:
        items.append(f"Validation blockers: {'; '.join(blockers)}.")
    items.extend(memory.failures[:8] or memory.unknowns[:8])
    if not items:
        return ["No validation failures were recorded."]
    deduped: list[str] = []
    for item in items:
        if item in deduped:
            continue
        deduped.append(item)
    return deduped[:8]
