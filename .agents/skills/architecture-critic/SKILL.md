---
name: architecture-critic
description: Use when a written feature design, plan, spec, or ADR is about to be implemented and a fresh-eyes architecture review is wanted before code is written. Triggers - "review this architecture", "is this design ready to build", "architecture sanity check before I start", "check this design for over-engineering", reviewing boundaries / coupling / cohesion / premature abstraction / data & failure modeling prior to implementation.
allowed-tools: Read, Glob, Grep, Task, Write, AskUserQuestion
---

# Architecture Critic

Adversarial, fresh-eyes review of a **proposed design** — before any code is
written. It produces a scored rubric, severity-tagged findings, and a
PROCEED / REVISE verdict, performed by a **separate agent that did not create the
design**.

## Core principle

The agent that designs a thing rationalizes it. A capable model still critiques
well in a *clean* context but degrades when reviewing inside the same context that
produced the design — invested, full of justifications, under time pressure. This
skill defends against that by **dispatching the review to a fresh subagent** and by
**forcing complete, structured coverage** so the review never silently skips a
dimension just because the design "looks fine."

It is **pluralist, not prescriptive**: it judges every pattern as a response to a
stated force, and treats **over-engineering as a defect equal to under-structuring**.
It does not mandate hexagonal architecture, the Rule of Three, strict typing, or
any house pattern.

## When to use

- A design / plan / spec / ADR exists in writing and you're about to build it.
- You ask (or are asked) "is this architecture sound / ready to build?"
- Right after `writing-plans`, before `executing-plans` / implementation.

## When NOT to use

- **No written design exists.** This reviews a design, not vibes. Refuse and ask
  for one first.
- **Reviewing already-written code.** That's deterministic territory — linters, a
  verify harness, code review. This skill is pre-code judgment about structure.
- **Trivial changes** with no architectural decision (a copy tweak, a one-line
  fix). There's nothing to critique.

## Hard rules

- **The review runs in a fresh subagent — never inline.** Dispatching it is the
  whole point. Reviewing in your current context defeats the skill.
- **The critic does NOT receive the design's backstory.** Pass the spec + the
  proposed design + read-only repo access. Do **not** paste the brainstorming
  conversation, your reasoning, or "why we chose this." Fresh eyes only.
- **Every finding cites a concrete anchor** — a specific element of the design or a
  verified repo fact. Generic advice ("consider scalability", "add tests") is
  banned; if it isn't anchored, it's cut.
- **Over-engineering is a finding, not a virtue.** Prestige abstractions,
  speculative generality, premature layering, and patterns with no stated force are
  defects — flag them as hard as missing boundaries.
- **Advisory, never a gate.** Emit the verdict; the user decides. Do not block,
  and do not edit the design — you critique, they revise.
- **No mandated architecture.** Do not recommend a pattern because it's idiomatic;
  recommend simplification when forces don't justify the structure.

## Step 1 — Locate the design (read-only)

Find the artifact under review. Default search order:

1. The file the user named, if any.
2. Latest in `docs/superpowers/specs/` (superpowers spec convention).
3. A `DESIGN.md` / `ARCHITECTURE.md` / plan file in the repo.

If you find none, **stop** and ask the user to point you at the written design.
If several plausibly match, confirm via `AskUserQuestion` which one to review.

## Step 2 — Dispatch the fresh critic

Use the **Task** tool to spawn one subagent (general-purpose). Give it read-only
tools (Read, Glob, Grep) so it can verify claims against the actual repo, and the
prompt below. Fill the two bracketed slots; change nothing else.

