# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.evaluation import (
    CandidateOutput,
    EvaluationRequest,
    Evaluator,
    PassThroughEvaluator,
)
from miniagent.metering.pricing import PriceConfig
from miniagent.metering.usage import calculate_request_cost
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.models.protocol import (
    ModelContextLimitError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
)
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
from miniagent.runtime.context import (
    ContextCapacityError,
    InvalidSummaryError,
    PromptTokenEstimator,
    QwenPromptTokenEstimator,
    build_summary_request,
    select_compaction_prefix,
    should_compact,
    validate_summary,
)
from miniagent.runtime.events import CompactionAttempt
from miniagent.runtime.execution import (
    ContinueRun,
    EvaluateCandidate,
    RunExecution,
    RunOutcome,
    RunTermination,
    TerminateRun,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    CandidateDisposition,
    ExecutionPhase,
    TerminateDisposition,
    TerminationDecision,
    ToolsDisposition,
)
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.result import RunError, RunResult, TerminationReason
from miniagent.runtime.state import (
    RunDefinition,
    RunState,
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
        estimator: PromptTokenEstimator | None = None,
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
        self.estimator = estimator or QwenPromptTokenEstimator()

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
        state = state.model_copy(
            update={
                "definition": RunDefinition(
                    agent_spec=state.definition.agent_spec,
                    config=state.definition.config,
                    tool_definitions=tuple(self.tools.definitions(agent.allowed_tools)),
                    tool_contract_metadata=tuple(
                        self.tools.contract_metadata(name)
                        for name in agent.allowed_tools
                    ),
                )
            }
        )
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
        return await self._drive(execution)

    async def _drive(self, execution: RunExecution) -> RunResult:
        agent = execution.state.definition.agent_spec
        approval = ApprovalCoordinator(
            config=execution.state.definition.config,
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
        results = None
        try:
            while True:
                execution.account_active_time()
                if self.cancellation_token.is_cancelled:
                    raise CancellationRequested
                phase = execution.state.execution.phase
                if phase in (ExecutionPhase.PREPARE, ExecutionPhase.REQUEST_MODEL):
                    if execution.state.execution.current_step is None:
                        termination = execution.before_next_step()
                        if termination is not None:
                            await execution.record_termination(
                                TerminationDecision(
                                    status="failed",
                                    reason=termination.reason,
                                    error=termination.error,
                                )
                            )
                            continue
                    outcome = await self._request_model(execution, agent)
                elif phase is ExecutionPhase.EXECUTE_TOOLS:
                    tool_outcome = await tools.execute(
                        execution, agent, execution.state.execution.tool_batch
                    )
                    results = tool_outcome.results
                    outcome = (
                        TerminateRun(tool_outcome.termination)
                        if tool_outcome.termination
                        else ContinueRun()
                    )
                elif phase is ExecutionPhase.COMPLETE_STEP:
                    if results is None:
                        store = execution.journal.recorder.store
                        if store is None:
                            raise ValueError(
                                "Completed batch recovery requires persisted tool results"
                            )
                        facts = await store.list_tool_results(
                            execution.state.identity.run_id,
                            step_number=execution.state.execution.steps_started,
                        )
                        results = [fact.result for fact in facts]
                    termination = await execution.complete_tool_step(results)
                    results = None
                    outcome = (
                        TerminateRun(termination) if termination else ContinueRun()
                    )
                elif phase is ExecutionPhase.EVALUATE_CANDIDATE:
                    disposition = execution.state.execution.response_disposition
                    if not isinstance(disposition, CandidateDisposition):
                        raise ValueError(
                            "Candidate phase requires candidate disposition"
                        )
                    outcome = await self._evaluate(
                        execution,
                        EvaluateCandidate(CandidateOutput(text=disposition.text)),
                    )
                elif phase is ExecutionPhase.TERMINATE:
                    disposition = execution.state.execution.response_disposition
                    if not isinstance(disposition, TerminateDisposition):
                        raise ValueError(
                            "Terminate phase requires a persisted decision"
                        )
                    decision = disposition.decision
                    if (
                        decision.final_output is not None
                        and execution.pending_evaluation_result is None
                    ):
                        execution.stage_evaluation_result(
                            passed=decision.status == "succeeded",
                            final_output=decision.final_output,
                        )
                    return await execution.finish(
                        RunTermination(
                            status=RunStatus(decision.status),
                            reason=decision.reason,
                            error=decision.error,
                        )
                    )
                else:
                    raise ValueError(f"Unsupported execution phase: {phase}")
                if isinstance(outcome, TerminateRun):
                    evaluation = execution.pending_evaluation_result
                    await execution.record_termination(
                        TerminationDecision(
                            status="succeeded"
                            if outcome.termination.status is RunStatus.SUCCEEDED
                            else "cancelled"
                            if outcome.termination.status is RunStatus.CANCELLED
                            else "failed",
                            reason=outcome.termination.reason,
                            error=outcome.termination.error,
                            final_output=evaluation.final_output
                            if evaluation
                            else None,
                        )
                    )
        except CancellationRequested:
            operation = execution.state.execution.active_operation
            if operation is not None and operation.kind in ("model", "summary"):
                await execution.interrupt_operation(unconfirmed=True)
            return await execution.finish(
                RunTermination(
                    status=RunStatus.CANCELLED,
                    reason=TerminationReason.CANCEL_REQUESTED,
                )
            )

    @staticmethod
    def _context_failure(code: str, reason: str) -> RunTermination:
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.MODEL_FAILED,
            error=RunError(code=code, message=reason),
        )

    async def _prepare_request(
        self, execution: RunExecution, *, force: bool = False
    ) -> ModelRequest | RunTermination:
        old_request = self._build_request(execution.state)
        try:
            plan = select_compaction_prefix(
                execution.state, old_request, self.estimator, force=force
            )
        except ContextCapacityError as exc:
            await execution.fail_compaction("CONTEXT_CAPACITY_EXCEEDED", str(exc))
            return self._context_failure("CONTEXT_CAPACITY_EXCEEDED", str(exc))
        if plan is None:
            return old_request
        source = None
        recorder = execution.journal.recorder
        if recorder.store is not None and recorder.record is not None:
            source = await recorder.store.load_version(
                execution.state.identity.run_id, recorder.record.latest_snapshot_version
            )
            if source is None:
                raise ValueError("compaction source Snapshot is unavailable")
            plan = replace(
                plan,
                source_snapshot_version=source.version,
                source_event_sequence=source.event_sequence,
            )
        summary_request = build_summary_request(execution.state, plan)
        summary_tokens = self.estimator.estimate(summary_request)
        if summary_tokens + plan.budget.summary_reserve > plan.budget.C - plan.budget.S:
            reason = "summary input plus reserve exceeds context capacity"
            await execution.fail_compaction("COMPACTION_INPUT_TOO_LARGE", reason)
            return (
                self._context_failure("COMPACTION_INPUT_TOO_LARGE", reason)
                if plan.hard_trigger
                else old_request
            )
        if not should_compact(
            plan,
            summary_request_tokens=summary_tokens,
            remaining_steps=execution.state.definition.config.max_steps
            - execution.state.execution.steps_started,
        ):
            return old_request
        attempts: list[CompactionAttempt] = []
        response = None
        failure = "summary generation failed"
        config = execution.state.definition.config
        for number in range(1, config.compaction_max_retries + 2):
            if self.cancellation_token.is_cancelled:
                raise CancellationRequested
            await execution.begin_compaction_attempt(plan, number)
            try:
                response = await await_with_cancellation(
                    asyncio.wait_for(
                        self.provider.generate(summary_request),
                        timeout=config.compaction_timeout_seconds,
                    ),
                    self.cancellation_token,
                )
            except CancellationRequested:
                attempt = CompactionAttempt(attempt=number, error_code="CANCELLED")
                attempts.append(attempt)
                await execution.record_compaction_attempt(attempt)
                await execution.fail_compaction(
                    "COMPACTION_FAILED", "compaction cancelled", tuple(attempts)
                )
                raise
            except Exception as exc:  # noqa: BLE001
                failure = f"{type(exc).__name__}: {exc}"
                attempt = CompactionAttempt(
                    attempt=number, error_code=type(exc).__name__
                )
                attempts.append(attempt)
                await execution.record_compaction_attempt(attempt)
                response = None
                if (
                    isinstance(exc, TimeoutError)
                    or isinstance(exc, ModelProviderError)
                    and exc.retryable
                ):
                    continue
                break
            cost = None
            if self.pricing is not None and response.usage is not None:
                target = execution.state.definition.agent_spec.model
                cost = calculate_request_cost(
                    provider=target.provider,
                    model=target.model,
                    usage=response.usage,
                    pricing=self.pricing,
                )
            attempt = CompactionAttempt(
                attempt=number,
                usage=response.usage,
                request_cost=cost,
                provider_request_id=response.provider_request_id,
                error_code=response.error.code if response.error else None,
            )
            attempts.append(attempt)
            await execution.record_compaction_attempt(attempt)
            if self.cancellation_token.is_cancelled:
                await execution.fail_compaction(
                    "COMPACTION_FAILED", "compaction cancelled", tuple(attempts)
                )
                raise CancellationRequested
            if response.error is not None:
                failure = response.error.message
                retryable = response.error.retryable
                response = None
                if retryable:
                    continue
            break
        if response is None:
            await execution.fail_compaction(
                "COMPACTION_FAILED", failure, tuple(attempts)
            )
            return (
                self._context_failure("COMPACTION_FAILED", failure)
                if plan.hard_trigger
                else old_request
            )
        try:
            conversation = await validate_summary(response, plan, recorder.store)
            candidate_request = old_request.model_copy(
                update={"messages": conversation.messages}
            )
            new_tokens = self.estimator.estimate(candidate_request)
            target_tokens = (
                plan.budget.B
                if plan.hard_trigger
                else execution.state.definition.config.compaction_target_ratio
                * plan.budget.B
            )
            if new_tokens > target_tokens:
                raise InvalidSummaryError(
                    "candidate summary exceeds target input budget"
                )
        except InvalidSummaryError as exc:
            await execution.fail_compaction(
                "COMPACTION_INVALID_SUMMARY", str(exc), tuple(attempts)
            )
            return (
                self._context_failure("COMPACTION_INVALID_SUMMARY", str(exc))
                if plan.hard_trigger
                else old_request
            )
        if self.cancellation_token.is_cancelled:
            await execution.fail_compaction(
                "COMPACTION_FAILED", "compaction cancelled", tuple(attempts)
            )
            raise CancellationRequested
        await execution.publish_compaction(
            conversation,
            plan,
            attempts=tuple(attempts),
            new_tokens=new_tokens,
            summary_input_tokens=summary_tokens,
            source_snapshot_id=source.snapshot_id if source is not None else None,
        )
        return self._build_request(execution.state)

    async def _request_model(
        self,
        execution: RunExecution,
        agent: AgentSpec,
    ) -> RunOutcome:
        if self.cancellation_token.is_cancelled:
            raise CancellationRequested
        if execution.state.execution.current_step is None:
            request = await self._prepare_request(execution)
            if isinstance(request, RunTermination):
                return TerminateRun(request)
            if self.cancellation_token.is_cancelled:
                raise CancellationRequested
            await execution.begin_step(
                provider=agent.model.provider, model=agent.model.model
            )
        else:
            if execution.state.execution.active_operation is not None:
                raise ValueError(
                    "Prior model attempt must be interrupted before continuation"
                )
            request = self._build_request(execution.state)
            await execution.begin_model_retry()
        for business_attempt in range(2):
            try:
                response = await await_with_cancellation(
                    self.provider.generate(request), self.cancellation_token
                )
            except CancellationRequested:
                raise
            except ModelContextLimitError as exc:
                await execution.record_model_failure("CONTEXT_CAPACITY_EXCEEDED")
                if business_attempt == 1:
                    return TerminateRun(
                        self._context_failure("CONTEXT_CAPACITY_EXCEEDED", str(exc))
                    )
                previous_version = execution.state.context.current_version
                retry_request = await self._prepare_request(execution, force=True)
                if isinstance(retry_request, RunTermination):
                    return TerminateRun(retry_request)
                if execution.state.context.current_version == previous_version:
                    return TerminateRun(
                        self._context_failure(
                            "COMPACTION_FAILED",
                            "Provider rejected context and compaction did not publish",
                        )
                    )
                if self.cancellation_token.is_cancelled:
                    raise CancellationRequested
                request = retry_request
                await execution.begin_model_retry()
                continue
            except Exception as exc:  # noqa: BLE001
                await execution.interrupt_operation(unconfirmed=True)
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
            break

        if (
            isinstance(self.estimator, QwenPromptTokenEstimator)
            and response.usage is not None
        ):
            self.estimator.observe(request, response.usage.input_tokens)
        request_cost = None
        if self.pricing is not None:
            if response.usage is None:
                await execution.interrupt_operation(unconfirmed=True)
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
        if decision is None:
            disposition = ToolsDisposition()
        elif decision.target is RunStatus.EVALUATING:
            if decision.candidate_output is None:
                raise ValueError("Evaluation decision requires candidate output")
            disposition = CandidateDisposition(text=decision.candidate_output.text)
        else:
            if decision.reason is None:
                raise ValueError("Termination decision requires reason")
            disposition = TerminateDisposition(
                decision=TerminationDecision(
                    status="failed", reason=decision.reason, error=decision.error
                )
            )
        await execution.record_model_response(
            response, request_cost, disposition=disposition
        )
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
            artifacts=evaluation.artifacts,
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
        if len(messages) < 2 or messages[:2] != (
            SystemMessage(content=agent.system_prompt),
            UserMessage(content=state.identity.task),
        ):
            raise ValueError("fixed system/task prefix does not match Run definition")
        if (
            tuple(self.tools.definitions(agent.allowed_tools))
            != state.definition.tool_definitions
            or tuple(self.tools.contract_metadata(name) for name in agent.allowed_tools)
            != state.definition.tool_contract_metadata
        ):
            raise ValueError("tool contract does not match persisted Run definition")
        return ModelRequest(
            model=agent.model.model,
            messages=tuple(message.model_copy(deep=True) for message in messages),
            tools=tuple(
                tool.model_copy(deep=True) for tool in state.definition.tool_definitions
            ),
            generation=agent.generation.model_copy(deep=True),
        )
