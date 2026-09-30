---
name: test-design-orchestrator
description: Design black-box tests from requirements, stories, acceptance criteria, rules, use cases, state models or bug reports. Picks an ISTQB technique per requirement (boundary values, partitions, decision tables, pairwise, state transitions) with traceable tests and coverage notes. Not for tool export.
metadata:
  dispatcher-layer: execution
  dispatcher-lifecycle: active
  dispatcher-category: testing
  dispatcher-capabilities: test-design, black-box-technique-selection, confirmation-test-design
  dispatcher-accepted-intents: design_confirmation_tests, select_test_design_technique, generate_structured_test_cases
  dispatcher-input-artifacts: requirement_text, acceptance_criteria, user_story, business_rules, use_case, state_model, defect_report
  dispatcher-output-artifacts: test_case_set, technique_selection, assumptions_log
  dispatcher-stack-tags: testing, framework-agnostic, black-box
  dispatcher-risk: medium
  dispatcher-writes-files: false
---

# Test Design Orchestrator

> **Author:** jovd83 | **Version:** 2.0.0

Turn a test basis into traceable black-box test cases. Split the input into test units, pick the technique that fits each unit's logic, apply it with the matching technique subskill, and keep assumptions and coverage visible.

## Scope

- **In scope:** choosing test design techniques, designing test cases, logging assumptions and open questions, reporting coverage and residual risk.
- **Out of scope:** rendering tests for test-management or automation tools (Xray, Zephyr, TestLink, TestRail, importable `.feature` files, CSV). Deliver review-ready markdown. If the user wants a tool format, say that rendering is a separate formatting step; when a skill router is available, hand the finished tests off with intent `render_test_artifact`. Never invent a tool schema.
- Also out of scope: reviewing requirement quality, writing a test strategy, writing automation code.

## Workflow

### 1. Normalize the test basis

