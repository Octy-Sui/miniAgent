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
