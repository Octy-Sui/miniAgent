# Handoff Template

Use this structure for `docs/HANDOFF.md`. Omit empty optional sections instead
of leaving placeholders in the finished document.

````markdown
# Task Handoff: <task title>

<!-- handoff: captured-against <short-sha> | worktree <clean-or-dirty> | updated <YYYY-MM-DD> -->

## Goal and success criteria

<Goal and observable completion conditions.>

## Current state

<Where the task stands and what is currently true.>

## Completed

- <Completed outcome.>

## Current changes

- `<path>` — <intent and ownership of the change.>

## Decisions and constraints

- <Decision or constraint and why it matters.>

## Validated evidence

- `<command or file evidence>` — <result and which state it validates.>

## Not yet validated

- <Assumption, missing check, and risk if it is wrong.>

## Remaining work

1. <Next priority and acceptance condition.>

## Risks, blockers, and open questions

- <Concrete issue requiring attention.>

## Critical files

- `<path>` — <why it must be loaded.>

## Context to load

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. <Only other required context.>

## Next action

<One precise action.>

```shell
<Executable command, when one naturally exists.>
```

## Resume prompt

`$handoff resume`
````

Quality bar:

- Validation is tied to the exact state it checked.
- Earlier results are not presented as evidence for later edits.
- Current changes identify pre-existing user work separately.
- The next action can be started without reconstructing the old conversation.
