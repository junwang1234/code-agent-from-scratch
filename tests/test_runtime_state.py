from __future__ import annotations

from src.runtime.memory_manager import AgentMemory
from src.models import DiscoveredCommand, FactItem, FileContext, ReadRange, SuccessCriterionStatus, Task, ValidationCommand, ValidationDiscoveryState
from src.runtime.memory_manager import build_incremental_prompt_state, build_snapshot_prompt_state, create_memory, reduce_memory
from src.runtime.observation_analysis import facts_from_shell_query, summarize_shell_query
from src.runtime.tool_outcomes import apply_file_range_outcome
from src.tools.core import ReadFileRangeToolResult
from src.tools.shell import ShellQueryResult

from tests.helpers import RepoTestCase, make_edit_plan, make_plan, make_tool_action


def _discovered(kind: str, argv: list[str], *, source: str = "repo-hint") -> DiscoveredCommand:
    return DiscoveredCommand(
        kind=kind,
        command=ValidationCommand(kind=kind, argv=argv),
        source=source,
        confidence=0.9,
    )


class RuntimeStateTest(RepoTestCase):
    def test_orchestrator_merges_codex_fact_updates(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        merge_action = make_tool_action(
            step_id="step_2",
            reason="merge facts",
            tool_name="rg_probe",
            tool_input={"pattern": "def ", "paths": ["app/routes/auth.py"]},
            fact_updates=[FactItem(statement="app/routes/auth.py contains the token-issuing auth flow.", files=["app/routes/auth.py"], confidence="high", status="confirmed", source="codex")],
        )
        memory.apply_action_updates(merge_action)
        self.assertTrue(any(fact.source == "codex" for fact in memory.state.facts))
        self.assertTrue(any(fact.status == "confirmed" for fact in memory.state.facts))

    def test_fact_update_can_retract_existing_fact(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.facts.append(FactItem(statement="README.md is the primary entrypoint.", files=["README.md"], confidence="medium", status="confirmed", source="local"))
        retract_action = make_tool_action(
            step_id="step_2",
            reason="retract stale fact",
            tool_name="head_file",
            tool_input={"paths": ["README.md"], "lines": 20},
            fact_updates=[FactItem(statement="README.md is the primary entrypoint.", files=["README.md"], confidence="low", status="retracted", source="codex")],
        )
        memory.apply_action_updates(retract_action)
        retracted = next(fact for fact in memory.state.facts if fact.statement == "README.md is the primary entrypoint.")
        self.assertEqual(retracted.status, "retracted")
        self.assertEqual(retracted.source, "codex")

    def test_reduce_memory_builds_working_summary_and_open_questions(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.facts.extend([FactItem("SKILL.md is the primary top-level document.", ["SKILL.md"], "high")])
        memory.state.unknowns.append("Need to inspect tests.")
        reduce_memory(memory)
        self.assertIn("SKILL.md is the primary top-level document.", memory.state.working_summary)
        self.assertTrue(memory.state.open_questions)

    def test_build_snapshot_prompt_state_is_reduced(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        for index in range(8):
            memory.state.note_observation("head_file", f"head_file paths=file{index}.md lines=20", f"summary {index}", [f"highlight {index}"])
        reduce_memory(memory)
        state = build_snapshot_prompt_state(memory, 3)
        self.assertIn("working_summary", state)
        self.assertIn("open_questions", state)
        self.assertLessEqual(len(state["recent_observations"]), 4)

    def test_record_file_range_merges_read_coverage_into_file_context(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.current_step_id = "step_1"
        lines = (self.repo / "app" / "long_module.py").read_text(encoding="utf-8").splitlines()
        apply_file_range_outcome(
            memory,
            result=ReadFileRangeToolResult(
                path="app/long_module.py",
                start_line=1,
                end_line=20,
                excerpt="\n".join(lines[:20]),
            ),
        )
        apply_file_range_outcome(
            memory,
            result=ReadFileRangeToolResult(
                path="app/long_module.py",
                start_line=10,
                end_line=30,
                excerpt="\n".join(lines[9:30]),
            ),
        )
        context = memory.state.file_contexts["app/long_module.py"]
        self.assertEqual(len(context.read_ranges), 1)
        self.assertEqual(context.read_ranges[0].start_line, 1)
        self.assertEqual(context.read_ranges[0].end_line, 30)
        self.assertTrue(context.excerpts)
        self.assertIn("alpha", "".join(context.symbols_seen))

    def test_agent_session_create_reduces_memory_immediately(self) -> None:
        plan = make_plan("Explain the architecture")
        session = AgentMemory.create(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory = session.state
        self.assertTrue(memory.working_summary)
        self.assertTrue(memory.open_questions)

    def test_build_snapshot_prompt_state_includes_file_contexts(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.file_contexts["app/routes/auth.py"] = FileContext(path="app/routes/auth.py", read_ranges=[ReadRange(start_line=1, end_line=80)], last_summary="auth helper file", patch_ready=True)
        state = build_snapshot_prompt_state(memory, 3)
        self.assertTrue(state["file_contexts"])
        self.assertEqual(state["file_contexts"][0]["path"], "app/routes/auth.py")
        self.assertIn("app/routes/auth.py", state["patch_ready_files"])

    def test_shell_query_summary_and_facts_capture_targeted_matches(self) -> None:
        result = ShellQueryResult(
            command="rg",
            args=["-n", "workflow|scripts/", "SKILL.md"],
            output=[
                "SKILL.md:12:Use scripts/agent_rebuttal.py as the workflow entrypoint.",
                "SKILL.md:18:Run python scripts/agent_rebuttal.py --mode quick.",
            ],
            truncated=False,
            exit_code=0,
        )
        summary, highlights = summarize_shell_query(result)
        facts = facts_from_shell_query(result, highlights)
        self.assertIn("returned 2 raw line(s)", summary)
        self.assertTrue(any("scripts/agent_rebuttal.py" in item for item in highlights))
        self.assertTrue(any("references implementation path scripts/agent_rebuttal.py" in fact.statement for fact in facts))

    def test_build_snapshot_prompt_state_keeps_raw_shell_output(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.current_step_id = "step_1"
        memory.state.note_observation("rg_search", "rg_search pattern=token paths=src/auth.py", "rg returned 1 raw line(s).", ["src/auth.py:2:    return 'token'"], raw_output=["src/auth.py:2:    return 'token'"], metadata={"command": "rg", "exit_code": 0})
        reduce_memory(memory)
        state = build_snapshot_prompt_state(memory, 3)
        self.assertEqual(state["recent_observations"][0]["raw_output"], ["src/auth.py:2:    return 'token'"])
        self.assertEqual(state["recent_observations"][0]["metadata"]["command"], "rg")

    def test_build_incremental_prompt_state_keeps_only_latest_delta(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.current_step_id = "step_1"
        memory.state.note_observation("list_tree", "depth=2", "tree summary", ["README.md"], raw_output=["README.md"], metadata={"command": "tree"})
        memory.state.note_observation("rg_probe", "rg_probe pattern=token paths=src/auth.py", "rg returned 1 raw line(s).", ["src/auth.py:2:return token"], raw_output=["src/auth.py:2:return token"], metadata={"command": "rg"})
        memory.state.last_completed_step_ids = ["step_1"]
        memory.state.last_criterion_updates = [SuccessCriterionStatus(criterion="Top-level structure is mapped to concrete paths.", status="met", note="Mapped with list_tree.")]
        reduce_memory(memory)
        state = build_incremental_prompt_state(memory, 3)
        self.assertEqual(state["latest_observation"]["tool"], "rg_probe")
        self.assertEqual(state["latest_observation"]["raw_output"], ["src/auth.py:2:return token"])
        self.assertEqual(state["completed_steps_delta"], ["step_1"])
        self.assertNotIn("recent_observations", state)
        self.assertNotIn("task", state)
        self.assertNotIn("goal", state)
        self.assertNotIn("latest_fact_updates", state)

    def test_reduce_memory_archives_completed_step_raw_output(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.current_step_id = "step_1"
        memory.state.note_observation("find_paths", "find_paths paths=scripts max_depth=2 file_type=f name_glob=*.py", "find returned 3 raw line(s).", ["scripts/main.py", "scripts/lib.py"], raw_output=["scripts/main.py", "scripts/lib.py", "scripts/util.py"], metadata={"command": "find"})
        memory.state.plan.steps[0].status = "completed"
        reduce_memory(memory)
        state = build_snapshot_prompt_state(memory, 3)
        self.assertEqual(memory.state.observations[0].raw_output, [])
        self.assertTrue(memory.state.archived_step_notes)
        self.assertEqual(state["recent_observations"], [])
        self.assertTrue(state["archived_steps"])

    def test_prompt_state_includes_validation_discovery(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="repo123",
            selected_test=_discovered("test", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], source="python-tests-layout"),
            selected_lint=_discovered("lint", ["python", "-m", "ruff", "check", "."], source="python-ruff"),
            selected_format=_discovered("format", ["python", "-m", "ruff", "format", "."], source="python-ruff"),
            blockers=["repo-local virtualenv is not available"],
            evidence=["tests/ exists", "ruff configuration or dependency detected"],
        )
        snapshot = build_snapshot_prompt_state(memory, 3)
        incremental = build_incremental_prompt_state(memory, 3)
        self.assertEqual(snapshot["validation_discovery"]["repo_fingerprint"], "repo123")
        self.assertEqual(snapshot["validation_discovery"]["selected_test"]["argv"][:3], ["python", "-m", "unittest"])
        self.assertEqual(snapshot["validation_discovery"]["selected_lint"]["source"], "python-ruff")
        self.assertEqual(snapshot["validation_discovery"]["blockers"], ["repo-local virtualenv is not available"])
        self.assertEqual(incremental["validation_discovery"]["selected_format"]["rendered"], "python -m ruff format .")
