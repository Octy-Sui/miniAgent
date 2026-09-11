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
