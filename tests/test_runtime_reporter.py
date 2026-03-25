from __future__ import annotations

import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from src.models import DiscoveredCommand, Task, ValidationCommand, ValidationDiscoveryState
from src.presentation.runtime_reporter import ANSI_BOLD, ANSI_CYAN, ANSI_DIM, ANSI_GREEN, ANSI_RED, ANSI_RESET, RuntimeReporter
from src.runtime.memory_manager import create_memory
from src.tools import RepoFilesystem
from tests.helpers import make_edit_plan


def _discovered(kind: str, argv: list[str], *, source: str = "repo-hint") -> DiscoveredCommand:
    return DiscoveredCommand(
        kind=kind,
        command=ValidationCommand(kind=kind, argv=argv),
        source=source,
        confidence=0.9,
    )


class RuntimeReporterDiffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "sample.py").write_text("value = 1\n", encoding="utf-8")
        self.tools = RepoFilesystem(self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_report_diff_colors_added_and_deleted_lines(self) -> None:
        write_result = self.tools.apply_patch("src/sample.py", "value = 1", "value = 2")
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)

        reporter.report_diff(write_result)

        output = stream.getvalue()
        self.assertIn(f"{ANSI_RED}-value = 1{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_GREEN}+value = 2{ANSI_RESET}", output)

    def test_report_diff_does_not_treat_file_headers_as_added_or_deleted_lines(self) -> None:
        write_result = self.tools.apply_patch("src/sample.py", "value = 1", "value = 2")
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)

        reporter.report_diff(write_result)

        output = stream.getvalue()
        self.assertIn(f"{ANSI_BOLD}{ANSI_DIM}--- a/src/sample.py{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_BOLD}{ANSI_DIM}+++ b/src/sample.py{ANSI_RESET}", output)
        self.assertNotIn(f"{ANSI_RED}--- a/src/sample.py{ANSI_RESET}", output)
        self.assertNotIn(f"{ANSI_GREEN}+++ b/src/sample.py{ANSI_RESET}", output)

    def test_report_diff_styles_hunk_headers_without_changing_diff_banner(self) -> None:
        write_result = self.tools.apply_patch("src/sample.py", "value = 1", "value = 2")
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)

        reporter.report_diff(write_result)

        output = stream.getvalue()
        self.assertIn("[diff] src/sample.py", output)
        self.assertIn(f"{ANSI_BOLD}{ANSI_CYAN}@@ -1 +1 @@{ANSI_RESET}", output)

    def test_report_action_styles_step_and_action_lines(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)
        action = SimpleNamespace(
            step_id="step_2",
            kind="tool",
            tool_name="head_file",
            tool_input={"paths": ["src/runtime_reporter.py"], "lines": 20},
        )

        reporter.report_action("Inspect runtime reporter output", action)

        output = stream.getvalue()
        self.assertIn(f"{ANSI_BOLD}{ANSI_CYAN}[step]{ANSI_RESET} step_2", output)
        self.assertIn(f"{ANSI_DIM}in progress{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_BOLD}[action]{ANSI_RESET} {ANSI_CYAN}head_file{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_DIM}paths=src/runtime_reporter.py lines=20{ANSI_RESET}", output)

    def test_report_action_repaired_highlights_original_and_repaired_forms(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)
        original = SimpleNamespace(kind="tool", tool_name="rg_probe", tool_input={"pattern": "foo", "paths": ["src"]})
        repaired = SimpleNamespace(kind="tool", tool_name="rg_search", tool_input={"pattern": "foo", "paths": ["src"]})

        reporter.report_action_repaired(original, repaired)

        output = stream.getvalue()
        self.assertIn(f"{ANSI_BOLD}{ANSI_DIM}[repair]{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_RED}rg_probe pattern=foo paths=src{ANSI_RESET}", output)
        self.assertIn(f"{ANSI_GREEN}rg_search pattern=foo paths=src{ANSI_RESET}", output)

    def test_report_step_completion_styles_completed_steps(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)
        memory = SimpleNamespace(
            plan=SimpleNamespace(steps=[SimpleNamespace(id="step_2"), SimpleNamespace(id="step_3")]),
            last_completed_step_ids=["step_2"],
        )

        reporter.report_step_completion(memory)

        output = stream.getvalue()
        self.assertIn("step_2", output)
        self.assertIn("completed", output)

    def test_report_finish_includes_selected_validation_commands_and_blockers(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream)
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), make_edit_plan("Patch auth flow"))
        memory.state.changed_files.add("src/sample.py")
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="repo123",
            selected_test=_discovered("test", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], source="python-tests-layout"),
            blockers=["repo-local virtualenv is not available"],
        )
        response = SimpleNamespace(
            result_kind="edit",
            changed_files=["src/sample.py"],
            validation=["Selected test command: python -m unittest discover -s tests -v via python-tests-layout."],
            risks=["Validation blockers: repo-local virtualenv is not available."],
        )

        reporter.report_finish(memory.state, response, elapsed_seconds=1.2)

        output = stream.getvalue()
        self.assertIn("[summary] selected test: python -m unittest discover -s tests -v via python-tests-layout", output)
        self.assertIn("[summary] validation blockers: repo-local virtualenv is not available", output)


if __name__ == "__main__":
    unittest.main()
