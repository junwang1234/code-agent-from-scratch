from __future__ import annotations

from ..models import TaskResult


def _compose_understanding_result(memory: SessionState) -> TaskResult:
    answer = _build_answer(memory)
    if _was_step_budget_exhausted(memory):
        answer = "Incomplete run: the step budget was exhausted before the agent returned a final answer.\n\n" + answer
    repo_map = _dedupe_repo_map(memory.repo_map)
    evidence = memory.evidence[:6]
    unknowns = memory.unknowns or [
        "The agent stayed within bounded file reads and may have missed behavior outside the inspected files."
    ]
    suggested_next_questions = memory.suggested_next_questions_override or _suggest_next_questions(memory)
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
    if _was_step_budget_exhausted(memory):
        progress = summary
        summary = "Incomplete run: the step budget was exhausted before the agent returned a finish action."
        if progress:
            summary += f" Last recorded progress: {progress}"
    changed_files = sorted(memory.changed_files)[:12]
    validation = memory.validation_runs[:8] or ["No validation runs were recorded."]
    risks = memory.failures[:8] or memory.unknowns[:8] or ["No validation failures were recorded."]
    return TaskResult(result_kind="edit", primary_text=summary, changed_files=changed_files, validation=validation, risks=risks)


def render_markdown(response: TaskResult) -> str:
    if response.result_kind == "edit":
        return _render_edit_markdown(response)
    return _render_understanding_markdown(response)


def _render_understanding_markdown(response: TaskResult) -> str:
    parts = ["## Answer", response.primary_text, "", "## Evidence"]
    if response.evidence:
        for item in response.evidence:
            files = ", ".join(item.files)
            parts.append(f"- {item.claim} ({item.confidence}; files: {files})")
    else:
        parts.append("- No strong evidence was gathered.")
    parts.extend(["", "## Repo Map"])
    if response.repo_map:
        for entry in response.repo_map:
            parts.append(f"- {entry.path}: {entry.note}")
    else:
        parts.append("- No repo map entries were captured.")
    parts.extend(["", "## Unknowns"])
    for unknown in response.unknowns:
        parts.append(f"- {unknown}")
    parts.extend(["", "## Success Criteria"])
    for criterion in response.success_criteria:
        detail = f" ({criterion.note})" if criterion.note else ""
        parts.append(f"- [{criterion.status}] {criterion.criterion}{detail}")
    parts.extend(["", "## Suggested Next Questions"])
    for question in response.suggested_next_questions:
        parts.append(f"- {question}")
    return "\n".join(parts).strip() + "\n"


def _render_edit_markdown(response: TaskResult) -> str:
    parts = ["## Summary", response.primary_text, "", "## Files Changed"]
    if response.changed_files:
        for path in response.changed_files:
            parts.append(f"- {path}")
    else:
        parts.append("- No files were changed.")
    parts.extend(["", "## Validation"])
    for item in response.validation:
        parts.append(f"- {item}")
    parts.extend(["", "## Risks"])
    for risk in response.risks:
        parts.append(f"- {risk}")
    return "\n".join(parts).strip() + "\n"
