from __future__ import annotations

from pathlib import Path

from ..models import FactItem
from ..tools.shell import CommandResult, ShellQueryResult


def summarize_test_result(result: CommandResult) -> str:
    lines = [line.strip() for line in result.output if line.strip()]
    ran_line = next((line for line in lines if line.startswith("Ran ") and " test" in line), "")
    if not ran_line:
        return ""
    parts = ran_line.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return ""
    total = int(parts[1])
    if any(line == "OK" or line.startswith("OK ") for line in lines):
        return f"Test results: {total} tested, {total} passed."
    failed_line = next((line for line in lines if line.startswith("FAILED")), "")
    if failed_line:
        return f"Test results: {total} tested, failures present."
    return f"Test results: {total} tested."


def classify_file(path: str, excerpt: str) -> str:
    lowered = excerpt.lower()
    if "def main" in lowered or "if __name__" in lowered or "app =" in lowered:
        return "likely entrypoint"
    if "route" in lowered or "router" in lowered or "endpoint" in lowered:
        return "routing or request handling"
    if "config" in path.lower():
        return "configuration"
    return "inspected file"


def summarize_tree(tree: list[str]) -> tuple[str, list[str]]:
    if not tree:
        return "Repository tree is empty.", ["No files or directories were returned."]
    top_level_dirs = sorted({entry.rstrip("/") for entry in tree if entry.endswith("/") and "/" not in entry.rstrip("/")})
    top_level_files = sorted({entry for entry in tree if not entry.endswith("/") and "/" not in entry})
    summary_parts: list[str] = []
    if top_level_dirs:
        summary_parts.append("Top-level dirs: " + ", ".join(top_level_dirs[:6]))
    if top_level_files:
        summary_parts.append("Root files: " + ", ".join(top_level_files[:6]))
    if not summary_parts:
        summary_parts.append("Tree contains nested entries but no root-level items were captured.")
    highlights: list[str] = []
    if top_level_dirs:
        highlights.append(f"Primary directories: {', '.join(top_level_dirs[:4])}")
    if top_level_files:
        highlights.append(f"Primary root files: {', '.join(top_level_files[:4])}")
    nested_files = [entry for entry in tree if not entry.endswith("/") and "/" in entry]
    representative = representative_nested_files(nested_files)
    if representative:
        highlights.append("Representative nested files: " + ", ".join(representative))
    return ". ".join(summary_parts) + ".", highlights[:4]


def summarize_excerpt(path: str, excerpt: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in excerpt.splitlines()]
    meaningful = [line for line in lines if line and line not in {"---", "```"}]
    if not meaningful:
        return f"{path} appears empty or contains no meaningful text in the bounded read.", []
    suffix = Path(path).suffix.lower()
    if suffix in {".md", ".markdown", ""}:
        return summarize_markdown(path, meaningful)
    return summarize_code_or_text(path, meaningful)


def summarize_markdown(path: str, meaningful: list[str]) -> tuple[str, list[str]]:
    title = next((line[2:].strip() for line in meaningful if line.startswith("# ")), "")
    headings = [line.lstrip("#").strip() for line in meaningful if line.startswith("#")]
    prose = [line for line in meaningful if not line.startswith("#")]
    summary_parts = [f"{path} is a markdown document"]
    if title:
        summary_parts.append(f"titled '{title}'")
    if prose:
        summary_parts.append(f"opening content: {prose[0][:120]}")
    highlights: list[str] = []
    if title:
        highlights.append(f"Title: {title}")
    if headings:
        highlights.append("Headings: " + ", ".join(headings[:3]))
    if prose:
        highlights.append("Key line: " + prose[0][:160])
    return "; ".join(summary_parts) + ".", highlights[:3]


def summarize_code_or_text(path: str, meaningful: list[str]) -> tuple[str, list[str]]:
    imports = [line for line in meaningful if line.startswith(("import ", "from "))]
    defs = [line for line in meaningful if line.startswith(("def ", "class "))]
    summary_parts = [f"{path} is a source or text file"]
    if defs:
        summary_parts.append("with top-level definitions " + ", ".join(item.split("(")[0] for item in defs[:3]))
    elif imports:
        summary_parts.append("with imports " + ", ".join(imports[:3]))
    else:
        summary_parts.append(f"opening content: {meaningful[0][:120]}")
    highlights: list[str] = []
    if defs:
        highlights.append("Definitions: " + ", ".join(defs[:3]))
    if imports:
        highlights.append("Imports: " + ", ".join(imports[:3]))
    if not defs and meaningful:
        highlights.append("Key line: " + meaningful[0][:160])
    return "; ".join(summary_parts) + ".", highlights[:3]


