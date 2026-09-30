---
name: use-case-testing
description: Use case testing subskill of test-design-orchestrator. Turns actor-system flows with main, alternate and exception paths into path-based tests with postcondition checks.
metadata:
  author: jovd83
  version: 2.0.0
---

# Use Case Testing

Use for a test unit organized as an actor-system interaction: a trigger, preconditions, a main success scenario, alternate and exception paths, and postconditions.

Read `./references/use-case-testing.md` before generating: it holds the rules behind each step, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. Extract actor, trigger, preconditions, main success scenario, alternates, exceptions, postconditions and failure guarantees.
2. Build the path inventory: where each alternate or exception branches, its condition, and where it ends.
3. One test per path; loops are tested once around and then to completion.
4. Combine alternates in one test only when they are independent and the combination is realistic.
5. Check postconditions on every path, including failure guarantees on aborted paths.
6. Split data rules inside a step (a range, a rule set) into their own units.
7. Add one test per role when roles change behaviour.

## Required Output

- use case summary
- path inventory
- test case summary (shared conventions template), `Covers` = path ID; detailed test cases when the user wants executable scripts
- coverage row
