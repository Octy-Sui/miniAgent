# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

from datetime import UTC, datetime
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.evaluation import EvaluationRequest, Evaluator, PassThroughEvaluator
from miniagent.metering.pricing import PriceConfig
from miniagent.metering.usage import calculate_request_cost
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.models.protocol import ModelProvider, ModelRequest
from miniagent.policy import (
    ApprovalHandler,
    DefaultPolicyEngine,
    PolicyContext,
    PolicyEngine,
)
from miniagent.runtime.approval import ApprovalCoordinator
from miniagent.runtime.cancellation import (
    CancellationRequested,
    CancellationToken,
    await_with_cancellation,
)
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.execution import (
    ContinueRun,
    EvaluateCandidate,
    RunExecution,
    RunOutcome,
    RunTermination,
    TerminateRun,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.result import RunError, RunResult, TerminationReason
from miniagent.runtime.state import (
    RunState,
    RunDefinition,
    RunStatus,
    create_run_state,
)
from miniagent.runtime.store_contracts import RuntimeStore
from miniagent.runtime.termination import evaluate_model_response
from miniagent.runtime.tool_runner import ToolCallRunner
from miniagent.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: ToolRegistry,
        config: RuntimeConfig | None = None,
        cancellation_token: CancellationToken | None = None,
        evaluator: Evaluator | None = None,
        pricing: PriceConfig | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_context: PolicyContext | None = None,
        approval_handler: ApprovalHandler | None = None,
        run_store: RuntimeStore | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.config = config or RuntimeConfig()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.evaluator = evaluator or PassThroughEvaluator()
        self.pricing = pricing
        self.policy_engine = policy_engine or DefaultPolicyEngine()
        self.policy_context = policy_context or PolicyContext(
            workspace_id="runtime",
            workspace_kind="local",
            revision="in-process-v1",
        )
        self.approval_handler = approval_handler
        self.run_store = run_store

    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
    ) -> RunResult:
        self._validate_agent_tools(agent)
        self.config.validate_output_limit(agent.generation.max_output_tokens)
        if self.pricing is not None:
            self.pricing.price_for(agent.model.provider, agent.model.model)

        now = datetime.now(UTC)
        state = create_run_state(
            run_id=run_id or uuid4(),
            task=task,
            agent_spec=agent.model_copy(deep=True),
            config=self.config.model_copy(deep=True),
            messages=(
                SystemMessage(content=agent.system_prompt),
                UserMessage(content=task),
            ),
            created_at=now,
        )
        state = state.model_copy(update={"definition": RunDefinition(agent_spec=state.definition.agent_spec, config=state.definition.config, tool_definitions=tuple(self.tools.definitions(agent.allowed_tools)), tool_contract_metadata=tuple(self.tools.contract_metadata(name) for name in agent.allowed_tools))})
        execution = await RunExecution.start(
            state,
            RunJournal(
                RunRecorder(
                    run_id=state.identity.run_id,
                    store=self.run_store,
                    recovery_context=recovery_context,
                )
            ),
        )
        approval = ApprovalCoordinator(
            config=self.config,
            handler=self.approval_handler,
            policy_engine=self.policy_engine,
            cancellation_token=self.cancellation_token,
        )
        tools = ToolCallRunner(
            tools=self.tools,
            policy_engine=self.policy_engine,
            policy_context=self.policy_context,
            approval=approval,
            cancellation_token=self.cancellation_token,
        )
        try:
            while True:
                if self.cancellation_token.is_cancelled:
                    raise CancellationRequested
                termination = execution.before_next_step()
                if termination is not None:
                    outcome: RunOutcome = TerminateRun(termination)
                    break
                outcome = await self._run_step(execution, agent, tools)
                if isinstance(outcome, ContinueRun):
                    continue
                if isinstance(outcome, EvaluateCandidate):
                    outcome = await self._evaluate(execution, outcome)
                break
        except CancellationRequested:
            outcome = TerminateRun(
                RunTermination(
                    status=RunStatus.CANCELLED,
                    reason=TerminationReason.CANCEL_REQUESTED,
                )
            )
        if not isinstance(outcome, TerminateRun):
            raise TypeError("Run loop ended without a termination outcome")
        return await execution.finish(outcome.termination)

    async def _run_step(
        self,
        execution: RunExecution,
        agent: AgentSpec,
        tools: ToolCallRunner,
    ) -> RunOutcome:
        await execution.begin_step(
            provider=agent.model.provider,
            model=agent.model.model,
        )
        try:
            response = await await_with_cancellation(
                self.provider.generate(self._build_request(execution.state)),
                self.cancellation_token,
            )
        except CancellationRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            return TerminateRun(
                RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.MODEL_FAILED,
                    error=RunError(
                        code="PROVIDER_EXCEPTION",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                )
            )

        request_cost = None
        if self.pricing is not None:
            if response.usage is None:
                return TerminateRun(
                    RunTermination(
                        status=RunStatus.FAILED,
                        reason=TerminationReason.MODEL_FAILED,
                        error=RunError(
                            code="MODEL_USAGE_MISSING",
                            message="Provider response did not include token usage",
                        ),
                    )
                )
            request_cost = calculate_request_cost(
                provider=agent.model.provider,
                model=agent.model.model,
                usage=response.usage,
                pricing=self.pricing,
            )
        decision = evaluate_model_response(response)
        await execution.record_model_response(
            response,
            request_cost,
            tool_calls=(response.message.tool_calls if decision is None else None),
        )
        if decision is not None:
            if decision.target is RunStatus.FAILED:
                if decision.reason is None:
                    raise ValueError("Failed model decision requires a reason")
                return TerminateRun(
                    RunTermination(
                        status=RunStatus.FAILED,
                        reason=decision.reason,
                        error=decision.error,
                    )
                )
            if decision.target is not RunStatus.EVALUATING:
                raise ValueError(f"Unsupported model decision: {decision.target}")
            if decision.candidate_output is None:
                raise ValueError("Evaluation decision requires candidate output")
            return EvaluateCandidate(decision.candidate_output)

        tool_outcome = await tools.execute(
            execution,
            agent,
            execution.state.execution.tool_batch,
        )
        if tool_outcome.termination is not None:
            return TerminateRun(tool_outcome.termination)
        termination = await execution.complete_tool_step(tool_outcome.results)
        if termination is not None:
            return TerminateRun(termination)
        return ContinueRun()

    async def _evaluate(
        self,
        execution: RunExecution,
        outcome: EvaluateCandidate,
    ) -> TerminateRun:
        candidate = outcome.candidate_output
        await execution.begin_evaluation(candidate.text)
        try:
            evaluation = await await_with_cancellation(
                self.evaluator.evaluate(
                    EvaluationRequest(
                        run_id=execution.state.identity.run_id,
                        task=execution.state.identity.task,
                        candidate=candidate,
                    )
                ),
                self.cancellation_token,
            )
        except CancellationRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            return TerminateRun(
                RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.EVALUATION_FAILED,
                    error=RunError(
                        code="EVALUATOR_EXCEPTION",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                )
            )

        if evaluation.passed:
            final_output = (
                evaluation.output if evaluation.output is not None else candidate.text
            )
            termination = RunTermination(
                status=RunStatus.SUCCEEDED,
                reason=TerminationReason.TASK_VERIFIED,
            )
        else:
            termination = RunTermination(
                status=RunStatus.FAILED,
                reason=TerminationReason.EVALUATION_FAILED,
                error=(
                    RunError(
                        code=evaluation.error.code,
                        message=evaluation.error.message,
                        retryable=evaluation.error.retryable,
                    )
                    if evaluation.error is not None
                    else None
                ),
            )
            final_output = None
        execution.stage_evaluation_result(
            passed=evaluation.passed,
            final_output=final_output,
        )
        return TerminateRun(termination)

    def _validate_agent_tools(self, agent: AgentSpec) -> None:
        try:
            self.tools.definitions(agent.allowed_tools)
        except ValueError as exc:
            raise ValueError(f"Invalid tools for agent {agent.name!r}: {exc}") from exc

    def _build_request(self, state: RunState) -> ModelRequest:
        agent = state.definition.agent_spec
        messages = state.conversation.messages
        if len(messages) < 2 or messages[:2] != (SystemMessage(content=agent.system_prompt), UserMessage(content=state.identity.task)):
            raise ValueError("fixed system/task prefix does not match Run definition")
        if tuple(self.tools.definitions(agent.allowed_tools)) != state.definition.tool_definitions or tuple(self.tools.contract_metadata(name) for name in agent.allowed_tools) != state.definition.tool_contract_metadata:
            raise ValueError("tool contract does not match persisted Run definition")
        return ModelRequest(
            model=agent.model.model,
            messages=tuple(message.model_copy(deep=True) for message in messages),
            tools=tuple(tool.model_copy(deep=True) for tool in state.definition.tool_definitions),
            generation=agent.generation.model_copy(deep=True),
        )
