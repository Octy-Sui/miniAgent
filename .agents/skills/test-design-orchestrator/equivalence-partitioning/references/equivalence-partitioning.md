# Equivalence Partitioning Reference

Use equivalence partitioning when many input values should behave the same and one representative value can stand in for the class.

## Best-Fit Inputs

- allowed and disallowed categories
- validated input classes
- protocol or format classes, such as valid and invalid identifiers
- outputs that fall into classes (a discount tier, an error category)

Ordered ranges with limits belong to boundary value analysis, which builds the same partitions and adds the edges.

## Rules per Workflow Step

### 1. Inputs and outputs

List every input of the unit. Add outputs when they fall into classes, because each output class needs at least one test that produces it.

### 2. Partitions

- **Valid partitions:** values the unit accepts, one partition per distinct behaviour.
- **Invalid partitions:** values it must reject. Consider wrong type, wrong format, empty, missing/null, and values outside the allowed set.
- Include an invalid partition only when such a value can reach the system. A dropdown cannot submit free text, but the API behind it can.

### 3. Why equivalent

Every partition needs the rule that makes its values interchangeable. Without one, the equivalence is an assumption, or the partition needs splitting. Values the requirement lists together but that run through different screens, code paths or messages are separate partitions.

### 4. Representatives

Pick a typical value, not an edge. Edges belong to boundary value analysis.

### 5. Building tests

- **Valid partitions:** combine representatives of different inputs in as few tests as possible, until every valid partition appears at least once (each choice coverage).
- **Invalid partitions:** one per test, all other inputs valid, so a failure points at one partition.

## Coverage

EP coverage = partitions exercised / partitions identified, per input. With several inputs, each choice coverage means every partition of every input is used at least once. Partitions blocked by an open question count as not covered.

## Output Templates

Partition table:

| Input | Partition ID | Validity | Values / rule | Why equivalent | Representative |
|---|---|---|---|---|---|
| <input> | EP1 | valid | <values> | <shared rule> | <value> |

Reduction table, when valid partitions were combined:

| Test ID | Partitions combined | Reduction rationale |
|---|---|---|
| TC-U1-01 | EP1, EP4, EP7 | all valid; a failure still points at one input because each input's other partitions are tested separately |

Then the shared test case summary and the coverage row.

## Common Failure Modes

- treating every value as its own partition
- collapsing partitions that produce different behaviour, or claiming equivalence without stating the rule
- forgetting invalid partitions, or inventing their rejection behaviour
- combining invalid representatives in one test and obscuring the defect source
