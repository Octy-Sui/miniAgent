# Test Design Orchestrator

[![Validate Skills](https://github.com/jovd83/test-design-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/jovd83/test-design-orchestrator/actions/workflows/ci.yml)
[![version](https://img.shields.io/badge/version-2.0.0-blue)](CHANGELOG.md)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/jovd83)

`test-design-orchestrator` is an AgentSkill that turns requirements into traceable black-box test cases. The root skill splits the input into test units, selects the ISTQB technique that fits each unit's logic, explains that choice to the user, and applies the technique through a self-contained technique subskill.

## What This Skill Does

- Splits mixed specifications into test units (a length limit, a rule set, a lifecycle, ...) and tells the user how and why.
- Selects a technique per unit with an ordered decision list: state transition, decision table, classification tree / n-wise, use case testing, boundary value analysis, equivalence partitioning, or acceptance-criteria conversion.
- Generates test cases with concrete expected results, explicit assumptions and open questions, and coverage stated with each technique's own measure.
- Designs confirmation tests from defect reports, including the test level at which the defect is observable.
- Generates and verifies pairwise / n-wise combination sets with a bundled script instead of hand-built tables.

## What This Skill Does Not Do

- It does not render tests for test-management or automation tools (Xray, Zephyr, TestLink, TestRail, importable feature files). It delivers review-ready markdown; formatting is left to a dedicated formatting or export skill.
- It does not review requirement quality, write test strategies or write automation code.
- It does not invent missing business rules. Gaps become open questions or labelled assumptions.

## Repository Layout

```text
.
|-- SKILL.md                               # orchestrator: normalize, split, select, route, assemble
|-- references/
|   `-- test-design-shared-conventions.md  # shared rules + test case, assumption and coverage templates
|-- boundary-value-analysis/               # each technique folder is self-contained:
|   |-- SKILL.md                           #   when to use, workflow, required output
|   |-- references/
|   |   `-- boundary-value-analysis.md     #   detailed rules, coverage measure, templates, failure modes
|   `-- example.md                         #   worked input and output
|-- equivalence-partitioning/
|-- decision-table/
|-- classification-tree-nwise/
|   `-- scripts/nwise.py                   # pairwise / n-wise generator and verifier (stdlib only)
|-- state-transition/
|-- use-case-testing/
|-- acceptance-criteria-to-test-cases/
|-- agents/openai.yaml
|-- assets/                                # icons
|-- evals/                                 # trigger and technique-selection fixtures
|-- scripts/validate-skill-repo.py
`-- tests/
```

Per invocation the agent reads the root `SKILL.md`, the shared conventions, and for each selected technique only: its `SKILL.md`, its reference file and, when needed, its example.

## Workflow

1. Normalize the test basis; batch open questions into one round, otherwise log assumptions.
2. Split the input into test units.
3. Select a technique per unit (the logic inside the text decides, not the document type).
4. Open the deliverable with a test design plan: unit, source, logic, technique, why.
5. Apply each technique subskill.
6. Deliver assumptions, technique artifacts, test cases and coverage.

## Installation

```bash
npx skills add jovd83/test-design-orchestrator --skill test-design-orchestrator
```

For a manual install, copy this folder into your skills directory as `test-design-orchestrator`. The installable parts are `SKILL.md`, `references/`, the technique folders and `agents/openai.yaml`.

## Memory Model

- Runtime: working notes (units, techniques, assumptions, IDs) for the current task only.
- Project-local: a reusable test-design brief, only when the user asks for it.
- Shared memory: out of scope.

## Validation and Evaluation

```bash
pip install -r scripts/requirements.txt
python scripts/validate-skill-repo.py
python -m unittest discover -s tests -v
python classification-tree-nwise/scripts/nwise.py <model.json> --strength 2
```

Evaluation fixtures:

- `evals/trigger-queries.json`: should / should-not trigger checks, including near misses with neighbouring testing skills.
- `evals/technique-selection-cases.json`: expected technique per scenario, including mixed specs, a user-mandated technique and a defect report.

## Contributing

- Keep technique guidance, templates and examples inside the technique folder that uses them (`<technique>/references/`, `<technique>/example.md`); only rules shared by every technique go in the root `references/`.
- Update `evals/` together with any change to the description or the selection order.
- Keep the root frontmatter under 1000 characters (dispatcher limit); `validate-skill-repo.py` checks it.

## License

MIT. See [LICENSE](./LICENSE).
