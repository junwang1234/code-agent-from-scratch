# code-agent-from-scratch

`code-agent-from-scratch` is a code-agent learning project built from scratch.

It runs against a local repository, keeps execution bounded, and uses the local `codex` CLI as the backend that provides LLM inference for structured planning and next-action generation. The Python runtime owns tool safety, execution, memory updates, retries, and final output shaping.

## Goal

Given a repository path and a natural-language task, the agent should:

- inspect the repo in bounded local steps
- plan directly from the task
- either explain the codebase or make a small validated change

Examples:

- "Explain this repository's architecture."
- "Find the auth flow."
- "Rename this helper and update its tests."
- "Read the code first and then fix the failing token path."

## Current Status

The current implementation supports:

- one interactive CLI entrypoint
- one Codex-backed planner flow
- bounded local discovery tools
- bounded local edit tools
- validated local command execution for tests and formatting
- compact runtime memory across a bounded run
- interactive multi-turn sessions with saved history
- optional planner trace logging
- two final result shapes: understanding and edit

Still intentionally out of scope:

- arbitrary shell execution against the target repo
- PR automation
- daemon or server mode
- non-Codex backends

## Project Layout

```text
code-agent-from-scratch/
  docs/
    ARCHITECTURE.md
    SOURCE_OF_TRUTH.md
  src/
    app/
      interactive_loop.py
      main.py
      session_service.py
      session_store.py
      task_builder.py
    models/
      actions.py
      artifacts.py
      memory.py
      plan.py
      results.py
      task.py
    planning/
      base.py
      prompt_builder.py
      prompt_refresh_strategy.py
      structured_planner.py
    presentation/
      responder.py
      runtime_reporter.py
    providers/
      base.py
      codex_cli.py
      codex_request_adapter.py
    runtime/
      action_execution.py
      action_normalizer.py
      action_outcomes.py
      action_repair.py
      agent_runtime.py
      events.py
      execution_commands.py
      file_context_helpers.py
      memory_manager.py
      observation_analysis.py
      result_composer.py
      tool_outcomes.py
      turn_artifacts.py
    tools/
      command.py
      core.py
      executor.py
      file_tools.py
      registry.py
      repo_filesystem.py
      search.py
      shell.py
  tests/
```

High-level ownership:

- `src/app`: CLI entrypoint, REPL loop, and interactive session persistence
- `src/models`: shared dataclasses only
- `src/planning`: planner contract, prompt building, and Codex-backed structured planner
- `src/providers`: Codex CLI provider and request adaptation
- `src/runtime`: bounded run loop, memory updates, action normalization, retries, outcome application, and result composition
- `src/tools`: bounded repo, search, edit, and command tools
- `src/presentation`: terminal progress reporting and final markdown rendering

## Requirements

- Python 3.11+
- local `codex` CLI

The runtime code uses the Python standard library only. `requirements.txt` just installs this package in editable mode.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

Start a fresh interactive session against the current repository:

```bash
.venv/bin/python -m src.app.main
```

Target a different repository:

```bash
.venv/bin/python -m src.app.main /path/to/repo
```

Resume a saved interactive session:

```bash
.venv/bin/python -m src.app.main --resume
```

Useful runtime flags:

- `--step-budget`: maximum number of execution actions
- `--planner codex`: planner selection; `codex` is the only supported backend today
- `--progress quiet|normal|verbose`: runtime progress verbosity
- `--trace`: enable planner trace metadata in the run header
- `--trace-stderr`: emit low-level planner trace lines to stderr
- `--planner-timeout-sec`: timeout for each planner call
- `--trace-file`: write planner request/response traces to JSON

Example with tracing:

```bash
.venv/bin/python -m src.app.main /path/to/repo \
  --planner codex \
  --step-budget 20 \
  --trace \
  --planner-timeout-sec 120 \
  --trace-file /tmp/repo-understanding-trace.json
```

## How It Works

For one interactive turn:

1. `src.app.main` parses CLI args, builds the Codex-backed planner, and constructs `AgentRuntime`.
2. `src.app.interactive_loop` starts or resumes the interactive session.
3. `src.app.task_builder` combines the new user request with saved session context.
4. `src.runtime.agent_runtime.AgentRuntime` starts one bounded run.
5. `src.planning.structured_planner.StructuredPlanner` asks Codex for a structured plan, then one next action at a time.
6. `src.runtime.action_normalizer` and `src.runtime.action_repair` convert weak or unsafe proposals into deterministic execution commands.
7. `src.runtime.action_execution.ActionExecutor` dispatches validated tool calls through `src.tools`.
8. `src.runtime.tool_outcomes` and `src.runtime.memory_manager` record observations, evidence, facts, changed files, and retry context.
9. `src.runtime.result_composer` shapes the final `TaskResult`.
10. `src.presentation.responder` renders markdown, and `src.app.session_service` persists the turn.

Codex is used for structured reasoning, not for arbitrary tool execution. All actual repo interaction stays local and bounded inside the Python runtime.

## Tool Surface

Read and discovery tools:

- `list_tree`
- `head_file`
- `rg_probe`
- `rg_search`
- `rg_files`
- `find_paths`
- `list_files`
- `read_file_range`
- `search_code`

Write and validation tools:

- `write_file`
- `apply_patch`
- `run_command`
- `run_tests`
- `format_code`

The `finish` action is modeled explicitly in the planner schema, but it is handled by the runtime rather than dispatched as a normal tool.

## Interactive Sessions

Interactive history is stored under `.history/` inside the target repository. Saved session state includes:

- prior user turns
- accumulated facts
- changed files
- validation runs
- last-turn unknowns
- the persisted Codex session id used for resume

## Output Shape

Understanding-style runs render:

- `Answer`
- `Evidence`
- `Repo Map`
- `Unknowns`
- `Success Criteria`
- `Suggested Next Questions`

Edit-style runs render:

- `Summary`
- `Files Changed`
- `Validation`
- `Risks`
