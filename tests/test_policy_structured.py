from __future__ import annotations

import io
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.providers.base import StructuredCall
from src.providers.codex_cli import CodexCliProvider
from src.planning.prompt_builder import PlanningPromptBuilder
from src.planning.structured_planner import StructuredPlanner, UNIFIED_TOOL_SPECS, build_planner
from src.runtime.memory_manager import create_memory, reduce_memory
from src.models import Task

from tests.helpers import RepoTestCase, make_edit_plan, make_plan


class StructuredPolicyTest(RepoTestCase):
    def test_build_planner_rejects_non_codex_policy_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported planner"):
            build_planner("auto")

    def test_codex_policy_parses_structured_outputs(self) -> None:
        class StubStructuredPlanner(StructuredPlanner):
            def __init__(self) -> None:
                super().__init__(provider=CodexCliProvider(codex_bin="codex"))
                self.calls = 0

            def _generate_plan(self, call: StructuredCall) -> dict:
                self.calls += 1
                return {
                    "goal": "Explain the repository architecture.",
                    "question_type": "architecture",
                    "steps": [
                        {"id": "step_1", "purpose": "Inspect the repo tree.", "allowed_tools": ["list_tree"], "depends_on": []},
                        {"id": "step_2", "purpose": "Summarize the findings.", "allowed_tools": ["finish"], "depends_on": ["step_1"]},
                    ],
                    "success_criteria": ["Identify major modules."],
                    "constraints": ["Use only bounded local exploration tools."],
                    "hypotheses": ["Entrypoints and config will reveal the architecture."],
                    "search_terms": ["main"],
                    "file_hints": ["README"],
                    "unknowns_to_resolve": ["True entrypoint may still be unclear."],
                }

            def _generate_action(self, call: StructuredCall) -> dict:
                self.calls += 1
                return {
                    "kind": "finish",
                    "step_id": "step_2",
                    "reason": "Enough evidence exists.",
                    "tool_call": None,
                    "updates": {
                        "completed_step_ids": [],
                        "criterion_updates": [{"criterion": "Identify major modules.", "status": "met", "note": "Major modules were identified."}],
                        "fact_updates": [{"statement": "app/main.py appears to be the main entrypoint.", "files": ["app/main.py"], "confidence": "high", "status": "confirmed"}],
                    },
                    "finish": {"answer": "The architecture centers on app/main.py.", "evidence": [], "repo_map": [], "unknowns": [], "suggested_next_questions": []},
                }

        planner = StubStructuredPlanner()
        plan = planner.make_plan(Task(repo_path=self.repo, question="Explain the architecture"))
        self.assertEqual(plan.steps[0].allowed_tools, ["list_tree"])
        memory_plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), memory_plan)
        action = planner.next_action(memory, 2)
        self.assertEqual(action.kind, "finish")
        self.assertEqual(action.fact_updates[0].statement, "app/main.py appears to be the main entrypoint.")
        self.assertEqual(action.fact_updates[0].source, "codex")

    def test_codex_policy_timeout_raises_clear_error(self) -> None:
        provider = CodexCliProvider(codex_bin="codex", timeout_sec=1)
        with patch("src.providers.codex_cli.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("src.providers.codex_cli.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=1)):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    provider.generate_plan(StructuredCall(prompt="prompt", schema={"type": "object"}, call_kind="plan"))

    def test_codex_policy_reuses_session_id_across_calls(self) -> None:
        provider = CodexCliProvider(codex_bin="codex")
        thread_id = "019d09c7-3273-7eb2-b768-f875efef2314"
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output_path = Path(command[command.index("--output-last-message") + 1])
            payload = {"ok": len(commands)}
            output_path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n", stderr="")

        with patch("src.providers.codex_cli.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("src.providers.codex_cli.subprocess.run", side_effect=fake_run):
                first_payload = provider.generate_plan(StructuredCall(prompt="first prompt", schema={"type": "object"}, call_kind="plan"))
                second_payload = provider.generate_action(StructuredCall(prompt="second prompt", schema={"type": "object"}, call_kind="action"))

        self.assertEqual(first_payload, {"ok": 1})
        self.assertEqual(second_payload, {"ok": 2})
        self.assertEqual(commands[0][:2], ["codex", "exec"])
        self.assertNotIn("resume", commands[0])
        self.assertIn("--output-schema", commands[0])
        self.assertEqual(commands[1][:4], ["codex", "exec", "resume", thread_id])
        self.assertNotIn("--output-schema", commands[1])
        self.assertEqual(provider.get_session_id(), thread_id)

    def test_codex_plan_reuses_resumed_session_with_schema_in_prompt(self) -> None:
        provider = CodexCliProvider(codex_bin="codex")
        provider.set_session_id("019d09c7-3273-7eb2-b768-f875efef2314")
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("src.providers.codex_cli.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("src.providers.codex_cli.subprocess.run", side_effect=fake_run):
                payload = provider.generate_plan(
                    StructuredCall(
                        prompt="plan prompt",
                        schema={"type": "object", "properties": {"goal": {"type": "string"}}},
                        call_kind="plan",
                    )
                )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(commands[0][:4], ["codex", "exec", "resume", "019d09c7-3273-7eb2-b768-f875efef2314"])
        self.assertNotIn("--output-schema", commands[0])
        self.assertIn("Schema:", commands[0][-1])

    def test_codex_policy_writes_trace_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trace_path = Path(temp_dir) / "codex-trace.jsonl"

            class StubStructuredPlanner(StructuredPlanner):
                def __init__(self, path: Path) -> None:
                    self.provider = CodexCliProvider(codex_bin="codex", trace_file=path)
                    super().__init__(provider=self.provider)

                def _generate_action(self, call: StructuredCall) -> dict:
                    self.provider._write_trace_event({"event": "codex_request", "call_kind": "action", "prompt": call.prompt, "schema": call.schema})
                    payload = {
                        "kind": "finish",
                        "step_id": "step_1",
                        "reason": "done",
                        "tool_call": None,
                        "updates": {"completed_step_ids": [], "criterion_updates": [], "fact_updates": []},
                        "finish": {"answer": "ok", "evidence": [], "repo_map": [], "unknowns": [], "suggested_next_questions": []},
                    }
                    self.provider._write_trace_event({"event": "codex_response", "call_kind": "action", "payload": payload})
                    return payload

            planner = StubStructuredPlanner(trace_path)
            memory_plan = make_plan("Explain the architecture")
            memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), memory_plan)
            planner.next_action(memory, 1)
            events = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event"], "codex_request")
            self.assertEqual(events[1]["event"], "codex_response")

    def test_codex_policy_does_not_emit_stderr_trace_by_default(self) -> None:
        provider = CodexCliProvider(codex_bin="codex")
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            provider._emit_trace("should stay quiet")
        self.assertEqual(stderr.getvalue(), "")

    def test_codex_policy_retries_once_after_invalid_json(self) -> None:
        provider = CodexCliProvider(codex_bin="codex")
        thread_id = "019d09c7-3273-7eb2-b768-f875efef2314"
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output_path = Path(command[command.index("--output-last-message") + 1])
            if len(commands) == 1:
                output_path.write_text('{"kind": "tool", bad json}', encoding="utf-8")
            else:
                output_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n", stderr="")

        with patch("src.providers.codex_cli.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("src.providers.codex_cli.subprocess.run", side_effect=fake_run):
                payload = provider.generate_action(StructuredCall(prompt="prompt", schema={"type": "object"}, call_kind="action"))

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(commands), 2)
        self.assertIn("previous reply was not valid JSON", commands[1][-1])

    def test_codex_policy_raises_clear_error_after_invalid_json_retry(self) -> None:
        provider = CodexCliProvider(codex_bin="codex")

        def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text('{"kind": "tool", bad json}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("src.providers.codex_cli.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("src.providers.codex_cli.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "returned invalid JSON after retry"):
                    provider.generate_action(StructuredCall(prompt="prompt", schema={"type": "object"}, call_kind="action"))

    def test_action_prompt_uses_incremental_state_after_first_turn(self) -> None:
        prompt_builder = PlanningPromptBuilder(UNIFIED_TOOL_SPECS)
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.state.current_step_id = "step_1"
        memory.state.note_observation("rg_search", "rg_search pattern=token paths=src/auth.py", "rg returned 1 raw line(s).", ["src/auth.py:2:return token"], raw_output=["src/auth.py:2:return token"], metadata={"command": "rg"})
        first_prompt, first_kind = prompt_builder.build_action_prompt(memory, 3)
        self.assertEqual(first_kind, "snapshot")
        self.assertIn("snapshot state below", first_prompt)
        self.assertIn("\"available_tools\"", first_prompt)
        prompt_builder.refresh_strategy.record_action_prompt_use(memory.state, first_kind)
        second_prompt, second_kind = prompt_builder.build_action_prompt(memory, 2)
        self.assertEqual(second_kind, "incremental")
        self.assertIn("Resumed repository-agent session.", second_prompt)
        self.assertIn("\"latest_observation\"", second_prompt)
        self.assertNotIn("\"available_tools\"", second_prompt)
        self.assertNotIn("\"task\"", second_prompt)
        self.assertNotIn("\"goal\"", second_prompt)

    def test_action_prompt_refreshes_on_step_change_and_failure(self) -> None:
        prompt_builder = PlanningPromptBuilder(UNIFIED_TOOL_SPECS)
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.current_step_id = "step_1"
        memory.state.note_observation("read_file_range", "read_file_range path=app/routes/auth.py start_line=1 end_line=40", "read summary", ["def login"], raw_output=["app/routes/auth.py:1:def login():"], metadata={"path": "app/routes/auth.py"})
        reduce_memory(memory)
        first_prompt, first_kind = prompt_builder.build_action_prompt(memory, 4)
        self.assertEqual(first_kind, "snapshot")
        prompt_builder.refresh_strategy.record_action_prompt_use(memory.state, first_kind)
        second_prompt, second_kind = prompt_builder.build_action_prompt(memory, 3)
        self.assertEqual(second_kind, "incremental")
        self.assertIn("\"latest_observation\"", second_prompt)
        self.assertIn("Resumed repository-agent session.", second_prompt)
        prompt_builder.refresh_strategy.record_action_prompt_use(memory.state, second_kind)
        memory.plan.steps[0].status = "completed"
        third_prompt, third_kind = prompt_builder.build_action_prompt(memory, 2)
        self.assertEqual(third_kind, "snapshot")
        self.assertIn("\"available_tools\"", third_prompt)
        prompt_builder.refresh_strategy.record_action_prompt_use(memory.state, third_kind)
        memory.state.failures.append("python -m unittest failed with exit code 1.")
        failure_prompt, failure_kind = prompt_builder.build_action_prompt(memory, 1)
        self.assertEqual(failure_kind, "snapshot")
        self.assertIn("\"failures\"", failure_prompt)
