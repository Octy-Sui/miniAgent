<!-- repo-map:start -->
<!-- repo-map: provisional-against 439ff6a | mapped 2026-10-21 -->
# Repository Orientation

miniAgent 是 Python 3.13 的最小 Agent Runtime：模型在有界 Step loop 中调用工具；
Runtime 管状态、终止、usage/cost、cancellation 和 evaluation；CLI 管装配与 Workspace 生命周期。

## Current execution path

`miniagent` / `miniagent run` (`src/miniagent/cli/main.py`) → bootstrap/application (`src/miniagent/cli/bootstrap.py`, `src/miniagent/cli/application.py`) → `AgentRuntime`
(`src/miniagent/runtime/loop.py`, `src/miniagent/runtime/execution.py`) → provider + tools → evaluator → SQLite journal → patch/artifact collection → optional trajectory → `ApplicationResult` / Workspace cleanup.
Default composition wires `QwenProvider`, strict tool arguments, bounded observations, `read_file`, `shell`, Local/Docker Workspace.
Local uses `PassThroughEvaluator`; Docker uses candidate submission + `SWEbenchEvaluator` (`src/miniagent/cli/candidate_submission.py`, `src/miniagent/evaluation/swebench.py`).
Plain CLI Docker lacks internal task context; use `trigger` with a model present in both config files (`trigger`, `src/miniagent/cli/bootstrap.py`, `config/model_limits.toml`, `config/model_pricing.toml`).

Runtime requires explicit `run_store` and `artifact_store`; model requests/responses, raw tool returns and business command streams are captured regardless of JSON export (`src/miniagent/runtime/loop.py`, `src/miniagent/runtime/tool_runner.py`, `src/miniagent/environments/output_capture.py`).
Trajectory export is wired separately from contract-only `RunTrace`: `--trajectory/--no-trajectory` overrides `EXPORT_TRAJECTORY` (default false); resume inherits the frozen choice. Export failures preserve the Run outcome (`src/miniagent/cli/trajectory.py`, `src/miniagent/trajectory/`, `src/miniagent/cli/settings.py`).
SQLite Run/Event/Snapshot/Approval and Event Span scopes are wired; compaction validates sources at completed-Step boundaries and commits Event/Snapshot atomically (`src/miniagent/runtime/context.py`, `src/miniagent/runtime/journal.py`).
History consumers use fixed sequence boundaries and pages of at most 16 full Events (`src/miniagent/runtime/history.py`, `src/miniagent/storage/queries/events.py`).
Cross-process resume: `miniagent resume --run-id <uuid> [--database <path>] [--dry-run]`; frozen definitions/original Workspace identity are checked and uncertain shell execution blocks replay (`src/miniagent/cli/resume.py`, `src/miniagent/runtime/resume.py`).
`miniagent-dashboard` serves read-only Run/Event/Span、Snapshot/compaction、evaluation 和 saved artifact views (`src/miniagent/dashboard/cli.py`, `src/miniagent/dashboard/app.py`; optional extra in `pyproject.toml`).
General `RecoveryController` and `RunTrace` aggregation remain contract-only (`src/miniagent/runtime/recovery.py`, `src/miniagent/runtime/tracing.py`); treat `docs/blueprint.md` as roadmap.

## Start here

- CLI/config/composition/finalization: `src/miniagent/cli/`
- Trajectory encoding/evidence/export: `src/miniagent/trajectory/`, `src/miniagent/cli/trajectory.py`, `tests/it/trajectory/`, `tests/sit/trajectory/`
- Dashboard queries/projections/pages: `src/miniagent/dashboard/`, `tests/{ut,it,sit,e2e}/dashboard/`
- Evaluation/data/harness: `src/miniagent/evaluation/`, `tests/{ut,it,sit}/evaluation/`
- Run lifecycle/interruption/resume/compaction: `src/miniagent/runtime/`, `src/miniagent/cli/signals.py`
- Model protocol/provider/pricing: `src/miniagent/models/`, `src/miniagent/providers/`, `src/miniagent/metering/`
- Workspace/tools/artifacts: `src/miniagent/environments/`, `src/miniagent/tools/`, `src/miniagent/artifacts/`
- Behavioral tests: `tests/{ut,it,sit,e2e}/<domain>/`; fixtures/drivers: `tests/support/`
- Detailed routes: `docs/repository-map.md`; historical case inventory: `tests/inventory.json` (not coverage; collect again after additions).

