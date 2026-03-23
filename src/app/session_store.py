from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from ..models import FactItem, TaskResult


HISTORY_DIRNAME = ".history"
SESSION_FILENAME_PREFIX = "interactive-session-"
SESSION_FILENAME_SUFFIX = ".json"


@dataclass(slots=True)
class SessionTurn:
    user_request: str
    result_kind: str
    summary: str


@dataclass(slots=True)
class InteractiveSession:
    repo_path: str
    session_id: str | None = None
    turn_count: int = 0
    history: list[SessionTurn] = field(default_factory=list)
    facts: list[FactItem] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    validation_runs: list[str] = field(default_factory=list)
    last_result_summary: str = ""
    last_unknowns: list[str] = field(default_factory=list)

    def record_turn(self, user_request: str, result: TaskResult, facts: list[FactItem]) -> None:
        self.turn_count += 1
        self.last_result_summary = result.primary_text
        self.last_unknowns = list(result.unknowns)
        self.history.append(SessionTurn(user_request=user_request, result_kind=result.result_kind, summary=result.primary_text))
        self.history = self.history[-8:]
        self.facts = _merge_facts(self.facts, facts)
        self.changed_files = _merge_unique_strings(self.changed_files, result.changed_files)
        self.validation_runs = _merge_unique_strings(self.validation_runs, result.validation)


@dataclass(slots=True)
class SavedInteractiveSession:
    path: Path
    repo_path: str
    session_id: str | None
    turn_count: int


def history_dir(repo_path: Path) -> Path:
    return repo_path / HISTORY_DIRNAME


def create_session_path(repo_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    unique_suffix = uuid4().hex[:8]
    filename = f"{SESSION_FILENAME_PREFIX}{timestamp}-{unique_suffix}{SESSION_FILENAME_SUFFIX}"
    return history_dir(repo_path) / filename


def default_session_path(repo_path: Path) -> Path:
    return create_session_path(repo_path)


def list_sessions(repo_path: Path) -> list[SavedInteractiveSession]:
    saved_sessions: list[SavedInteractiveSession] = []
    for path in sorted(history_dir(repo_path).glob(f"{SESSION_FILENAME_PREFIX}*{SESSION_FILENAME_SUFFIX}")):
        state = load_session(path)
        if state is None:
            continue
        saved_sessions.append(SavedInteractiveSession(path=path, repo_path=state.repo_path, session_id=state.session_id, turn_count=state.turn_count))
    saved_sessions.sort(key=lambda item: item.path.stat().st_mtime, reverse=True)
    return saved_sessions


def find_session_by_id(repo_path: Path, session_id: str) -> SavedInteractiveSession | None:
    session_id = session_id.strip()
    if not session_id:
        return None
    for saved_session in list_sessions(repo_path):
        if saved_session.session_id == session_id:
            return saved_session
    return None


def load_session(path: Path) -> InteractiveSession | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return InteractiveSession(
        repo_path=str(payload["repo_path"]),
        session_id=payload.get("session_id"),
        turn_count=int(payload.get("turn_count", 0)),
        history=[SessionTurn(user_request=str(item.get("user_request", "")), result_kind=str(item.get("result_kind", "answer")), summary=str(item.get("summary", ""))) for item in payload.get("history", [])],
        facts=[
            FactItem(
                statement=str(item.get("statement", "")),
                files=[str(file_path) for file_path in item.get("files", [])],
                confidence=str(item.get("confidence", "medium")),
                status=str(item.get("status", "confirmed")),
                source=str(item.get("source", "local")),
            )
            for item in payload.get("facts", [])
        ],
        changed_files=[str(item) for item in payload.get("changed_files", [])],
        validation_runs=[str(item) for item in payload.get("validation_runs", [])],
        last_result_summary=str(payload.get("last_result_summary", "")),
        last_unknowns=[str(item) for item in payload.get("last_unknowns", [])],
    )


def save_session(path: Path, state: InteractiveSession) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _merge_unique_strings(existing: list[str], new_values: list[str]) -> list[str]:
    merged = list(existing)
    for value in new_values:
        if value not in merged:
            merged.append(value)
    return merged[-20:]


def _merge_facts(existing: list[FactItem], new_values: list[FactItem]) -> list[FactItem]:
    merged = list(existing)
    seen = {(fact.statement, tuple(fact.files)) for fact in merged}
    for fact in new_values:
        key = (fact.statement, tuple(fact.files))
        if key in seen:
            continue
        seen.add(key)
        merged.append(fact)
    return merged[-20:]
