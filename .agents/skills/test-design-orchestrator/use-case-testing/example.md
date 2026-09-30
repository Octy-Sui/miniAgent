# Worked Example: Use Case Testing

## Input

> **UC-12 Reset password**
> Actor: registered user. Precondition: the user has an account with a verified email address.
> Main success scenario:
> 1. The user requests a password reset and enters their email address.
> 2. The system sends a reset link.
> 3. The user opens the link.
> 4. The user enters a new password that meets the password policy.
> 5. The system confirms the change.
> Extensions:
> 1a. The email address is unknown: the system shows the same confirmation message as in step 2 but sends nothing.
> 3a. The link is older than 24 hours: the system refuses it and offers to send a new link.
> 4a. The new password violates the policy: the system shows the policy error and the user returns to step 4.
> Postcondition: the new password works; the old password no longer does.

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | UC-12 | reset flow with three extensions | Use Case Testing | actor-system flow with alternate and exception paths; postconditions matter |
| U2 | UC-12 step 3a | link validity of 24 hours | Boundary Value Analysis | an ordered time limit inside one step; tested as its own unit so the flow stays readable |

The 24-hour limit is split out because use case testing checks that the expired-link path exists and behaves correctly, not where exactly the limit sits. U2 is not expanded in this example; see the boundary value analysis subskill.

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | Earlier reset links stop working once a new one is sent | defines a failure guarantee for PATH-E1 | TC-U1-03 |
| Q-1 | Does a successful reset end the user's other active sessions? | possible extra postcondition | TC-U1-01 |

## Path inventory

| Path ID | Branches at step | Condition | Ends | Postcondition to check |
|---|---|---|---|---|
| PATH-MAIN | - | - | completes at step 5 | new password works, old one does not |
| PATH-A1 (1a) | 1 | unknown email | terminates | same message as step 2; no email sent; no account data revealed |
| PATH-E1 (3a) | 3 | link older than 24 h | terminates (new link offered) | password unchanged; the old password still works |
| PATH-A2 (4a) | 4 | new password violates the policy | loops back to step 4, then completes | password unchanged after the error; after a valid retry the main postcondition holds |

## Test cases

| ID | Title | Covers | Preconditions | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | User resets the password through the emailed link | PATH-MAIN | registered user with verified email | change confirmed; login works with the new password and fails with the old one | UC-12 |
| TC-U1-02 | Reset request for an unknown email reveals nothing | PATH-A1 | email address not registered | same confirmation message as TC-U1-01 step 2; no email arrives | UC-12 1a |
| TC-U1-03 | Expired reset link is refused | PATH-E1 | reset link older than 24 hours | link refused, new link offered; the old password still works | UC-12 3a |
| TC-U1-04 | Policy error then valid retry | PATH-A2 | valid reset link | first attempt shows the policy error and leaves the password unchanged; the second, valid attempt is confirmed and the new password works | UC-12 4a |

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | Use Case Testing | 4 paths | 4 | 100% | session handling after reset (Q-1) |
