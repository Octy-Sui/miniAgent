# Use Case Testing Reference

Use use-case testing when the requirement is organized around actor goals, triggers, flows and outcomes.

## Best-Fit Inputs

- formal use cases
- workflow narratives
- actor-centered interaction documents
- main flow plus alternate flow descriptions

## Rules per Workflow Step

### 1. Extraction

Extract the actor, trigger, preconditions, the numbered main success scenario, alternates, exceptions, success postconditions and failure guarantees (what must stay unchanged when the flow aborts).

### 2. Path inventory

Give each path an ID (`PATH-MAIN`, `PATH-A1`, `PATH-E1`), or reuse the use case's own numbering (`3a`) when it has one. For each alternate or exception, record the step where it branches, the condition, and where it ends: it rejoins the main flow at step n, or terminates.

### 3. One test per path

One test for the main path, and one for each alternate or exception that changes behaviour or outcome. A path that loops back (retry after a validation error) is tested once around the loop and then to completion.

### 4. Combining alternates

Combine alternates in one test only when they are independent and the combination is realistic. Otherwise keep them apart so a failure points at one path.

### 5. Postconditions

Check success postconditions on paths that complete, and failure guarantees (nothing saved, nothing charged, old state intact) on paths that abort.

### 6. Data rules inside a step

A range or rule set inside a step is a separate test unit with its own technique. Reference it from the step instead of expanding it here.

### 7. Roles

When different actors can run the use case with different permissions, add one test per role that changes behaviour.

## Coverage

Use case coverage = paths exercised / paths identified (main + alternates + exceptions).

## Output Templates

Path inventory:

| Path ID | Branches at step | Condition | Ends | Postcondition to check |
|---|---|---|---|---|
| PATH-MAIN | - | - | completes | <success postcondition> |
| PATH-A1 | <step> | <condition> | rejoins at step <n> / terminates | <postcondition or guarantee> |

Then the shared test case summary. Use the detailed test case template, with an expected result per step, when the user wants executable scripts.

## Common Failure Modes

- testing only the happy path
- losing the alternate path reference in the tests
- skipping postcondition checks on alternate and exception paths, or the final state or business outcome
- rewriting the use case into rule tables; when a step really is a rule set, it becomes a separate unit
