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
