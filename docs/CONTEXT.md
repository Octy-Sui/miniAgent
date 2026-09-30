# MiniAgent Runtime

MiniAgent runs a model-directed task through bounded steps and controlled tool calls.

## Language

**Run**:
The complete execution of one user task, ending with exactly one structured result.
_Avoid_: Session, workflow

**Agent**:
The active model behavior that decides the next action in a Run. A Run has only one active Agent at a time.
_Avoid_: Worker, model

**Agent Specification**:
The configuration that defines an Agent's prompt, model target, behavior parameters, and permitted tools.
_Avoid_: Agent runtime, agent instance

**Step**:
One model request together with all tool calls produced by its response.
_Avoid_: Round, turn

**Tool Call**:
One Agent-requested invocation of a named tool with arguments, correlated with its Tool Result within a Run.
_Avoid_: Step, command

**Tool Result**:
The outcome of one Tool Call, including its execution status, observations, and any failure information. Incomplete observations do not by themselves mean that execution failed.
_Avoid_: Model response, final output

**Policy Decision**:
A determination of whether a specific Tool Call is permitted, prohibited, or requires human approval under the applicable rules and execution conditions.
_Avoid_: Tool result, approval response

**Approval Request**:
A pending request for human authorization of one specific Tool Call under stated execution conditions. Approval does not override a policy prohibition or authorize other calls.
_Avoid_: Permission grant, run approval

**Approval Resolution**:
The recorded disposition of an Approval Request, distinguishing a human approval or rejection from expiry or invalidation.
_Avoid_: Policy decision, tool result

**Agent Segment**:
A consecutive sequence of Steps executed by the same active Agent within a Run.
_Avoid_: Sub-run

**Termination Reason**:
The specific condition that caused a Run to end, distinct from the Run's broad completion status.
_Avoid_: Finish reason

**Run Status**:
The lifecycle stage of a Run, including active stages and terminal outcomes.
_Avoid_: Finish reason, model status

**Run State**:
The durable facts required to inspect or resume a Run, identified by one stable Run ID.
_Avoid_: Runtime object, process state

**Working Context**:
The selected recent conversation and tool observations presented to the active Agent, alongside the task instructions and Task Memory.
_Avoid_: Complete run history, run state

**Task Memory**:
A Run-scoped record of evidence-backed observations, hypotheses, rejected strategies, changes, and verification status retained across context compactions. It distinguishes observations from interpretations and preserves their sources.
_Avoid_: Long-term memory, run trace

**Context Compaction**:
The replacement of older Working Context with source-linked Task Memory while preserving the Run's original evidence and execution state.
_Avoid_: History deletion, workspace restore

**Run Event**:
An immutable, ordered fact about something that happened during a Run. Run Events are the audit history from which current state can be explained or reconstructed.
_Avoid_: Message, log line

**Run Event Sequence**:
The position of a committed Run Event within its Run, starting at one and increasing by one for each subsequent event. It orders that Run's history independently of timestamps, Steps, and state transitions.
_Avoid_: Step number, state version, global event order

**Run Trace**:
A queryable observability view, identified by its Run's stable identity, that correlates ordered Run Events with timed operation spans to explain causality, timing, and outcomes. It is derived from durable facts and is not recovery state.
_Avoid_: Run event, run snapshot, log

**Trace Span**:
A timed operation within a Run Trace, such as a model request, Tool Call, Workspace command, or Evaluation. Its parent expresses logical ownership, not a requirement that parent and child time ranges contain one another.
_Avoid_: Step, run event

**Span Category**:
The role a Trace Span plays in the Run hierarchy, distinct from a telemetry protocol's transport-oriented span kind.
_Avoid_: Span kind, event type

**Run Snapshot**:
A versioned copy of Run State at a known Run Event sequence, used to resume without replaying the complete event history.
_Avoid_: Workspace checkpoint, event

**Workspace**:
The Run-owned code environment in which commands execute and candidate changes are produced. Isolation guarantees depend on the selected Workspace Backend.
_Avoid_: Runtime, repository

**Local Workspace**:
A Workspace backed by a Run-exclusive host directory. It isolates code state from other Runs but is not a security or resource-isolation boundary.
_Avoid_: Host sandbox, current directory

**Docker Workspace**:
A Workspace backed by a dedicated container that applies the configured process, compute, memory, capability, and network controls.
_Avoid_: Local workspace, task image

**Workspace Backend**:
The lifecycle implementation that creates, recovers, and destroys one kind of Workspace.
_Avoid_: Workspace, execution environment

**Workspace Checkpoint**:
A restorable identity for the code state of a Workspace at a safe boundary.
_Avoid_: Run snapshot, artifact

**Artifact**:
An immutable, addressable large output produced by a Run, such as a patch, complete command output, test report, or trace export.
_Avoid_: Event payload, message

**Trace Artifact**:
An immutable exported serialization of a Run Trace stored as an Artifact for retention or transport. It is not the authoritative Run Event history or a recovery source.
_Avoid_: Run trace, event store

**Recovery Decision**:
A deterministic instruction describing how the Runtime should respond to failure, stagnation, regression, interruption, or budget pressure.
_Avoid_: Recovery execution, retry result

**Evaluation**:
An independent determination of whether a Candidate Output satisfies the task without unacceptable regression.
_Avoid_: Agent finish, model judgment

**Benchmark Task**:
A versioned software issue, repository revision, verification contract, and Task Image used as one reproducible evaluation unit.
_Avoid_: Run, prompt

**Development Set**:
A fixed collection of Benchmark Tasks that may be inspected and rerun while developing the Agent and Runtime.
_Avoid_: Evaluation set, smoke test

**Evaluation Set**:
A frozen collection of Benchmark Tasks kept separate from development and used unchanged for comparative experiments.
_Avoid_: Development set, leaderboard

**Task Image**:
The container image associated with one Benchmark Task and containing its repository revision and prepared execution environment.
_Avoid_: Workspace, base image

**Candidate Output**:
The result submitted by an Agent for independent evaluation; it is not evidence that the Run succeeded.
_Avoid_: Final result

**Final Output**:
The externally reported result of a Run after successful evaluation.
_Avoid_: Candidate output

**Model Usage**:
The provider-reported token consumption of one model request, including nested cached input and reasoning output counts.
_Avoid_: Run usage, token cost

**Input Pricing**:
Input is priced either as separate uncached and cached categories or as one category containing all input tokens.
_Avoid_: Model usage, output pricing

**Run Usage**:
The accumulated model and tool consumption of a Run, including its model cost.
_Avoid_: Model usage, request usage

**Request Cost**:
The model charge attributed to one provider response under the active price configuration.
_Avoid_: Run cost, token usage
