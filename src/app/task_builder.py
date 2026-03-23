from __future__ import annotations

from .session_store import InteractiveSession


def build_context_prefix(session: InteractiveSession) -> str:
    lines: list[str] = []
    if session.turn_count == 0:
        return ""
    lines.extend(["Interactive session context:", f"- Prior turns: {session.turn_count}"])
    if session.history:
        lines.append("- Recent turn summaries:")
        for turn in session.history[-3:]:
            lines.append(f"  - User: {turn.user_request.strip()} | Result: {turn.summary.strip()}")
    if session.facts:
        lines.append("- Confirmed facts:")
        for fact in session.facts[-5:]:
            fact_files = f" ({', '.join(fact.files[:3])})" if fact.files else ""
            lines.append(f"  - {fact.statement}{fact_files}")
    if session.changed_files:
        lines.append(f"- Changed files so far: {', '.join(session.changed_files[-8:])}")
    if session.validation_runs:
        lines.append(f"- Validation runs so far: {', '.join(session.validation_runs[-5:])}")
    if session.last_unknowns:
        lines.append("- Remaining unknowns from the last turn:")
        for item in session.last_unknowns[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def build_task_question(session: InteractiveSession, user_request: str) -> str:
    prefix = build_context_prefix(session)
    return user_request if not prefix else prefix + "\n\nCurrent user request:\n" + user_request
