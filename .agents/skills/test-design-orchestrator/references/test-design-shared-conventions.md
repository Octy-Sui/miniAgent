# Shared Test Design Conventions

Rules and output templates that every technique subskill uses. Read once per task.

## Core Principles

- Keep source facts separate from assumptions. Label assumptions `ASM-n` and reference them in the tests they shape.
- Prefer the smallest test set that meets the unit's coverage target.
- Make every expected result observable and falsifiable. "The system handles it correctly" is not an oracle; "the order total shows EUR 45.00" is.
- Stop and ask only when a missing rule changes an expected result, not when it only changes wording.

## Traceability

- Carry requirement, story, rule and use-case IDs forward unchanged.
- When the input has no IDs, create local labels (`AC-1`, `RULE-2`, `PATH-3`) and say that you did.
- Test IDs follow `TC-<unit>-<nn>` (for example `TC-U2-03`). They are unique across the deliverable and stay stable between revisions.

## Negative Tests

- One invalid condition per test, with every other input valid. Several invalid values in one test hide which check failed (defect masking).
- Combine invalid conditions only in a deliberate robustness test, and label it as such.
- When the rejection behaviour (message, code, resulting state) is not specified, log an open question. Do not invent a message.

## Reduction

- Reduce test volume only when every retained test still has a clear oracle.
- Name the reduction rule (each choice, pairwise, collapsed decision-table columns, combined valid partitions) and state what it gives up.

## Output Templates

Markdown by default: tables for summaries, numbered steps for execution detail. Keep IDs identical across every table in the deliverable.

These templates describe test design content. Rendering them for a test-management tool is a separate formatting step outside this skill; the field names below (`id`, `title`, `objective`, `preconditions`, `steps[].action`, `steps[].expected_result`, `traceability`) follow a common normalized test case structure so a formatter can consume them without re-analysis.

### Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | "between 8 and 12" is inclusive | changes the expected result at 8 and 12 | TC-U1-05, TC-U1-08 |
| Q-1 | Maximum input length of the field? | defines the upper boundary of the too-long partition | U1 coverage |

### Test case summary (default)

| ID | Title | Covers | Test data / preconditions | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | Reject 7-character password | BV 7 (P1 max) | password `abcdefg` | rejected with "Password length is invalid." | REQ-PASSWORD-LENGTH |

`Covers` names the coverage items the test exercises: a partition, boundary value, decision-table column, transition, path or criterion. This column is what makes the coverage numbers checkable.

### Detailed test case (only when step-level detail is requested)

```markdown
### TC-U1-01 - <title>

- Objective: <what this test proves>
- Technique: <technique>
- Covers: <coverage items>
- Trace: <source IDs>
- Preconditions:
  1. <starting condition>
- Test data: <values>

| Step | Action | Expected result |
|---|---|---|
| 1 | <action> | <observable result> |
```

### Coverage

One row per unit, using the technique's own coverage measure:

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | BVA (3-value) | 10 boundary values | 10 | 100% | upper limit of the field unknown (Q-1) |

When a unit has no countable items, write `n/a` instead of a percentage. Add a coverage matrix only when the reviewer needs item-to-test mapping:

| Coverage item | Category | Covered by | Notes |
|---|---|---|---|
| BV 8 | valid boundary | TC-U1-05 | |
