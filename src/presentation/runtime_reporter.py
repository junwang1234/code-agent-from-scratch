from __future__ import annotations

import difflib
import io
from typing import TextIO

from ..models import Action, SessionState, StructuredPlan, Task, TaskResult, WriteResult


ANSI_RESET = "\x1b[0m"
ANSI_GREEN = "\x1b[32m"
ANSI_RED = "\x1b[31m"
ANSI_CYAN = "\x1b[36m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"


class RuntimeReporter:
    def __init__(self, *, stream: TextIO | None = None, level: str = "normal") -> None:
        self.stream = stream or io.StringIO()
        self.level = level
        self._last_reported_step_id: str | None = None

    def enabled(self) -> bool:
        return self.level != "quiet"

    def report_run_start(self, task: Task, step_budget: int, trace_enabled: bool) -> None:
        if not self.enabled():
            return
        self._write(f"[run] task: {task.question}")
        self._write(f"[run] repo: {task.repo_path}")
        self._write(f"[run] budget: {step_budget} steps")
        self._write(f"[run] trace: {'on' if trace_enabled else 'off'}")
        self._write("")

    def report_plan(self, plan: StructuredPlan) -> None:
        if not self.enabled():
            return
        self._write(f"[plan] goal: {plan.goal}")
        for step in plan.steps:
            tools = ", ".join(step.allowed_tools[:4])
            suffix = f" [{tools}]" if tools and self.level == "verbose" else ""
            self._write(f"[plan] {step.id} {step.status:<11} {step.purpose}{suffix}")
        self._write("")

    def report_action(self, step_purpose: str, action: Action) -> None:
        if not self.enabled():
            return
        if self._last_reported_step_id != action.step_id:
            self._write(f"{ANSI_BOLD}{ANSI_CYAN}[step]{ANSI_RESET} {action.step_id}  {ANSI_DIM}in progress{ANSI_RESET}  {step_purpose}")
            self._last_reported_step_id = action.step_id
        if action.kind == "finish":
            self._write(f"{ANSI_BOLD}[action]{ANSI_RESET} {ANSI_CYAN}finish{ANSI_RESET}")
        else:
            payload = self._render_payload(action.tool_input)
            details = f" {ANSI_DIM}{payload}{ANSI_RESET}" if payload else ""
            self._write(f"{ANSI_BOLD}[action]{ANSI_RESET} {ANSI_CYAN}{action.tool_name}{ANSI_RESET}{details}")

    def report_action_repaired(self, original: Action, repaired: Action) -> None:
        if not self.enabled():
            return
        original_text = "finish" if original.kind == "finish" else f"{original.tool_name} {self._render_payload(original.tool_input)}".rstrip()
        repaired_text = "finish" if repaired.kind == "finish" else f"{repaired.tool_name} {self._render_payload(repaired.tool_input)}".rstrip()
        if original_text == repaired_text:
            return
        self._write(f"{ANSI_BOLD}{ANSI_DIM}[repair]{ANSI_RESET} {ANSI_RED}{original_text}{ANSI_RESET} {ANSI_DIM}->{ANSI_RESET} {ANSI_GREEN}{repaired_text}{ANSI_RESET}")

    def report_result(self, summary: str) -> None:
        if not self.enabled():
            return
        self._write(f"[result] {summary}")

    def report_diff(self, write_result: WriteResult, max_lines: int = 80) -> None:
        if not self.enabled():
            return
        self._write(f"[diff] {write_result.path}")
        diff_lines = list(
            difflib.unified_diff(
                write_result.old_content.splitlines(),
                write_result.new_content.splitlines(),
                fromfile=f"a/{write_result.path}",
                tofile=f"b/{write_result.path}",
                lineterm="",
            )
        )
        if not diff_lines:
            self._write("(no textual diff)")
            return
        truncated = len(diff_lines) > max_lines
        for line in diff_lines[:max_lines]:
            self._write(self._format_diff_line(line))
        if truncated:
            self._write("... diff truncated ...")

    def report_step_completion(self, memory: SessionState) -> None:
        if not self.enabled():
            return
        for step in memory.plan.steps:
            if step.id in memory.last_completed_step_ids:
                self._write(f"[step] {step.id} completed")

    def report_finish(self, memory: SessionState, response: TaskResult, elapsed_seconds: float | None = None) -> None:
        if not self.enabled():
            return
        outcome = "incomplete" if step_budget_exhausted(memory.unknowns) else "completed"
        completed_steps = sum(1 for step in memory.plan.steps if step.status == "completed")
        total_steps = len(memory.plan.steps)
        self._write("")
        self._write(f"[summary] {outcome}")
        self._write(f"[summary] steps: {completed_steps}/{total_steps} completed")
        if elapsed_seconds is not None:
            self._write(f"[summary] elapsed: {self._format_elapsed(elapsed_seconds)}")
        if response.result_kind == "edit":
            changed = ", ".join(response.changed_files[:4]) if response.changed_files else "none"
            validation = response.validation[0] if response.validation else "none"
            risks = response.risks[0] if response.risks else "none"
            self._write(f"[summary] changed files: {changed}")
            self._write(f"[summary] validation: {validation}")
            self._write(f"[summary] risks: {risks}")
        else:
            evidence_count = len(response.evidence)
            unknowns = len(response.unknowns)
            key_files = _key_files_from_evidence(response.evidence)
            self._write(f"[summary] key files: {', '.join(key_files) if key_files else 'none'}")
            self._write(f"[summary] evidence: {evidence_count} items")
            self._write(f"[summary] unknowns: {unknowns}")
        self._write("")

    def _format_elapsed(self, elapsed_seconds: float) -> str:
        if elapsed_seconds < 60:
            return f"{elapsed_seconds:.1f}s"
        minutes, seconds = divmod(elapsed_seconds, 60)
        if minutes < 60:
            return f"{int(minutes)}m{int(seconds):02d}s"
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours}h{minutes:02d}m"

    def _render_payload(self, payload: dict) -> str:
        if not payload:
            return ""
        parts: list[str] = []
        for key, value in payload.items():
            if value in (None, [], ""):
                continue
            rendered = ",".join(str(item) for item in value[:4]) if isinstance(value, list) else str(value)
            parts.append(f"{key}={rendered}")
        return " ".join(parts[:6])

    def _format_diff_line(self, line: str) -> str:
        if line.startswith("+++") or line.startswith("---"):
            return f"{ANSI_BOLD}{ANSI_DIM}{line}{ANSI_RESET}"
        if line.startswith("@@"):
            return f"{ANSI_BOLD}{ANSI_CYAN}{line}{ANSI_RESET}"
        if line.startswith("+"):
            return f"{ANSI_GREEN}{line}{ANSI_RESET}"
        if line.startswith("-"):
            return f"{ANSI_RED}{line}{ANSI_RESET}"
        return line

    def _write(self, line: str) -> None:
        self.stream.write(line + "\n")
        self.stream.flush()


def build_reporter(level: str = "normal", stream: TextIO | None = None) -> RuntimeReporter:
    return RuntimeReporter(level=level, stream=stream)


def _key_files_from_evidence(evidence: list, limit: int = 4) -> list[str]:
    files: list[str] = []
    for item in evidence:
        for path in item.files:
            if path not in files:
                files.append(path)
            if len(files) >= limit:
                return files
    return files


def step_budget_exhausted(unknowns: list[str]) -> bool:
    return any("step budget was exhausted" in unknown.lower() for unknown in unknowns)
