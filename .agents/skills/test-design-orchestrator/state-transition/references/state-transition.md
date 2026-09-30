# State Transition Reference

Use state-transition testing when behaviour depends on the current state and the legality of the next event.

## Best-Fit Inputs

- lifecycle workflows
- approval pipelines
- session or account status changes
- devices or services with operating modes

## Rules per Workflow Step

### 1. The model

List states (conditions the system stays in, not process steps), events, guards (conditions on an event) and actions. Mark the start state and any final states.

### 2. The state table

Every state against every event. Each cell is one of:

- `next state / action`: a valid transition
- `rejected`: the source says the event is not allowed in this state (note how it is rejected, if stated)
- `?`: unspecified. It becomes an open question.

Do not fill `?` cells with "invalid". Whether the system ignores the event, rejects it or fails is behaviour you would be inventing.

### 3. Coverage targets (ISTQB CTFL v4)

| Target | Meaning | When |
|---|---|---|
| all states | every state visited | weakest; smoke level only |
| valid transitions (0-switch) | every valid transition exercised at least once | **default** |
| all transitions | valid transitions plus every specified invalid transition | rejections matter (security, money, compliance) or the user asks for negative coverage |
| 1-switch | every pair of consecutive transitions | high-risk lifecycles only |

### 4. Paths

Build paths from the start state. Cover as many target transitions per path as you can, but end a path at a final state or before it gets so long that a failure would be hard to locate. Each specified invalid transition gets its own short test: reach the state, fire the event, check the rejection and that the state did not change.

### 5. Checking state

Check the expected state after every step, not only at the end of a path.

### 6. Guards

Guards with thresholds (`amount > 1000`) get a secondary boundary value analysis table.

## Coverage

Report the measure of the chosen target: states visited / states, valid transitions exercised / valid transitions, invalid transitions tested / specified invalid transitions. `?` cells are listed as not covered.

## Output Templates

State table:

| State \ Event | <event 1> | <event 2> | <event 3> |
|---|---|---|---|
| <state 1> | <state 2> / <action> | rejected | ? |

Transition list, so paths and tests can reference transitions:

| ID | From | Event [guard] | To | Action |
|---|---|---|---|---|
| T1 | <state> | <event> [<guard>] | <state> | <action> |

Paths:

| Path ID | Coverage target | Start state | Event sequence | End state | Transitions covered |
|---|---|---|---|---|---|
| PATH-1 | valid transitions | <start> | <e1>, <e2> | <end> | T1, T2 |

Then the shared test case summary and the coverage row.

## Common Failure Modes

- using state-transition testing for what is really a rule matrix or a simple range check
- confusing process steps with persistent states
- omitting the start state
- inventing hidden states to make the model look complete
- assuming invalid transitions without knowing the expected behaviour, or mixing them with `?` cells
