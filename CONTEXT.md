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
