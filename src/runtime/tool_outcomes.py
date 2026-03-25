from __future__ import annotations

from ..runtime.memory_manager import AgentMemory
from ..models import FactItem, RepoMapEntry
from ..tools.core import HeadFileToolResult, ReadFileRangeToolResult
from ..tools.shell import CommandResult, ShellQueryResult, format_shell_query


def apply_tree_outcome(memory: AgentMemory, *, tree: list[str], depth: int) -> str:
    from .observation_analysis import facts_from_tree, summarize_tree

    summary, highlights = summarize_tree(tree)
    memory.record_tree_observation(depth=depth, tree=tree, summary=summary, highlights=highlights, facts=facts_from_tree(tree))
    memory.compact()
    return summary


def apply_file_range_outcome(memory: AgentMemory, *, result: ReadFileRangeToolResult) -> str:
    from .action_repair import render_explicit_tool_action
    from .observation_analysis import classify_file, facts_from_excerpt, summarize_excerpt

    path = result.path
    safe_start = result.start_line
    safe_end = result.end_line
    excerpt = result.excerpt
    excerpt_lines = excerpt.splitlines()
    raw_output = [f"{path}:{safe_start + index}:{line}" for index, line in enumerate(excerpt_lines)]
    summary, highlights = summarize_excerpt(path, excerpt)
    memory.record_file_read(
        tool="read_file_range",
        tool_input=render_explicit_tool_action("read_file_range", {"path": path, "start_line": safe_start, "end_line": safe_end}),
        path=path,
        start_line=safe_start,
        end_line=safe_end,
        excerpt=excerpt,
        summary=summary,
        highlights=highlights[:4],
        facts=facts_from_excerpt(path, excerpt, highlights),
        repo_note=classify_file(path, excerpt),
        raw_output=raw_output,
    )
    memory.compact()
    return summary


def apply_head_file_outcome(memory: AgentMemory, *, result: HeadFileToolResult) -> str:
    from .action_repair import render_explicit_tool_action
    from .observation_analysis import classify_file, facts_from_excerpt, summarize_excerpt

    raw_output: list[str] = []
    highlights: list[str] = []
    summaries: list[str] = []
    facts: list[FactItem] = []
    repo_entries: list[RepoMapEntry] = []
    inspected_files: list[str] = []
    for item in result.excerpts:
        path = item.path
        safe_start = item.start_line
        safe_end = item.end_line
        excerpt = item.excerpt
        excerpt_lines = excerpt.splitlines()
        raw_output.extend(f"{path}:{safe_start + index}:{line}" for index, line in enumerate(excerpt_lines))
        summary, path_highlights = summarize_excerpt(path, excerpt)
        summaries.append(summary)
        highlights.extend(path_highlights[:2])
        memory.record_file_context(path=path, start_line=safe_start, end_line=safe_end, excerpt=excerpt, summary=summary, step_id=memory.state.current_step_id)
        facts.extend(facts_from_excerpt(path, excerpt, path_highlights))
        repo_entries.append(RepoMapEntry(path=path, note=classify_file(path, excerpt)))
        inspected_files.append(path)
    memory.record_head_file_batch(
        tool_input=render_explicit_tool_action("head_file", {"paths": result.paths, "lines": result.lines}),
        summaries=summaries,
        highlights=highlights,
        raw_output=raw_output,
        repo_entries=repo_entries,
        facts=facts,
        inspected_files=inspected_files,
    )
    memory.compact()
    return memory.state.observations[-1].result_summary


def apply_shell_outcome(memory: AgentMemory, *, observation_tool: str, result: ShellQueryResult) -> str:
    from .action_repair import extract_find_option, render_explicit_tool_action
    from .observation_analysis import facts_from_shell_query, summarize_shell_query

    summary, highlights = summarize_shell_query(result)
    if observation_tool == "rg_search":
        pattern = result.args[1] if result.args and result.args[0] == "-n" and len(result.args) >= 2 else ""
        paths = result.args[2:] if result.args[:1] == ["-n"] else result.args[1:]
        tool_input = render_explicit_tool_action("rg_search", {"pattern": pattern, "paths": paths})
    elif observation_tool == "rg_probe":
        pattern = result.args[1] if result.args and result.args[0] == "-n" and len(result.args) >= 2 else ""
        paths = result.args[2:] if result.args[:1] == ["-n"] else result.args[1:]
        tool_input = render_explicit_tool_action("rg_probe", {"pattern": pattern, "paths": paths})
    elif observation_tool == "rg_files":
        paths = [arg for arg in result.args if not arg.startswith("-")]
        tool_input = render_explicit_tool_action("rg_files", {"paths": paths})
    elif observation_tool in {"find_paths", "list_files"}:
        tool_input = render_explicit_tool_action(
            observation_tool,
            {
                "paths": [arg for arg in result.args if not arg.startswith("-") and arg not in {"f", "d"} and not arg.isdigit() and "*" not in arg],
                "max_depth": extract_find_option(result.args, "-maxdepth"),
                "file_type": extract_find_option(result.args, "-type"),
                "name_glob": extract_find_option(result.args, "-name"),
            },
        )
    elif observation_tool == "search_code":
        pattern = result.args[1] if result.args and result.args[0] == "-n" and len(result.args) >= 2 else ""
        paths = result.args[2:] if result.args[:1] == ["-n"] else result.args[1:]
        tool_input = render_explicit_tool_action("search_code", {"pattern": pattern, "paths": paths})
    else:
        tool_input = format_shell_query(result.command, result.args)
    inspected_files = []
    for line in result.output:
        path, _, _ = line.partition(":")
        if path:
            inspected_files.append(path)
    memory.record_shell_observation(
        observation_tool=observation_tool,
        tool_input=tool_input,
        summary=summary,
        highlights=highlights,
        raw_output=result.output,
        metadata={"command": result.command, "args": result.args, "exit_code": result.exit_code, "truncated": result.truncated, "line_count": len(result.output)},
        inspected_files=inspected_files,
        facts=facts_from_shell_query(result, highlights),
    )
    memory.compact()
    return summary


def apply_write_outcome(memory: AgentMemory, *, tool_name: str, write_result, summary: str) -> str:
    memory.record_write(tool_name=tool_name, path=write_result.path, summary=summary)
    memory.compact()
    return summary


def apply_command_outcome(memory: AgentMemory, *, tool_name: str, result: CommandResult, discovery_state=None) -> str:
    from .observation_analysis import summarize_test_result

    rendered = format_shell_query(result.command, result.args)
    summary = f"{rendered} exited with code {result.exit_code} and produced {len(result.output)} line(s)."
    if tool_name == "run_tests":
        test_summary = summarize_test_result(result)
        if test_summary:
            summary = f"{summary} {test_summary}"
    highlights = [line[:180] for line in result.output[:6]]
    memory.record_command(
        tool_name=tool_name,
        rendered=rendered,
        summary=summary,
        highlights=highlights,
        raw_output=result.output,
        metadata={"command": result.command, "args": result.args, "exit_code": result.exit_code, "truncated": result.truncated, "line_count": len(result.output)},
        validation_note=summary,
        success=result.exit_code == 0,
    )
    memory.record_validation_discovery_state(discovery_state)
    memory.compact()
    return summary
