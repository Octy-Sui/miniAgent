---
name: repo-map
description: Create or refresh lean, evidence-backed repository orientation for Codex. Use explicitly for $repo-map, repository mapping, stale map refreshes, or a focused subsystem map. Do not use for ordinary feature work, bug fixes, or one-off file lookup.
---

# Repo Map

Build the smallest repository map that prevents a competent engineer from choosing
the wrong entry point, layer, command, or test. Prefer fresh navigation over a
precomputed index.

## Modes

- **NEW**: no `repo-map` managed block exists in the root `AGENTS.md`.
- **REFRESH**: managed output exists; revalidate stale claims and update the minimum
  affected content.
- **FOCUS**: the request names a subsystem; map that area deeply and change the root
  summary only if a repository-wide fact changed.

State the selected mode and why. `$repo-map` selects NEW or REFRESH automatically;
`$repo-map refresh` forces REFRESH; `$repo-map focus <scope>` selects FOCUS.

## Outputs

Maintain two layers:

1. A short, always-loaded block at the top of the root `AGENTS.md`.
2. A detailed, on-demand map at `docs/repository-map.md`.

Wrap the root block in exact markers so manual instructions remain untouched:

```markdown
<!-- repo-map:start -->
<!-- repo-map: verified-against <short-sha> | mapped YYYY-MM-DD -->
# Repository Orientation
...
<!-- repo-map:end -->
```

Use the same metadata comment near the top of `docs/repository-map.md`.

Keep the root block under about 50 lines and the detailed map under about 150 lines.
If removing a line would not make a future agent more likely to make a mistake,
remove it.

## Map the Repository

First inspect the Git root, status, HEAD, existing agent instructions, both managed
outputs, and any existing stamp. Preserve unrelated and user-authored changes.

Navigate with `rg`, `rg --files`, Git, targeted reads, and import/call-site tracing.
Do not create embeddings or a persistent symbol index. For a small repository, map
inline. For a genuinely large or polyglot repository, use independent read-only
lenses concurrently when collaboration is available and local instructions allow it:

- entry points and package structure;
- build, test, type-check, lint, and run commands;
- architecture, data flow, and key protocols;
- non-default conventions, invariants, and gotchas;
- tests, fixtures, and the narrowest useful verification commands.

Every retained claim must cite a real repository path or the exact command that
proves it. Derive commands from project configuration or CI and run safe read-only
commands before calling them verified. Mark unsafe or unavailable commands as
unverified; never infer success from the presence of a config file.

Distinguish implementation state explicitly:

- `wired`: reachable from the current composition root or execution path;
- `contract-only`: a type, protocol, or isolated implementation exists but is not
  connected to the current path;
- `roadmap`: described only as intended future behavior.

Code and tests outrank narrative documentation for current behavior. Keep a domain
glossary separate from implementation navigation, and never treat a handoff,
backlog, or design document as proof that behavior is wired.

## Write and Refresh

An explicit create, map, refresh, or focus request authorizes updates to the two
managed outputs. If the user asks for preview or read-only analysis, show the draft
without writing. Never commit or push unless separately requested.

On NEW, insert the managed block above existing root instructions and create the
detailed map. On REFRESH, replace only the marked root block and make the smallest
accurate detailed-map diff. On FOCUS, update only the scoped routes and any global
summary made inaccurate by the findings.

Treat `verified-against` as the committed code baseline whose repository facts were
checked. It is not the commit containing the map and is not expected to equal the
current HEAD after the map is committed. This avoids a self-referential stamp that
can never match its own commit ID.

For REFRESH, resolve the stamped revision and inspect both
`<verified-against>..HEAD` and relevant working-tree changes. Ignore changes limited
to these map-maintenance paths when deciding whether repository facts are stale:

- the managed block in `AGENTS.md`;
- `docs/repository-map.md`;
- `.agents/skills/repo-map/`.

If no other paths changed, make no output or stamp change. If committed repository
facts changed, revalidate affected claims and advance `verified-against` to the
current HEAD only after validation succeeds. An invalid or missing revision requires
full revalidation.

If relevant uncommitted changes exist, map the actual working tree but use
`provisional-against <short-sha>` instead of `verified-against`; a later refresh must
fully revalidate after those changes are committed. Changes limited to the three
map-maintenance paths above do not make a map provisional.

Before finishing:

- confirm all cited paths exist;
- confirm current, contract-only, and roadmap claims are not conflated;
- run the repository's applicable verification commands and `git diff --check`;
- review the full diff for overwritten manual instructions or unrelated changes;
- report the mode, updated outputs, verified commands, and anything left unverified.
