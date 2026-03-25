from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from ...models.validation import DiscoveredCommand, ValidationCommand, ValidationDiscoveryState


PACKAGE_MANAGERS = ("pnpm", "yarn", "npm")
DOC_HINT_FILES = ("AGENTS.md", "README.md")


class ValidationDiscoveryService:
    def __init__(self) -> None:
        self._cache: dict[Path, ValidationDiscoveryState] = {}

    def discover(self, repo_path: Path) -> ValidationDiscoveryState:
        resolved_repo = repo_path.resolve()
        state = discover_validation_commands(resolved_repo)
        cached = self._cache.get(resolved_repo)
        if cached is not None and cached.repo_fingerprint == state.repo_fingerprint:
            return cached
        self._cache[resolved_repo] = state
        return state


def discover_validation_commands(repo_path: Path) -> ValidationDiscoveryState:
    resolved_repo = repo_path.resolve()
    evidence: list[str] = []
    state = ValidationDiscoveryState(repo_fingerprint=_repo_fingerprint(resolved_repo))

    test_candidates: list[DiscoveredCommand] = []
    lint_candidates: list[DiscoveredCommand] = []
    format_candidates: list[DiscoveredCommand] = []

    doc_test, doc_lint, doc_format = _discover_explicit_doc_commands(resolved_repo, evidence)
    workflow_test, workflow_lint, workflow_format = _discover_explicit_workflow_commands(resolved_repo, evidence)

    test_candidates.extend(doc_test)
    test_candidates.extend(workflow_test)
    test_candidates.extend(_discover_python(resolved_repo, evidence))
    test_candidates.extend(_discover_node(resolved_repo, evidence))
    test_candidates.extend(_discover_rust(resolved_repo, evidence))
    test_candidates.extend(_discover_go(resolved_repo, evidence))
    test_candidates.extend(_discover_java(resolved_repo, evidence))

    lint_candidates.extend(doc_lint)
    lint_candidates.extend(workflow_lint)
    lint_candidates.extend(_discover_node_lint(resolved_repo, evidence))
    lint_candidates.extend(_discover_python_lint(resolved_repo, evidence))
    lint_candidates.extend(_discover_rust_lint(resolved_repo, evidence))

    format_candidates.extend(doc_format)
    format_candidates.extend(workflow_format)
    format_candidates.extend(_discover_node_format(resolved_repo, evidence))
    format_candidates.extend(_discover_python_format(resolved_repo, evidence))
    format_candidates.extend(_discover_rust_format(resolved_repo, evidence))

    state.test_candidates = _rank_candidates(_dedupe_candidates(test_candidates))
    state.lint_candidates = _rank_candidates(_dedupe_candidates(lint_candidates))
    state.format_candidates = _rank_candidates(_dedupe_candidates(format_candidates))
    state.selected_test = _select_candidate(state.test_candidates)
    state.selected_lint = _select_candidate(state.lint_candidates)
    state.selected_format = _select_candidate(state.format_candidates)
    state.evidence = _dedupe(evidence)[:12]
    state.blockers = _collect_blockers(state)[:6]
    return state


