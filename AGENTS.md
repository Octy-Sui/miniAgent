<!-- repo-map:start -->
<!-- repo-map: verified-against 13c87be | mapped 2026-09-17 -->
# Repository Orientation

miniAgent 是 Python 3.13 的最小 Agent Runtime：模型在有界 Step loop 中调用工具，
Runtime 负责状态、终止、usage/cost、cancellation 和 evaluation；CLI 负责依赖装配与
Workspace 生命周期。

## Current execution path

`miniagent` CLI (`src/miniagent/cli/main.py`) → bootstrap/workspace
(`src/miniagent/cli/bootstrap.py`) → `AgentRuntime`
(`src/miniagent/runtime/loop.py`) → provider + `ToolRegistry`/tools → evaluator →
`RunResult` and optional patch artifact. Default composition wires `QwenProvider`,
strict tool argument validation, bounded structured tool results, `read_file`, `shell`,
Local/Docker Workspace, and `PassThroughEvaluator`.

`src/miniagent/runtime/events.py`, `src/miniagent/runtime/snapshots.py`, and
`src/miniagent/runtime/recovery.py` currently define contracts and validation models;
they are not wired into `AgentRuntime`. Treat `docs/blueprint.md` as roadmap, not
proof of current behavior.

## Start here

- CLI/config/composition: `src/miniagent/cli/`, `src/miniagent/models/settings.py`
- Run lifecycle/termination/cancellation/usage: `src/miniagent/runtime/`
- Model protocol/provider/pricing: `src/miniagent/models/`, `src/miniagent/providers/`
- Workspace isolation/tools/artifacts: `src/miniagent/environments/`,
  `src/miniagent/tools/`, `src/miniagent/artifacts/`
- Behavioral coverage: matching `tests/test_*.py` modules
- Detailed task routes and implementation status: `docs/repository-map.md`

## Source of truth and verified commands

Code and tests define current behavior. `docs/CONTEXT.md` defines canonical domain
terms; keep implementation details out of it. Use `docs/repository-map.md` for navigation.

- Tests: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q`
- Type check: `ty check`
- CLI surface: `uv run miniagent --help`

<!-- repo-map:end -->

# Python Style Guide

1. Target Python 3.13.x.
2. Use type annotations. Prefer built-in generics such as `list[str]`,
   `dict[str, int]`, and `str | None` over legacy `typing.List`,
   `typing.Dict`, and `Optional`.
3. Prefer `pathlib` over `os.path`. Use `Path.read_text()` and
   `Path.write_text()` for small text files; use streaming I/O when
   appropriate.
4. Use `typer` for command-line interfaces.
5. Keep comments minimal. Comments should explain non-obvious decisions,
   constraints, or reasoning rather than restating the code.
6. Do not modify or append to README files unless explicitly requested.
7. Use Jinja for reusable or non-trivial text and prompt templates.
   Prefer f-strings for simple interpolation.
8. Use `Pydantic` BaseModel for configuration and externally sourced structured data.
   Use `dataclass` for simple internal data containers.
9. Do not catch exceptions unless you can recover, retry, translate the
   exception, or add meaningful context. Otherwise let exceptions propagate.
10. Prefer concise, straightforward implementations. Do not sacrifice
    readability or correctness merely to reduce line count.
11. Avoid intermediate variables that only alias an expression without
    adding meaning. Keep them when they improve readability, debugging,
    or represent a domain concept.
12. Prefer fail-fast behavior over silently swallowing errors.
13. This repository favors minimal abstractions and minimal dependencies.
    Do not introduce helpers, classes, abstractions, or dependencies until
    they provide clear value.

## Test style

1. Use `pytest`, not `unittest`.
2. Prefer real behavior over mocks. Do not mock or patch internal implementation details unless explicitly requested. Use fakes or mocks only at external boundaries when necessary.
3. Test meaningful behavior, contracts, edge cases, and failure modes. Avoid trivial tests that merely restate Python, framework behavior, or implementation details.
4. Keep each test focused on one behavior. Multiple assertions are fine when they verify the same behavior or result.
5. Inline expressions that are used by only one assertion:

```python
# bad
result = func()
assert result == expected

# good
assert func() == expected
```

Keep intermediate values when they are reused or improve readability.

6. For `pytest.mark.parametrize`, use a tuple of argument names and a list of argument values:

```python
@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (1, 2),
        (2, 4),
    ],
)
```

7. Test observable behavior and public contracts rather than internal implementation details.
8. When the reason for an exception is part of the contract, use `pytest.raises(..., match=...)` to verify the expected failure.
9. Run tests only for changes that can affect runtime behavior. Skip tests for docs, instructions, comments, formatting, and non-runtime config. Prefer targeted tests over the full suite.

# Commit Messages

Use the following format:

* `test: description` for test changes.
* `ci: description` for CI configuration and GitHub workflow changes.
* `dev: description` for development tooling and development instructions, including Cursor or Claude rules.
* `fix(component): description` for bug fixes.
* `feat(component): description` for new capabilities.
* `enh(component): description` for improvements to existing capabilities, behavior, performance, or usability.
* `docs: description` for documentation.
* `ref(component): description` for internal refactoring that does not intentionally change behavior.
* `chore: description` or `chore(deps): description` for maintenance tasks such as dependency updates, pre-commit hooks, and import cleanup.

Descriptions should explain the intent of the change rather than implementation details.

Use lowercase descriptions and do not end them with a period.

## Style notes

<IMPORTANT>Do **NOT** add `Co-authored-by: Cursor` or similar AI attribution lines to commit messages or trailers.</IMPORTANT>

## Reviewing

Before preparing a commit message, review the changes for critical issues.

Flag as critical:

* Unintentional unhandled exceptions.
* Logic that appears incorrect or internally inconsistent.
* Breaking changes to public interfaces, protocols, schemas, configuration formats, or CLI contracts without corresponding updates to implementations or callers.

Flag as minor:

* Imports not at the top of the file.

Minor issues should be reported but should not block the commit.

Do not flag stylistic preferences or unrelated minor improvements.

## Components

Use these component names for scoped commits:

* `models` — model interfaces and providers such as litellm, anthropic, openai, portkey, and openrouter.
* `agents` — agent classes such as default, interactive, and multimodal agents.
* `env` — execution environments such as docker, local, singularity, bubblewrap, and swerex.
* `config` — configuration files and configuration handling.
* `run` — run scripts such as mini and hello_world.
* `benchmarks` — benchmark runners such as swebench and inspector.
* `cli` — CLI argument parsing and behavior.
* `deps` — dependency maintenance.

For changes spanning multiple components, use the component that best represents the intent of the change rather than listing multiple components. Split unrelated changes into separate commits when practical.
