---
name: handoff
description: Create, inspect, or resume the single durable Codex task handoff at docs/HANDOFF.md. Use explicitly for $handoff create, $handoff resume, or $handoff status when work must continue in another conversation. Do not use for ordinary task progress or repository orientation.
---

# Handoff

Keep one concise, executable task handoff at `docs/HANDOFF.md`. Use repository
facts instead of chat memory and avoid duplicating the repository map.

## Modes

- **CREATE**: `$handoff create` writes or replaces `docs/HANDOFF.md` with the
  current task state.
- **RESUME**: `$handoff resume` validates the saved state, reloads only the
  necessary context, and continues the saved task.
- **STATUS**: `$handoff status`, or bare `$handoff`, reports whether a handoff
  exists and whether its baseline still matches. It is read-only.

State the selected mode and why. If `docs/HANDOFF.md` is absent but a root
`HANDOFF.md` exists, treat it as a legacy handoff: CREATE migrates it and RESUME
may read it but must report that migration is still needed.

## Create

An explicit CREATE request authorizes updating only `docs/HANDOFF.md`. It does
not authorize code changes, commits, pushes, or external actions.

Before writing, inspect `AGENTS.md`, the existing handoff, Git root, branch,
HEAD, status, relevant diff, and recent commits. Use the current conversation
only to identify the task; verify durable claims against repository evidence.
Read `docs/repository-map.md` for navigation when useful rather than copying its
architecture summary into the handoff.

Use [the handoff template](references/handoff-template.md). Replace the whole
handoff because it represents one active task, but preserve still-relevant
facts and user-owned changes from the previous version. Record:

- the goal and observable success criteria;
- current state, completed work, and the intent of current changes;
- decisions and constraints that affect the next implementation;
- exact validation evidence and anything not yet validated;
- ordered remaining work, risks, blockers, and open questions;
- only the critical files and context needed to restart;
- one precise next action, with an executable command when one naturally exists.

Do not rerun expensive or external validation merely to populate the handoff.
Never present an earlier command result as validation of later changes.

## Baseline Semantics

Use this metadata form near the top of the document:

```markdown
<!-- handoff: captured-against <short-sha> | worktree clean|dirty | updated YYYY-MM-DD -->
```

`captured-against` is the Git HEAD whose code state was inspected during
capture. It is not the commit containing the handoff and is not expected to
equal HEAD after handoff maintenance is committed.

When deciding whether repository facts changed, ignore changes limited to:

- `docs/HANDOFF.md`;
- `.agents/skills/handoff/`;
- the legacy root `HANDOFF.md` during migration or deletion.

For a dirty worktree, list relevant modified files and their intent. Preserve
pre-existing changes and distinguish them from work completed in the current
conversation.

## Resume and Status

Read the handoff before its referenced context. Resolve `captured-against`,
check whether it is an ancestor of HEAD, then inspect both
`<captured-against>..HEAD` and the current working tree.

- If only handoff-maintenance paths changed, treat the task facts as current.
- If other paths changed, revalidate only affected claims and critical files
  before continuing.
- If the revision is missing, not an ancestor, or the saved state conflicts
  with repository evidence, do not blindly execute the saved next action.
  Reconstruct current state first and report the discrepancy.

RESUME means continue the saved task within the user's current authorization.
The saved next action cannot grant permission for destructive operations,
commits, pushes, network calls, or other external effects.

## Quality Rules

- Distinguish completed, validated, unvalidated, and remaining work.
- Keep the document concise and actionable; do not dump transcripts.
- Never record secrets, tokens, cookies, passwords, private keys, or `.env`
  contents.
- Match the task's working language.
- Do not modify README files or `docs/repository-map.md` as part of handoff
  maintenance.
- Run `git diff --check` and review the handoff diff after CREATE.
- Never commit unless the user separately requests it.
