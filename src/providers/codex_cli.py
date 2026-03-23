from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .base import StructuredCall
from .codex_request_adapter import CodexPreparedRequest, prepare_codex_request


class CodexCliProvider:
    source_name = "codex"

    def __init__(
        self,
        codex_bin: str = "codex",
        workdir: Path | None = None,
        trace_to_stderr: bool = False,
        timeout_sec: int = 60,
        trace_file: Path | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.workdir = (workdir or Path(__file__).resolve().parents[2]).resolve()
        self.trace_to_stderr = trace_to_stderr
        self.timeout_sec = timeout_sec
        self.trace_file = trace_file
        self._trace_events: list[dict] = []
        self._session_id: str | None = None

    def get_session_id(self) -> str | None:
        return self._session_id

    def set_session_id(self, session_id: str | None) -> None:
        self._session_id = session_id

    def _execute_structured_call(self, call: StructuredCall) -> dict:
        if shutil.which(self.codex_bin) is None:
            raise RuntimeError(f"Codex executable not found: {self.codex_bin}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "output.json"
            parse_error: json.JSONDecodeError | None = None
            output_text = ""
            for attempt_index in range(1, 3):
                request = prepare_codex_request(call, session_id=self._session_id, attempt_index=attempt_index)
                if request.use_output_schema:
                    schema_path.write_text(json.dumps(request.schema), encoding="utf-8")
                command = self._build_codex_command(request, schema_path, output_path)
                start_time = time.monotonic()
                self._emit_trace(
                    f"starting {call.call_kind} call; prompt_chars={len(request.prompt)} timeout_sec={self.timeout_sec} session_id={self._session_id or 'new'} attempt={attempt_index}"
                )
                self._write_trace_event(
                    {
                        "event": "codex_request",
                        "call_kind": call.call_kind,
                        "timestamp": time.time(),
                        "prompt": request.prompt,
                        "prompt_lines": request.prompt.splitlines(),
                        "prompt_chars": len(request.prompt),
                        "schema": request.schema,
                        "timeout_sec": self.timeout_sec,
                        "session_id": self._session_id,
                        "attempt": attempt_index,
                        "command": command[:-1] + ["<prompt>"],
                    }
                )
                try:
                    result = subprocess.run(
                        command,
                        cwd=self.workdir,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=self.timeout_sec,
                    )
                except subprocess.TimeoutExpired as exc:
                    elapsed = time.monotonic() - start_time
                    self._emit_trace(f"{call.call_kind} call timed out after {elapsed:.1f}s")
                    self._write_trace_event(
                        {
                            "event": "codex_timeout",
                            "call_kind": call.call_kind,
                            "timestamp": time.time(),
                            "elapsed_sec": round(elapsed, 3),
                            "timeout_sec": self.timeout_sec,
                            "attempt": attempt_index,
                        }
                    )
                    raise RuntimeError(f"Codex {call.call_kind} call timed out after {self.timeout_sec}s.") from exc
                elapsed = time.monotonic() - start_time
                session_id = self._extract_session_id(result.stdout)
                if session_id is not None:
                    self._session_id = session_id
                self._emit_trace(
                    f"completed {call.call_kind} call; exit_code={result.returncode} elapsed_sec={elapsed:.1f} session_id={self._session_id or 'unknown'} attempt={attempt_index}"
                )
                if result.returncode != 0:
                    self._emit_trace(_truncate_trace(result.stderr.strip() or result.stdout.strip()))
                    self._write_trace_event(
                        {
                            "event": "codex_error",
                            "call_kind": call.call_kind,
                            "timestamp": time.time(),
                            "elapsed_sec": round(elapsed, 3),
                            "exit_code": result.returncode,
                            "session_id": self._session_id,
                            "attempt": attempt_index,
                            "stderr": result.stderr,
                            "stdout": result.stdout,
                        }
                    )
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex exec failed.")
                output_text = output_path.read_text(encoding="utf-8")
                self._emit_trace(f"{call.call_kind} output_chars={len(output_text)}")
                try:
                    payload = json.loads(output_text)
                except json.JSONDecodeError as exc:
                    parse_error = exc
                    self._emit_trace(f"{call.call_kind} returned invalid JSON on attempt {attempt_index}: {_truncate_trace(output_text)}")
                    self._write_trace_event(
                        {
                            "event": "codex_invalid_json",
                            "call_kind": call.call_kind,
                            "timestamp": time.time(),
                            "elapsed_sec": round(elapsed, 3),
                            "session_id": self._session_id,
                            "attempt": attempt_index,
                            "output_chars": len(output_text),
                            "output_text": output_text,
                            "error": str(exc),
                        }
                    )
                    if attempt_index == 1:
                        self._write_trace_event(
                            {
                                "event": "codex_retry_after_invalid_json",
                                "call_kind": call.call_kind,
                                "timestamp": time.time(),
                                "session_id": self._session_id,
                            }
                        )
                        continue
                    raise RuntimeError(
                        f"Codex {call.call_kind} call returned invalid JSON after retry: {exc}. Raw output starts with: {_truncate_trace(output_text, 240)}"
                    ) from exc
                self._write_trace_event(
                    {
                        "event": "codex_response",
                        "call_kind": call.call_kind,
                        "timestamp": time.time(),
                        "elapsed_sec": round(elapsed, 3),
                        "exit_code": result.returncode,
                        "session_id": self._session_id,
                        "attempt": attempt_index,
                        "output_chars": len(output_text),
                        "payload": payload,
                        "payload_pretty": json.dumps(payload, ensure_ascii=False, indent=2),
                    }
                )
                return payload
            raise RuntimeError(f"Codex {call.call_kind} call returned invalid JSON: {parse_error}. Raw output starts with: {_truncate_trace(output_text, 240)}")

    def generate_plan(self, call: StructuredCall) -> dict:
        return self._execute_structured_call(call)

    def generate_action(self, call: StructuredCall) -> dict:
        return self._execute_structured_call(call)

    def _build_codex_command(self, request: CodexPreparedRequest, schema_path: Path, output_path: Path) -> list[str]:
        command = [self.codex_bin, "exec"]
        if self._session_id:
            command.extend(["resume", self._session_id])
        else:
            command.extend(["--skip-git-repo-check", "--sandbox", "read-only", "-C", str(self.workdir)])
        command.extend(["--json", "--output-last-message", str(output_path), request.prompt])
        if request.use_output_schema:
            command[2:2] = ["--output-schema", str(schema_path)]
        return command

    def _extract_session_id(self, stdout_text: str) -> str | None:
        for line in stdout_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    return thread_id
        return None

    def _emit_trace(self, message: str) -> None:
        if not self.trace_to_stderr:
            return
        sys.stderr.write(f"[codex-policy] {message}\n")
        sys.stderr.flush()

    def _write_trace_event(self, event: dict) -> None:
        if self.trace_file is None:
            return
        self._trace_events.append(event)
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        self.trace_file.write_text(json.dumps(self._trace_events, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _truncate_trace(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"
