# Acceptance Criteria to Test Cases Reference

Use this approach when the source is a user story or a list of acceptance criteria and the goal is behaviour-focused scenarios.

## Best-Fit Inputs

- user stories with explicit acceptance criteria
- refinement notes with example behaviours
- requirements already phrased as user-observable outcomes

## Rules per Workflow Step

### 1. Separating the input

Separate the story, the acceptance criteria, notes and examples. Number criteria `AC-n` when they have no IDs, and say that you did.

### 2. Embedded logic

Check each criterion for a range, a rule combination, a state change or a multi-step flow. If you find some that is not yet its own unit, say so, for example "AC-3 contains a threshold; tested as U2 with boundary value analysis", instead of flattening it into one scenario.

### 3. Example mapping

For each criterion, write the concrete examples that illustrate it (the main example first, then each stated alternative or rejection) and the questions it raises. Examples become scenarios; questions become open gaps.

### 4. Scenario rules

- One scenario covers one business behaviour.
- `Given`: the starting context (state, data), not a series of clicks.
- `When`: one action or event.
- `Then`: an observable result that a tester can check without knowing the implementation. Expected results are observable, not aspirational.
- `And` only when it keeps the scenario readable.
- Keep steps implementation-agnostic unless the user asks for UI-level steps.
- Title = context + outcome ("Expired card is rejected at checkout"), not a ticket label.

### 5. Negative scenarios

Add them when the system legitimately branches and the rejection behaviour is stated or safely implied. A plausible but undefined rejection is a gap, not a made-up `Then`.

### 6. Structure

- `Scenario Outline` with an `Examples` table only for the same behaviour over several data rows.
- `Background` for setup shared by all scenarios of the story.
- `Rule` to group the scenarios of one business rule.
- One capability per feature; split when a feature combines unrelated behaviour.

## Traceability

Keep the acceptance criterion ID visible on every scenario (as a tag in the design notation) and reuse the same labels across the mapping table and the scenarios.

## Coverage

Acceptance criteria coverage = criteria with at least one scenario / criteria. Also list criteria whose negative behaviour is undefined.

## Output Templates

Criterion mapping:

| Criterion | Examples | Scenarios | Questions / gaps |
|---|---|---|---|
| AC-1 | <example 1>; <example 2> | TC-U1-01, TC-U1-02 | - |

Scenario (design notation; producing an importable `.feature` file is a formatting step outside this skill):

```gherkin
@AC-1
Scenario: <context and outcome>
  Given <starting context>
  When <one action or event>
  Then <observable outcome>
```

Then the coverage row from the shared conventions.

## Common Failure Modes

- merging unrelated criteria into one scenario and losing traceability
- inventing rejection behaviour that the criteria never define
- turning vague wording ("fast", "user-friendly") into hard assertions without logging an assumption
- writing a `Then` that cannot be observed
