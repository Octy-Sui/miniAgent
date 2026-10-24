# Required Run Persistence Implementation Plan

**Goal:** Remove the internal storeless Run execution mode already disallowed by AgentRuntime.

**Architecture:** Keep RunJournal transaction orchestration and RunRecorder commit confirmation. Require both stores in execution construction; use committed Tool Results, execution evidence, Snapshot identity and approval versions throughout.

**Spec:** `docs/superpowers/specs/2026-10-20-trajectory-architecture-design.md`, section 7; the user authorized implementing the architecture-review candidate.

## Constraints

- Preserve the legitimate uncreated recorder state and reject commits before creation.
- Publish State/Trace only after successful persistence; never silently downgrade to memory.
- Preserve atomic State/Event/Approval/Snapshot commits, bounded history reads and resume budgets.
- Preserve frozen definitions and persisted schemas; do not change unrelated working-tree edits.
- No new dependencies or automatic commits; implement in the current checkout.

## Task 1: Enforce required internal dependencies

- [x] Add tests rejecting a missing RuntimeStore and ArtifactStore before Run creation; observe failure.
- [x] Require RuntimeStore in RunRecorder and ArtifactStore in RunExecution start/attach/construction.
- [x] Migrate direct constructors and test fixtures to explicit real temporary stores.

## Task 2: Remove alternate execution semantics

- [x] Remove in-memory Tool Result and ended-execution mirrors; retain bounded persisted-history queries.
- [x] Remove missing-record sequence/Snapshot fallbacks and nullable commit results; keep an explicit initialized-record check.
- [x] Remove approval-version and suspension fallbacks; preserve Journal transaction responsibilities.
- [x] Migrate the commit-failure test to a real SQLite constraint failure; verify state, trace and persisted position do not advance.

## Task 3: Verify and review

- [x] Run targeted runtime/CLI/evaluation/trajectory tests and type checking; format only changed Python files.
- [x] Independently review invariants, then run graphify update and inspect the final diff.

## Review focus

- Startup callback receives the created record before the first running-state commit.
- Before creation, reading a required record fails clearly rather than inventing identity.
- Resume, approval expiry and cancellation retain committed execution evidence and versions.
- ArtifactStore injection is consistent across live execution and preparation-only callers.

## Evidence

- Baseline: persistence-dependency and runtime tests: 24 passed.

- Internal dependency tests observed RED, then GREEN; core runtime/compaction regression: 67 passed.
- Type checking and targeted Ruff checks/formatting passed.
- Independent read-only review: no Critical or Important findings; direct constructor coverage checked.
- Broader regression (runtime, trajectory, CLI, evaluation and signals): 819 passed, 1 skipped in 434.67s with macOS sysctl access. Log: `/private/tmp/miniagent-required-persistence-unsandboxed.log`. The earlier sandbox run failed on denied process-identity sysctl access.
- graphify update completed: 6,434 nodes and 23,942 edges; generated outputs retained in graphify-out/.
