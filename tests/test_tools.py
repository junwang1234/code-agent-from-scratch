from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from src.tools import MAX_READ_LINES, RepoFilesystem, ToolExecutionContext, ToolExecutor, build_default_tool_registry
from src.tools.core import CommandToolResult, ReadFileRangeToolResult, TreeToolResult
from src.tools.shell import SafeCommandRunner, ShellQueryRunner, format_shell_query


class RepoFilesystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "auth.py").write_text("def login():\n    return 'token'\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Example\nUse python src/auth.py to print the token.\n", encoding="utf-8")
        (self.repo / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n\nclass SmokeTest(unittest.TestCase):\n    def test_truth(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.tools = RepoFilesystem(self.repo)
        self.shell_runner = ShellQueryRunner(self.repo)
        self.command_runner = SafeCommandRunner(self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_tree(self) -> None:
        tree = self.tools.list_tree(depth=2)
        self.assertIn("src/", tree)
        self.assertIn("README.md", tree)

    def test_bounded_read(self) -> None:
        long_file = self.repo / "src" / "long.py"
        long_file.write_text("\n".join(f"line {index}" for index in range(1, 200)), encoding="utf-8")
        start, end, excerpt = self.tools.read_file("src/long.py", 1, 500)
        self.assertEqual(start, 1)
        self.assertEqual(end, MAX_READ_LINES)
        self.assertEqual(len(excerpt.splitlines()), MAX_READ_LINES)

    def test_read_file_blocks_path_escape(self) -> None:
        with self.assertRaises(ValueError):
            self.tools.read_file("../outside.py", 1, 5)

    def test_write_file_and_apply_patch(self) -> None:
        changed = self.tools.write_file("src/new_module.py", "VALUE = 1\n")
        self.assertEqual(changed.path, "src/new_module.py")
        self.assertEqual(changed.old_content, "")
        patched = self.tools.apply_patch("src/new_module.py", "VALUE = 1", "VALUE = 2")
        self.assertEqual(patched.path, "src/new_module.py")
        self.assertIn("VALUE = 1", patched.old_content)
        self.assertIn("VALUE = 2", patched.new_content)
        self.assertIn("VALUE = 2", (self.repo / "src" / "new_module.py").read_text(encoding="utf-8"))

    def test_shell_query_rg_extracts_matching_lines(self) -> None:
        result = self.shell_runner.run("rg", ["-n", "python|token", "README.md", "src/auth.py"])
        joined = "\n".join(result.output)
        self.assertIn("README.md", joined)
        self.assertIn("src/auth.py", joined)
        self.assertIn(result.exit_code, {0, 1})
        self.assertEqual(format_shell_query(result.command, result.args), "rg -n 'python|token' README.md src/auth.py")

    def test_shell_query_rg_accepts_pattern_starting_with_dash(self) -> None:
        result = self.shell_runner.run("rg", ["-n", "--", "--progress", "README.md"])
        self.assertIn(result.exit_code, {0, 1})
        self.assertEqual(format_shell_query(result.command, result.args), "rg -n -- --progress README.md")

    def test_shell_query_find_lists_repo_relative_files(self) -> None:
        result = self.shell_runner.run("find", ["src", "-maxdepth", "2", "-type", "f"])
        self.assertTrue(any(line.endswith("src/auth.py") or line == "src/auth.py" for line in result.output))
        self.assertEqual(result.exit_code, 0)

    def test_shell_query_rg_files_lists_repo_relative_files(self) -> None:
        result = self.shell_runner.run("rg", ["--files", "src"])
        self.assertIn("src/auth.py", result.output)

    def test_shell_query_rg_falls_back_when_rg_is_unavailable(self) -> None:
        with patch("src.tools.shell.shutil.which", return_value=None):
            result = self.shell_runner.run("rg", ["-n", "python|token", "README.md", "src/auth.py"])
        joined = "\n".join(result.output)
        self.assertIn("README.md:2:Use python src/auth.py to print the token.", joined)
        self.assertIn("src/auth.py:2:    return 'token'", joined)
        self.assertEqual(result.exit_code, 0)

    def test_shell_query_rejects_bad_flags_and_escaped_paths(self) -> None:
        with self.assertRaises(ValueError):
            self.shell_runner.run("rg", ["--hidden", "token", "README.md"])
        with self.assertRaises(ValueError):
            self.shell_runner.run("rg", ["-n", "token", "../outside.py"])
        with self.assertRaises(ValueError):
            self.shell_runner.run("find", ["src", "-exec", "cat", "{}", ";"])

    def test_safe_command_runner_runs_unittest(self) -> None:
        result = self.command_runner.run_tests("unittest", extra_args=["discover", "-s", "tests", "-v"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any("test_truth" in line for line in result.output))

    def test_safe_command_runner_accepts_unittest_dotted_target(self) -> None:
        result = self.command_runner.run_tests("unittest", targets=["tests.test_smoke"], extra_args=["-v"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(any("test_truth" in line for line in result.output))

    def test_safe_command_runner_rejects_disallowed_python_module(self) -> None:
        with self.assertRaises(ValueError):
            self.command_runner.run("python", ["-m", "http.server"])

    def test_default_tool_registry_exposes_tool_specs(self) -> None:
        registry = build_default_tool_registry()
        self.assertIn("list_tree", registry.names())
        self.assertIn("finish", registry.names())
        self.assertEqual(sorted(tool["name"] for tool in registry.specs()), registry.names())

    def test_default_tool_registry_executes_tools_via_context(self) -> None:
        registry = build_default_tool_registry()
        context = ToolExecutionContext(
            repo_filesystem=self.tools,
            shell_runner=self.shell_runner,
            command_runner=self.command_runner,
        )
        tree_result = registry.get("list_tree").execute(context, {"depth": 2})
        self.assertIsInstance(tree_result, TreeToolResult)
        self.assertIn("src/", tree_result.tree)
        command_result = registry.get("run_tests").execute(context, {"runner": "unittest", "extra_args": ["discover", "-s", "tests", "-v"]})
        self.assertIsInstance(command_result, CommandToolResult)
        self.assertEqual(command_result.result.exit_code, 0)

    def test_default_tool_registry_file_read_tool_executes_read(self) -> None:
        registry = build_default_tool_registry()
        context = ToolExecutionContext(
            repo_filesystem=self.tools,
            shell_runner=self.shell_runner,
            command_runner=self.command_runner,
        )
        read_result = registry.get("read_file_range").execute(context, {"path": "src/auth.py", "start_line": 1, "end_line": 20})
        self.assertIsInstance(read_result, ReadFileRangeToolResult)
        self.assertEqual(read_result.path, "src/auth.py")
        self.assertIn("def login()", read_result.excerpt)

    def test_tool_executor_centralizes_context_and_dispatch(self) -> None:
        executor = ToolExecutor(self.repo)
        tree_result = executor.execute("list_tree", {"depth": 2})
        self.assertIsInstance(tree_result, TreeToolResult)
        self.assertIn("src/", tree_result.tree)
        read_result = executor.execute("read_file_range", {"path": "src/auth.py", "start_line": 1, "end_line": 20})
        self.assertIsInstance(read_result, ReadFileRangeToolResult)
        self.assertIn("return 'token'", read_result.excerpt)


if __name__ == "__main__":
    unittest.main()
