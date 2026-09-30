---
name: acceptance-criteria-to-test-cases
description: Acceptance criteria subskill of test-design-orchestrator. Converts behavioural acceptance criteria into traceable Given/When/Then scenarios, one behaviour per scenario, with gaps flagged instead of invented.
metadata:
  author: jovd83
  version: 2.0.0
---

# Acceptance Criteria to Test Cases

Use for acceptance criteria written as behaviour ("the user sees...", Given/When/Then) that contain no range, rule combination, lifecycle or combinatorial logic. A criterion that does contain such logic belongs to its own test unit with the matching technique; its tests keep the AC ID.

Read `./references/acceptance-criteria-to-test-cases.md` before generating: it holds the rules behind each step, the scenario-writing rules, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. Separate the story, the criteria, notes and examples; number criteria `AC-n` when they have no IDs.
2. Check each criterion for embedded logic and flag it as its own unit instead of flattening it.
3. Map examples and questions per criterion.
4. Write one scenario per example: context in `Given`, one action in `When`, an observable result in `Then`.
5. Add negative scenarios only when the rejection behaviour is stated or safely implied; otherwise log a gap.
6. Use `Scenario Outline`, `Background` and `Rule` only when they improve clarity.

## Required Output

- criterion mapping (criterion, examples, scenarios, questions)
- scenarios in Given/When/Then design notation with AC tags
- coverage row
