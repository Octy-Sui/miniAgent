# File Prompts Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Load system and instance prompts from `config/deepseek_prompt.yaml` at runtime.

**Architecture:** Validate YAML with Pydantic and render instance prompts with strict Jinja. CLI composition supplies resolved text to existing Run persistence; resume continues using frozen text. Batch workers consume a snapshot captured before launching jobs.

**Tech Stack:** Python 3.13, Pydantic, PyYAML, Jinja, pytest.

**Spec:** Approved conversation proposal, with the user's clarification that both SWE-bench entrypoints use the current eval_batch instance prompt.

## Global Constraints

- Preserve current working-tree system and eval_batch instance prompt text.
- Keep ordinary CLI task text and resume behavior unchanged.
- No import-time file reads; validate before creating a Workspace.
- Preserve existing user edits; work in the current checkout as requested.

## Review Focus

- Missing/unreadable files, malformed YAML, wrong fields and blank prompts fail with the file path.
- Undefined or malformed templates fail before execution.
- Task text containing Jinja syntax is not evaluated again.
- CLI help and resume do not require the prompt file.
- Changing the original prompt file cannot alter active batch workers.

### Task 1: Load and compose file prompts

**Files:** `config/deepseek_prompt.yaml`, `src/miniagent/cli/prompts.py`, `src/miniagent/agents/default_agent.py`, `src/miniagent/cli/main.py`, `trigger`, `src/miniagent/cli/eval_batch.py`, `pyproject.toml`, `uv.lock`, targeted CLI tests.

**Interfaces:** `PromptConfig`, `load_prompts(path: Path | None = None) -> PromptConfig`, `build_default_agent(system_prompt: str) -> AgentSpec`; `parse_config(..., prompts: PromptConfig | None = None)` renders SWE-bench tasks only.

- [x] Write loader/validation and composition tests, then verify failure.
- [x] Migrate exact current text and implement loading, runtime composition and batch snapshots.
- [x] Run targeted CLI, trigger and resume tests; run changed-file Ruff and type checks.
- [x] Review diff for exception paths and caller compatibility; report results without committing user edits.

## Execution Evidence

- Initial tests failed because runtime YAML loading and shared template rendering did not exist.
- Migration comparison against saved pre-edit strings confirmed exact system text and rendered instance text preservation.
- Related trajectory/trigger/prompt regression group: 61 passed.
- Related CLI/resume/storage/evaluation regression group: 239 passed, 2 skipped.
- Changed-file Ruff, `ty check`, and `git diff --check` passed.
- Independent review found conditional undefined variables could escape empty-input validation. Fixed with template variable analysis and a RED→GREEN regression. A second RED→GREEN regression verifies task-dependent rendering errors retain the source filename.
- Temporary-source CLI tests now explicitly supply `PROMPT_FILE`; trigger tests use their Docker executable for both evaluation and Workspace preflight instead of depending on the host daemon.
