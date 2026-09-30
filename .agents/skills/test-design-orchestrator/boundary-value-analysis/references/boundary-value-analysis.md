# Boundary Value Analysis Reference

Use BVA when the requirement defines ordered limits and the main defect risk is around those limits.

## Best-Fit Inputs

- numeric ranges
- text length limits
- date and time windows
- item counts
- ranked or sequential positions

## Rules per Workflow Step

### 1. Partition first

Split the domain into ordered partitions, valid and invalid. The minimum and maximum of each partition are its boundary values (ISTQB CTFL v4). This also covers the partitions themselves, so the same field needs no separate equivalence-partitioning pass.

### 2. Inclusivity

"Between 8 and 12" is ambiguous. Assume inclusive and log the assumption. "At least", "from" and "or more" are inclusive; "over", "more than" and "below" are exclusive.

### 3. Step size

The step size is the smallest meaningful increment of the domain. A wrong step size is the most common BVA error: just above 49.99 is 50.00 for money but 50 for whole euros.

| Domain | Step |
|---|---|
| integers, counts | 1 |
| money | the currency precision (0.01) |
| dates and times | the stated granularity (1 day, 1 minute, 1 second) |
| text length | 1 character; clarify characters vs bytes when multi-byte input is possible |
| decimals | the stated precision |

### 4. Implicit boundaries

Add them only when they are real for the unit and the behaviour is stated or safely inferable: empty or zero, the field or data-type maximum, the first or last allowed date. Mark them as inferred. When zero is meaningful (quantity 0, amount 0.00), it gets its own value.

### 5. Choosing values

- **3-value BVA (default):** each boundary value plus both neighbours. It catches defects that 2-value misses: if `x <= 10` is implemented as `x == 10`, the 2-value tests (10, 11) pass, while 9 from 3-value fails.
- **2-value BVA (lean set, on request):** each boundary value plus its closest neighbour in the adjacent partition.
- Drop neighbours that cannot exist (length -1) and remove duplicates where adjacent partitions share values.

### 6. Building tests

One test per value. Invalid values go in separate tests, one invalid value per test with all other inputs valid. Valid values of different fields may share a test when a failure stays diagnosable.

### 7. Expected results

Take them from the source: accepted, or rejected with the stated message. When the rejection behaviour or the meaning of "accepted" is not stated, log a question or an assumption instead of guessing.

## Coverage

BVA coverage = boundary values (and neighbours, for 3-value) exercised / identified. Name the variant (2-value or 3-value) and list boundaries you could not test because a limit is unknown.

## Output Templates

Boundary inventory:

| Condition | Partition | Validity | Min | Max | Step | Stated / inferred |
|---|---|---|---|---|---|---|
| <field> | P1 <name> | invalid | <min> | <max> | <step> | stated |

Boundary values:

| Boundary value | Partition | Test values (2- or 3-value) | Expected |
|---|---|---|---|
| 8 (P2 min) | P2 valid | 7, 8, 9 | 7 rejected; 8, 9 accepted |

Then the shared test case summary and the coverage row.

## Common Failure Modes

- using BVA for unordered categories
- inventing boundaries that the requirement does not imply, or not separating inferred boundaries from stated ones
- ignoring unit mismatches: bytes vs characters, inclusive dates across time zones, amounts with and without VAT
- missing the zero case when zero is meaningful
- combining several invalid boundaries in one test and losing the diagnostic value, unless the user asks for robustness testing