- Identify the source type and extract actors, inputs, rules, states, flows, expected outcomes, and IDs (story, requirement, rule, use-case IDs).
- If the input is a defect report, or the user wants tests that prove a bug, also follow [Defect confirmation](#defect-confirmation).
- Collect open questions while you read. Ask them in **one** round, and only when an answer would change an expected result. Otherwise continue and log the gap as a numbered assumption (`ASM-1`, ...). A visible assumption the reviewer can correct costs less than a stalled design session, especially when this skill runs inside a larger workflow.

### 2. Split the input into test units

A test unit is one piece of logic with one dominant shape: a field rule, a rule set, a lifecycle, a flow, a group of interacting parameters, a behavioural acceptance criterion.

Real specs mix shapes. One story can hold a length limit, a discount rule and a status lifecycle. Each technique has its own coverage model, so stretching one technique over all of it hides gaps: a decision table says nothing about the EUR 49.99 edge, and boundary analysis says nothing about which rule combinations exist.

- Single-shape input is one unit. Do not split for the sake of it.
- Logic about the same field or the same rule belongs in one unit.
- Keep the source ID on every unit so traceability survives the split.

### 3. Select a technique per unit

Judge the logic inside the text, not the document type. An acceptance criterion saying "free shipping from EUR 50" is a boundary unit that happens to be traced to an AC.

Walk these questions in order for each unit. The first "yes" wins.

| # | Question | Technique | Tell-tale signs |
|---|---|---|---|
| 0 | Did the user name a technique? | That technique; note it when the logic suggests another | "use pairwise", "give me a decision table" |
| 1 | Are there durable states, where whether an event is allowed depends on the current state? | State Transition | status, lifecycle, "only while", "can no longer", "from X to Y" |
| 2 | Is the outcome defined by rules that combine two or more conditions? | Decision Table | "if ... and ... then", eligibility, pricing, approval logic |
| 3 | Do several parameters interact, with no rule defining the outcome per combination, and would all combinations be too many? | Classification Tree / N-Wise | browsers x OS x locale, configuration options, "must work for all" |
| 4 | Is it a multi-step actor-system flow with alternate or exception paths? | Use Case Testing | main success scenario, extensions, "if the card is declined the system..." |
| 5 | Is the domain ordered (number, length, date, count, position) with limits? | Boundary Value Analysis | min/max, "between", "at least", "up to", "from EUR 50" |
| 6 | Are there unordered classes of values that should behave alike? | Equivalence Partitioning | types, categories, formats, allowed vs rejected values |
| 7 | Behavioural acceptance criteria with none of the logic above inside | Acceptance Criteria to Test Cases | Given/When/Then, "the user sees..." |

Why this order: state and rule logic decide more outcomes than a single field does, so they win ties. Boundary Value Analysis outranks Equivalence Partitioning because it builds the same partitions and then adds the edges, so the same field never needs both.

Add a **secondary technique** inside a unit only when it closes a gap the primary cannot, and give it its own table under that unit:

- Decision Table + Boundary Value Analysis on conditions that are ranges (`income >= 40000`)
- State Transition + Boundary Value Analysis on guard thresholds (`amount > 1000`)
- Classification Tree + Equivalence Partitioning or Boundary Value Analysis to choose each classification's values

When the choice between two techniques depends on missing information (is the range inclusive, are the states durable, which combinations are allowed), add it to the step 1 questions.

If no technique fits (the user wants error guessing, a checklist or an exploratory charter), say so and describe what would help instead of forcing a technique.

### 4. Tell the user how the work was split and why

Open the deliverable with a test design plan so the reviewer can challenge the split before reading the tests:

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | AC-1 | 8-12 character code | Boundary Value Analysis | ordered length limit; defects cluster at 7/8 and 12/13 |
| U2 | AC-2 | discount by tier and order total | Decision Table + BVA on EUR 50 | outcome depends on two conditions combined |

With more than one unit, add two or three sentences under the table: which logic was separated, and what a single technique would have missed. With one unit, the plan is one row.

If the user only asked which technique fits, the plan plus one line per close alternative ("why not decision table: only one condition decides the outcome") is the full answer. Stop there.

### 5. Apply each technique

Read `./references/test-design-shared-conventions.md` once: it holds the shared rules and the templates for assumptions, test cases and coverage. Then read the subskill for each selected technique, once even if several units use it, and follow it:

- Boundary Value Analysis: `./boundary-value-analysis/SKILL.md`
- Equivalence Partitioning: `./equivalence-partitioning/SKILL.md`
- Decision Table: `./decision-table/SKILL.md`
- Classification Tree / N-Wise: `./classification-tree-nwise/SKILL.md`
- State Transition: `./state-transition/SKILL.md`
- Use Case Testing: `./use-case-testing/SKILL.md`
- Acceptance Criteria to Test Cases: `./acceptance-criteria-to-test-cases/SKILL.md`

Each subskill folder holds its `SKILL.md` (when to use, workflow, required output), a `references/` file with the detailed rules, coverage measure, templates and failure modes, and an `example.md` with a worked input and output. The subskill tells you when to read each.

### 6. Assemble and check

Deliver in this order (templates in the shared conventions):

1. Test design plan
2. Assumptions and open questions
3. Per unit: the technique artifact (partition table, decision table, state table, ...) followed by its test cases
4. Coverage and residual risk

If the user asked for tests only, drop the commentary but keep every assumption that shapes an expected result.

Before finishing, check that:

- each unit's technique matches its logic and the plan says why
- every test has a concrete expected result taken from the source or from a logged assumption
- invalid inputs are tested one at a time
- coverage is stated with the technique's own measure, and "full coverage" is claimed only when that measure is 100%
- source IDs are carried through unchanged

## Defect confirmation

Use when the input is a defect report or the user wants tests that prove a bug and its fix.

1. Extract the observed behaviour, the expected behaviour (from the requirement, not from the bug), reproduction steps, data and environment.
2. Write the **confirmation test** first: the exact failing condition, with the correct behaviour as expected result. It fails on the defective version and passes once the fix is in.
3. Treat the logic around the defect as a test unit and pick its technique with step 3, to add a few neighbouring tests that catch an incomplete fix. "No discount at exactly 10 items" becomes Boundary Value Analysis at 9, 10 and 11 items; a wrong rule outcome becomes that decision-table column plus the columns next to it.
4. State the **test level(s)** at which the defect is observable and cheapest to reproduce (unit, service/API, UI/end-to-end, performance, security), each with a one-line reason, on a line starting with `Test level(s):`. Prefer the lowest level that still shows the failure.

## Guardrails

- Do not invent requirements, states, rules, boundaries or expected results. Unknown means an open question or a labelled assumption.
- One table per technique. Do not merge techniques into one hybrid table unless the user asks for it.
- Explain every reduction (pairwise, collapsed decision table, combined valid partitions) and never call a reduced set exhaustive.
- If a unit would produce an impractical number of tests, say so, reduce with the technique's own rule, and state what the reduced set leaves uncovered.

## Memory

Keep working notes (units, techniques, assumptions, IDs) for the current task only. Save a reusable test-design brief in the user's project only when asked. Shared cross-project memory is out of scope.
