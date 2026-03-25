from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ..models import ApprovedCommandScope
from ..planning.base import BasePlanner
from .session_store import (
    InteractiveSession,
    SavedInteractiveSession,
    default_session_path,
    find_session_by_id,
    list_sessions,
    load_session,
    save_session,
)


@dataclass(slots=True)
class ActiveSession:
    session: InteractiveSession
    save_path: Path


class InteractiveSessionService:
    def start(
        self,
        repo_path: Path,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
        resume: str | None,
        session_path: Path | None,
    ) -> ActiveSession:
        session, resolved_path = self._prepare_session(
            repo_path,
            input_stream=input_stream,
            output_stream=output_stream,
            resume=resume,
            session_path=session_path,
        )
        return ActiveSession(session=session, save_path=resolved_path)

    def restore_planner_session(self, planner: BasePlanner, session_id: str | None) -> None:
        planner.set_session_id(session_id)

    def session_id(self, planner: BasePlanner) -> str | None:
        return planner.get_session_id()

    def record_outcome(
        self,
        active_session: ActiveSession,
        *,
        user_request: str,
        outcome,
        planner: BasePlanner,
        approved_command_scopes: list[ApprovedCommandScope] | None = None,
    ) -> None:
        active_session.session.record_turn(user_request, outcome.result, outcome.artifacts.facts)
        active_session.session.session_id = self.session_id(planner)
        active_session.session.approved_command_scopes = list(approved_command_scopes or active_session.session.approved_command_scopes)
        save_session(active_session.save_path, active_session.session)

    def _prepare_session(
        self,
        repo_path: Path,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
        resume: str | None,
        session_path: Path | None,
    ) -> tuple[InteractiveSession, Path]:
        if session_path is not None:
            return load_session(session_path) or InteractiveSession(repo_path=str(repo_path)), session_path
        if resume is None:
            return InteractiveSession(repo_path=str(repo_path)), default_session_path(repo_path)
        saved_interactive_session = self._resolve_saved_session(repo_path, input_stream=input_stream, output_stream=output_stream, resume=resume)
        if saved_interactive_session is None:
            return InteractiveSession(repo_path=str(repo_path)), default_session_path(repo_path)
        return load_session(saved_interactive_session.path) or InteractiveSession(repo_path=str(repo_path)), saved_interactive_session.path

    def _resolve_saved_session(self, repo_path: Path, *, input_stream: TextIO, output_stream: TextIO, resume: str) -> SavedInteractiveSession | None:
        if resume == "__prompt__":
            return self._prompt_for_session(repo_path, input_stream=input_stream, output_stream=output_stream)
        saved_interactive_session = find_session_by_id(repo_path, resume)
        if saved_interactive_session is None:
            raise ValueError(f"No saved session found for session id: {resume}")
        return saved_interactive_session

    def _prompt_for_session(self, repo_path: Path, *, input_stream: TextIO, output_stream: TextIO) -> SavedInteractiveSession | None:
        sessions = list_sessions(repo_path)
        if not sessions:
            output_stream.write(f"[session] no saved sessions found under {repo_path / '.history'}; starting fresh session.\n")
            output_stream.flush()
            return None
        output_stream.write("[session] available saved sessions:\n")
        for saved_interactive_session in sessions:
            session_label = saved_interactive_session.session_id or "(pending session id)"
            output_stream.write(f"[session] id: {session_label} | turns: {saved_interactive_session.turn_count} | file: {saved_interactive_session.path.name}\n")
        output_stream.write("[session] enter session id to resume: ")
        output_stream.flush()
        selected_id = input_stream.readline().strip()
        if not selected_id:
            output_stream.write("[session] no session selected; starting fresh session.\n")
            output_stream.flush()
            return None
        saved_interactive_session = find_session_by_id(repo_path, selected_id)
        if saved_interactive_session is None:
            output_stream.write(f"[session] session id not found: {selected_id}; starting fresh session.\n")
            output_stream.flush()
        return saved_interactive_session
