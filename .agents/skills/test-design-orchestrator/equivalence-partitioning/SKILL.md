---
name: equivalence-partitioning
description: Equivalence partitioning subskill of test-design-orchestrator. Splits unordered input or output domains into classes that behave alike and tests one representative per class.
metadata:
  author: jovd83
  version: 2.0.0
---

# Equivalence Partitioning

Use for a test unit whose values fall into unordered classes that the system should treat the same way: types, categories, formats, allowed vs rejected values. One representative per class is enough because every value in the class runs through the same logic. When the classes are ordered ranges with limits, use `../boundary-value-analysis/SKILL.md` instead; it builds the same partitions and adds the edges.

Read `./references/equivalence-partitioning.md` before generating: it holds the rules behind each step, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. List the inputs of the unit, and the outputs when outputs fall into classes.
2. Split each into valid and invalid partitions, one partition per distinct behaviour.
3. State the rule that makes the values in each partition interchangeable.
4. Pick one typical representative per partition.
5. Combine valid representatives into as few tests as possible (each choice); keep invalid representatives one per test.

## Required Output

- partition table with the "why equivalent" rule
- reduction table when valid partitions were combined
- test case summary (shared conventions template), `Covers` = partition IDs
- coverage row
