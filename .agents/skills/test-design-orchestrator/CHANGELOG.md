# Changelog

All notable changes to this repository are documented here.

## [2.0.0] - 2026-09-23

### Changed
- Technique selection moved into the root `SKILL.md` as an ordered decision list; the logic inside a requirement decides the technique, not the document type.
- Mixed specifications are split into test units with one technique each, and the deliverable opens with a test design plan that explains the split.
- Each technique folder is self-contained: a short `SKILL.md` (when to use, workflow, required output), its own `references/<technique>.md` (detailed rules, coverage measure, markdown output templates, failure modes) and a worked `example.md`. Subskill paths now resolve relative to the subskill folder.
- Technique guidance deepened: BVA step size and ISTQB 2/3-value rules, each-choice for EP, spec-defect findings for decision tables, default coverage targets for state transitions, path inventory for use cases, embedded-logic handling for acceptance criteria.
- Ask-vs-assume policy unified: one batched question round, otherwise labelled assumptions.
- `validate-skill-repo.py` resolves references relative to each file, checks the new layout, the frontmatter limit and one version across SKILL.md, subskills, README and CHANGELOG.

### Added
- `classification-tree-nwise/scripts/nwise.py`: deterministic each-choice / pairwise / n-wise generator with constraints, coverage verification and mermaid tree output.
- Defect confirmation mode: confirmation test, neighbouring tests and the test level(s) where the defect is observable.
- Unit tests in `tests/` for the generator and the repository validator.
- Near-miss trigger queries and new technique-selection cases.

### Removed
- `test-case-formatter/` bridge and every formatting asset owned by a formatting/export skill: BDD/Xray/Zephyr templates, TestLink PDF, formatter guides, `format-validator.py`, `package-xray-features.py`.
- `technique-selector/` subskill and `technique-selection-matrix.md` (merged into the root skill).
- Root-level technique references (moved into each technique's own `references/` folder).
- Jinja templates (translated into markdown templates inside the technique folders) and the root `examples/` folder (moved into the technique folders).
- Hard-coded paths to other skills.

## [1.0.1] - 2026-04-30

### Changed
- Trim `SKILL.md` frontmatter to fit the 1000-character dispatcher limit (description trim, migrate non-dispatcher fields to body).