def representative_nested_files(nested_files: list[str]) -> list[str]:
    preferred = []
    for needle in ("scripts/", "tests/", "references/"):
        preferred.extend([path for path in nested_files if path.startswith(needle)])
    ordered = []
    for path in preferred + nested_files:
        if path not in ordered:
            ordered.append(path)
    return ordered[:4]


def facts_from_tree(tree: list[str]) -> list[FactItem]:
    top_level_dirs = sorted({entry.rstrip("/") for entry in tree if entry.endswith("/") and "/" not in entry.rstrip("/")})
    top_level_files = sorted({entry for entry in tree if not entry.endswith("/") and "/" not in entry})
    facts: list[FactItem] = []
    if top_level_dirs:
        facts.append(FactItem(statement="Top-level directories are " + ", ".join(top_level_dirs[:6]) + ".", files=[entry + "/" for entry in top_level_dirs[:6]], confidence="high"))
    if top_level_files:
        facts.append(FactItem(statement="Root files include " + ", ".join(top_level_files[:6]) + ".", files=top_level_files[:6], confidence="high"))
    return facts[:2]


def facts_from_excerpt(path: str, excerpt: str, highlights: list[str]) -> list[FactItem]:
    suffix = Path(path).suffix.lower()
    lines = [line.strip() for line in excerpt.splitlines() if line.strip() and line.strip() not in {"---", "```"}]
    facts: list[FactItem] = []
    if suffix in {".md", ".markdown", ""}:
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
        if title:
            facts.append(FactItem(statement=f"{path} is a top-level document titled '{title}'.", files=[path], confidence="high"))
        for item in highlights[:2]:
            label, _, value = item.partition(": ")
            if value:
                facts.append(FactItem(statement=f"{path} {label.lower()} {value}.", files=[path], confidence="medium"))
    else:
        defs = [line.split("(")[0] for line in lines if line.startswith(("def ", "class "))]
        if defs:
            facts.append(FactItem(statement=f"{path} defines {', '.join(defs[:3])}.", files=[path], confidence="high"))
        elif lines:
            facts.append(FactItem(statement=f"{path} opens with '{lines[0][:120]}'.", files=[path], confidence="medium"))
    return dedupe_facts(facts)[:3]


def facts_from_shell_query(result: ShellQueryResult, highlights: list[str]) -> list[FactItem]:
    facts: list[FactItem] = []
    files: list[str] = []
    for line in result.output:
        path, _, remainder = line.partition(":")
        if path and path not in files:
            files.append(path)
        lowered = remainder.lower()
        if "scripts/" in lowered:
            match = extract_repo_path_from_text(remainder)
            if match:
                facts.append(FactItem(statement=f"{path} references implementation path {match}.", files=[path, match], confidence="medium"))
        if any(term in lowered for term in ("python ", "uv run", "workflow", "entrypoint")):
            facts.append(FactItem(statement=f"{path} contains workflow or execution guidance surfaced by a shell-first query.", files=[path], confidence="medium"))
    if files:
        facts.append(FactItem(statement=f"shell-first query inspected targeted text matches across {', '.join(files[:4])}.", files=files[:4], confidence="medium"))
    for item in highlights[:2]:
        if item not in {"Output truncated.", "No matching lines returned."}:
            facts.append(FactItem(statement=f"shell-first query highlight: {item}", files=files[:2], confidence="low"))
    return dedupe_facts(facts)[:4]


def dedupe_facts(facts: list[FactItem]) -> list[FactItem]:
    ordered: list[FactItem] = []
    seen: set[str] = set()
    for fact in facts:
        if fact.statement in seen:
            continue
        seen.add(fact.statement)
        ordered.append(fact)
    return ordered


def dedupe_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def summarize_shell_query(result: ShellQueryResult) -> tuple[str, list[str]]:
    if not result.output:
        return f"{result.command} returned no matching lines.", ["No matching lines returned."]
    summary = f"{result.command} returned {len(result.output)} raw line(s)."
    highlights = [line[:180] for line in result.output[:6]]
    if result.truncated:
        highlights.append("Output truncated.")
    return summary, highlights[:6]


def extract_repo_path_from_text(text: str) -> str | None:
    for token in text.replace("`", " ").replace("'", " ").replace('"', " ").split():
        if "/" not in token:
            continue
        cleaned = token.strip(".,:;()[]{}")
        if cleaned.startswith(("scripts/", "tests/", "references/", "src/")):
            return cleaned
    return None
