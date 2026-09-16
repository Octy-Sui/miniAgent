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

**Run Event**:
An immutable, ordered fact about something that happened during a Run. Run Events are the audit history from which current state can be explained or reconstructed.
_Avoid_: Message, log line

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
An immutable, addressable large output produced by a Run, such as a patch, complete command output, test report, or trace.
_Avoid_: Event payload, message

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
