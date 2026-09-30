# Worked Example: Decision Table

## Input

> **LOAN-RULES**
> - Approve the loan when the applicant is over 21, income is at least 40000, and fraud flag is false.
> - Send for manual review when the applicant is over 21, income is at least 40000, and fraud flag is true.
> - Reject the loan when the applicant is 21 or younger.
> - Reject the loan when income is below 40000.

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | LOAN-RULES | outcome from age, income and fraud flag combined | Decision Table + BVA on age and income | the outcome depends on three conditions together; two of them are thresholds, so their edges get a secondary boundary table |

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| ASM-1 | Age is in whole years; "over 21" means 22 or older | defines the age boundary (21 / 22) | TC-U1-04, TC-U1-05, TC-U1-06 |
| ASM-2 | Income is in euros with cents (step 0.01) | defines the income boundary (39 999.99 / 40 000.00) | TC-U1-07 to TC-U1-09 |
| Q-1 | Does a fraud flag still matter when the loan is rejected anyway (columns 4, 6, 8)? The rules say reject; confirm that no fraud escalation is expected. | could add an action to R3 and R4 | TC-U1-03, TC-U1-04 |
| Q-2 | Are age and income rejections distinguishable (reason code or message)? | sharpens expected results; keeps R3 and R4 apart | TC-U1-03, TC-U1-04 |

## Full decision table (8 combinations)

| Type | Name | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| Condition | Age > 21 | Y | Y | Y | Y | N | N | N | N |
| Condition | Income >= 40000 | Y | Y | N | N | Y | Y | N | N |
| Condition | Fraud flag | N | Y | N | Y | N | Y | N | Y |
| Action | Outcome | Approve | Manual review | Reject | Reject | Reject | Reject | Reject | Reject |

Findings: no unspecified or contradictory combinations. Columns 7 and 8 match both reject rules, which agree, so there is no conflict. Q-1 records the only open point.

## Collapsed decision table

| Type | Name | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|
| Condition | Age > 21 | Y | Y | Y | N |
| Condition | Income >= 40000 | Y | Y | N | - |
| Condition | Fraud flag | N | Y | - | - |
| Action | Outcome | Approve | Manual review | Reject (income) | Reject (age) |

Optimization notes: R3 merges columns 3-4 (fraud flag has no effect once income is too low); R4 merges columns 5-8 (age alone decides). R3 and R4 stay separate because the rejection reason may differ (Q-2).

## Secondary: boundary values on the threshold conditions (3-value)

| Condition | Boundary | Test values | Expected (other conditions valid, fraud flag false) |
|---|---|---|---|
| Age > 21 | 21 / 22 | 21, 22, 23 | 21 reject; 22, 23 approve |
| Income >= 40000 | 39 999.99 / 40 000.00 | 39 999.99, 40 000.00, 40 000.01 | 39 999.99 reject; 40 000.00, 40 000.01 approve |

Values 20 and 39 999.98 are left out: they are neighbours on the reject side, and the reject behaviour is already covered by R3 and R4.

## Test cases

| ID | Title | Covers | Test data | Expected result | Trace |
|---|---|---|---|---|---|
| TC-U1-01 | Approve eligible applicant without fraud flag | R1 | age 35, income 55 000, fraud N | loan approved | LOAN-RULES |
| TC-U1-02 | Send eligible applicant with fraud flag to manual review | R2 | age 35, income 55 000, fraud Y | loan sent for manual review | LOAN-RULES |
| TC-U1-03 | Reject applicant with income below 40000 | R3 | age 35, income 30 000, fraud N | loan rejected (reason per Q-2) | LOAN-RULES |
| TC-U1-04 | Reject applicant aged 21 or younger | R4, age BV 21 | age 21, income 55 000, fraud N | loan rejected (reason per Q-2) | LOAN-RULES |
| TC-U1-05 | Approve applicant aged exactly 22 | age BV 22 | age 22, income 55 000, fraud N | loan approved | LOAN-RULES |
| TC-U1-06 | Approve applicant aged 23 | age neighbour 23 | age 23, income 55 000, fraud N | loan approved | LOAN-RULES |
| TC-U1-07 | Reject income one cent below threshold | income BV 39 999.99 | age 35, income 39 999.99, fraud N | loan rejected | LOAN-RULES |
| TC-U1-08 | Approve income exactly at threshold | income BV 40 000.00 | age 35, income 40 000.00, fraud N | loan approved | LOAN-RULES |
| TC-U1-09 | Approve income one cent above threshold | income neighbour 40 000.01 | age 35, income 40 000.01, fraud N | loan approved | LOAN-RULES |

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | Decision Table (collapsed) | 4 columns | 4 | 100% | fraud handling on rejected loans unconfirmed (Q-1) |
| U1 | BVA (3-value, secondary) | 6 values | 6 | 100% | age as whole years assumed (ASM-1) |
