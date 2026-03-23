from __future__ import annotations

from pathlib import Path

from ..models import WriteResult


MAX_TREE_ENTRIES = 200
MAX_READ_LINES = 80
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
}


class RepoFilesystem:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise ValueError(f"Repo path does not exist or is not a directory: {repo_path}")

    def list_tree(self, depth: int = 2) -> list[str]:
        results: list[str] = []
        self._walk_tree(self.repo_path, current_depth=0, max_depth=max(depth, 0), results=results)
        return results[:MAX_TREE_ENTRIES]

    def read_file(self, path: str, start_line: int = 1, end_line: int = 40) -> tuple[int, int, str]:
        resolved = self._resolve_repo_path(path)
        safe_start = max(start_line, 1)
        safe_end = max(safe_start, min(end_line, safe_start + MAX_READ_LINES - 1))
        lines = self._safe_read_text(resolved).splitlines()
        snippet = lines[safe_start - 1 : safe_end]
        return safe_start, safe_end, "\n".join(snippet)

    def read_file_range(self, path: str, start_line: int = 1, end_line: int = 40) -> tuple[int, int, str]:
        return self.read_file(path, start_line, end_line)

    def write_file(self, path: str, content: str) -> WriteResult:
        resolved = self._resolve_repo_path_for_write(path)
        old_content = ""
        if resolved.exists() and resolved.is_file():
            old_content = self._safe_read_text(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return WriteResult(path=resolved.relative_to(self.repo_path).as_posix(), old_content=old_content, new_content=content)

    def apply_patch(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> WriteResult:
        resolved = self._resolve_repo_path(path)
        original = self._safe_read_text(resolved)
        if old_text not in original:
            raise ValueError(f"Patch target text was not found in {path}")
        updated = original.replace(old_text, new_text) if replace_all else original.replace(old_text, new_text, 1)
        resolved.write_text(updated, encoding="utf-8")
        return WriteResult(path=resolved.relative_to(self.repo_path).as_posix(), old_content=original, new_content=updated)

    def _walk_tree(self, root: Path, current_depth: int, max_depth: int, results: list[str]) -> None:
        if len(results) >= MAX_TREE_ENTRIES or current_depth > max_depth:
            return
        entries = sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        for entry in entries:
            if entry.name in IGNORED_DIRS:
                continue
            rel_path = entry.relative_to(self.repo_path).as_posix()
            suffix = "/" if entry.is_dir() else ""
            results.append(rel_path + suffix)
            if len(results) >= MAX_TREE_ENTRIES:
                return
            if entry.is_dir():
                self._walk_tree(entry, current_depth + 1, max_depth, results)

    def _resolve_repo_path(self, path: str) -> Path:
        candidate = (self.repo_path / path).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            raise ValueError(f"Path escapes repository root: {path}")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"File does not exist: {path}")
        return candidate

    def _resolve_repo_path_for_write(self, path: str) -> Path:
        candidate = (self.repo_path / path).resolve()
        if self.repo_path not in candidate.parents and candidate != self.repo_path:
            raise ValueError(f"Path escapes repository root: {path}")
        return candidate

    def _safe_read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
