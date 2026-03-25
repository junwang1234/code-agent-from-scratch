# Architecture

## Module Responsibilities

The repository is organized by responsibility, not by framework or provider.

- `src/app`
  - CLI parsing
  - interactive REPL loop
  - interactive session load/save behavior
  - request shaping for multi-turn sessions
- `src/models`
  - pure shared dataclasses
- `src/planning`
  - planner interface
  - prompt construction
  - Codex-backed structured planner
- `src/providers`
  - LLM provider protocol
  - Codex CLI transport and request adaptation
- `src/runtime`
  - one bounded run
  - runtime memory updates and compaction
  - action normalization and repair
  - tool outcome interpretation
  - validation environment discovery, probing, selection, and bootstrap policy
  - turn artifact construction
  - final result composition
- `src/tools`
  - bounded local repo, search, edit, and command tools
  - validation environment tools for discovery, probing, and bootstrap
- `src/presentation`
  - runtime progress reporting
  - final markdown rendering

## Main Flow

For one interactive turn, the flow is:

1. `src/app/main.py` parses CLI args and builds the planner, reporter, and runtime.
2. `src/app/interactive_loop.py` starts or resumes the interactive session.
3. `src/app/task_builder.py` builds the current task question from session context plus the new user request.
4. `src/runtime/agent_runtime.py` starts a bounded run.
5. `src/planning/structured_planner.py` asks Codex for a plan, then asks for one next action at a time.
6. `src/runtime/action_normalizer.py` and `src/runtime/action_repair.py` turn the proposal into a deterministic execution command.
7. `src/runtime/action_execution.py` dispatches that command through the validated tool registry.
8. `src/runtime/tool_outcomes.py` and `src/runtime/memory_manager.py` write the resulting observations back into runtime memory.
9. `src/runtime/result_composer.py` shapes the final `TaskResult`.
10. `src/presentation/responder.py` renders markdown, and `src/app/session_service.py` persists the turn.

## Layering Rules

The current layering rules are:

- `models` contains data only
- `app` owns long-lived user interaction
- `planning` owns prompts and structured planner calls
- `providers` own backend transport details
- `runtime` owns state transitions, retries, safety repairs, and final result composition
- `tools` execute bounded local actions
- `presentation` renders output, but does not decide runtime behavior

## Naming Conventions

Within `runtime`, file names should describe the concrete behavior:

- `action_*` for action execution, normalization, repair, and runtime-facing outcomes
- `memory_*` for runtime memory and prompt-state handling
- `observation_*` for analysis of observed tool output
- `tool_outcomes.py` for mapping raw tool results into memory updates
- `turn_*` for final per-turn artifact shaping

Avoid vague names like `helpers` when the module has a clear responsibility. The current code already uses explicit names such as `action_repair.py`, `file_context_helpers.py`, and `result_composer.py`.

## Validation Subsystem

Validation environment logic now lives in `src/runtime/validation/`.

This subpackage owns:

- resolver-driven discovery across supported ecosystems
- candidate ranking and stable profile/plan ids
- cheap probing before default selection
- bootstrap-plan approval policy and execution orchestration
- validation-specific prompt-state summaries

Current resolver modules:

- `src/runtime/validation/resolvers/python.py`
- `src/runtime/validation/resolvers/node.py`
- `src/runtime/validation/resolvers/go.py`
- `src/runtime/validation/resolvers/java.py`
- `src/runtime/validation/resolvers/rust.py`
- `src/runtime/validation/resolvers/ci_fallback.py`

The planner-facing tool surface for this subsystem is:

- `discover_validation_env`
- `probe_validation_profile`
- `bootstrap_validation_env`

Existing command tools also consume discovered validation commands:

- `run_tests` can execute selected profile test argv
- `format_code` can execute selected profile formatter argv
- `run_command` can execute selected profile lint argv
