# code-agent-from-scratch: Source of Truth

This document describes the repository as it exists in code today.

## What This Repo Is

`code-agent-from-scratch` is a Python 3.11+ code-agent learning project built from scratch.

It targets local repositories, keeps execution bounded, and uses the local `codex` CLI as the backend for structured LLM inference. Codex generates plans and next actions; the Python runtime owns tool safety, execution, memory updates, retry behavior, and final response shaping.

The runtime:

1. accepts one natural-language task against a target repository
2. asks Codex for a structured plan
3. maintains compact runtime memory
4. executes one validated local action at a time
5. records failures and retries through local guardrails
6. returns either an understanding-style answer or an edit-style summary

## Main Entry Point

The CLI entrypoint is `src/app/main.py`.

Supported invocations:

- `python -m src.app.main`
- `python -m src.app.main /path/to/repo`
- `python -m src.app.main --resume`

If `repo_path` is omitted, the CLI defaults it to the current working directory.

Interactive mode is implemented in `src/app/interactive_loop.py`.

## Package Layout

The current code is organized by responsibility:

- `src/app`: CLI entrypoint, interactive loop, session service, session persistence, and task-question building
- `src/models`: shared dataclasses and result types
- `src/planning`: planner contract, prompt building, refresh strategy, and the Codex-backed structured planner
- `src/providers`: provider protocol, Codex CLI provider, and Codex request adaptation
- `src/runtime`: bounded run loop, action normalization, action repair, memory updates, observation analysis, outcome application, event sinks, turn artifacts, and final result composition
- `src/tools`: bounded repo, search, edit, and command tooling
- `src/presentation`: terminal progress reporting and final markdown rendering

Representative files:

- `src/app/main.py`
- `src/app/interactive_loop.py`
- `src/app/session_service.py`
- `src/app/session_store.py`
- `src/planning/structured_planner.py`
- `src/planning/prompt_builder.py`
- `src/providers/codex_cli.py`
- `src/providers/codex_request_adapter.py`
- `src/runtime/agent_runtime.py`
- `src/runtime/action_execution.py`
- `src/runtime/action_repair.py`
- `src/runtime/memory_manager.py`
- `src/runtime/tool_outcomes.py`
- `src/runtime/result_composer.py`
- `src/tools/registry.py`
- `src/tools/repo_filesystem.py`
- `src/tools/shell.py`
- `src/presentation/responder.py`
- `src/presentation/runtime_reporter.py`

## Runtime Flow

The runtime entrypoint is `src/runtime/agent_runtime.py`.

At a high level:

1. `src/app/main.py` builds the planner, runtime reporter, and `AgentRuntime`.
2. `src/app/interactive_loop.py` loads or creates an interactive session.
3. `src/app/task_builder.py` combines the new user request with saved session context.
4. `planner.make_plan(task)` returns a `StructuredPlan`.
5. `AgentMemory.create()` builds the initial `SessionState`.
6. `planner.next_action(memory, remaining_steps)` produces one next action proposal.
7. `ActionExecutor.normalize_command()` repairs or redirects unsafe or weak proposals into an `ExecutionCommand`.
8. `ActionExecutor.execute_command()` dispatches the command to validated local tools.
9. `src/runtime/tool_outcomes.py` applies the raw tool result back into runtime memory.
10. `src/runtime/result_composer.py` converts runtime state into the final `TaskResult`.
11. `src/presentation/responder.py` renders the final markdown response.
12. `src/app/session_service.py` persists the turn and saved Codex session id.

If the planner never produces a valid finish action before the step budget is exhausted, the runtime returns an incomplete result with the current evidence and unknowns.

## Runtime Components

Important runtime-side components:

- `AgentRuntime`: bounded run loop and optional event recording
- `AgentMemory`: runtime memory wrapper around `SessionState`
- `ActionExecutor`: deterministic command normalization and execution
- `ProposalNormalizer`: first-pass action normalization
- `action_repair.py`: deterministic repair logic for unsafe, weak, or repeated actions
- `tool_outcomes.py`: memory updates derived from raw tool results
- `observation_analysis.py`: summarization and fact extraction from observed output
- `turn_artifacts.py`: final per-turn artifacts returned alongside the user-facing result
- `result_composer.py`: converts runtime state into either an understanding result or an edit result

Runtime event sinks live in `src/runtime/events.py`:

- `InMemoryRuntimeEventLog`
- `JsonlRuntimeEventLog`

## Core Data Model

The shared dataclasses live in `src/models`.

Important request and planning types:

- `Task`
- `Plan`
- `PlanStep`
- `StructuredPlan`

Important action and result types:

- `ToolCall`
- `MemoryUpdates`
- `FinishPayload`
- `Action`
- `ActionExecutionError`
- `TaskResult`
- `TurnArtifacts`
- `RunOutcome`

Important working-state types:

- `Observation`
- `EvidenceItem`
- `FileSnippet`
- `ReadRange`
- `FileContext`
- `RepoMapEntry`
- `FactItem`
- `SuccessCriterionStatus`
- `WriteResult`
- `SessionState`

