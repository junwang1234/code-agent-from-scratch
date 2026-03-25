from __future__ import annotations

from pathlib import Path
import sys
from typing import TextIO

from ..models import ApprovalRequest, Task
from ..presentation.responder import render_markdown
from ..runtime.agent_runtime import AgentRuntime
from .session_service import InteractiveSessionService
from .task_builder import build_task_question


def run_interactive(
    repo_path: Path,
    *,
    runtime: AgentRuntime,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    resume: str | None = None,
    session_path: Path | None = None,
) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    service = InteractiveSessionService()
    active_session = service.start(
        repo_path,
        input_stream=input_stream,
        output_stream=output_stream,
        resume=resume,
        session_path=session_path,
    )
    session = active_session.session
    if Path(session.repo_path).resolve() != repo_path.resolve():
        raise ValueError(f"Session file repo path does not match interactive repo: {session.repo_path} != {repo_path}")
    if session.session_id is not None:
        service.restore_planner_session(runtime.planner, session.session_id)
    runtime.set_approved_command_scopes(session.approved_command_scopes)
    runtime.set_approval_handler(lambda request: _prompt_for_approval(request, input_stream=input_stream, output_stream=output_stream))
    output_stream.write(f"[session] repo: {repo_path}\n")
    output_stream.flush()

    while True:
        output_stream.write("> ")
        output_stream.flush()
        raw_line = input_stream.readline()
        if raw_line == "":
            output_stream.write("\n")
            output_stream.flush()
            return 0
        user_request = raw_line.strip()
        if not user_request:
            continue
        if user_request.lower() in {"exit", "quit"}:
            return 0
        output_stream.write(f"[session] turn: {session.turn_count + 1}\n")
        output_stream.flush()
        task = Task(repo_path=repo_path, question=build_task_question(session, user_request))
        outcome = runtime.run_with_artifacts(task)
        output_stream.write(render_markdown(outcome.result))
        output_stream.flush()
        service.record_outcome(
            active_session,
            user_request=user_request,
            outcome=outcome,
            planner=runtime.planner,
            approved_command_scopes=runtime.approved_command_scopes,
        )


def _prompt_for_approval(request: ApprovalRequest, *, input_stream: TextIO, output_stream: TextIO) -> bool:
    rendered = " ".join(request.argv)
    if request.install_suggestion is not None:
        output_stream.write("[approval] Install missing tool and retry validation?\n")
        output_stream.write(f"[approval] command: {rendered}\n")
        output_stream.write(f"[approval] cwd: {request.working_dir}\n")
        output_stream.write(f"[approval] reason: {request.reason}\n")
        output_stream.write(f"[approval] install source: {request.install_suggestion.source}\n")
        output_stream.write(f"[approval] install: {' '.join(request.install_suggestion.argv)}\n")
    else:
        output_stream.write("[approval] Run repo command with approved bash?\n")
        output_stream.write(f"[approval] command: {rendered}\n")
        output_stream.write(f"[approval] cwd: {request.working_dir}\n")
        output_stream.write(f"[approval] reason: {request.reason}\n")
    output_stream.write("Approve? [y/N] ")
    output_stream.flush()
    response = input_stream.readline()
    if response == "":
        output_stream.write("\n")
        output_stream.flush()
        return False
    approved = response.strip().lower() in {"y", "yes"}
    output_stream.write("\n")
    output_stream.flush()
    return approved
