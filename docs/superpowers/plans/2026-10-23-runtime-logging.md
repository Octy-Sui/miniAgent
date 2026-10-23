# Runtime logging implementation plan

> Execute inline using superpowers:executing-plans and test-driven-development.

**Goal:** Visible, safe runtime progress through miniagent, trigger, eval and resume.
**Architecture:** Standard logging with bounded background delivery to stderr and rotating JSONL. Context-local operation heartbeat; committed event summaries reuse journal facts. CLI owns configuration, domain code only emits diagnostics.
**Tech Stack:** Python 3.13, logging, asyncio, ContextVar, Pydantic settings, pytest.
**Spec:** Approved logging and heartbeat design in this conversation.

## Constraints

No new dependencies, no raw task/model/tool contents in logs, no README changes. Preserve cancellation, stdout and journal semantics. Default INFO, 15-second heartbeat, per-invocation JSONL with 20 MiB rotation and three backups. Logging failure must not fail business execution.

## Review focus

Cancellation must reclaim heartbeat tasks; parallel contexts must not leak; failed persistence must not emit committed success; eval workers must configure logging independently; unwritable log files must fall back to stderr.

## Tasks

- [x] 1. Add diagnostics context, operation heartbeat and logging configuration. Test stderr/JSONL, content exclusion, disabled heartbeat, cancellation, concurrency, failure fallback and nested sessions before implementation.
- [x] 2. Project committed events to safe summaries after RunRecorder commit. Test real runtime success/failure and failed persistence; wire operation scopes into model/tool/preparation/evaluation/finalization.
- [x] 3. Configure all invocation paths including eval parent/workers; emit batch progress and interruption details. Test actual subprocess entrypoints without external services and preserve existing contracts.
- [x] 4. Run targeted CLI/runtime/evaluation tests, ruff and ty, review the diff, document usage and limitations.

## Execution ledger

- Worktree creation blocked by filesystem sandbox (git ref lock); continuing in existing workspace under the skill's fallback. Existing untracked docs/swe-bench-verified-medium.json is excluded.
- User explicitly authorized execution of the discussed design; no extra plan approval requested. No commits requested; leave changes reviewable in working tree.

- Foundation tests first failed on missing modules; implemented safe stderr/JSONL delivery, context binding, nested heartbeats and settings.
- Runtime integration tests first failed on absent event projection and heartbeat; now cover committed ordering, failed persistence, cancellation and tool summaries.
- Entry-point tests exercise subprocess preparation failures for miniagent/trigger/eval and resume dry-run, plus preserved help/default-model contracts.
- Ruling: unify trigger default with DEFAULT_AGENT because its previous deepseek-v4-pro default lacks configured limits; preserve CLI/env overrides.
- Independent review found caller cancellation swallowed during heartbeat cleanup and cross-invocation delivery contamination. Both reproduced with failing tests, fixed, and reviewer re-verified (11 foundation tests passed).
- Initial broad run: 667 passed, 3 skipped, 2 failures from concurrent in-progress edits; final rerun passed: 681 passed, 3 skipped in 156.64s. Sandbox sysctl restrictions require escalated process-integration verification; no production workaround added.
- Verification: miniagent/trigger/eval --help exit 0; targeted CLI/runtime/evaluation suite passes. Real SWE-bench opt-in tests remain skipped; no paid external model task was launched.
- Ruff and ty pass. Usage/configuration documented in docs/logging.md. No README or unrelated user files changed.
