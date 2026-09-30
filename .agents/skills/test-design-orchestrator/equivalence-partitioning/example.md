# Worked Example: Equivalence Partitioning

## Input

> **RULE-APPLICANT** - Applicant type can be employee, contractor, or guest. Guests are rejected. Employees and contractors may continue.

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | RULE-APPLICANT | applicant type decides continue or reject | Equivalence Partitioning | one unordered input with classes of equal behaviour; no ordering, so no boundaries |

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | Employees and contractors follow the same path after this check | justifies one partition EP1 | TC-U1-01 |
| ASM-2 | "Rejected" means the applicant cannot continue to the next step | makes TC-U1-02 observable | TC-U1-02 |
| Q-1 | What happens with an empty or unknown applicant type (for example through the API)? | defines EP3 | EP3 not tested |
| Q-2 | Is a rejection message shown, and which one? | sharpens the expected result of TC-U1-02 | TC-U1-02 |

## Partition table

| Input | Partition ID | Validity | Values / rule | Why equivalent | Representative |
|---|---|---|---|---|---|
| Applicant type | EP1 | valid | employee, contractor | both "may continue" (ASM-1) | employee |
| Applicant type | EP2 | invalid (rejected) | guest | "Guests are rejected" | guest |
| Applicant type | EP3 | invalid | empty, unknown value | behaviour not specified (Q-1) | - |

ASM-1 is cheap to remove: if employees and contractors see different screens later, split EP1 and add one test with `contractor`.

## Test cases

| ID | Title | Covers | Test data | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | Employee may continue | EP1 | applicant type `employee` | the application continues to the next step | RULE-APPLICANT |
| TC-U1-02 | Guest is rejected | EP2 | applicant type `guest` | the application cannot continue (ASM-2); message per Q-2 | RULE-APPLICANT |

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | Equivalence Partitioning | 3 partitions | 2 | 67% | EP3 blocked by Q-1; contractor path assumed equal (ASM-1) |
