from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from src.models import ValidationCommand
from src.runtime.action_repair import classify_action_exception
from src.runtime.validation import ValidationDiscoveryService, discover_validation_commands
from src.runtime.validation.failures import normalize_validation_failure
from src.runtime.validation.state import collect_validation_blockers, summarize_discovered_command, summarize_discovery_state
from tests.helpers import make_tool_action


class ValidationRuntimeTest(unittest.TestCase):
    def test_model_exports_are_available(self) -> None:
        command = ValidationCommand(kind="test", argv=["python", "-m", "unittest"])
        self.assertEqual(command.kind, "test")

    def test_python_discovery_selects_repo_local_unittest_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "tests").mkdir()
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            (repo / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
            (repo / "tests" / "test_smoke.py").write_text("import unittest\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertIsNotNone(discovery.selected_test)
        self.assertEqual(discovery.selected_test.command.argv[:3], [".venv/bin/python", "-m", "unittest"])
        self.assertFalse(discovery.selected_test.blockers)
        self.assertTrue(any("tests/ exists" == item for item in discovery.evidence))

    def test_repo_doc_explicit_command_beats_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            (repo / "tests").mkdir()
            (repo / "AGENTS.md").write_text("Run validation with `.venv/bin/python -m pytest -q`.\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_test.source, "repo-doc")
        self.assertEqual(discovery.selected_test.command.argv, [".venv/bin/python", "-m", "pytest", "-q"])

    def test_workflow_explicit_command_extracts_python_test_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "test.yml").write_text("jobs:\n  test:\n    steps:\n      - run: .venv/bin/python -m pytest -q\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_test.source, "ci-workflow-explicit")
        self.assertEqual(discovery.selected_test.command.argv, [".venv/bin/python", "-m", "pytest", "-q"])

    def test_python_discovery_selects_pytest_when_config_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertIsNotNone(discovery.selected_test)
        self.assertEqual(discovery.selected_test.command.argv[:4], [".venv/bin/python", "-m", "pytest", "-q"])

    def test_python_discovery_exposes_ruff_lint_and_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_lint.command.argv, [".venv/bin/python", "-m", "ruff", "check", "."])
        self.assertEqual(discovery.selected_format.command.argv, [".venv/bin/python", "-m", "ruff", "format", "."])

    def test_python_discovery_records_blockers_when_interpreter_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "tests").mkdir()
            (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertIsNone(discovery.selected_test)
        self.assertIn("repo-local Python interpreter was not detected", discovery.blockers)

    def test_node_discovery_uses_package_manager_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("src.runtime.validation.discovery.shutil.which", return_value="/usr/bin/pnpm"):
            repo = Path(temp_dir)
            (repo / "package.json").write_text(
                '{\n  "packageManager": "pnpm@9.0.0",\n  "scripts": {"test": "vitest run", "format": "prettier --write .", "lint": "eslint ."}\n}\n',
                encoding="utf-8",
            )

            discovery = discover_validation_commands(repo)

        self.assertIsNone(discovery.selected_test)
        self.assertEqual(discovery.test_candidates[0].command.argv, ["pnpm", "test"])
        self.assertEqual(discovery.lint_candidates[0].command.argv, ["pnpm", "run", "lint"])
        self.assertEqual(discovery.format_candidates[0].command.argv, ["pnpm", "run", "format"])
        self.assertIn("node_modules is not installed", discovery.blockers)

    def test_java_discovery_prefers_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            wrapper = repo / "gradlew"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o755)

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_test.command.argv, ["./gradlew", "test"])

    def test_rust_discovery_picks_test_lint_and_format_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("src.runtime.validation.discovery.shutil.which", return_value="/usr/bin/cargo"):
            repo = Path(temp_dir)
            (repo / "Cargo.toml").write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "test.yml").write_text("jobs:\n  test:\n    steps:\n      - run: cargo clippy\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_test.command.argv, ["cargo", "test"])
        self.assertEqual(discovery.selected_lint.command.argv[:2], ["cargo", "clippy"])
        self.assertEqual(discovery.selected_format.command.argv, ["cargo", "fmt"])

    def test_go_discovery_uses_go_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("src.runtime.validation.discovery.shutil.which", return_value="/usr/bin/go"):
            repo = Path(temp_dir)
            (repo / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")

            discovery = discover_validation_commands(repo)

        self.assertEqual(discovery.selected_test.command.argv, ["go", "test", "./..."])

    def test_service_caches_by_repo_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "tests").mkdir()
            (repo / ".venv" / "bin").mkdir(parents=True)
            wrapper = repo / ".venv" / "bin" / "python"
            wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
            wrapper.chmod(0o755)
            service = ValidationDiscoveryService()

            first = service.discover(repo)
            second = service.discover(repo)

            self.assertIs(first, second)
            before = first.repo_fingerprint
            (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            after = service.discover(repo)

        self.assertNotEqual(before, after.repo_fingerprint)
        self.assertIsNot(first, after)

    def test_state_helpers_and_failure_normalization_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / "tests").mkdir()
            (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
            discovery = discover_validation_commands(repo)

        summary = summarize_discovery_state(discovery)
        self.assertEqual(summary["repo_fingerprint"], discovery.repo_fingerprint)
        self.assertTrue(collect_validation_blockers(discovery))
        self.assertEqual(normalize_validation_failure("No module named pytest"), "missing_dependency")
        self.assertEqual(normalize_validation_failure("AssertionError: expected x", fallback_kind="test_failure"), "test_failure")
        self.assertEqual(normalize_validation_failure("No validation command selected for lint/build execution."), "no_discovered_command")
        self.assertEqual(normalize_validation_failure("Unsupported command: bash"), "invalid_command")
        self.assertEqual(normalize_validation_failure("Explicit approval required before running setup/install command: npm install"), "env_setup_failure")
        self.assertEqual(summarize_discovered_command(discovery.lint_candidates[0]), "python -m ruff check . via python-ruff")

    def test_run_command_failure_classifies_no_discovered_command(self) -> None:
        action = make_tool_action(step_id="step_3", tool_name="run_command", tool_input={}, reason="Run lint.")
        error = classify_action_exception(action, ValueError("No validation command selected for lint/build execution."))
        self.assertEqual(error.failure_kind, "no_discovered_command")

    def test_run_command_failure_classifies_setup_approval_blocker(self) -> None:
        action = make_tool_action(step_id="step_3", tool_name="run_command", tool_input={"argv": ["npm", "install"]}, reason="Install first.")
        error = classify_action_exception(action, ValueError("Explicit approval required before running setup/install command: npm install"))
        self.assertEqual(error.failure_kind, "env_setup_failure")


if __name__ == "__main__":
    unittest.main()
