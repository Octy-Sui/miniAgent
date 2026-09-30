---
name: classification-tree-nwise
description: Classification tree and n-wise subskill of test-design-orchestrator. Models interacting parameters and generates verified each-choice, pairwise or higher-strength combination sets instead of the full Cartesian product.
metadata:
  author: jovd83
  version: 2.0.0
---

# Classification Tree and N-Wise

Use for a test unit with several interacting parameters where no rule defines the outcome per combination, and all combinations would be too many: browsers x OS x locale, product configuration options, integration settings. When the outcome is defined per combination by rules, use `../decision-table/SKILL.md` instead.

Read `./references/classification-tree-nwise.md` before generating: it holds the rules behind each step, the model format, the coverage measure, the output templates and the common failure modes. Read `./example.md` when the shape of the artifact is unclear.

## Workflow

1. Build the classification tree: parameters and their non-overlapping classes.
2. Record constraints (forbidden combinations) before generating.
3. Pick the interaction strength: pairwise by default.
4. Generate the combinations with the bundled script; do not build them by hand. Paths are relative to this subskill folder:

   ```bash
   python ./scripts/nwise.py model.json --strength 2               # value table (default)
   python ./scripts/nwise.py model.json --format matrix            # classification-tree X matrix
   python ./scripts/nwise.py model.json --format tree              # mermaid classification tree
   python ./scripts/nwise.py model.json --verify existing-tests.json  # check a set someone already has
   ```

   If Python is not available, build the set by hand, check every pair explicitly, and label it "pairwise (manually checked)".
5. Give every combination a concrete expected result, composed from per-class expectations.

## Required Output

- classification tree (mermaid) and class table with class expectations
- combination set with the `Expected result` column filled in
- negative tests for constraints that are also business rules
- coverage row with the reduction versus the allowed combinations