`SessionState` is the central mutable execution snapshot carried across one bounded run.

## Planning And Provider Boundary

The planner contract is defined in `src/planning/base.py`.

The live planner implementation is `StructuredPlanner` in `src/planning/structured_planner.py`:

- it builds plan prompts and action prompts through `PlanningPromptBuilder`
- it calls a provider twice conceptually: once for plan generation and once for next-action generation
- it exposes one unified tool schema for both understanding and edit runs

Prompt-state shaping lives in `src/planning/prompt_builder.py` and `src/planning/prompt_refresh_strategy.py`:

- compact prompt state for refresh turns
- incremental prompt state for follow-up turns
- refresh when the active step changes, after failure growth, or after several incremental turns

Provider wiring is split under `src/providers`:

- `src/providers/base.py` defines the provider protocol
- `src/providers/codex_cli.py` is the only implemented provider in this repo today
- `src/providers/codex_request_adapter.py` adapts structured planner calls into Codex CLI requests, including retry-after-invalid-JSON behavior

`CodexCliProvider` supports:

- structured JSON plan and action calls
- Codex session reuse across turns
- retry after invalid JSON
- optional stderr trace logging
- optional JSON trace-file output

## Tool Surface

The planner can choose from these validated local tools:

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

`finish` is included in the tool schema, but it is handled by the runtime rather than executed through the tool registry.

Tooling is split across:

- `src/tools/repo_filesystem.py` for bounded repo file operations
- `src/tools/shell.py` for validated shell queries and validated command execution
- `src/tools/file_tools.py` for read and write tool surfaces
- `src/tools/search.py` for search and discovery tools
- `src/tools/command.py` for command, test, and formatting tools
- `src/tools/registry.py` for the tool registry abstraction
- `src/tools/executor.py` for dispatch through the registry

## Safety Model

Safety is enforced locally, not delegated to Codex.

Repo file safety:

- all file paths are resolved relative to the target repo
- path escapes are rejected
- reads are bounded
- tree walking ignores common generated or tooling directories

Shell query safety:

- `ShellQueryRunner` only allows validated `rg` and `find`
- only allowlisted flags are accepted
- shell control tokens are rejected
- repo-relative paths are normalized before execution
- Python fallbacks exist for `rg` and `find`

Command execution safety:

- `SafeCommandRunner` only allows a small executable allowlist
- Python commands must use `-m` with an allowlisted module
- `run_tests` only supports `unittest` and `pytest`
- `format_code` only supports `ruff` and `black`

Runtime action safety:

- finish can be rejected when evidence is too weak
- early write attempts can be repaired into inspection work
- repeated identical failures are redirected after bounded retries
- tool failures are captured as structured retry context instead of crashing the run

## Memory And Prompt Compaction

Runtime memory construction and mutation live in `src/runtime/memory_manager.py`.

The runtime memory layer is responsible for:

- storing observations, facts, evidence, repo-map entries, file contexts, changed files, validation runs, and failures
- tracking current-step context and recent completed steps
- serializing compact prompt state for the planner
- carrying recent structured action failures and retry counts
- compacting memory after observations and mutations

Prompt builder output can include:

- a compact full-state snapshot
- a smaller incremental delta snapshot

## Interactive Sessions

Interactive session coordination lives in `src/app/session_service.py`.

Interactive session persistence lives in `src/app/session_store.py`.

Interactive history is stored under `.history/` in the target repository and tracks:

- prior user turns
- accumulated facts
- changed files
- validation runs
- remaining unknowns from the last turn
- the persisted Codex session id used for resume

## Response Rendering

Final result composition is in `src/runtime/result_composer.py`.

Terminal progress output is in `src/presentation/runtime_reporter.py`.

Final markdown rendering is in `src/presentation/responder.py`.

Understanding-style runs render:

- `## Answer`
- `## Evidence`
- `## Repo Map`
- `## Unknowns`
- `## Success Criteria`
- `## Suggested Next Questions`

Edit-style runs render:

- `## Summary`
- `## Files Changed`
- `## Validation`
- `## Risks`

If the step budget is exhausted, the final response is marked incomplete.

## Requirements

Runtime requirements:

- Python 3.11+
- local `codex` CLI

Optional local tools used when available:

- `rg`
- `find`
- `pytest`
- `ruff`
- `black`

## Tests

The active automated test files are:

- `tests/test_app_sessions.py`
- `tests/test_policy_structured.py`
- `tests/test_runtime_helpers.py`
- `tests/test_runtime_orchestrator.py`
- `tests/test_runtime_reporter.py`
- `tests/test_runtime_state.py`
- `tests/test_tools.py`

The test suite covers:

- interactive session creation and resume behavior
- Codex planner parsing, retries, session reuse, and tracing
- bounded runtime orchestration and action repair
- prompt-state serialization and runtime memory compaction
- bounded repo tools, shell queries, and command safety
- edit flows and validation flows
- progress reporting and final markdown output
