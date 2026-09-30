# Decision Table Reference

Use decision tables when the requirement defines discrete conditions that combine into outcomes or actions.

## Best-Fit Inputs

- eligibility rules
- pricing or discount rules
- approval and rejection logic
- multi-flag policy decisions

## Rules per Workflow Step

### 1. Conditions and actions

Use `Y`/`N` for boolean conditions (limited entry). Use the value classes for multi-valued conditions (extended entry, for example tier = gold / silver / none).

### 2. Counting combinations

The full rule space is the product of the number of values per condition. State that number. Above roughly 32 columns, do not draw the full table: build the collapsed table directly from the stated rules, and check whether part of the unit is really a classification-tree unit.

### 3. Filling columns

Fill every column with the action from the source, or mark it:

- `?` unspecified: no rule covers this combination. It becomes an open question.
- `impossible`: the conditions cannot occur together. Say why.
- `conflict`: two rules give different actions. This is a spec defect; report it.

### 4. Collapsing

Collapse only columns that differ in one condition and have the same actions and the same expected result: replace that condition with `-` (don't care). Never collapse to hide a `?` or a conflict, and keep columns apart when their observable results differ (for example different rejection reasons).

### 5. Deriving tests

One test per retained column, with concrete data that satisfies its conditions. When a condition is a range (`income >= 40000`), pick a value clearly inside it here and add a boundary value analysis table for the threshold as a secondary technique.

## Findings to Report

- unspecified combinations (`?`)
- contradictory rules (`conflict`)
- redundant rules (a rule fully covered by another)

## Coverage

Decision table coverage = columns exercised / feasible columns. Say whether you count the full or the collapsed table. `?` columns are not covered until answered.

## Output Templates

Decision table (full or collapsed; say which):

| Type | Name | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|
| Condition | <condition 1> | Y | Y | N | ? |
| Condition | <condition 2> | Y | N | - | ? |
| Action | <outcome> | <action> | <action> | <action> | ? |

When columns were collapsed, add optimization notes: which columns merged and why their expected result is the same.

Then the shared test case summary and the coverage row.

## Common Failure Modes

- inventing actions for unstated combinations
- merging rule columns too aggressively
- hiding a meaningful rule difference or business ambiguity behind `-` (don't care) cells
- presenting a fake complete matrix instead of a coverage gap note
