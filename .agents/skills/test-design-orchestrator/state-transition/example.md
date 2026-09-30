# Worked Example: State Transition

## Input

> **ACC-LIFECYCLE**
> - A new account starts in `Pending Verification`.
> - Verifying email moves the account to `Active`.
> - Suspected fraud moves the account to `Suspended` from any active state.
> - Admin review can move `Suspended` to `Active` or `Closed`.
> - A `Closed` account cannot transition back to any other state.

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | ACC-LIFECYCLE | account status and the events allowed in each status | State Transition (all transitions) | whether an event is allowed depends on the current status; the source explicitly forbids leaving `Closed`, so invalid transitions matter |

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | "Any active state" means only `Active` | decides whether fraud can suspend a pending account | T2, Q-1 |
| Q-1 | Can suspected fraud suspend a `Pending Verification` account? | would add a transition | state table `?` |
| Q-2 | What happens when an event is not listed for a state (verify email while `Active`, admin close while `Active`)? | defines 7 `?` cells | not covered |
| Q-3 | How is an event on a `Closed` account rejected (error message, no-op)? | sharpens the expected results of TC-U1-03 to TC-U1-05 | TC-U1-03 to TC-U1-05 |

## State table

| State \ Event | Verify email | Suspected fraud | Admin reactivates | Admin closes |
|---|---|---|---|---|
| Pending Verification (start) | Active | ? (Q-1) | ? | ? |
| Active | ? | Suspended | ? | ? |
| Suspended | ? | ? | Active | Closed |
| Closed (final) | rejected | rejected | rejected | rejected |

## Transitions

| ID | From | Event | To |
|---|---|---|---|
| T1 | Pending Verification | Verify email | Active |
| T2 | Active | Suspected fraud | Suspended |
| T3 | Suspended | Admin reactivates | Active |
| T4 | Suspended | Admin closes | Closed |
| X1-X4 | Closed | any event | stays Closed (rejected) |

## Paths

| Path ID | Coverage target | Start state | Event sequence | End state | Transitions covered |
|---|---|---|---|---|---|
| PATH-1 | valid transitions | Pending Verification | verify email, suspected fraud, admin reactivates | Active | T1, T2, T3 |
| PATH-2 | valid transitions | Pending Verification | verify email, suspected fraud, admin closes | Closed | T1, T2, T4 |

## Test cases

| ID | Title | Covers | Preconditions | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | Suspended account is reactivated after admin review | PATH-1 (T1, T2, T3) | new account | after each event the status is Active, Suspended, Active | ACC-LIFECYCLE |
| TC-U1-02 | Suspended account is closed after admin review | PATH-2 (T1, T2, T4) | new account | after each event the status is Active, Suspended, Closed | ACC-LIFECYCLE |
| TC-U1-03 | Closed account cannot be reactivated | X3 | account in Closed | event rejected (Q-3); status stays Closed | ACC-LIFECYCLE |
| TC-U1-04 | Closed account ignores email verification | X1 | account in Closed | event rejected (Q-3); status stays Closed | ACC-LIFECYCLE |
| TC-U1-05 | Closed account cannot be suspended | X2 | account in Closed | event rejected (Q-3); status stays Closed | ACC-LIFECYCLE |

X4 (admin closes an already closed account) is left out: it has the same observable result as X1-X3 and adds little risk. Add it if closing triggers side effects such as notifications.

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | State Transition: valid transitions | 4 transitions | 4 | 100% | |
| U1 | State Transition: specified invalid transitions | 4 | 3 | 75% | X4 skipped on purpose; 8 `?` cells open (Q-1, Q-2) |
