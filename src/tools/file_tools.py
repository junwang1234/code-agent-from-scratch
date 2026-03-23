from __future__ import annotations

from typing import Any

from .core import FileExcerpt, HeadFileToolResult, ReadFileRangeToolResult, Tool, ToolExecutionContext, TreeToolResult, WriteToolResult


class ListTreeTool(Tool):
    name = "list_tree"
    description = "Return a bounded shallow directory tree."
    input_schema = {
        "type": "object",
        "properties": {"depth": {"type": "integer", "minimum": 0, "maximum": 4}},
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> TreeToolResult:
        depth = int(payload.get("depth") or 2)
        return TreeToolResult(tree=context.repo_filesystem.list_tree(depth=depth), depth=depth)


class HeadFileTool(Tool):
    name = "head_file"
    description = "Read the first lines of one or more repo-relative files."
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
            "lines": {"type": "integer", "minimum": 5, "maximum": 80},
        },
        "required": ["paths"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> HeadFileToolResult:
        paths = [str(item) for item in payload.get("paths") or []]
        lines = int(payload.get("lines") or 40)
        effective_lines = max(5, min(lines, 80))
        excerpts = [
            FileExcerpt(path=path, start_line=safe_start, end_line=safe_end, excerpt=excerpt)
            for path in paths[:3]
            for safe_start, safe_end, excerpt in [context.repo_filesystem.read_file(path, 1, effective_lines)]
        ]
        return HeadFileToolResult(paths=paths[:3], lines=effective_lines, excerpts=excerpts)


class ReadFileRangeTool(Tool):
    name = "read_file_range"
    description = "Read a bounded line range from a repo-relative file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ReadFileRangeToolResult:
        path = str(payload.get("path") or "")
        start_line = int(payload.get("start_line") or 1)
        end_line = int(payload.get("end_line") or min(start_line + 39, 80))
        safe_start, safe_end, excerpt = context.repo_filesystem.read_file_range(path, start_line, end_line)
        return ReadFileRangeToolResult(path=path, start_line=safe_start, end_line=safe_end, excerpt=excerpt)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write complete file contents to a repo-relative path."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> WriteToolResult:
        path = str(payload.get("path") or "")
        content = str(payload.get("content") or "")
        write_result = context.repo_filesystem.write_file(path, content)
        return WriteToolResult(tool_name=self.name, write_result=write_result, summary=f"Wrote full file content to {write_result.path}.")


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Replace an existing exact text span in a repo-relative file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }

    def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> WriteToolResult:
        path = str(payload.get("path") or "")
        old_text = str(payload.get("old_text") or "")
        new_text = str(payload.get("new_text") or "")
        replace_all = bool(payload.get("replace_all") or False)
        write_result = context.repo_filesystem.apply_patch(path, old_text, new_text, replace_all=replace_all)
        summary = f"Patched {write_result.path} by replacing {'all' if replace_all else 'one'} matching text span(s)."
        return WriteToolResult(tool_name=self.name, write_result=write_result, summary=summary)
