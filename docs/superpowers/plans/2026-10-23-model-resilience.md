# Model request resilience implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Recover bounded transient model failures and continue on unusable usage metadata without weakening pricing, persistence, or tool execution contracts.

**Architecture:** Provider translates one HTTP attempt into a usable response or a classified error. Runtime owns retry timing and persists progress in the existing journal. Usage validation is independent of response validation. No additional database, dependency, or recovery framework.

**Tech Stack:** Python 3.13, Pydantic, asyncio, existing OpenAI-compatible client, SQLite, pytest.

**Spec:** Approved conversation recommendation, with the user's correction: price configuration validation and cost calculation failures remain strict; eval cost remains an important metric.

## Global constraints

- Keep SDK retries disabled; each runtime attempt is observable.
- At most five consecutive failed business attempts; delays: 429 10/30/60/120, 500 and connection 1/2/5/10, 502/503/504/read timeout 5/10/20/40 seconds, plus positive jitter.
- Honor Retry-After, support cancellation during waits, use a 900-second logical request deadline.
- Preserve existing context repair and compaction retry limits without multiplying retries.
- Invalid or missing usage does not discard valid content or replay tools. Unknown consumption remains explicit.
- Preserve strict pricing, persistence, tool supervision, evaluation, and cancellation; optional output failures cannot replace the Run result.
- Do not edit README or the user's untracked docs/swe-bench-verified-medium.json.

## Review focus

- Mixed errors and restart cannot reset retry limits or bypass remaining wait.
- A saved response cannot be submitted or charged twice after recovery.
- Invalid usage and valid tool calls must still execute once; invalid tool calls remain rejected.
- Old frozen definitions must remain verifiable when defaults are added.
- Summary retries, cost failures, and auxiliary output errors must retain their distinct semantics.

### Task 1: Provider classification and usage separation

Files: models/protocol.py, providers/qwen.py, tests/it/providers/.
Interfaces: ModelFailure metadata on ModelProviderError; ModelResponse usage_issue and safe usage evidence.
- [x] Add failing HTTP/transport classification and invalid-usage continuation tests.
- [x] Implement safe classification, Retry-After parsing, independent usage normalization.
- [x] Run provider tests; keep pricing untouched.

### Task 2: Durable bounded Runtime retry

Files: runtime/config.py, progress.py, execution.py, loop.py, retry.py, events.py; tests/ut/runtime/test_model_retry.py and tests/it/runtime/test_model_retry.py.
Interfaces: retry configuration; persisted business and summary retry progress; failure metadata on attempt end events.
- [x] Add failing tests for recovery, exhaustion, mixed failures, cancellation, deadline and restart.
- [x] Implement retry scheduling, atomic failure/progress commits, cancellation-aware waits and stable Step identity.
- [x] Reuse policy for compaction without nested attempt budgets.
- [x] Verify request/context resume and storage compatibility.

### Task 3: Nonfatal usage and auxiliary outputs

Files: runtime/loop.py, execution.py, cli/eval_batch.py and consumers as needed; tests/it/runtime/test_model_evidence.py, test_terminal_resume_boundaries.py, tests/it/cli/.
- [x] Change expected behavior for valid responses without usage; retain explicit cost failure tests.
- [x] Preserve cost completeness and request success semantics; optional export/statistics failures stay separate from Run outcome.
- [x] Verify CLI/bootstrap pricing and trajectory/dashboard contracts.

### Task 4: Verification and review

- [x] Ruff check/format changed Python files; ty check.
- [x] Run affected provider/runtime/storage/CLI/evaluation/trajectory/dashboard tests.
- [x] Independent whole-change review; address critical issues and rerun affected checks.
- [x] Report changes, verification and remaining limitations.

## Execution ledger

- Work started on feat/model-request-resilience; existing checkout retained to avoid moving the user's workspace. Git branch creation required sandbox approval and succeeded.
- User authorized implementation of the in-chat recommendation; no additional design approval requested.
- Baseline provider/model evidence/request resume tests started before edits.

- Independent review identified three boundary defects; failing tests reproduced each before correction: pricing validation with missing usage, first summary attempt deadline, and SDK coercion of invalid usage counters. Reviewer follow-up confirmed all three fixes.
- Added a real SQLite reopen test during summary backoff; persisted wait time and attempt numbering survive interruption. Historical frozen configs omit the new optional config and retain their prior behavior.
- Sandboxed system-process/socket tests required an authorized rerun; that subset passed (159 tests). Final affected-area regression is recorded below.

- Final verification: 1,088 affected-area tests passed in 100.48 seconds; Ruff check/format, `ty check`, and `git diff --check` passed. Test output: `/private/tmp/model-resilience-final.log`. No live provider calls or historical eval reruns.
- Delivery: the user requested committing this change and merging it into local `main`. Price validation and calculation remain strict; partial usage/cost is explicitly incomplete. No remote push is requested.
