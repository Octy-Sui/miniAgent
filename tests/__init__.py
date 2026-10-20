"""Tests grouped by the boundary exercised, then by module.

ut: isolated logic and contracts, with doubles for collaborators as needed.
it: component collaboration, SDK adaptation, and real file/database/process I/O.
sit: application execution/recovery and Local, Docker, SWE-bench system journeys.
e2e: browser journeys and installed command entrypoints through user-visible output.

Shared fixtures, builders, drivers and acceptance tooling live in support/.
System and E2E tests may replace the external model service; their scope does not
imply live-model validation. inventory.json counts collected parameterized cases,
including environment-gated cases; it does not measure code/requirement coverage.

Run one level with `uv run pytest tests/ut -q` (or it, sit, e2e).
"""
