# Tool Result Projection Implementation Plan

> Execute inline using executing-plans and test-driven-development. The user has approved the design and requested implementation.

**Goal:** Keep tool execution output once in structured data; generate model messages and persistence dictionaries on demand.

**Architecture:** ToolResult has no content field. Its to_response_item(limits) returns a ToolMessage; tool_result_metadata() returns a complete JSON-compatible dictionary. ToolEnded records the canonical result and no observation. Runtime decisions use canonical results rather than parsing model messages. Rejected returns retain separate raw evidence because they differ from the effective result.

**Tech Stack:** Python 3.13, Pydantic, SQLite, pytest.

**Spec:** Approved conversation design, including atomic result/state commit before the next model request. No historical database compatibility required.

## Constraints
- Preserve captured command bytes and actual model requests in trajectory.
- No README edits, new dependencies, provider-specific protocol, or per-tool rendering dispatch.
- Keep output bounded without mutating canonical data.

## Tasks
- [x] Add failing tests for single-copy shell results, complete metadata, bounded model messages, and runtime persistence without observation.
- [x] Implement contracts/projection and migrate tool producers and fixtures to structured data.
- [x] Update runtime completion, event validation, storage consumers, dashboard, and trajectory.
- [x] Run targeted tests, changed-file Ruff, type checking; review failures and final diff.

## Review focus
- Large output and Unicode must retain complete canonical data while producing bounded model messages.
- A nonzero exit or timeout must retain its error and available output.
- Rejected returns must preserve original evidence and effective error separately.
- Failure to persist must prevent advancing conversation or calling the model.
- Resume must use persisted execution status, independent of model-output truncation.

## Implementation decisions
- Accepted returns are stored once in ToolEnded.result. Separate raw_result evidence is retained only for rejected returns; large rejected returns still use artifacts.
- ToolBatchOutcome no longer accumulates unused full results across a batch.
- Oversized structured model payloads may omit data with an explicit omission marker; canonical persistence is unchanged. Model-message budget allocation avoids repeated full-payload serialization.
- Independent review found and re-verified the structural-size and serialization-cost cases.

## Verification
- Initial complete suite: 1963 passed, 20 skipped; two dashboard unset-field regressions were reproduced, fixed, and both passed on `pytest --lf`.
- Latest targeted tool/runtime/storage/dashboard/trajectory verification: 622 passed, 6 deselected; all seven new projection cases pass.
- Changed-file Ruff check/format, `ty check`, and `git diff --check` pass.