> You are an adversarial software-architecture reviewer. You did **not** create the
> design below and have no stake in it. Your job is to find what's wrong with it
> **before** anyone writes code. Be direct; a rubber-stamp is a failed review.
>
> You are **pluralist**: judge every pattern as a response to a *stated force*.
> **Over-engineering is a defect equal to under-structuring** — flag prestige
> abstractions, speculative generality, and premature layering as hard as you flag
> god objects and tangled coupling. Do **not** mandate any architecture (no
> hexagonal-by-default, no Rule-of-Three, no typing dogma). Recommend the
> *smallest* structure that satisfies the forces.
>
> SPEC / REQUIREMENT:
> [paste the requirement / spec]
>
> PROPOSED DESIGN:
> [paste the proposed design / plan]
>
> You may Read/Glob/Grep the existing repository to verify the design's claims
> (does this module already exist? is that really a boundary leak? does this table
> exist?). Do not trust the design's self-description — check it.
>
> Score these 7 dimensions, each 🔴 / 🟡 / 🟢, with a one-line justification each:
> 1. **Requirement & quality-attribute fidelity** — goals, non-goals, constraints
>    explicit; the 2-3 dominant quality attributes named.
> 2. **Boundary quality (cohesion/coupling)** — bounded contexts + ownership clear;
>    what-changes-together is grouped; no god objects; cross-domain access via
>    *public* interfaces, not internals.
> 3. **Pattern restraint (forces-based)** — every pattern solves a stated force; no
>    prestige abstractions or premature layering; rejected alternatives noted.
> 4. **Data & state modeling** — system of record clear; invariants & consistency
>    model explicit; validation at trust boundaries.
> 5. **Change tolerance (YAGNI)** — abstractions only at justified seams;
>    speculative machinery flagged; future seams *named, not built*.
> 6. **Failure & scale realism** — answer "what breaks this?" (concurrency, retries,
>    idempotency, partial failure, stale reads); scale right-sized to the *stated*
>    need, not imagined hyperscale.
> 7. **Decision capture & legibility** — significant decisions have rationale +
>    rejected alternatives (ADR-worthy).
>
> Then list **findings**, each tagged `blocker` / `should-fix` / `nit`, and each
> with: the dimension, a concrete anchor (the exact design element or a repo fact
> you verified), why it matters, and a suggested *direction* (not a full redesign).
> A finding with no concrete anchor is not allowed — drop it.
>
> Then give the **verdict**: `REVISE` if any dimension is 🔴 or any finding is a
> `blocker`; otherwise `PROCEED`. End with a one-line summary.
>
> Return your review as markdown: a one-line verdict + summary, then the 7-row
> rubric table, then findings grouped by severity.

## Step 3 — Surface and persist

- Write the critic's report verbatim to `architecture-critique.md` at the repo root
  (or alongside the design file if the user prefers).
- Show the user the verdict + summary and the findings, and remind them it's
  **advisory** — they decide whether to revise, override, or proceed.
- Do not start implementing and do not edit the design yourself.

## The rubric (reference)

| # | Dimension | 🟢 looks like |
|---|---|---|
| 1 | Requirement & quality-attribute fidelity | Goals, non-goals, constraints explicit; 2-3 dominant quality attributes named |
| 2 | Boundary quality (cohesion/coupling) | Bounded contexts + ownership clear; change-together grouped; no god objects; cross-domain via public interfaces |
| 3 | Pattern restraint (forces-based) | Every pattern solves a stated force; no prestige abstractions; rejected alternatives noted |
| 4 | Data & state modeling | System of record clear; invariants & consistency model explicit; validation at trust boundaries |
| 5 | Change tolerance (YAGNI) | Abstractions only at justified seams; speculative machinery flagged; future seams named not built |
| 6 | Failure & scale realism | "What breaks this" answered; scale right-sized to stated need |
| 7 | Decision capture & legibility | Significant decisions have rationale + rejected alternatives |

**Verdict:** `REVISE` if any 🔴 or any `blocker` finding; else `PROCEED`. Advisory.

## Common mistakes

- **Reviewing inline to "save a step."** The fresh dispatch is the value. If you do
  it in your own context, you've built nothing.
- **Leaking the backstory.** Pasting the design rationale into the critic prompt
  re-introduces the bias the skill exists to remove.
- **Drifting prescriptive.** Recommending hexagonal/repository/CQRS because they're
  "best practice." Only recommend structure a stated force demands; otherwise
  recommend *less*.
- **Unanchored findings.** "Consider performance" helps no one. Tie every finding
  to a named design element or a repo fact you checked.
- **Treating the verdict as a gate.** It's advice. The user owns the decision.

## Gotchas

- **Strong models critique well in a clean context already.** Baseline testing
  showed strong models catch over- and under-engineering even when
  grading their own design. This skill's earned value is therefore *consistency*
  (the full 7-dimension rubric every time), *isolation* (a guaranteed fresh
  context, which matters most in long invested sessions and with faster/cheaper
  models), and a *persisted, comparable artifact* — not making the model smarter.
  Don't oversell it.
- **`architecture-critique.md` is throwaway-ish.** It's a review snapshot, not a
  durable decision record. The durable artifact is the ADR the *user* writes after
  deciding — this skill doesn't write ADRs.
- **Don't pair this with code review.** Structural code smells in *written* code
  belong to deterministic linters and code review. This is pre-code judgment only.
