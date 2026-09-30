# Classification Tree and N-Wise Reference

Use this technique when several parameters interact and exhaustive testing is too large. 4 parameters with 4 values each give 256 combinations; pairwise needs about 16 and still covers every pair of values, and most interaction defects involve two parameters.

## Best-Fit Inputs

- configurable product options
- forms with many independent selectors
- combinatorial integration points
- matrices of channels, roles, products, browsers, devices or environments

## Rules per Workflow Step

### 1. The classification tree

For each parameter (classification), list its classes. Classes must not overlap and together must cover the whole parameter. Choose them with equivalence partitioning (one class per behaviour) and add boundary classes when a parameter is ordered.

### 2. Constraints

Record combinations that are impossible or not allowed ("Safari only on macOS") before generating. Write each as a forbidden partial combination. A constraint that is also a business rule ("free members cannot sort by price") still deserves its own negative test outside the combination set.

### 3. Interaction strength

| Strength | Name | When |
|---|---|---|
| 1 | minimum criterion / each choice | every class at least once; smoke level |
| 2 | pairwise | **default** |
| 3 or higher | n-wise | only for high-risk units; say why |

### 4. Generation

Hand-made pairwise sets for more than three parameters routinely miss pairs, and then the "pairwise" label is false. Use the bundled generator (the commands are in the subskill's `SKILL.md`). Model file format:

```json
{
  "parameters": {"Browser": ["Chrome", "Safari"], "OS": ["Windows", "macOS"]},
  "constraints": [{"Browser": "Safari", "OS": "Windows"}],
  "strength": 2
}
```

Each constraint is a forbidden partial combination. The script reports the feasible tuples it covered, the number of allowed combinations it replaced, and any tuples the constraints make impossible. Use `--verify` to check a set the team already has.

### 5. Expected results

Define the expected effect of each class once (sort = price ascending: results ordered by price, lowest first; locale = fr: French labels and price format), then compose each combination's expected result from those. If the requirement defines no effect for a class, log a question. "The system handles the combination without error" is not an oracle.

## Coverage

N-wise coverage = feasible t-tuples covered / feasible t-tuples. Always state the strength and the size of the full set next to the reduced set ("16 tests instead of 72 allowed combinations"). Name what the reduced set does not cover: interactions of more than t parameters.

## Output Templates

Classification tree: the script's `--format tree` output (mermaid).

Parameter and class table:

| Classification | Classes | Chosen with | Class expectations |
|---|---|---|---|
| <parameter> | <class 1>, <class 2> | EP / BVA | <expected effect of each class> |

Combination set: the script's value table (`--format markdown`) with the `Expected result` column filled in, or the X matrix (`--format matrix`) when the reviewer is used to classification-tree tools.

Then the coverage row from the shared conventions.

## Common Failure Modes

- generating the full Cartesian product by habit; only do it when the user wants exhaustive coverage or the space is already small (roughly 20 combinations or fewer)
- ignoring constraints until after generation, or counting impossible combinations in the coverage
- using high-order combinations without a risk reason
- pretending all parameters matter equally when one dominates the risk; say so, and consider testing its values against every class of the others
