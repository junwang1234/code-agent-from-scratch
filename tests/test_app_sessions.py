from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from src.app.main import build_parser, run_interactive
from src.app.session_store import InteractiveSession, SessionTurn, find_session_by_id, list_sessions, load_session, save_session
from src.app.task_builder import build_context_prefix, build_task_question
from src.models import FactItem, SuccessCriterionStatus
from src.presentation.runtime_reporter import RuntimeReporter
from src.runtime.agent_runtime import AgentRuntime

from tests.helpers import RepoTestCase, ScriptedPolicy, make_finish_action, make_plan, make_tool_action


class AppSessionsTest(RepoTestCase):
    def test_task_builder_uses_interactive_session_context(self) -> None:
        session = InteractiveSession(
            repo_path=str(self.repo),
            session_id="thread-123",
            turn_count=2,
            history=[SessionTurn("Explain auth", "answer", "Architecture summary.")],
            facts=[FactItem(statement="app/main.py invokes login().", files=["app/main.py"])],
            changed_files=["app/routes/auth.py"],
            validation_runs=["python -m unittest -v tests.test_auth"],
            last_unknowns=["Token refresh behavior remains unclear."],
        )
        prefix = build_context_prefix(session)
        question = build_task_question(session, "What changed so far?")
        self.assertIn("Interactive session context:", prefix)
        self.assertIn("Changed files so far: app/routes/auth.py", prefix)
        self.assertIn("Current user request:\nWhat changed so far?", question)

    def test_build_parser_supports_interactive_arguments(self) -> None:
        parser = build_parser()
        args = parser.parse_args([str(self.repo), "--progress", "quiet"])
        self.assertEqual(args.repo_path, self.repo)
        self.assertEqual(args.progress, "quiet")
        self.assertEqual(args.planner, "codex")
        self.assertIsNone(args.resume)

    def test_build_parser_defaults_repo_path_to_current_directory(self) -> None:
        parser = build_parser()
        original_cwd = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as current_dir:
                os.chdir(current_dir)
                interactive_args = parser.parse_args([])
        finally:
            os.chdir(original_cwd)
        self.assertEqual(interactive_args.repo_path.resolve(), Path(current_dir).resolve())

    def test_build_parser_supports_interactive_resume_flag(self) -> None:
        parser = build_parser()
        prompt_args = parser.parse_args([str(self.repo), "--resume"])
        direct_args = parser.parse_args([str(self.repo), "--resume", "thread-123"])
        self.assertEqual(prompt_args.resume, "__prompt__")
        self.assertEqual(direct_args.resume, "thread-123")

    def test_build_parser_supports_stderr_trace_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args([str(self.repo), "--trace-stderr"])
        self.assertTrue(args.trace_stderr)

    def test_session_round_trip_preserves_session_id_and_history(self) -> None:
        session_path = self.repo / ".session.json"
        state = InteractiveSession(
            repo_path=str(self.repo),
            session_id="thread-123",
            turn_count=2,
            changed_files=["app/routes/auth.py"],
            validation_runs=["python -m unittest -v tests.test_auth"],
            last_result_summary="Updated the token flow.",
            last_unknowns=["Token refresh behavior remains unclear."],
        )
        state.history.append(SessionTurn("Explain auth", "answer", "Architecture summary."))
        state.facts.append(FactItem(statement="app/main.py invokes login().", files=["app/main.py"]))
        save_session(session_path, state)
        loaded = load_session(session_path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.repo_path, str(self.repo))
        self.assertEqual(loaded.session_id, "thread-123")
        self.assertEqual(loaded.turn_count, 2)
        self.assertEqual(loaded.history[0].user_request, "Explain auth")
        self.assertEqual(loaded.facts[0].statement, "app/main.py invokes login().")

    def test_list_sessions_and_find_session_by_id_read_from_history_directory(self) -> None:
        newer_path = self.repo / ".history" / "interactive-session-newer.json"
        older_path = self.repo / ".history" / "interactive-session-older.json"
        save_session(older_path, InteractiveSession(repo_path=str(self.repo), session_id="thread-older", turn_count=1))
        save_session(newer_path, InteractiveSession(repo_path=str(self.repo), session_id="thread-newer", turn_count=3))
        saved_sessions = list_sessions(self.repo)
        self.assertCountEqual([item.session_id for item in saved_sessions], ["thread-newer", "thread-older"])
        found = find_session_by_id(self.repo, "thread-older")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.path, older_path)

    def test_interactive_mode_starts_fresh_without_resume(self) -> None:
        plan = make_plan("Explain the architecture")
        actions = [
            make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Inspect the repo before finishing the first turn."),
            make_finish_action(
                step_id="step_4",
                reason="Answer the first turn.",
                answer="Architecture summary.",
                unknowns=["Validation coverage is still unclear."],
                completed_step_ids=["step_1"],
                criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied in the interactive turn.") for criterion in plan.success_criteria[:2]],
                fact_updates=[FactItem(statement="README.md is present.", files=["README.md"])],
            ),
        ]
        policy = ScriptedPolicy(plan, actions)
        policy.session_id = "thread-initial"
        output = io.StringIO()
        runtime = AgentRuntime(step_budget=4, planner=policy, reporter=RuntimeReporter(stream=output, level="quiet"))
        exit_code = run_interactive(self.repo, runtime=runtime, input_stream=io.StringIO("Explain the architecture\nquit\n"), output_stream=output)
        self.assertEqual(exit_code, 0)
        saved_sessions = list_sessions(self.repo)
        self.assertEqual(len(saved_sessions), 1)
        first_state = load_session(saved_sessions[0].path)
        assert first_state is not None
        self.assertEqual(first_state.turn_count, 1)
        self.assertEqual(first_state.session_id, "thread-initial")
        self.assertIn("Architecture summary.", output.getvalue())
        self.assertEqual(policy.plan_questions[0], "Explain the architecture")

    def test_interactive_mode_resumes_session_state_with_resume_session_id(self) -> None:
        plan = make_plan("Explain the architecture")
        first_policy = ScriptedPolicy(
            plan,
            [
                make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Inspect the repo before finishing the first turn."),
                make_finish_action(
                    step_id="step_4",
                    reason="Answer the first turn.",
                    answer="Architecture summary.",
                    unknowns=["Validation coverage is still unclear."],
                    completed_step_ids=["step_1"],
                    criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied in the interactive turn.") for criterion in plan.success_criteria[:2]],
                    fact_updates=[FactItem(statement="README.md is present.", files=["README.md"])],
                ),
            ],
        )
        first_policy.session_id = "thread-initial"
        first_output = io.StringIO()
        first_exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=first_policy, reporter=RuntimeReporter(stream=first_output, level="quiet")),
            input_stream=io.StringIO("Explain the architecture\nquit\n"),
            output_stream=first_output,
        )
        self.assertEqual(first_exit_code, 0)
        saved_session = find_session_by_id(self.repo, "thread-initial")
        self.assertIsNotNone(saved_session)
        assert saved_session is not None

        resumed_output = io.StringIO()
        resumed_policy = ScriptedPolicy(
            plan,
            [
                make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 2}, reason="Inspect the repo before finishing after resume."),
                make_finish_action(
                    step_id="step_4",
                    reason="Done.",
                    answer="Second session answer.",
                    completed_step_ids=["step_1"],
                    criterion_updates=[SuccessCriterionStatus(criterion=criterion, status="met", note="Satisfied after resume.") for criterion in plan.success_criteria[:2]],
                ),
            ],
        )
        resumed_exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=resumed_policy, reporter=RuntimeReporter(stream=resumed_output, level="quiet")),
            input_stream=io.StringIO("What changed so far?\nquit\n"),
            output_stream=resumed_output,
            resume="thread-initial",
        )
        self.assertEqual(resumed_exit_code, 0)
        self.assertEqual(resumed_policy.session_id, "thread-initial")
        self.assertIn("Interactive session context:", resumed_policy.plan_questions[0])
        self.assertIn("Architecture summary.", resumed_policy.plan_questions[0])
        resumed_state = load_session(saved_session.path)
        self.assertIsNotNone(resumed_state)
        assert resumed_state is not None
        self.assertEqual(resumed_state.turn_count, 2)
        self.assertEqual(resumed_state.history[-1].user_request, "What changed so far?")

    def test_interactive_mode_prompts_for_session_id_when_resume_flag_has_no_value(self) -> None:
        session_path = self.repo / ".history" / "interactive-session-existing.json"
        save_session(session_path, InteractiveSession(repo_path=str(self.repo), session_id="thread-prompt", turn_count=1, history=[SessionTurn("Explain auth", "answer", "Architecture summary.")]))
        output = io.StringIO()
        resumed_policy = ScriptedPolicy(
            make_plan("Explain auth"),
            [make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 1}, reason="Reconfirm the repo before finishing the resumed turn.")] + [make_finish_action(step_id="step_4", reason="Done.", answer="Prompt resume answer.", completed_step_ids=["step_1"])] * 4,
        )
        exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=resumed_policy, reporter=RuntimeReporter(stream=output, level="quiet")),
            input_stream=io.StringIO("thread-prompt\nContinue\nquit\n"),
            output_stream=output,
            resume="__prompt__",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("[session] available saved sessions:", output.getvalue())
        self.assertEqual(resumed_policy.session_id, "thread-prompt")

    def test_interactive_mode_starts_fresh_when_resume_prompt_has_no_saved_sessions(self) -> None:
        output = io.StringIO()
        policy = ScriptedPolicy(
            make_plan("Explain auth"),
            [make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 1}, reason="Inspect the repo before finishing the fresh turn.")] + [make_finish_action(step_id="step_4", reason="Done.", answer="Fresh session answer.", completed_step_ids=["step_1"])] * 4,
        )
        exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=policy, reporter=RuntimeReporter(stream=output, level="quiet")),
            input_stream=io.StringIO("Continue anyway\nquit\n"),
            output_stream=output,
            resume="__prompt__",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("no saved sessions found", output.getvalue())
        self.assertEqual(policy.plan_questions[0], "Continue anyway")
        self.assertIsNone(policy.session_id)

    def test_interactive_mode_starts_fresh_when_resume_prompt_selection_is_blank(self) -> None:
        session_path = self.repo / ".history" / "interactive-session-existing.json"
        save_session(session_path, InteractiveSession(repo_path=str(self.repo), session_id="thread-prompt", turn_count=1))
        output = io.StringIO()
        policy = ScriptedPolicy(
            make_plan("Explain auth"),
            [make_tool_action(step_id="step_1", tool_name="list_tree", tool_input={"depth": 1}, reason="Inspect the repo before finishing the fresh turn.")] + [make_finish_action(step_id="step_4", reason="Done.", answer="Fresh session answer.", completed_step_ids=["step_1"])] * 4,
        )
        exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=policy, reporter=RuntimeReporter(stream=output, level="quiet")),
            input_stream=io.StringIO("\nContinue anyway\nquit\n"),
            output_stream=output,
            resume="__prompt__",
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("no session selected; starting fresh session.", output.getvalue())
        self.assertEqual(policy.plan_questions[0], "Continue anyway")
        self.assertIsNone(policy.session_id)

    def test_interactive_mode_exits_cleanly_without_writing_session_on_immediate_quit(self) -> None:
        plan = make_plan("Explain the architecture")
        policy = ScriptedPolicy(plan, [])
        output = io.StringIO()
        exit_code = run_interactive(
            self.repo,
            runtime=AgentRuntime(step_budget=4, planner=policy, reporter=RuntimeReporter(stream=output, level="quiet")),
            input_stream=io.StringIO("quit\n"),
            output_stream=output,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(list_sessions(self.repo), [])
