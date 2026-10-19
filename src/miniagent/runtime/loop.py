# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import overload
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    execution_scope,
)
from miniagent.evaluation import (
    CandidateOutput,
    EvaluationError,
    EvaluationRequest,
    EvaluationResult,
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
    await_with_stop,
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
from miniagent.runtime.events import (
    CompactionAttempt,
    CompactionRequested,
    ResumeCompleted,
    ResumeStarted,
)
from miniagent.runtime.execution import (
    ContinueRun,
    EvaluateCandidate,
    RunExecution,
    RunOutcome,
    RunTermination,
    TerminateRun,
    evaluation_scope,
)
from miniagent.runtime.interruption import (
    InterruptionRequested,
    InterruptionToken,
    current_interruption_token,
    interruption_scope,
    raise_if_interrupted,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    CandidateDisposition,
    ExecutionPhase,
    TerminateDisposition,
    TerminationDecision,
    ToolsDisposition,
)
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.result import (
    RunError,
    RunResult,
    RunSuspension,
    TerminationReason,
)
from miniagent.runtime.state import (
    RunDefinition,
    RunState,
    RunStatus,
    create_run_state,
)
from miniagent.runtime.store_contracts import RunRecord, RuntimeStore
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

    @overload
    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        interruption_token: None = None,
    ) -> RunResult: ...

    @overload
    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        interruption_token: InterruptionToken,
    ) -> RunResult | RunSuspension: ...

    @overload
    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        interruption_token: InterruptionToken | None,
    ) -> RunResult | RunSuspension: ...

    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        interruption_token: InterruptionToken | None = None,
    ) -> RunResult | RunSuspension:
        if (
            interruption_token is not None
            and interruption_token.is_requested
            and not self.cancellation_token.is_cancelled
        ):
            raise InterruptionRequested("Run interrupted before initial record")
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
        starting = RunExecution.start(
            state,
            RunJournal(
                RunRecorder(
                    run_id=state.identity.run_id,
                    store=self.run_store,
                    recovery_context=recovery_context,
                )
            ),
            on_record_created=on_record_created,
        )
        execution = await await_with_stop(
            starting, CancellationToken(), interruption_token
        )
        return await self._drive(execution, interruption_token=interruption_token)

    @overload
    async def resume(
        self, record: RunRecord, *, interruption_token: None = None
    ) -> RunResult: ...

    @overload
    async def resume(
        self, record: RunRecord, *, interruption_token: InterruptionToken | None
    ) -> RunResult | RunSuspension: ...

    async def resume(
        self, record: RunRecord, *, interruption_token: InterruptionToken | None = None
    ) -> RunResult | RunSuspension:
        if self.run_store is None:
            raise ValueError("Resume requires a persisted Run store")
        latest = await self.run_store.get(record.state.identity.run_id)
        if latest != record:
            raise ValueError("Resume record is stale")
        state = record.state
        if state.lifecycle.status not in (
            RunStatus.PREPARING,
            RunStatus.RUNNING,
            RunStatus.EVALUATING,
        ):
            raise ValueError("Resume requires a completed recovery status")
        if (
            state.recovery.session is not None
            or state.recovery.suspension is not None
            or state.execution.active_operation is not None
        ):
            raise ValueError("Resume requires completed recovery and closed operations")
        events = await self.run_store.list_events(state.identity.run_id)
        if (
            not events
            or events[-1].sequence != record.last_event_sequence
            or not isinstance(events[-1].payload, ResumeCompleted)
        ):
            raise ValueError("Resume requires the latest ResumeCompleted event")
        completed = events[-1].payload
        started = [
            event
            for event in events
            if isinstance(event.payload, ResumeStarted)
            and event.payload.resume_attempt_id == completed.resume_attempt_id
        ]
        if len(started) != 1:
            raise ValueError("Resume completed event has no unique start")
        start = started[0]
        payload = start.payload
        assert isinstance(payload, ResumeStarted)
        expected_status = {"created": "preparing", "waiting_approval": "running"}.get(
            completed.business_status, completed.business_status
        )
        if (
            payload.source_sequence != completed.source_sequence
            or payload.business_status != completed.business_status
            or payload.phase != completed.phase
            or completed.phase != state.execution.phase
            or expected_status != state.lifecycle.status.value
            or not completed.source_sequence < start.sequence < events[-1].sequence
            or sum(isinstance(event.payload, ResumeStarted) for event in events)
            != state.recovery.resume_attempts
        ):
            raise ValueError(
                "ResumeCompleted scope differs from current state or source"
            )
        self._validate_agent_tools(state.definition.agent_spec)
        state.definition.config.validate_output_limit(
            state.definition.agent_spec.generation.max_output_tokens
        )
        journal = RunJournal.attach(
            RunRecorder.attach(self.run_store, record),
            await self.run_store.list_unconsumed_approvals(state.identity.run_id),
        )
        return await self._drive(
            RunExecution.attach(record, journal), interruption_token=interruption_token
        )

    @overload
    async def _drive(
        self, execution: RunExecution, *, interruption_token: None = None
    ) -> RunResult: ...

    @overload
    async def _drive(
        self, execution: RunExecution, *, interruption_token: InterruptionToken | None
    ) -> RunResult | RunSuspension: ...

    async def _drive(
        self,
        execution: RunExecution,
        *,
        interruption_token: InterruptionToken | None = None,
    ) -> RunResult | RunSuspension:
        with interruption_scope(interruption_token):
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
            try:
                while True:
                    execution.account_active_time()
                    if self.cancellation_token.is_cancelled:
                        raise CancellationRequested
                    raise_if_interrupted()
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
                        outcome = (
                            TerminateRun(tool_outcome.termination)
                            if tool_outcome.termination
                            else ContinueRun()
                        )
                    elif phase is ExecutionPhase.COMPLETE_STEP:
                        termination = await execution.complete_tool_step()
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
                        await execution.record_termination(
                            TerminationDecision(
                                status="succeeded"
                                if outcome.termination.status is RunStatus.SUCCEEDED
                                else "cancelled"
                                if outcome.termination.status is RunStatus.CANCELLED
                                else "failed",
                                reason=outcome.termination.reason,
                                error=outcome.termination.error,
                            )
                        )
            except InterruptionRequested:
                token = current_interruption_token()
                assert token is not None and token.deadline is not None
                if token.remaining_seconds() <= 0:
                    raise
                try:
                    return await execution.suspend(
                        token.interruption, deadline=token.deadline
                    )
                except TimeoutError as exc:
                    raise InterruptionRequested(
                        "Suspension commit deadline exhausted"
                    ) from exc
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
        progress = execution.state.execution.request_progress
        original = None
        store = execution.journal.recorder.store
        if progress.source_version is not None and store is not None:
            requests = [
                event.payload
                for event in await store.list_events(execution.state.identity.run_id)
                if isinstance(event.payload, CompactionRequested)
                and event.payload.source_snapshot_version == progress.source_version
            ]
            if not requests or any(
                item.covered_messages != requests[0].covered_messages
                or item.hard_trigger != requests[0].hard_trigger
                for item in requests
            ):
                raise ValueError(
                    "Original compaction request is missing or inconsistent"
                )
            original = requests[0]
        try:
            plan = select_compaction_prefix(
                execution.state,
                old_request,
                self.estimator,
                force=force or original is not None,
                covered_messages=original.covered_messages if original else None,
            )
            if plan is not None and original is not None:
                plan = replace(
                    plan,
                    hard_trigger=force
                    or original.hard_trigger
                    or plan.T > plan.budget.B,
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
                execution.state.identity.run_id,
                execution.state.execution.request_progress.source_version
                or recorder.record.latest_snapshot_version,
            )
            if source is None:
                raise ValueError("compaction source Snapshot is unavailable")
            if (
                source.state.conversation != execution.state.conversation
                or source.state.context != execution.state.context
            ):
                raise ValueError(
                    "Compaction source history differs from original source"
                )
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
        if original is None and not should_compact(
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
        progress = execution.state.execution.request_progress
        number = progress.summary_retry_used + (2 if progress.retry_pending else 1)
        while number <= config.compaction_max_retries + 1:
            if (
                progress.last_failure is not None
                and not progress.last_failure.retryable
            ):
                failure = progress.last_failure.message
                break
            if self.cancellation_token.is_cancelled:
                raise CancellationRequested
            raise_if_interrupted()
            await execution.begin_compaction_attempt(
                plan, number, branch="forced_compaction" if force else "boundary"
            )
            try:
                response = await await_with_stop(
                    asyncio.wait_for(
                        self.provider.generate(summary_request),
                        timeout=config.compaction_timeout_seconds,
                    ),
                    self.cancellation_token,
                    current_interruption_token(),
                )
            except InterruptionRequested:
                raise
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
                retryable = (
                    isinstance(exc, TimeoutError)
                    or isinstance(exc, ModelProviderError)
                    and exc.retryable
                )
                await execution.record_compaction_attempt(
                    attempt,
                    failure=RunError(
                        code=type(exc).__name__, message=failure, retryable=retryable
                    ),
                )
                response = None
                if retryable:
                    number += 1
                    progress = execution.state.execution.request_progress
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
            await execution.record_compaction_attempt(
                attempt,
                failure=RunError(
                    code=response.error.code,
                    message=response.error.message,
                    retryable=response.error.retryable,
                )
                if response.error
                else None,
            )
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
                    number += 1
                    progress = execution.state.execution.request_progress
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
            raise_if_interrupted()
            await execution.begin_step(
                provider=agent.model.provider, model=agent.model.model
            )
        else:
            if execution.state.execution.active_operation is not None:
                raise ValueError(
                    "Prior model attempt must be interrupted before continuation"
                )
            progress = execution.state.execution.request_progress
            if progress.branch == "forced_compaction":
                request = await self._prepare_request(execution, force=True)
                if isinstance(request, RunTermination):
                    return TerminateRun(request)
                await execution.begin_model_retry(context_retry=True)
            else:
                request = self._build_request(execution.state)
                await execution.begin_model_retry(
                    context_retry=progress.branch == "business"
                    and progress.retry_pending
                )
        while True:
            try:
                response = await await_with_stop(
                    self.provider.generate(request),
                    self.cancellation_token,
                    current_interruption_token(),
                )
            except (CancellationRequested, InterruptionRequested):
                raise
            except ModelContextLimitError as exc:
                await execution.record_model_failure("CONTEXT_CAPACITY_EXCEEDED")
                if execution.state.execution.request_progress.business_retry_used >= 1:
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
                await execution.begin_model_retry(context_retry=True)
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
        self, execution: RunExecution, outcome: EvaluateCandidate
    ) -> ContinueRun:
        candidate = outcome.candidate_output
        await execution.begin_evaluation(candidate.text)
        operation = execution.state.execution.active_operation
        assert operation is not None
        try:
            with (
                evaluation_scope(execution.evaluation_observer()),
                execution_scope(execution.execution_observer(operation.attempt_id)),
            ):
                evaluation = await await_with_stop(
                    self.evaluator.evaluate(
                        EvaluationRequest(
                            run_id=execution.state.identity.run_id,
                            task=execution.state.identity.task,
                            candidate=candidate,
                        )
                    ),
                    self.cancellation_token,
                    current_interruption_token(),
                )
        except (
            CancellationRequested,
            InterruptionRequested,
            RunPersistenceError,
            ManagedExecutionError,
        ):
            raise
        except Exception as exc:  # noqa: BLE001
            evaluation = EvaluationResult(
                passed=False,
                error=EvaluationError(
                    code="EVALUATOR_EXCEPTION", message=f"{type(exc).__name__}: {exc}"
                ),
            )
        await execution.record_evaluation_result(evaluation)
        return ContinueRun()

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
