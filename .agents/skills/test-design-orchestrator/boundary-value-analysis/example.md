# Worked Example: Boundary Value Analysis

## Input

> **REQ-PASSWORD-LENGTH** - Password length must be between 8 and 12 characters inclusive. Passwords shorter than 8 or longer than 12 must be rejected with the message "Password length is invalid."

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | REQ-PASSWORD-LENGTH | password length 8-12 | Boundary Value Analysis (3-value) | ordered length limit with explicit rejection behaviour; the risk sits at the edges |

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | Length counts characters, not bytes | defines the step size (1 character) | all tests |
| ASM-2 | "Accepted" means the length error is not shown; other password rules are out of scope and the test passwords satisfy them | makes the valid tests observable | TC-U1-05 to TC-U1-08 |
| Q-1 | What is the maximum input length of the field (for example 64 or 255)? | it is the upper boundary of P3 | P3 max not tested |

## Boundary inventory

| Condition | Partition | Validity | Min | Max | Step | Stated / inferred |
|---|---|---|---|---|---|---|
| Password length | P1 too short | invalid | 0 | 7 | 1 char | stated (< 8); 0 inferred |
| Password length | P2 allowed | valid | 8 | 12 | 1 char | stated, inclusive |
| Password length | P3 too long | invalid | 13 | ? (Q-1) | 1 char | stated (> 12) |

## Boundary values (3-value)

| Boundary value | Partition | Test values | Expected |
|---|---|---|---|
| 0 (P1 min) | P1 | 0, 1 (-1 cannot exist) | rejected |
| 7 (P1 max) | P1 | 6, 7, 8 | 6, 7 rejected; 8 accepted |
| 8 (P2 min) | P2 | 7, 8, 9 | 7 rejected; 8, 9 accepted |
| 12 (P2 max) | P2 | 11, 12, 13 | 11, 12 accepted; 13 rejected |
| 13 (P3 min) | P3 | 12, 13, 14 | 12 accepted; 13, 14 rejected |

After removing duplicates: 0, 1, 6, 7, 8, 9, 11, 12, 13, 14 (10 values).

## Test cases

| ID | Title | Covers | Test data | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | Reject empty password | BV 0 (P1 min, inferred) | `""` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |
| TC-U1-02 | Reject 1-character password | neighbour of 0 | `a` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |
| TC-U1-03 | Reject 6-character password | neighbour of 7 | `abcde1` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |
| TC-U1-04 | Reject password shorter than minimum length | BV 7 (P1 max) | `abcdef1` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |
| TC-U1-05 | Accept 8-character password | BV 8 (P2 min) | `abcdefg1` | accepted, no length error (ASM-2) | REQ-PASSWORD-LENGTH |
| TC-U1-06 | Accept 9-character password | neighbour of 8 | `abcdefgh1` | accepted, no length error (ASM-2) | REQ-PASSWORD-LENGTH |
| TC-U1-07 | Accept 11-character password | neighbour of 12 | `abcdefghij1` | accepted, no length error (ASM-2) | REQ-PASSWORD-LENGTH |
| TC-U1-08 | Accept 12-character password | BV 12 (P2 max) | `abcdefghijk1` | accepted, no length error (ASM-2) | REQ-PASSWORD-LENGTH |
| TC-U1-09 | Reject 13-character password | BV 13 (P3 min) | `abcdefghijkl1` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |
| TC-U1-10 | Reject 14-character password | neighbour of 13 | `abcdefghijklm1` | rejected: "Password length is invalid." | REQ-PASSWORD-LENGTH |

Detailed form of one test, for when step-level detail is requested:

### TC-U1-04 - Reject password shorter than minimum length

- Objective: validate the upper boundary of the too-short partition
- Technique: Boundary Value Analysis
- Covers: BV 7 (P1 max)
- Trace: REQ-PASSWORD-LENGTH
- Preconditions:
  1. The registration form is open.
- Test data: password `abcdef1` (7 characters)

| Step | Action | Expected result |
|---|---|---|
| 1 | Enter a password with 7 characters. | The password field accepts the input. |
| 2 | Submit the form. | The user sees "Password length is invalid." and is not registered. |

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | BVA (3-value) | 10 values | 10 | 100% | P3 upper boundary unknown (Q-1); multi-byte characters not covered (ASM-1) |
