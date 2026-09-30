# Worked Example: Acceptance Criteria to Test Cases

## Input

> **STORY-88** - As a shopper I want to pay for my basket so that I can complete my order.
> - AC-1: When the shopper submits valid card details, the payment is authorized and the order is confirmed.
> - AC-2: When the shopper submits an expired card, the payment is rejected and the shopper sees an expiration error.
> - AC-3: Given a registered shopper, when they apply a valid gift card during checkout, then the order total is reduced and the remaining balance is shown.
> - AC-4: Shipping is free for orders of EUR 50 or more.

## Test design plan

| Unit | Source | Logic | Technique | Why this technique |
|---|---|---|---|---|
| U1 | AC-1, AC-2, AC-3 | behaviour of payment and gift card | Acceptance Criteria to Test Cases | behavioural criteria without ranges, rule combinations or states |
| U2 | AC-4 | free-shipping threshold | Boundary Value Analysis | an ordered money limit; the risk sits at EUR 49.99 / 50.00, which a single scenario would miss |

AC-4 reads like the other criteria but hides a threshold, so it is tested as its own unit. U2 is not expanded here; see the boundary value analysis subskill.

## Assumptions and open questions

| ID | Assumption or question | Why it matters | Affects |
|---|---|---|---|
| Q-1 | What happens with an invalid (not expired) card, or when the gateway is down? | AC-2 covers only expiry; other rejections are undefined | gap, not tested |
| Q-2 | What if the gift card balance exceeds the order total? | AC-3 does not say whether the total goes to zero or the card is refused | gap, not tested |

## Criterion mapping

| Criterion | Examples | Scenarios | Questions / gaps |
|---|---|---|---|
| AC-1 | valid card is authorized | TC-U1-01 | - |
| AC-2 | expired card is rejected with an expiration error | TC-U1-02 | other rejection reasons undefined (Q-1) |
| AC-3 | EUR 20 gift card on an EUR 80 order | TC-U1-03 | balance larger than the total (Q-2) |
| AC-4 | - | split out as U2 | - |

## Scenarios

```gherkin
@STORY-88 @AC-1
Scenario: Successful card payment with valid details
  Given a shopper has items in the basket
  And the payment gateway is available
  When the shopper submits valid card details
  Then the payment is authorized
  And the order is confirmed

@STORY-88 @AC-2
Scenario: Payment is rejected for an expired card
  Given a shopper has items in the basket
  And the payment gateway is available
  When the shopper submits an expired card
  Then the payment is rejected
  And the shopper sees an expiration error

@STORY-88 @AC-3
Scenario: Gift card reduces the order total
  Given a registered shopper with an order total of EUR 80.00
  And a valid gift card with a balance of EUR 20.00
  When the shopper applies the gift card during checkout
  Then the order total is EUR 60.00
  And the remaining gift card balance shown is EUR 0.00
```

## Coverage

| Unit | Technique | Coverage items | Covered | Coverage | Not covered / residual risk |
|---|---|---|---|---|---|
| U1 | Acceptance criteria | 3 criteria | 3 | 100% | undefined rejections (Q-1) and gift card overflow (Q-2) |
| U2 | BVA | see U2 | | | |
