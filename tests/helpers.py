from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from src.models import Action, FactItem, PlanStep, StructuredPlan, SuccessCriterionStatus, Task
from src.planning.base import BasePlanner


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class ScriptedPolicy(BasePlanner):
    def __init__(self, plan: StructuredPlan, actions: list[Action]) -> None:
        self.plan = plan
        self.actions = list(actions)
        self.session_id: str | None = None
        self.plan_questions: list[str] = []

    def make_plan(self, task) -> StructuredPlan:
        self.plan_questions.append(task.question)
        return self.plan

    def next_action(self, session, remaining_steps: int) -> Action:
        return self.actions.pop(0)

    def get_session_id(self) -> str | None:
        return self.session_id

    def set_session_id(self, session_id: str | None) -> None:
        self.session_id = session_id


def make_plan(question: str = "Explain the architecture") -> StructuredPlan:
    return StructuredPlan(
        goal=f"Understand the repository well enough to answer: {question.strip()}",
        question_type="repo_understanding",
        steps=[
            PlanStep(
                id="step_1",
                purpose="Map the top-level repository structure and identify candidate files or directories.",
                allowed_tools=["list_tree"],
            ),
            PlanStep(
                id="step_2",
                purpose="Probe representative docs or code files with bounded reads or narrow grep queries.",
                allowed_tools=["head_file", "rg_probe", "rg_files", "find_paths"],
                depends_on=["step_1"],
            ),
            PlanStep(
                id="step_3",
                purpose="Run broader targeted extraction on the most relevant paths surfaced by the probes.",
                allowed_tools=["rg_search", "rg_probe", "find_paths"],
                depends_on=["step_2"],
            ),
            PlanStep(
                id="step_4",
                purpose="Finish with a grounded explanation, evidence, and remaining unknowns.",
                allowed_tools=["finish"],
                depends_on=["step_3"],
            ),
        ],
        success_criteria=[
            "Top-level structure is mapped to concrete paths.",
            "At least one representative doc or code file is inspected.",
            "Major architectural claims are supported by evidence.",
        ],
        constraints=[
            "Use only bounded local exploration tools.",
        ],
        hypotheses=[
            "Top-level structure, a few representative files, and targeted searches should reveal the main architecture.",
        ],
        search_terms=[],
        file_hints=[],
        unknowns_to_resolve=[
            "Which file is the main runtime or operational entrypoint.",
        ],
    )


def make_edit_plan(question: str = "Implement a change") -> StructuredPlan:
    return StructuredPlan(
        goal=f"Modify the repository to complete: {question.strip()}",
        question_type="code_change",
        steps=[
            PlanStep(
                id="step_1",
                purpose="Inspect the target file or nearby files before editing.",
                allowed_tools=["list_files", "read_file_range", "search_code"],
            ),
            PlanStep(
                id="step_2",
                purpose="Apply a bounded change to the target file.",
                allowed_tools=["apply_patch", "write_file"],
                depends_on=["step_1"],
            ),
            PlanStep(
                id="step_3",
                purpose="Validate the change with focused tests or commands.",
                allowed_tools=["run_tests", "run_command", "format_code"],
                depends_on=["step_2"],
            ),
            PlanStep(
                id="step_4",
                purpose="Finish with the change summary and residual risk.",
                allowed_tools=["finish"],
                depends_on=["step_3"],
            ),
        ],
        success_criteria=[
            "The relevant file is inspected before editing.",
            "A bounded code change is applied.",
            "Validation runs after the change.",
        ],
        constraints=[
            "Use only validated local edit and execution tools.",
            "Prefer small diffs.",
        ],
    )


def make_tool_action(
    *,
    step_id: str,
    reason: str,
    tool_name: str,
    tool_input: dict | None = None,
    completed_step_ids: list[str] | None = None,
    criterion_updates: list[SuccessCriterionStatus] | None = None,
    fact_updates: list[FactItem] | None = None,
) -> Action:
    return Action.tool(
        step_id=step_id,
        reason=reason,
        tool_name=tool_name,
        tool_input=tool_input,
        completed_step_ids=completed_step_ids,
        criterion_updates=criterion_updates,
        fact_updates=fact_updates,
    )


def make_finish_action(
    *,
    step_id: str,
    reason: str,
    answer: str = "",
    evidence=None,
    repo_map=None,
    unknowns=None,
    suggested_next_questions=None,
    completed_step_ids: list[str] | None = None,
    criterion_updates: list[SuccessCriterionStatus] | None = None,
    fact_updates: list[FactItem] | None = None,
) -> Action:
    return Action.finish_action(
        step_id=step_id,
        reason=reason,
        answer=answer,
        evidence=evidence,
        repo_map=repo_map,
        unknowns=unknowns,
        suggested_next_questions=suggested_next_questions,
        completed_step_ids=completed_step_ids,
        criterion_updates=criterion_updates,
        fact_updates=fact_updates,
    )


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "app").mkdir()
        (self.repo / "app" / "routes").mkdir(parents=True, exist_ok=True)
        (self.repo / "tests").mkdir()
        (self.repo / "README.md").write_text("# Sample repo\n", encoding="utf-8")
        (self.repo / "app" / "main.py").write_text(
            "from app.routes.auth import login\n\nif __name__ == '__main__':\n    login()\n",
            encoding="utf-8",
        )
        (self.repo / "app" / "routes" / "auth.py").write_text(
            "def login():\n    token = issue_token()\n    return token\n",
            encoding="utf-8",
        )
        (self.repo / "app" / "long_module.py").write_text(
            "\n".join(
                [
                    "def alpha():",
                    "    return 'a'",
                    "",
                    "def beta():",
                    "    return 'b'",
                    "",
                ]
                + [f"VALUE_{index} = {index}" for index in range(1, 80)]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.repo / "tests" / "test_auth.py").write_text(
            "import unittest\nfrom app.routes.auth import login\n\n\nclass AuthTest(unittest.TestCase):\n    def test_login_returns_revised_token(self):\n        self.assertEqual(login(), 'revised-token')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