## Source of truth and commands

Code/tests define behavior; `docs/CONTEXT.md` defines domain terms, not implementation navigation.

- Tests (not rerun for this docs-only refresh): `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest -q`; collection verified with `pytest --collect-only -q` through the same uv prefix.
- Verified type check: `uv --cache-dir /private/tmp/miniagent-uv-cache run ty check`.
- After Python changes: `uv run ruff check --fix <changed-files>` and `uv run ruff format <changed-files>`; avoid project-wide formatting.
- DEFAULT_AGENT uses configured `qwen3.8-flash`; working-tree configs also both contain `deepseek-v4.1-flash-1017`. Trigger default `deepseek-v4-pro` lacks matching config (`src/miniagent/agents/default_agent.py`, `trigger`, `config/model_limits.toml`, `config/model_pricing.toml`).
- Verified help through the same uv prefix: `miniagent --help`, `miniagent resume --help`, `python trigger --help`, `--extra dashboard miniagent-dashboard --help` (`pyproject.toml`).
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

Use these component names for scoped commits. Choose the component by the behavior being changed; the paths identify its usual implementation location.

| Component | Responsibility | Main paths |
| --- | --- | --- |
| `runtime` | Step loop, Run lifecycle, interruption/resume, context compaction, journal orchestration, and tracing contracts | `src/miniagent/runtime/` |
| `agents` | Agent specifications, default instructions, and model/tool selection | `src/miniagent/agents/` |
| `models` | Provider-independent model interfaces, messages, and request/response contracts | `src/miniagent/models/` |
| `providers` | Qwen/OpenAI-compatible API integration and provider protocol translation | `src/miniagent/providers/` |
| `tools` | Tool contracts, registry, argument validation, execution adapters, and result rendering | `src/miniagent/tools/` |
| `policy` | Tool permission decisions, rules, and approval contracts | `src/miniagent/policy/` |
| `env` | Local/Docker Workspace lifecycle, execution supervision, attach, and patch export | `src/miniagent/environments/` |
| `storage` | SQLite persistence, queries, migrations, transactions, and Run locks | `src/miniagent/storage/` |
| `artifacts` | Artifact content storage, manifests, and publication contracts | `src/miniagent/artifacts/` |
| `evaluation` | Independent evaluation, SWE-bench task data, harness execution, reports, and cleanup | `src/miniagent/evaluation/` |
| `metering` | Usage accounting, model pricing, and cost calculation | `src/miniagent/metering/` |
| `cli` | Command parsing, application composition, settings integration, signals, resume entrypoints, and delivery | `src/miniagent/cli/`, `trigger` |
| `dashboard` | Read-only dashboard entrypoint, HTTP routes, projections, templates, and static assets | `src/miniagent/dashboard/` |
| `config` | Repository configuration files, configuration formats, and loading/validation of those formats | `config/`, `src/miniagent/cli/model_limits.py` |
| `deps` | Runtime, optional, build, and development dependency maintenance | `pyproject.toml`, `uv.lock` |

Boundary rules:

* Use `runtime` for Run behavior and `cli` for command entrypoints and application assembly. Use `evaluation` for SWE-bench behavior. Replace the old `run` and `benchmarks` scopes in new commits with the owning component; historical messages need no changes.
* Use `models` for shared model contracts and `providers` for external API adaptation. Use `config` for configuration formats and loading, and `metering` for pricing and accounting behavior.
* Use `dashboard` for its CLI entrypoint as well as its server and UI. Use `env` for Workspace execution and `evaluation` for evaluator-owned Docker harness execution and cleanup.
* Tests that accompany a production change use that change's type and component. Test-only changes use `test:`, including cases under `tests/{ut,it,sit,e2e}/` and shared fixtures under `tests/support/`; test levels are not component names.
* Development tools such as `coverage.py` and agent instructions use `dev:`; documentation uses `docs:`; CI configuration uses `ci:`. These retain the unscoped formats defined above.
* Dependency-only changes use `chore(deps):`. A dependency update required by a feature or fix may be included in that change's commit; changing `pyproject.toml` alone does not determine the component.

For changes spanning multiple components, use the component that best represents the intent of the change rather than listing multiple components. Split unrelated changes into separate commits when practical.
