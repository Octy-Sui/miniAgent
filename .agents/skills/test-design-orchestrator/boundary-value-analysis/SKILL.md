---
name: boundary-value-analysis
description: Boundary value analysis subskill of test-design-orchestrator. Builds ordered partitions and boundary tests for numeric ranges, lengths, dates, counts and positions.
metadata:
  author: jovd83
  version: 2.0.0
---

# Boundary Value Analysis

Use for a test unit whose logic is an ordered domain with limits: numeric ranges, text lengths, date windows, item counts, ranked positions. Defects cluster at the edges (off-by-one, `<` instead of `<=`), so the tests sit on and next to each boundary. Not for unordered categories: use `../equivalence-partitioning/SKILL.md` for those.

Read `./references/boundary-value-analysis.md` before generating: it holds the rules behind each step, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. Partition the domain first. The minimum and maximum of each partition are its boundary values.
2. Record inclusivity for every limit, stated or assumed.
3. Fix the step size and write it in the boundary inventory.
4. Add implicit boundaries (empty, zero, field maximum) only when they are real for the unit, and mark them inferred.
5. Pick the values: 3-value BVA by default, 2-value when the user asks for a lean set. Drop neighbours that cannot exist and remove duplicates.
6. One test per value. Invalid values go one per test, with all other inputs valid.
7. Take expected results from the source. Unknown rejection behaviour becomes an open question.

## Required Output

- boundary inventory, including the step size
- boundary values table
- test case summary (shared conventions template), `Covers` = boundary value or neighbour
- coverage row naming the variant (2-value or 3-value)
