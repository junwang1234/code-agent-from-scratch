from __future__ import annotations

import argparse
from pathlib import Path
import sys

from ..planning.structured_planner import build_planner
from ..presentation.runtime_reporter import build_reporter
from ..runtime.agent_runtime import AgentRuntime
from .interactive_loop import run_interactive


class AgentArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args: list[str] | None = None, namespace: argparse.Namespace | None = None) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if getattr(parsed, "repo_path", None) is None:
            parsed.repo_path = Path.cwd().resolve()
        return parsed


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "repo_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the target repository. Defaults to the current working directory.",
    )
    parser.add_argument("--step-budget", type=int, default=20, help="Maximum number of execution actions. Default: 20.")
    parser.add_argument("--planner", choices=["codex"], default="codex", help="Planner to use. Default: codex.")
    parser.add_argument("--trace", action="store_true", help="Enable structured planner tracing for the run header and trace file support.")
    parser.add_argument("--trace-stderr", action="store_true", help="Emit low-level planner timing and error trace lines to stderr.")
    parser.add_argument("--planner-timeout-sec", type=int, default=120, help="Timeout for each planner plan or action call. Default: 120.")
    parser.add_argument("--trace-file", type=Path, help="Optional JSONL file that records planner prompts and responses.")
    parser.add_argument("--progress", choices=["quiet", "normal", "verbose"], default="normal", help="Terminal progress verbosity. Default: normal.")


def _add_interactive_args(parser: argparse.ArgumentParser) -> None:
    _add_runtime_args(parser)
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__prompt__",
        metavar="SESSION_ID",
        help="Resume a saved interactive session by session id. If omitted, you will be prompted to choose one.",
    )


def _build_runtime(args: argparse.Namespace) -> AgentRuntime:
    planner = build_planner(
        planner_kind=args.planner,
        workdir=Path(__file__).resolve().parents[1],
        trace_to_stderr=args.trace_stderr,
        timeout_sec=args.planner_timeout_sec,
        trace_file=args.trace_file,
    )
    return AgentRuntime(
        planner=planner,
        step_budget=args.step_budget,
        reporter=build_reporter(args.progress, sys.stdout),
        trace_enabled=args.trace or args.trace_file is not None or args.trace_stderr,
    )

def build_parser() -> argparse.ArgumentParser:
    parser = AgentArgumentParser(prog="code-agent-from-scratch")
    _add_interactive_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime = _build_runtime(args)
    return run_interactive(args.repo_path, runtime=runtime, resume=args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
