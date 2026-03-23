from __future__ import annotations

import re

from ..models import FileContext, FileSnippet, ReadRange, SessionState


def record_file_context(
    memory: SessionState,
    *,
    path: str,
    start_line: int,
    end_line: int,
    excerpt: str,
    summary: str,
    step_id: str | None,
) -> None:
    context = memory.file_contexts.get(path)
    if context is None:
        context = FileContext(path=path)
        memory.file_contexts[path] = context
    context.read_ranges = merge_read_ranges(context.read_ranges + [ReadRange(start_line=start_line, end_line=end_line)])
    snippet = FileSnippet(path=path, start_line=start_line, end_line=end_line, excerpt=excerpt)
    context.excerpts = merge_snippets(context.excerpts + [snippet])
    context.symbols_seen = merge_symbols(context.symbols_seen, extract_symbols(excerpt))
    context.last_summary = summary
    context.patch_ready = is_patch_ready(path, context.read_ranges)
    context.last_read_step_id = step_id
    memory.snippets = merge_snippets(memory.snippets + [snippet])[-12:]


def merge_read_ranges(ranges: list[ReadRange]) -> list[ReadRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start_line, item.end_line))
    merged: list[ReadRange] = [ReadRange(start_line=ordered[0].start_line, end_line=ordered[0].end_line)]
    for item in ordered[1:]:
        current = merged[-1]
        if item.start_line <= current.end_line + 1:
            current.end_line = max(current.end_line, item.end_line)
            continue
        merged.append(ReadRange(start_line=item.start_line, end_line=item.end_line))
    return merged


def merge_snippets(snippets: list[FileSnippet]) -> list[FileSnippet]:
    deduped: list[FileSnippet] = []
    seen: set[tuple[str, int, int]] = set()
    for item in snippets:
        key = (item.path, item.start_line, item.end_line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda item: (item.path, item.start_line, item.end_line))
    return deduped


def merge_symbols(existing: list[str], new_symbols: list[str]) -> list[str]:
    merged = list(existing)
    for item in new_symbols:
        if item not in merged:
            merged.append(item)
    return merged[-12:]


def extract_symbols(excerpt: str) -> list[str]:
    symbols: list[str] = []
    for line in excerpt.splitlines():
        match = re.match(r"\s*(?:def|class|async def)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            symbols.append(match.group(1))
    return symbols


def is_patch_ready(path: str, read_ranges: list[ReadRange]) -> bool:
    from pathlib import Path

    if not read_ranges:
        return False
    total_lines = sum(item.end_line - item.start_line + 1 for item in read_ranges)
    name = Path(path).name.lower()
    if "test" in name:
        return total_lines >= 25
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx"}:
        return total_lines >= 50
    if suffix in {".md", ".txt", ".rst"}:
        return total_lines >= 20
    return total_lines >= 40


def repair_redundant_read(memory: SessionState, path: str, start_line: int, end_line: int) -> dict | None:
    if not path:
        return None
    context = memory.file_contexts.get(path)
    if context is None:
        return None
    requested = ReadRange(start_line=start_line, end_line=end_line)
    uncovered = first_uncovered_gap(context.read_ranges, requested)
    if uncovered is None and context.patch_ready:
        fallback = next_uncovered_window(context.read_ranges, requested)
        if fallback is None:
            return {
                "tool_input": {"path": path, "start_line": start_line, "end_line": end_line},
                "reason": "This read target is already covered by prior file context; the runtime kept the request unchanged because no new bounded gap was available.",
            }
        return {
            "tool_input": {"path": path, "start_line": fallback.start_line, "end_line": fallback.end_line},
            "reason": f"Repaired redundant reread into the nearest uncovered window at lines {fallback.start_line}-{fallback.end_line}.",
        }
    if uncovered is not None and (uncovered.start_line != start_line or uncovered.end_line != end_line):
        return {
            "tool_input": {"path": path, "start_line": uncovered.start_line, "end_line": uncovered.end_line},
            "reason": f"Trimmed the reread to the uncovered gap at lines {uncovered.start_line}-{uncovered.end_line}.",
        }
    return None


def first_uncovered_gap(existing: list[ReadRange], requested: ReadRange) -> ReadRange | None:
    cursor = requested.start_line
    for item in existing:
        if item.end_line < cursor:
            continue
        if item.start_line > requested.end_line:
            break
        if item.start_line > cursor:
            return ReadRange(start_line=cursor, end_line=min(requested.end_line, item.start_line - 1))
        cursor = max(cursor, item.end_line + 1)
        if cursor > requested.end_line:
            return None
    if cursor <= requested.end_line:
        return ReadRange(start_line=cursor, end_line=requested.end_line)
    return None


def next_uncovered_window(existing: list[ReadRange], requested: ReadRange) -> ReadRange | None:
    window = max(20, requested.end_line - requested.start_line + 1)
    probe_start = requested.end_line + 1
    for _ in range(4):
        candidate = ReadRange(start_line=probe_start, end_line=probe_start + window - 1)
        uncovered = first_uncovered_gap(existing, candidate)
        if uncovered is not None:
            return uncovered
        probe_start += window
    return None
