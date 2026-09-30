---
name: decision-table
description: Decision table subskill of test-design-orchestrator. Models business rules that combine several conditions into outcomes, exposes missing or contradictory rules, and derives one test per rule column.
metadata:
  author: jovd83
  version: 2.0.0
---

# Decision Table

Use for a test unit whose outcome is defined by rules that combine two or more conditions: eligibility, pricing, discounts, approval and rejection logic. The table makes every combination visible, so it also exposes gaps and contradictions in the rules. Report those; they are often the most valuable result.

Read `./references/decision-table.md` before generating: it holds the rules behind each step, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. List conditions and actions (limited entry Y/N, or extended entry for multi-valued conditions).
2. Count the combinations and state the number; above roughly 32, build the collapsed table directly.
3. Fill every column from the source, or mark it `?` (unspecified), `impossible` or `conflict`.
4. Collapse only columns with the same actions and the same expected result.
5. Derive one test per retained column; add a boundary table for conditions that are ranges.

## Required Output

- decision table (full or collapsed; say which)
- findings: unspecified, contradictory and redundant rules
- optimization notes when columns were collapsed
- test case summary (shared conventions template), `Covers` = rule column
- coverage row
