from __future__ import annotations

from src.models import DiscoveredCommand, Task, ValidationCommand, ValidationDiscoveryState
from src.runtime.memory_manager import create_memory
from src.runtime.result_composer import compose_response

from tests.helpers import RepoTestCase, make_edit_plan


def _discovered(kind: str, argv: list[str], *, source: str = "repo-hint") -> DiscoveredCommand:
    return DiscoveredCommand(
        kind=kind,
        command=ValidationCommand(kind=kind, argv=argv),
        source=source,
        confidence=0.9,
    )


class ResultComposerValidationSummaryTest(RepoTestCase):
    def test_compose_response_includes_discovered_validation_commands_and_blockers(self) -> None:
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), make_edit_plan("Patch auth flow"))
        memory.state.changed_files.add("src/auth.py")
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="repo123",
            selected_test=_discovered("test", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], source="python-tests-layout"),
            selected_lint=_discovered("lint", ["python", "-m", "ruff", "check", "."], source="python-ruff"),
            blockers=["repo-local virtualenv is not available"],
        )

        result = compose_response(memory)

        self.assertEqual(result.result_kind, "edit")
        self.assertIn("Selected test command: python -m unittest discover -s tests -v via python-tests-layout.", result.validation)
        self.assertIn("Selected lint command: python -m ruff check . via python-ruff.", result.validation)
        self.assertEqual(result.risks[0], "Validation blockers: repo-local virtualenv is not available.")
