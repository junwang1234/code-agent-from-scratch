from __future__ import annotations

from ..models import FactItem, SessionState, TurnArtifacts
from .observation_analysis import dedupe_facts, dedupe_strings


def build_turn_artifacts(memory: SessionState) -> TurnArtifacts:
    return TurnArtifacts(
        facts=memory.facts[:],
        changed_files=sorted(memory.changed_files),
        validation_runs=memory.validation_runs[:],
        unknowns=memory.unknowns[:],
    )


def merge_fact_updates(memory: SessionState, updates: list[FactItem]) -> list[FactItem]:
    merged = list(memory.facts)
    for update in updates:
        validated = validate_fact_update(memory, update)
        if validated is None:
            continue
        merged = [fact for fact in merged if fact.statement != validated.statement]
        merged.append(validated)
    return dedupe_facts(merged)


def validate_fact_update(memory: SessionState, update: FactItem) -> FactItem | None:
    statement = update.statement.strip()
    if not statement:
        return None
    if update.confidence not in {"low", "medium", "high"}:
        return None
    if update.status not in {"candidate", "confirmed", "retracted"}:
        return None
    files = [path.strip() for path in update.files if isinstance(path, str) and path.strip()]
    files = dedupe_strings(files)
    valid_files: list[str] = []
    for path in files[:8]:
        candidate = (memory.task.repo_path / path).resolve()
        try:
            candidate.relative_to(memory.task.repo_path.resolve())
        except ValueError:
            continue
        if candidate.exists():
            valid_files.append(path)
    return FactItem(statement=statement, files=valid_files, confidence=update.confidence, status=update.status, source=update.source or "planner")
