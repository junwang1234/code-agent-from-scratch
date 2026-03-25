from __future__ import annotations

from src.models import DiscoveredCommand, FileContext, ReadRange, Task, ValidationCommand, ValidationDiscoveryState
from src.runtime.action_repair import fallback_tool_action, pick_script_target, repair_edit_tool_action, repair_tool_action
from src.runtime.memory_manager import create_memory
from src.runtime.observation_analysis import facts_from_excerpt, facts_from_tree, summarize_excerpt, summarize_tree

from tests.helpers import RepoTestCase, make_edit_plan, make_plan, make_tool_action


def _discovered(kind: str, argv: list[str], *, source: str = "repo-hint") -> DiscoveredCommand:
    return DiscoveredCommand(
        kind=kind,
        command=ValidationCommand(kind=kind, argv=argv),
        source=source,
        confidence=0.9,
    )


class RuntimeHelpersTest(RepoTestCase):
    def test_early_write_repairs_into_inspection(self) -> None:
        plan = make_edit_plan("Read first then fix the auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Read first then fix the auth flow"), plan)
        action = make_tool_action(step_id="step_1", tool_name="apply_patch", tool_input={"path": "app/routes/auth.py", "old_text": "return token", "new_text": "return 'x'"}, reason="Patch immediately.")
        repaired = repair_tool_action(memory, action)
        self.assertEqual(repaired.tool_name, "list_files")

    def test_markdown_summary_extracts_useful_content(self) -> None:
        excerpt = "---\ntitle: Sample\n---\n# Skill Title\nThis workflow compares two model outputs.\n## Steps\nDo the work.\n"
        summary, highlights = summarize_excerpt("SKILL.md", excerpt)
        self.assertIn("markdown document", summary)
        self.assertIn("Skill Title", summary)
        self.assertTrue(any("Key line" in item for item in highlights))

    def test_tree_summary_groups_root_dirs_and_files(self) -> None:
        summary, highlights = summarize_tree(["references/", "references/doc.md", "scripts/", "scripts/main.py", "tests/", "SKILL.md", "INDEX.md"])
        self.assertIn("Top-level dirs", summary)
        self.assertIn("Root files", summary)
        self.assertTrue(any("Primary directories" in item for item in highlights))
        self.assertTrue(any("Representative nested files" in item for item in highlights))

    def test_tree_facts_capture_root_structure(self) -> None:
        facts = facts_from_tree(["references/", "scripts/", "tests/", "SKILL.md", "INDEX.md"])
        self.assertTrue(any("Top-level directories" in fact.statement for fact in facts))
        self.assertTrue(any("Root files include" in fact.statement for fact in facts))

    def test_excerpt_facts_capture_markdown_title_and_highlights(self) -> None:
        excerpt = "# Skill Title\nThis workflow compares two model outputs.\n## Steps\nDo the work.\n"
        _summary, highlights = summarize_excerpt("SKILL.md", excerpt)
        facts = facts_from_excerpt("SKILL.md", excerpt, highlights)
        self.assertTrue(any("top-level document titled" in fact.statement for fact in facts))
        self.assertTrue(any("headings" in fact.statement.lower() or "key line" in fact.statement.lower() for fact in facts))

    def test_pick_script_target_prefers_real_entrypoint_over_init(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.record_observation("list_tree", "depth=2", "summary", ["Representative nested files: scripts/__init__.py, scripts/agent_rebuttal.py, scripts/build_prompt.py"])
        self.assertEqual(pick_script_target(memory), "scripts/agent_rebuttal.py")

    def test_repair_edit_tool_action_trims_redundant_read_to_uncovered_gap(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.file_contexts["app/routes/auth.py"] = FileContext(path="app/routes/auth.py", read_ranges=[ReadRange(start_line=1, end_line=80)], patch_ready=True)
        action = make_tool_action(step_id="step_1", tool_name="read_file_range", tool_input={"path": "app/routes/auth.py", "start_line": 30, "end_line": 60}, reason="Read the same area again.")
        repaired = repair_edit_tool_action(memory, action)
        self.assertEqual(repaired.tool_name, "read_file_range")
        self.assertNotEqual(repaired.tool_input["start_line"], 30)
        self.assertIn("Repaired redundant reread", repaired.reason)

    def test_fallback_prefers_explicit_shell_tools_for_tooling_repo(self) -> None:
        skill_repo = self.repo / "skill2"
        skill_repo.mkdir()
        (skill_repo / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (skill_repo / "scripts").mkdir()
        (skill_repo / "tests").mkdir()
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=skill_repo, question="Explain the architecture"), plan)
        action = fallback_tool_action(memory)
        self.assertEqual(action.tool_name, "list_tree")
        memory.record_observation("list_tree", "depth=2", "summary", ["Representative nested files: scripts/a.py"])
        action = fallback_tool_action(memory)
        self.assertEqual(action.tool_name, "rg_probe")
        self.assertEqual(action.tool_input["paths"], ["SKILL.md"])
        self.assertIn("pattern", action.tool_input)

    def test_repair_tool_action_rewrites_duplicate_explicit_shell_tool(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        memory.record_observation("rg_search", "rg_search pattern=workflow|scripts/|python|uv run paths=README.md", "rg returned 1 raw line(s).", ["README.md:1:# Example"], raw_output=["README.md:1:# Example"], metadata={"command": "rg"})
        memory.record_observation("rg_files", "rg_files paths=src", "rg returned 1 raw line(s).", ["src/auth.py"], raw_output=["src/auth.py"], metadata={"command": "rg"})
        action = make_tool_action(step_id="step_1", tool_name="rg_search", tool_input={"pattern": "workflow|scripts/|python|uv run", "paths": ["README.md"]}, reason="Repeat by mistake.")
        repaired = repair_tool_action(memory, action)
        self.assertIn(repaired.tool_name, {"rg_search", "find_paths"})

    def test_repair_tool_action_converts_early_broad_search_into_probe(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        action = make_tool_action(step_id="step_2", tool_name="rg_search", tool_input={"pattern": "workflow|entrypoint", "paths": ["README.md", "app"]}, reason="Try broad extraction too early.")
        repaired = repair_tool_action(memory, action)
        self.assertIn(repaired.tool_name, {"head_file", "rg_probe"})

    def test_repair_tool_action_trims_probe_targets(self) -> None:
        plan = make_plan("Explain the architecture")
        memory = create_memory(Task(repo_path=self.repo, question="Explain the architecture"), plan)
        action = make_tool_action(step_id="step_2", tool_name="head_file", tool_input={"paths": ["README.md", "app/main.py", "app/routes/auth.py", "extra.py"], "lines": 40}, reason="Probe too many files at once.")
        repaired = repair_tool_action(memory, action)
        self.assertEqual(len(repaired.tool_input["paths"]), 3)

    def test_fallback_edit_validation_runs_tests_directly_when_no_discovery_exists(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.inspected_files.add("app/routes/auth.py")
        memory.state.changed_files.add("app/routes/auth.py")
        action = fallback_tool_action(memory)
        self.assertEqual(action.tool_name, "run_tests")
        self.assertEqual(action.tool_input, {})

    def test_fallback_edit_validation_uses_discovered_test_command(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.inspected_files.add("app/routes/auth.py")
        memory.state.changed_files.add("app/routes/auth.py")
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="abc123",
            selected_test=_discovered("test", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]),
        )
        action = fallback_tool_action(memory)
        self.assertEqual(action.tool_name, "run_tests")
        self.assertEqual(action.tool_input["argv"][:3], ["python", "-m", "unittest"])

    def test_repair_edit_tool_action_uses_discovered_test_command(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="abc123",
            selected_test=_discovered("test", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]),
        )
        action = make_tool_action(step_id="step_3", tool_name="run_tests", tool_input={}, reason="Validate the change.")
        repaired = repair_edit_tool_action(memory, action)
        self.assertEqual(repaired.tool_input["argv"][:3], ["python", "-m", "unittest"])

    def test_repair_format_code_uses_discovered_formatter(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="abc123",
            selected_format=_discovered("format", ["python", "-m", "ruff", "format", "."]),
        )
        action = make_tool_action(step_id="step_3", tool_name="format_code", tool_input={}, reason="Format the change.")
        repaired = repair_edit_tool_action(memory, action)
        self.assertEqual(repaired.tool_input["argv"][:4], ["python", "-m", "ruff", "format"])

    def test_repair_run_command_uses_discovered_lint(self) -> None:
        plan = make_edit_plan("Patch auth flow")
        memory = create_memory(Task(repo_path=self.repo, question="Patch auth flow"), plan)
        memory.state.validation_discovery = ValidationDiscoveryState(
            repo_fingerprint="abc123",
            selected_lint=_discovered("lint", ["python", "-m", "ruff", "check", "."]),
        )
        action = make_tool_action(step_id="step_3", tool_name="run_command", tool_input={}, reason="Run lint.")
        repaired = repair_edit_tool_action(memory, action)
        self.assertEqual(repaired.tool_input["argv"][:4], ["python", "-m", "ruff", "check"])