def _discover_python(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    candidates: list[DiscoveredCommand] = []
    config = _read_text_if_exists(repo / "pytest.ini") or ""
    pyproject = _read_text_if_exists(repo / "pyproject.toml") or ""
    tox = _read_text_if_exists(repo / "tox.ini") or ""
    tests_dir = repo / "tests"
    interpreter = _python_interpreter(repo)
    blockers = _python_blockers(repo, interpreter)
    command_prefix = [interpreter] if interpreter else ["python"]
    if pyproject:
        evidence.append("pyproject.toml exists")
    if config:
        evidence.append("pytest.ini exists")
    if tox:
        evidence.append("tox.ini exists")
    if tests_dir.exists():
        evidence.append("tests/ exists")
    if interpreter:
        evidence.append(f"{interpreter} exists")

    if any(marker in text for text in (config, pyproject, tox) for marker in ("[tool.pytest", "[pytest]", "pytest")):
        candidates.append(
            DiscoveredCommand(
                kind="test",
                command=ValidationCommand(kind="test", argv=[*command_prefix, "-m", "pytest", "-q"]),
                source="python-config",
                confidence=0.93 if interpreter else 0.82,
                evidence=["pytest configuration detected"],
                blockers=list(blockers),
            )
        )
    if tests_dir.exists():
        candidates.append(
            DiscoveredCommand(
                kind="test",
                command=ValidationCommand(kind="test", argv=[*command_prefix, "-m", "unittest", "discover", "-s", "tests", "-v"]),
                source="python-tests-layout",
                confidence=0.82 if interpreter else 0.7,
                evidence=["tests/ layout supports unittest discovery"],
                blockers=list(blockers),
            )
        )
    return candidates


def _discover_explicit_doc_commands(repo: Path, evidence: list[str]) -> tuple[list[DiscoveredCommand], list[DiscoveredCommand], list[DiscoveredCommand]]:
    return _discover_explicit_commands_from_files(
        repo,
        evidence,
        paths=[repo / relative for relative in DOC_HINT_FILES],
        source="repo-doc",
        confidence=0.99,
    )


def _discover_explicit_workflow_commands(repo: Path, evidence: list[str]) -> tuple[list[DiscoveredCommand], list[DiscoveredCommand], list[DiscoveredCommand]]:
    workflows = repo / ".github" / "workflows"
    if not workflows.exists():
        return ([], [], [])
    return _discover_explicit_commands_from_files(
        repo,
        evidence,
        paths=sorted(workflows.glob("*.y*ml")),
        source="ci-workflow-explicit",
        confidence=0.97,
    )


def _discover_explicit_commands_from_files(
    repo: Path,
    evidence: list[str],
    *,
    paths: list[Path],
    source: str,
    confidence: float,
) -> tuple[list[DiscoveredCommand], list[DiscoveredCommand], list[DiscoveredCommand]]:
    test_candidates: list[DiscoveredCommand] = []
    lint_candidates: list[DiscoveredCommand] = []
    format_candidates: list[DiscoveredCommand] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = _read_text_if_exists(path) or ""
        relative = path.relative_to(repo).as_posix()
        evidence.append(f"{relative} exists")
        extracted = _extract_known_commands(text)
        if extracted:
            evidence.append(f"{relative} contains explicit validation commands")
        for kind, argv in extracted:
            candidate = DiscoveredCommand(
                kind=kind,
                command=ValidationCommand(kind=kind, argv=argv),
                source=source,
                confidence=confidence,
                evidence=[f"{relative} contains explicit {kind} command"],
                blockers=_command_blockers(repo, argv),
            )
            if kind == "test":
                test_candidates.append(candidate)
            elif kind == "lint":
                lint_candidates.append(candidate)
            elif kind == "format":
                format_candidates.append(candidate)
    return (test_candidates, lint_candidates, format_candidates)


def _extract_known_commands(text: str) -> list[tuple[str, list[str]]]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    extracted: list[tuple[str, list[str]]] = []
    for fragment in _command_fragments(text):
        parsed = _parse_known_command(fragment)
        if parsed is None:
            continue
        kind, argv = parsed
        key = (kind, tuple(argv))
        if key in seen:
            continue
        seen.add(key)
        extracted.append((kind, argv))
    return extracted


def _command_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        candidates = [raw_line.strip()]
        candidates.extend(match.strip() for match in re.findall(r"`([^`]+)`", raw_line))
        for candidate in candidates:
            normalized = _normalize_fragment(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            fragments.append(normalized)
    return fragments


def _normalize_fragment(fragment: str) -> str:
    value = fragment.strip().strip("`").strip()
    value = re.sub(r"^(?:[-*+]|\d+\.)\s+", "", value)
    value = re.sub(r"^run:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:test|lint|format)\s*:\s*", "", value, flags=re.IGNORECASE)
    if value.startswith("$"):
        value = value[1:].strip()
    return value


def _parse_known_command(fragment: str) -> tuple[str, list[str]] | None:
    tokens = fragment.split()
    if len(tokens) < 2:
        return None
    executable = tokens[0]
    if executable in {
        "python",
        "python3",
        "python3.10",
        "python3.11",
        "python3.12",
        ".venv/bin/python",
        "venv/bin/python",
        ".venv/Scripts/python.exe",
        "venv/Scripts/python.exe",
    } and len(tokens) >= 3 and tokens[1] == "-m":
        module = tokens[2]
        remainder = tokens[3:]
        if module in {"pytest", "unittest"}:
            return ("test", [executable, "-m", module, *remainder])
        if module == "ruff" and remainder and remainder[0] in {"check", "format"}:
            return ("lint" if remainder[0] == "check" else "format", [executable, "-m", "ruff", *remainder])
        if module == "black":
            return ("format", [executable, "-m", "black", *remainder])
        return None
    if executable == "pytest":
        return ("test", tokens)
    if executable == "ruff" and tokens[1] in {"check", "format"}:
        return ("lint" if tokens[1] == "check" else "format", tokens)
    if executable == "black":
        return ("format", tokens)
    if executable == "cargo" and tokens[1] in {"test", "fmt"}:
        return ("test" if tokens[1] == "test" else "format", tokens[:2])
    if executable == "cargo" and tokens[1] == "clippy":
        if tokens == ["cargo", "clippy"] or tokens == ["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"]:
            return ("lint", tokens)
        return None
    if executable == "go" and tokens[:3] == ["go", "test", "./..."]:
        return ("test", ["go", "test", "./..."])
    if executable in {"./gradlew", "./mvnw"} and tokens[:2] == [executable, "test"]:
        return ("test", [executable, "test"])
    if executable in {"npm", "pnpm"}:
        if tokens[:2] == [executable, "test"]:
            return ("test", [executable, "test"])
        if tokens[:3] == [executable, "run", "test"]:
            return ("test", [executable, "run", "test"])
        if tokens[:3] == [executable, "run", "lint"]:
            return ("lint", [executable, "run", "lint"])
        if tokens[:3] == [executable, "run", "format"]:
            return ("format", [executable, "run", "format"])
        if executable == "pnpm" and tokens[:2] == ["pnpm", "lint"]:
            return ("lint", ["pnpm", "run", "lint"])
        if executable == "pnpm" and tokens[:2] == ["pnpm", "format"]:
            return ("format", ["pnpm", "run", "format"])
        return None
    if executable == "yarn" and tokens[1] in {"test", "lint", "format"}:
        kind = "test" if tokens[1] == "test" else ("lint" if tokens[1] == "lint" else "format")
        return (kind, ["yarn", tokens[1]])
    return None


def _command_blockers(repo: Path, argv: list[str]) -> list[str]:
    if not argv:
        return ["validation command argv is empty"]
    command = argv[0]
    blockers: list[str] = []
    if command in {".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"}:
        if not (repo / command).exists():
            blockers.append(f"{command} is not available")
    elif command in {"python", "python3", "python3.10", "python3.11", "python3.12", "pytest", "ruff", "black"}:
        if command not in {"python", "python3"} and shutil.which(command) is None:
            blockers.append(f"{command} is not installed")
    elif command in {"npm", "pnpm", "yarn"}:
        if shutil.which(command) is None:
            blockers.append(f"{command} is not installed")
        if not (repo / "node_modules").exists():
            blockers.append("node_modules is not installed")
    elif command == "cargo":
        if shutil.which("cargo") is None:
            blockers.append("cargo is not installed")
    elif command == "go":
        if shutil.which("go") is None:
            blockers.append("go toolchain is not installed")
    elif command in {"./gradlew", "./mvnw"}:
        if not (repo / command).exists():
            blockers.append(f"{command} is not available")
    return blockers


def _discover_python_lint(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    pyproject = _read_text_if_exists(repo / "pyproject.toml") or ""
    if "[tool.ruff]" not in pyproject and "ruff" not in pyproject and "ruff" not in (_read_text_if_exists(repo / "requirements.txt") or ""):
        return []
    interpreter = _python_interpreter(repo)
    blockers = _python_blockers(repo, interpreter)
    command_prefix = [interpreter] if interpreter else ["python"]
    evidence.append("ruff configuration or dependency detected")
    return [
        DiscoveredCommand(
            kind="lint",
            command=ValidationCommand(kind="lint", argv=[*command_prefix, "-m", "ruff", "check", "."]),
            source="python-ruff",
            confidence=0.9 if interpreter else 0.78,
            evidence=["ruff configuration or dependency detected"],
            blockers=blockers,
        )
    ]


def _discover_python_format(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    pyproject = _read_text_if_exists(repo / "pyproject.toml") or ""
    if "[tool.ruff]" not in pyproject and "ruff" not in pyproject and "ruff" not in (_read_text_if_exists(repo / "requirements.txt") or ""):
        return []
    interpreter = _python_interpreter(repo)
    blockers = _python_blockers(repo, interpreter)
    command_prefix = [interpreter] if interpreter else ["python"]
    return [
        DiscoveredCommand(
            kind="format",
            command=ValidationCommand(kind="format", argv=[*command_prefix, "-m", "ruff", "format", "."]),
            source="python-ruff",
            confidence=0.88 if interpreter else 0.76,
            evidence=["ruff configuration or dependency detected"],
            blockers=blockers,
        )
    ]


def _python_interpreter(repo: Path) -> str | None:
    for candidate in (
        repo / ".venv" / "bin" / "python",
        repo / "venv" / "bin" / "python",
        repo / ".venv" / "Scripts" / "python.exe",
        repo / "venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate.relative_to(repo).as_posix()
    return None


def _python_blockers(repo: Path, interpreter: str | None) -> list[str]:
    blockers: list[str] = []
    if interpreter is None and any((repo / name).exists() for name in ("pyproject.toml", "requirements.txt", "pytest.ini", "tox.ini")):
        blockers.append("repo-local Python interpreter was not detected")
    if interpreter and (repo / "requirements.txt").exists() and not (repo / ".venv").exists():
        blockers.append("repo-local virtualenv is not available")
    return blockers


def _discover_node(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    pkg = _read_package_json(repo / "package.json")
    if pkg is None:
        return []
    evidence.append("package.json exists")
    scripts = pkg.get("scripts") or {}
    manager = _package_manager(pkg)
    manager_available = shutil.which(manager) is not None
    blockers = _node_blockers(repo, manager, manager_available)
    command = _node_script_command(manager, "test", scripts)
    if command is None:
        return []
    return [
        DiscoveredCommand(
            kind="test",
            command=ValidationCommand(kind="test", argv=command),
            source="package-json-script",
            confidence=0.96,
            evidence=[f"package.json defines test script and package manager {manager}"],
            blockers=blockers,
        )
    ]


def _discover_node_lint(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    pkg = _read_package_json(repo / "package.json")
    if pkg is None:
        return []
    scripts = pkg.get("scripts") or {}
    manager = _package_manager(pkg)
    manager_available = shutil.which(manager) is not None
    command = _node_script_command(manager, "lint", scripts)
    if command is None:
        return []
    return [
        DiscoveredCommand(
            kind="lint",
            command=ValidationCommand(kind="lint", argv=command),
            source="package-json-script",
            confidence=0.9,
            evidence=[f"package.json defines lint script and package manager {manager}"],
            blockers=_node_blockers(repo, manager, manager_available),
        )
    ]


def _discover_node_format(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    pkg = _read_package_json(repo / "package.json")
    if pkg is None:
        return []
    scripts = pkg.get("scripts") or {}
    manager = _package_manager(pkg)
    manager_available = shutil.which(manager) is not None
    command = _node_script_command(manager, "format", scripts)
    if command is None:
        return []
    return [
        DiscoveredCommand(
            kind="format",
            command=ValidationCommand(kind="format", argv=command),
            source="package-json-script",
            confidence=0.88,
            evidence=[f"package.json defines format script and package manager {manager}"],
            blockers=_node_blockers(repo, manager, manager_available),
        )
    ]


def _read_package_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _package_manager(pkg: dict) -> str:
    raw = str(pkg.get("packageManager") or "").strip()
    for manager in PACKAGE_MANAGERS:
        if raw.startswith(f"{manager}@"):
            return manager
    for manager in PACKAGE_MANAGERS:
        if pkg.get("scripts", {}).get(manager):
            return manager
    return "npm"


def _node_script_command(manager: str, script_name: str, scripts: dict) -> list[str] | None:
    if script_name not in scripts:
        return None
    if manager == "yarn":
        return ["yarn", script_name]
    if script_name == "test":
        return [manager, "test"]
    return [manager, "run", script_name]


def _node_blockers(repo: Path, manager: str, manager_available: bool) -> list[str]:
    blockers: list[str] = []
    if not manager_available:
        blockers.append(f"{manager} is not installed")
    if not (repo / "node_modules").exists():
        blockers.append("node_modules is not installed")
    return blockers


def _discover_rust(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    if not (repo / "Cargo.toml").exists():
        return []
    evidence.append("Cargo.toml exists")
    return [
        DiscoveredCommand(
            kind="test",
            command=ValidationCommand(kind="test", argv=["cargo", "test"]),
            source="cargo-manifest",
            confidence=0.9,
            evidence=["Cargo.toml exists"],
            blockers=[] if shutil.which("cargo") else ["cargo is not installed"],
        )
    ]


def _discover_rust_lint(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    if not (repo / "Cargo.toml").exists():
        return []
    workflow_text = _workflow_text(repo)
    if "cargo clippy" not in workflow_text:
        return []
    return [
        DiscoveredCommand(
            kind="lint",
            command=ValidationCommand(kind="lint", argv=["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"]),
            source="ci-workflow",
            confidence=0.85,
            evidence=["CI workflow runs cargo clippy"],
            blockers=[] if shutil.which("cargo") else ["cargo is not installed"],
        )
    ]


def _discover_rust_format(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    if not (repo / "Cargo.toml").exists():
        return []
    return [
        DiscoveredCommand(
            kind="format",
            command=ValidationCommand(kind="format", argv=["cargo", "fmt"]),
            source="cargo-manifest",
            confidence=0.82,
            evidence=["Cargo.toml exists"],
            blockers=[] if shutil.which("cargo") else ["cargo is not installed"],
        )
    ]


def _discover_go(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    if not (repo / "go.mod").exists():
        return []
    evidence.append("go.mod exists")
    return [
        DiscoveredCommand(
            kind="test",
            command=ValidationCommand(kind="test", argv=["go", "test", "./..."]),
            source="go-module",
            confidence=0.89,
            evidence=["go.mod exists"],
            blockers=[] if shutil.which("go") else ["go toolchain is not installed"],
        )
    ]


def _discover_java(repo: Path, evidence: list[str]) -> list[DiscoveredCommand]:
    candidates: list[DiscoveredCommand] = []
    if (repo / "gradlew").exists():
        evidence.append("gradlew exists")
        candidates.append(
            DiscoveredCommand(
                kind="test",
                command=ValidationCommand(kind="test", argv=["./gradlew", "test"]),
                source="gradle-wrapper",
                confidence=0.95,
                evidence=["gradlew exists"],
                blockers=[],
            )
        )
    if (repo / "mvnw").exists():
        evidence.append("mvnw exists")
        candidates.append(
            DiscoveredCommand(
                kind="test",
                command=ValidationCommand(kind="test", argv=["./mvnw", "test"]),
                source="maven-wrapper",
                confidence=0.94,
                evidence=["mvnw exists"],
                blockers=[],
            )
        )
    return candidates


def _workflow_text(repo: Path) -> str:
    workflows = repo / ".github" / "workflows"
    if not workflows.exists():
        return ""
    parts: list[str] = []
    for path in sorted(workflows.glob("*.y*ml")):
        parts.append(_read_text_if_exists(path) or "")
    return "\n".join(parts)


def _select_candidate(candidates: list[DiscoveredCommand]) -> DiscoveredCommand | None:
    return next((candidate for candidate in candidates if not candidate.blockers), None)


def _rank_candidates(candidates: list[DiscoveredCommand]) -> list[DiscoveredCommand]:
    source_priority = {
        "repo-doc": 6,
        "ci-workflow-explicit": 5,
        "package-json-script": 4,
        "gradle-wrapper": 4,
        "maven-wrapper": 4,
        "python-config": 3,
        "ci-workflow": 3,
        "python-tests-layout": 2,
        "cargo-manifest": 2,
        "go-module": 2,
    }
    return sorted(
        candidates,
        key=lambda item: (
            source_priority.get(item.source, 0),
            -item.confidence,
            len(item.blockers),
            item.source,
            " ".join(item.command.argv),
        ),
        reverse=True,
    )


def _dedupe_candidates(candidates: list[DiscoveredCommand]) -> list[DiscoveredCommand]:
    ordered: list[DiscoveredCommand] = []
    seen: set[tuple[str, tuple[str, ...], str, str]] = set()
    for candidate in candidates:
        key = (
            candidate.kind,
            tuple(candidate.command.argv),
            candidate.command.working_dir,
            candidate.source,
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def _collect_blockers(state: ValidationDiscoveryState) -> list[str]:
    blockers: list[str] = []
    for bucket in (state.test_candidates, state.lint_candidates, state.format_candidates):
        for candidate in bucket:
            for blocker in candidate.blockers:
                if blocker not in blockers:
                    blockers.append(blocker)
    return blockers


def _repo_fingerprint(repo: Path) -> str:
    relevant: list[tuple[str, str]] = []
    for relative in (
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "tox.ini",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradlew",
        "mvnw",
        "AGENTS.md",
        "README.md",
        ".venv/bin/python",
        ".venv/Scripts/python.exe",
    ):
        path = repo / relative
        if not path.exists():
            continue
        if path.is_file():
            relevant.append((relative, path.read_text(encoding="utf-8", errors="replace")[:4096]))
    workflows = repo / ".github" / "workflows"
    if workflows.exists():
        for path in sorted(workflows.glob("*.y*ml")):
            relative = path.relative_to(repo).as_posix()
            relevant.append((relative, path.read_text(encoding="utf-8", errors="replace")[:4096]))
    if (repo / "node_modules").exists():
        relevant.append(("node_modules", "present"))
    digest = hashlib.sha1()
    for relative, content in relevant:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def _read_text_if_exists(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
