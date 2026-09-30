---
name: state-transition
description: State transition subskill of test-design-orchestrator. Models states, events and guards of a lifecycle and derives valid-transition, invalid-transition and path tests.
metadata:
  author: jovd83
  version: 2.0.0
---

# State Transition

Use for a test unit with durable states, where whether an event is allowed, and what it does, depends on the current state: lifecycles, approval pipelines, account or session status, devices with operating modes. Not for a plain rule matrix or a range check.

Read `./references/state-transition.md` before generating: it holds the rules behind each step, the coverage targets, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. Identify states, events, guards and actions; mark the start state and any final states.
2. Build the state table (every state against every event); unspecified cells stay `?`.
3. Choose the coverage target; valid transitions (0-switch) is the default.
4. Build paths from the start state; give each specified invalid transition its own short test.
5. Check the state after every step, not only at the end of a path.
6. Add a boundary table for guards with thresholds.

## Required Output

- state table
- transition list
- path table
- test case summary (shared conventions template), `Covers` = transition or path IDs
- coverage row per target
