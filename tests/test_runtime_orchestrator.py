from __future__ import annotations

import io
from unittest.mock import patch

from src.models import Action, ActionExecutionError, FactItem, FinishPayload, SuccessCriterionStatus, Task, ToolCall
from src.presentation.responder import render_markdown
from src.presentation.runtime_reporter import RuntimeReporter
from src.runtime.action_execution import ActionExecutor
from src.runtime.agent_runtime import AgentRuntime
from src.runtime.memory_manager import build_incremental_prompt_state, build_snapshot_prompt_state, create_memory, reduce_memory
from src.runtime.result_composer import compose_response
from src.tools import RepoFilesystem

from tests.helpers import RepoTestCase, ScriptedPolicy, make_edit_plan, make_finish_action, make_plan, make_tool_action, strip_ansi


def _approved_bash_result(argv: list[str]) -> object:
    from src.tools.shell import CommandResult

    return CommandResult(command=argv[0], args=argv[1:], output=["ok"], truncated=False, exit_code=0, execution_mode="approved_bash")


class RuntimeOrchestratorTest(RepoTestCase):
    def test_list_tree_detects_small_tooling_repo(self) -> None:
        skill_repo = self.repo / "skill"
        skill_repo.mkdir()
        (skill_repo / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (skill_repo / "references").mkdir()
        (skill_repo / "scripts").mkdir()
        (skill_repo / "tests").mkdir()
        tree = RepoFilesystem(skill_repo).list_tree(depth=1)
        self.assertIn("SKILL.md", tree)
        self.assertIn("scripts/", tree)
        self.assertIn("references/", tree)

    def test_orchestrator_runs_local_react_loop(self) -> None:
        plan = make_plan("Find the auth flow")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Scan repo"),
            make_tool_action(
                step_id="step_2",
                tool_name="rg_probe",
                tool_input={"pattern": "def |token|login", "paths": ["app/routes/auth.py"]},
                reason="Probe auth file.",
                completed_step_ids=["step_1"],
            ),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered",
                answer="The auth flow appears to center on app/routes/auth.py.",
                evidence=[],
                repo_map=[],
                unknowns=["Session persistence is still unclear."],
                suggested_next_questions=["Where is token validation performed?"],
                completed_step_ids=["step_2"],
                criterion_updates=[
                    SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied during the scripted run.")
                    for criterion in plan.success_criteria
                ],
            ),
        ]
        response = AgentRuntime(step_budget=6, planner=ScriptedPolicy(plan, actions)).run(
            Task(repo_path=self.repo, question="Find the auth flow")
        )
        markdown = render_markdown(response)
        self.assertIn("The auth flow appears to center on app/routes/auth.py.", markdown)
        self.assertIn("## Success Criteria", markdown)
        self.assertIn("[met]", markdown)

    def test_orchestrator_run_with_artifacts_returns_facts_explicitly(self) -> None:
        plan = make_plan("Explain the architecture")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Scan repo"),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered",
                answer="Architecture summary.",
                completed_step_ids=["step_1"],
                criterion_updates=[
                    SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied during the scripted run.")
                    for criterion in plan.success_criteria[:2]
                ],
                fact_updates=[FactItem(statement="README.md is present.", files=["README.md"])],
            ),
        ]
        outcome = AgentRuntime(step_budget=6, planner=ScriptedPolicy(plan, actions)).run_with_artifacts(
            Task(repo_path=self.repo, question="Explain the architecture")
        )
        self.assertEqual(outcome.result.primary_text, "Architecture summary.")
        self.assertTrue(outcome.artifacts.facts)
        self.assertEqual(outcome.artifacts.facts[-1].statement, "README.md is present.")

    def test_unified_run_can_execute_understanding_task(self) -> None:
        plan = make_plan("Explain the architecture")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Scan repo"),
            make_tool_action(
                step_id="step_2",
                tool_name="head_file",
                tool_input={"paths": ["README.md"], "lines": 20},
                reason="Inspect one representative file.",
                completed_step_ids=["step_1"],
            ),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered",
                answer="Architecture summary.",
                completed_step_ids=["step_2"],
                criterion_updates=[
                    SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied during the unified scripted run.")
                    for criterion in plan.success_criteria
                ],
            ),
        ]
        response = AgentRuntime(step_budget=4, planner=ScriptedPolicy(plan, actions)).run(
            Task(repo_path=self.repo, question="Explain the architecture")
        )
        markdown = render_markdown(response)
        self.assertIn("## Answer", markdown)
        self.assertIn("Architecture summary.", markdown)

    def test_compose_response_uses_edit_result_shape_for_edit_runs(self) -> None:
        plan = make_edit_plan("Implement a change")
        memory = create_memory(Task(repo_path=self.repo, question="Implement a change"), plan)
        memory.state.changed_files.update({"app/main.py", "app/routes/auth.py"})
        memory.state.validation_runs.append("pytest tests/test_agent.py")
        memory.state.failures.append("Validation still leaves some residual risk.")
        response = compose_response(memory)
        markdown = render_markdown(response)
        self.assertIn("## Summary", markdown)
        self.assertIn("## Files Changed", markdown)
        self.assertIn("app/main.py", markdown)
        self.assertIn("pytest tests/test_agent.py", markdown)

    def test_orchestrator_runs_edit_loop_with_patch_and_validation(self) -> None:
        plan = make_edit_plan("Revise the token text")
        actions = [
            make_tool_action(
                step_id="step_1",
                tool_name="read_file_range",
                tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20},
                reason="Inspect the current implementation before patching.",
            ),
            make_tool_action(
                step_id="step_2",
                tool_name="apply_patch",
                tool_input={
                    "path": "app/routes/auth.py",
                    "old_text": "return token",
                    "new_text": "return 'revised-token'",
                },
                reason="Apply the requested bounded change.",
                completed_step_ids=["step_1"],
            ),
            make_tool_action(
                step_id="step_3",
                tool_name="run_tests",
                tool_input={"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]},
                reason="Validate the change.",
                completed_step_ids=["step_2"],
            ),
            make_finish_action(
                step_id="step_4",
                reason="The edit and validation are complete.",
                answer="Updated app/routes/auth.py so login() now returns the revised token and validated it with unittest discovery.",
                unknowns=[],
                completed_step_ids=["step_3"],
                criterion_updates=[
                    SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied during the scripted edit run.")
                    for criterion in plan.success_criteria
                ],
            ),
        ]
        response = AgentRuntime(step_budget=8, planner=ScriptedPolicy(plan, actions)).run(
            Task(repo_path=self.repo, question="Revise the token text")
        )
        markdown = render_markdown(response)
        self.assertIn("## Summary", markdown)
        self.assertIn("app/routes/auth.py", markdown)
        self.assertIn("discover -s tests -v", markdown)
        updated = (self.repo / "app" / "routes" / "auth.py").read_text(encoding="utf-8")
        self.assertIn("revised-token", updated)

    def test_prompt_state_serializes_structured_action_failure(self) -> None:
        plan = make_edit_plan("Retry a failed edit")
        memory = create_memory(Task(repo_path=self.repo, question="Retry a failed edit"), plan)
        failure = ActionExecutionError(
            step_id="step_2",
            tool_name="apply_patch",
            tool_input={"path": "app/routes/auth.py", "old_text": "return missing", "new_text": "return fixed"},
            failure_kind="invalid_input",
            message="Patch target text was not found in app/routes/auth.py",
            raw_output=["Patch target text was not found in app/routes/auth.py"],
            attempt_index=1,
            retryable=False,
        )
        memory.state.action_failures.append(failure)
        memory.state.last_action_failure = failure
        memory.state.failures.append("apply_patch failed (invalid_input) on attempt 1")
        memory.state.retry_counts["apply_patch:path=app/routes/auth.py|new_text=return fixed|old_text=return missing"] = 1
        reduce_memory(memory)

        snapshot = build_snapshot_prompt_state(memory, 3)
        incremental = build_incremental_prompt_state(memory, 3)

        self.assertEqual(snapshot["last_failed_action"]["tool_name"], "apply_patch")
        self.assertEqual(snapshot["recent_action_failures"][0]["failure_kind"], "invalid_input")
        self.assertEqual(incremental["latest_action_failure"]["message"], "Patch target text was not found in app/routes/auth.py")

    def test_orchestrator_records_failure_and_replans_from_context(self) -> None:
        class RecordingScriptedPolicy(ScriptedPolicy):
            def __init__(self, plan, actions) -> None:
                super().__init__(plan, actions)
                self.snapshot_states: list[dict] = []

            def next_action(self, session, remaining_steps: int):
                self.snapshot_states.append(build_snapshot_prompt_state(session, remaining_steps))
                return super().next_action(session, remaining_steps)

        plan = make_edit_plan("Retry a failed edit")
        actions = [
            make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Inspect before editing."),
            make_tool_action(
                step_id="step_2",
                tool_name="apply_patch",
                tool_input={"path": "app/routes/auth.py", "old_text": "return missing-token", "new_text": "return 'revised-token'"},
                reason="Apply the requested change.",
                completed_step_ids=["step_1"],
            ),
            make_tool_action(step_id="step_2", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Reinspect after the failed patch."),
            make_finish_action(
                step_id="step_4",
                reason="The failed patch was investigated and the run can conclude.",
                answer="The initial patch failed because the target text was absent; the file was re-read for a corrected follow-up.",
                unknowns=[],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded after the failure.") for criterion in plan.success_criteria],
            ),
        ]
        policy = RecordingScriptedPolicy(plan, actions)

        response = AgentRuntime(step_budget=6, planner=policy).run(Task(repo_path=self.repo, question="Retry a failed edit"))

        self.assertIn("initial patch failed", response.primary_text.lower())
        self.assertGreaterEqual(len(policy.snapshot_states), 3)
        failed_state = policy.snapshot_states[2]
        self.assertEqual(failed_state["last_failed_action"]["tool_name"], "apply_patch")
        self.assertEqual(failed_state["last_failed_action"]["failure_kind"], "invalid_input")
        self.assertIn("not found", failed_state["last_failed_action"]["message"].lower())
        self.assertTrue(any(key.startswith("apply_patch:") for key in failed_state["retry_counts"]))

    def test_orchestrator_blocks_third_identical_failed_action(self) -> None:
        plan = make_edit_plan("Retry a failed edit")
        actions = [
            make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Inspect before editing."),
            make_tool_action(
                step_id="step_2",
                tool_name="apply_patch",
                tool_input={"path": "app/routes/auth.py", "old_text": "return missing-token", "new_text": "return 'revised-token'"},
                reason="Attempt the patch.",
                completed_step_ids=["step_1"],
            ),
            make_tool_action(step_id="step_2", tool_name="apply_patch", tool_input={"path": "app/routes/auth.py", "old_text": "return missing-token", "new_text": "return 'revised-token'"}, reason="Attempt the patch again."),
            make_tool_action(step_id="step_2", tool_name="apply_patch", tool_input={"path": "app/routes/auth.py", "old_text": "return missing-token", "new_text": "return 'revised-token'"}, reason="Attempt the patch a third time."),
            make_finish_action(
                step_id="step_4",
                reason="Stop after the guarded retry behavior.",
                answer="The orchestrator blocked a third identical failing patch and switched to inspection.",
                unknowns=[],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded after guarded retries.") for criterion in plan.success_criteria],
            ),
        ]
        reporter_output = io.StringIO()
        response = AgentRuntime(step_budget=7, planner=ScriptedPolicy(plan, actions), reporter=RuntimeReporter(stream=reporter_output, level="normal")).run(
            Task(repo_path=self.repo, question="Retry a failed edit")
        )

        self.assertIn("blocked a third identical failing patch", response.primary_text.lower())
        output = reporter_output.getvalue()
        self.assertIn("[repair]", output)
        self.assertIn("read_file_range", output)

    def test_orchestrator_repairs_finish_that_requests_another_tool(self) -> None:
        plan = make_plan("Explain the architecture")
        actions = [
            Action(
                kind="finish",
                step_id="step_1",
                reason="Need deeper tree before finishing.",
                tool_call=ToolCall(tool_name="list_tree", payload={"depth": 2}),
                finish=FinishPayload(answer="Use list_tree first."),
            ),
            make_tool_action(step_id="step_2", tool_name="head_file", tool_input={"paths": ["README.md"], "lines": 20}, reason="Need one representative file read.", completed_step_ids=["step_1"]),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered.",
                answer="The repository centers on app/main.py and app/routes/auth.py.",
                completed_step_ids=["step_2"],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded.") for criterion in plan.success_criteria],
            ),
        ]
        response = AgentRuntime(step_budget=4, planner=ScriptedPolicy(plan, actions)).run(Task(repo_path=self.repo, question="Explain the architecture"))
        self.assertIn("app/main.py", response.answer)

    def test_orchestrator_executes_rg_probe_action(self) -> None:
        skill_repo = self.repo / "skill"
        skill_repo.mkdir()
        (skill_repo / "SKILL.md").write_text("# Skill\nUse scripts/agent_rebuttal.py as the workflow entrypoint.\n", encoding="utf-8")
        (skill_repo / "scripts").mkdir()
        (skill_repo / "scripts" / "agent_rebuttal.py").write_text("def main():\n    pass\n", encoding="utf-8")
        plan = make_plan("Explain the architecture")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Need map."),
            make_tool_action(
                step_id="step_2",
                tool_name="rg_probe",
                tool_input={"pattern": "workflow|scripts/", "paths": ["SKILL.md"]},
                reason="Extract entrypoint references from the skill doc.",
                completed_step_ids=["step_1"],
            ),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered.",
                answer="SKILL.md points to scripts/agent_rebuttal.py as the workflow entrypoint.",
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded.") for criterion in plan.success_criteria],
            ),
        ]
        response = AgentRuntime(step_budget=4, planner=ScriptedPolicy(plan, actions)).run(Task(repo_path=skill_repo, question="Explain the architecture"))
        self.assertIn("scripts/agent_rebuttal.py", response.answer)

    def test_agent_runtime_runs_with_same_outcome_shape_as_orchestrator(self) -> None:
        plan = make_plan("Find the auth flow")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Scan repo"),
            make_finish_action(
                step_id="step_4",
                reason="Enough evidence gathered",
                answer="The auth flow appears to center on app/routes/auth.py.",
                completed_step_ids=["step_1"],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied during the scripted run.") for criterion in plan.success_criteria],
            ),
        ]
        runtime = AgentRuntime(step_budget=6, planner=ScriptedPolicy(plan, actions))
        outcome = runtime.run_with_artifacts(Task(repo_path=self.repo, question="Find the auth flow"))
        self.assertIn("app/routes/auth.py", outcome.result.primary_text)
        self.assertTrue(outcome.artifacts.facts)

    def test_action_executor_normalize_switches_repeated_failed_search(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.inspected_files.add("README.md")
        action = make_tool_action(
            step_id="step_2",
            tool_name="rg_search",
            tool_input={"pattern": "token", "paths": ["README.md"]},
            reason="Repeat failing search.",
        )
        fingerprint = "rg_search:paths=README.md|pattern=token"
        memory.state.retry_counts[fingerprint] = 2
        memory.state.last_action_failure = ActionExecutionError(
            step_id="step_2",
            tool_name="rg_search",
            tool_input={"pattern": "token", "paths": ["README.md"]},
            failure_kind="no_results",
            message="No results.",
            retryable=True,
        )
        executor = ActionExecutor(self.repo)
        repaired = executor.normalize(memory, action, remaining_steps=3)
        self.assertNotEqual(repaired.tool_name, "rg_search")
        self.assertIn(repaired.tool_name, {"rg_probe", "head_file"})

    def test_runtime_reporter_renders_truncated_diff(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream, level="normal")
        from src.models import WriteResult

        reporter.report_diff(
            WriteResult(
                path="src/example.py",
                old_content="\n".join(f"old {index}" for index in range(120)),
                new_content="\n".join(f"new {index}" for index in range(120)),
            ),
            max_lines=10,
        )
        output = stream.getvalue()
        self.assertIn("[diff] src/example.py", output)
        self.assertIn("... diff truncated ...", output)

    def test_runtime_reporter_only_prints_step_header_when_step_changes(self) -> None:
        stream = io.StringIO()
        reporter = RuntimeReporter(stream=stream, level="normal")
        reporter.report_action("Inspect files.", make_tool_action(step_id="step_1", tool_name="head_file", tool_input={"paths": ["README.md"], "lines": 20}, reason="Inspect the file."))
        reporter.report_result("Read README.md")
        reporter.report_action("Inspect files.", make_tool_action(step_id="step_1", tool_name="rg_probe", tool_input={"pattern": "main", "paths": ["src"]}, reason="Probe within the same step."))
        reporter.report_result("Probed src")
        reporter.report_action("Apply patch.", make_tool_action(step_id="step_2", tool_name="apply_patch", tool_input={"path": "src/example.py", "old_text": "old", "new_text": "new"}, reason="Move to the next step."))

        output = strip_ansi(stream.getvalue())
        self.assertEqual(output.count("[step] step_1  in progress  Inspect files."), 1)
        self.assertIn("[step] step_2  in progress  Apply patch.", output)
        self.assertEqual(output.count("[action]"), 3)
        self.assertIn("[result] Read README.md", output)
        self.assertIn("[result] Probed src", output)

    def test_orchestrator_progress_output_includes_plan_actions_diff_and_summary(self) -> None:
        plan = make_edit_plan("Revise the token text")
        actions = [
            make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Inspect before patching."),
            make_tool_action(
                step_id="step_2",
                tool_name="apply_patch",
                tool_input={"path": "app/routes/auth.py", "old_text": "return token", "new_text": "return 'revised-token'"},
                reason="Patch the token return value.",
                completed_step_ids=["step_1"],
            ),
            make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]}, reason="Validate after patching.", completed_step_ids=["step_2"]),
            make_finish_action(
                step_id="step_4",
                reason="The run is complete.",
                answer="Updated the token path and validated it.",
                completed_step_ids=["step_3"],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded.") for criterion in plan.success_criteria],
            ),
        ]
        stream = io.StringIO()
        runtime = AgentRuntime(step_budget=8, planner=ScriptedPolicy(plan, actions), reporter=RuntimeReporter(stream=stream, level="normal"))
        with patch("src.runtime.agent_runtime.time.perf_counter", side_effect=[100.0, 101.2]):
            response = runtime.run(Task(repo_path=self.repo, question="Revise the token text"))
        output = strip_ansi(stream.getvalue())
        self.assertIn("[run] task: Revise the token text", output)
        self.assertIn("[plan] goal:", output)
        self.assertIn("[action] apply_patch", output)
        self.assertIn("[diff] app/routes/auth.py", output)
        self.assertIn("--- a/app/routes/auth.py", output)
        self.assertIn("[summary] completed", output)
        self.assertIn("[summary] elapsed: 1.2s", output)
        self.assertIn("[summary] changed files: app/routes/auth.py", output)
        self.assertIn("## Summary", render_markdown(response))

    def test_orchestrator_quiet_progress_suppresses_runtime_lines_but_keeps_final_markdown(self) -> None:
        plan = make_edit_plan("Revise the token text")
        actions = [
            make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Inspect before patching."),
            make_tool_action(step_id="step_2", tool_name="apply_patch", tool_input={"path": "app/routes/auth.py", "old_text": "return token", "new_text": "return 'revised-token'"}, reason="Patch the token return value.", completed_step_ids=["step_1"]),
            make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]}, reason="Validate after patching.", completed_step_ids=["step_2"]),
            make_finish_action(step_id="step_4", reason="The run is complete.", answer="Updated the token path and validated it.", completed_step_ids=["step_3"], criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded.") for criterion in plan.success_criteria]),
        ]
        stream = io.StringIO()
        runtime = AgentRuntime(step_budget=8, planner=ScriptedPolicy(plan, actions), reporter=RuntimeReporter(stream=stream, level="quiet"))
        with patch("src.runtime.agent_runtime.time.perf_counter", side_effect=[100.0, 101.2]):
            response = runtime.run(Task(repo_path=self.repo, question="Revise the token text"))
        output = stream.getvalue()
        self.assertNotIn("[step]", output)
        self.assertNotIn("[action]", output)
        self.assertNotIn("[result]", output)
        self.assertNotIn("[summary]", output)
        self.assertIn("## Summary", render_markdown(response))

    def test_orchestrator_verbose_progress_output_keeps_plan_header_and_summary(self) -> None:
        plan = make_edit_plan("Revise the token text")
        actions = [
            make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 1, "end_line": 20}, reason="Inspect before patching."),
            make_tool_action(step_id="step_2", tool_name="apply_patch", tool_input={"path": "app/routes/auth.py", "old_text": "return token", "new_text": "return 'revised-token'"}, reason="Patch the token return value.", completed_step_ids=["step_1"]),
            make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]}, reason="Validate after patching.", completed_step_ids=["step_2"]),
            make_finish_action(step_id="step_4", reason="The run is complete.", answer="Updated the token path and validated it.", completed_step_ids=["step_3"], criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Grounded.") for criterion in plan.success_criteria]),
        ]
        stream = io.StringIO()
        runtime = AgentRuntime(step_budget=8, planner=ScriptedPolicy(plan, actions), reporter=RuntimeReporter(stream=stream, level="verbose"))
        with patch("src.runtime.agent_runtime.time.perf_counter", side_effect=[100.0, 101.2]):
            runtime.run(Task(repo_path=self.repo, question="Revise the token text"))
        output = stream.getvalue()
        self.assertIn("[plan] goal:", output)
        self.assertIn("[plan] step_1 pending", output)
        self.assertIn("[summary] completed", output)
        self.assertIn("[summary] elapsed: 1.2s", output)

    def test_runtime_approves_and_retries_unsupported_validation_command_with_bash(self) -> None:
        plan = make_edit_plan("Run bun tests")
        actions = [
            make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={"argv": ["bun", "run", "test:unit"]}, reason="Run the project test command."),
        ] + [make_finish_action(step_id="step_4", reason="Done.", answer="Validated with bun.")] * 4
        runtime = AgentRuntime(step_budget=5, planner=ScriptedPolicy(plan, actions), approval_handler=lambda request: True)
        with patch("src.runtime.validation.failures.shutil.which", return_value="/usr/local/bin/bun"), patch(
            "src.tools.shell.SafeCommandRunner.run_approved_bash",
            return_value=_approved_bash_result(["bun", "run", "test:unit"]),
        ) as approved_bash:
            response = runtime.run(Task(repo_path=self.repo, question="Run bun tests"))
        self.assertIn("Validated with bun.", response.primary_text)
        self.assertEqual(runtime.approved_command_scopes[0].argv, ["bun", "run", "test:unit"])
        approved_bash.assert_called_once()

    def test_runtime_denial_records_failure_and_continues(self) -> None:
        plan = make_edit_plan("Run bun tests")
        actions = [
            make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={"argv": ["bun", "run", "test:unit"]}, reason="Run the project test command."),
        ] + [make_finish_action(step_id="step_4", reason="Done.", answer="Skipped validation after denial.")] * 4
        stream = io.StringIO()
        runtime = AgentRuntime(
            step_budget=5,
            planner=ScriptedPolicy(plan, actions),
            reporter=RuntimeReporter(stream=stream, level="normal"),
            approval_handler=lambda request: False,
        )
        with patch("src.runtime.validation.failures.shutil.which", return_value="/usr/local/bin/bun"):
            response = runtime.run(Task(repo_path=self.repo, question="Run bun tests"))
        self.assertIn("Skipped validation after denial.", response.primary_text)
        self.assertIn("approval_denied", stream.getvalue())

    def test_runtime_install_flow_runs_install_verify_and_retry(self) -> None:
        plan = make_edit_plan("Run bun tests")
        actions = [
            make_tool_action(
                step_id="step_3",
                tool_name="run_tests",
                tool_input={
                    "argv": ["bun", "run", "test:unit"],
                    "install_argv": ["brew", "install", "bun"],
                    "verify_argv": ["bun", "--version"],
                },
                reason="Run the project test command.",
            ),
        ] + [make_finish_action(step_id="step_4", reason="Done.", answer="Installed bun and validated.")] * 4
        runtime = AgentRuntime(step_budget=5, planner=ScriptedPolicy(plan, actions), approval_handler=lambda request: True)
        observed_argv: list[list[str]] = []

        def _fake_bash(self, argv, *, working_dir=".", env_overrides=None):
            observed_argv.append(list(argv))
            return _approved_bash_result(argv)

        with patch("src.runtime.validation.failures.shutil.which", return_value=None), patch("src.tools.shell.SafeCommandRunner.run_approved_bash", new=_fake_bash):
            response = runtime.run(Task(repo_path=self.repo, question="Run bun tests"))
        self.assertIn("Installed bun and validated.", response.primary_text)
        self.assertEqual(observed_argv[0], ["brew", "install", "bun"])
        self.assertEqual(observed_argv[1], ["bun", "--version"])
        self.assertEqual(observed_argv[2], ["bun", "run", "test:unit"])

    def test_runtime_install_flow_accepts_agent_proposed_install_command(self) -> None:
        plan = make_edit_plan("Run foobar tests")
        actions = [
            make_tool_action(
                step_id="step_3",
                tool_name="run_tests",
                tool_input={
                    "argv": ["foobar", "test"],
                    "install_argv": ["brew", "install", "foobar"],
                    "verify_argv": ["foobar", "--version"],
                },
                reason="Run the project test command.",
            ),
        ] + [make_finish_action(step_id="step_4", reason="Done.", answer="Installed foobar and validated.")] * 4
        runtime = AgentRuntime(step_budget=5, planner=ScriptedPolicy(plan, actions), approval_handler=lambda request: True)
        observed_argv: list[list[str]] = []

        def _fake_bash(self, argv, *, working_dir=".", env_overrides=None):
            observed_argv.append(list(argv))
            return _approved_bash_result(argv)

        with patch("src.runtime.validation.failures.shutil.which", return_value=None), patch("src.tools.shell.SafeCommandRunner.run_approved_bash", new=_fake_bash):
            response = runtime.run(Task(repo_path=self.repo, question="Run foobar tests"))
        self.assertIn("Installed foobar and validated.", response.primary_text)
        self.assertEqual(observed_argv[0], ["brew", "install", "foobar"])
        self.assertEqual(observed_argv[1], ["foobar", "--version"])
        self.assertEqual(observed_argv[2], ["foobar", "test"])
