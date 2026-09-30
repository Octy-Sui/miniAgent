# Context Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Qwen Run 在完整请求接近上下文预算时压缩已完成的旧 Step，安全续行，并以可审计的事务状态支持未来恢复。

**Architecture:** `AgentRuntime` 在 `begin_step` 前用完整业务 `ModelRequest` 做预算判断，`runtime/context.py` 选择完整 Step、构造无工具摘要请求并校验候选。`RunExecution` 计量辅助调用，`RunJournal` 经 SQLite 一次提交活动消息、压缩完成事件和 Snapshot；业务请求始终从已提交状态与持久化工具定义构造。

**Tech Stack:** Python 3.13、Pydantic、Typer、Jinja、SQLite、pytest、Qwen Chat Completions。

**Spec:** [功能需求](../specs/2026-09-24-context-compaction-design.md)、[架构设计](../specs/2026-09-24-context-compaction-architecture-design.md)；[测试设计](../specs/2026-09-29-context-compaction-test-design.md)给出 U1～U7 验证依据。`docs/context-compaction-design.md` 是另一版方案，与本计划的状态和范围不同，执行时以此处链接的两份设计为准。

## Global Constraints

- 首版仅支持当前 Qwen Provider；不实现跨进程恢复执行器、手动压缩命令、Provider 原生压缩、分块归纳或策略插件。
- 默认模型配置：`context_window_tokens = 32768`、`max_output_tokens = 4096`；`S = max(1024, ceil(0.05 × C))`、`B = C - O - S`，且 `B > 0`。
- 摘要输出预留为 `min(2048, floor(0.15 × B))`，配置时至少 256；摘要请求的估算输入加输出预留须 `≤ C - S`。
- `compaction_trigger_ratio = 0.70`、`compaction_target_ratio = 0.50`，且 `0 < target < trigger < 1`；`recent_steps_to_keep = 3`，至少 1。
- `compaction_timeout_seconds = 60`，正数；`compaction_max_retries = 1`，非负整数；单次压缩最多 `1 + retries` 次 Provider 调用。
- 软触发仅在 `T ≥ trigger × B`、`T ≤ B`、`H = min(3, max_steps - steps_started) ≥ 2`、`D > 0`、`H × D > K` 且有可压前缀时尝试，发布后须 `T_new ≤ target × B`；`T > B` 时跳过成本筛选，发布后须 `T_new ≤ B`。
- 固定 system prompt、原始 task、最近完整 Step 保持原文；工具调用与所有结果成组保留或成组替换；摘要只是一条普通 assistant 历史消息。
- 不改变已执行工具、审批、Workspace 或 Step 上限；已知摘要用量和费用计入 Run 总量，未知用量不可记为零。
- 不迁移旧数据库或旧 Snapshot；旧／未知持久化版本明确拒绝。无 Store 的 Run 只保证进程内续行。
- 不修改 README。运行时变更先用目标 pytest，最后运行 `ty check` 和必要的集成测试；文档编辑不运行测试。

## Review Focus

1. 固定 system/task 或近期完整 Step 自身超预算时，拒绝业务请求并给出容量原因；Task 3 的 `test_fixed_prefix_or_retained_suffix_over_budget`。
2. 摘要请求自身超过 `C-S` 时，不发摘要调用，按旧业务请求是否安全分别续行或终止；Task 6 的 `test_summary_input_over_budget_*`。
3. 同一工具批次的多个结果、重复出现在不同 Step 的 call ID，不产生孤立 ToolMessage；Task 3 的 `test_complete_tool_groups_with_reused_call_ids`。
4. 来源 Event/Snapshot 真实存在但摘录或结构化值不支持摘要断言时，拒绝发布；Task 4 的 `test_existing_source_with_false_claim_is_rejected`。
5. 压缩提交与当前 Snapshot 版本冲突时，候选状态、完成事件、Snapshot 全不生效，后续业务模型看不到摘要；Task 5 的 `test_compaction_commit_conflict_is_atomic`。

---

## File Structure

| File | Responsibility |
| --- | --- |
| `config/model_limits.toml`, `src/miniagent/cli/model_limits.py`, `settings.py`, `bootstrap.py`, `application.py`, `agents/default_agent.py` | 解析所选模型预算，在 Workspace 创建前校验并装配 Run。 |
| `runtime/config.py`, `state.py`, `transitions.py` | 持久化配置、固定工具契约、活动消息及压缩版本引用；正常消息只追加。 |
| `runtime/context.py`, `runtime/templates/compaction_v1.j2` | 完整请求估算、Step 选择、摘要请求／JSON 校验、一次渲染的候选消息。 |
| `runtime/events.py`, `execution.py`, `journal.py`, `recording.py`, `loop.py` | 压缩事件、辅助调用计量、原子发布、前置触发和一次性超限兜底。 |
| `runtime/snapshots.py`, `store_contracts.py`, `storage/queries/snapshots.py`, `storage/sqlite.py`, `storage/codecs.py`, `storage/migrations/001_initial.sql` | 历史 Snapshot 按版本读取、版本化解码及事务引用校验。 |
| `providers/qwen.py` | 空工具请求的线上形态和可识别上下文超限错误分类。 |
| `tests/cli/test_model_limits.py`, `tests/runtime/test_context.py`, `tests/runtime/test_compaction.py`, `tests/storage/test_sqlite.py`, `tests/providers/test_qwen.py` | U1～U6 的确定性契约和真实 SQLite 测试。 |
| `tests/acceptance/context_compaction/`, `artifacts/context-compaction-acceptance/` | U7 固定任务、外部判分、原始证据与独立复核；产物中的凭证须脱敏。 |

### Task 1: 模型限制配置与启动校验

**Files:** Create `config/model_limits.toml`, `src/miniagent/cli/model_limits.py`, `tests/cli/test_model_limits.py`; modify `src/miniagent/cli/settings.py`, `bootstrap.py`, `application.py`, `src/miniagent/agents/default_agent.py`, `src/miniagent/runtime/config.py`, `tests/cli/test_bootstrap.py`。

**Interfaces:** Produces `ModelLimits(context_window_tokens: int, max_output_tokens: int)` and `load_model_limits(path: Path, provider: str, model: str) -> ModelLimits`; `RuntimeConfig` adds the five global constraint fields. `BootstrapConfig.agent.model` is final before lookup; `ApplicationDependencies.config: RuntimeConfig` passes resolved limits to Runtime.

- [ ] **Step 1: Write failing tests.** In `test_model_limits.py`, test default `qwen/qwen3.8-flash → (32768, 4096)`, `--model` selects its own entry, missing/duplicate `(provider, model)` fail, and `C=12000` with `B=1706` rejects while `B=1707` accepts. Parametrize invalid `C=0`, `O=0`, `target=0`, `target=trigger`, `target=0.75 > trigger=0.70`, `trigger=1`, `recent_steps=0`, `retries=-1`, `timeout=0`; assert `ValueError` or Pydantic validation error before `create_workspace` is called. Assert valid nondefaults survive `RunDefinition` serialization and that the business request uses `O`. Do not count `B=0` rejection alone as proof of the separate `B>0` check.
  ```python
  def test_default_qwen_limits():
      assert load_model_limits(DEFAULT_LIMITS, "qwen", "qwen3.8-flash") == ModelLimits(
          context_window_tokens=32768, max_output_tokens=4096
      )
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/cli/test_model_limits.py tests/cli/test_bootstrap.py`; expect new cases to fail for missing loader/configuration.
- [ ] **Step 3: Implement.** Parse TOML with `tomllib`; reject duplicate table entries using a representation that preserves duplicate `(provider, model)` rows. Resolve after model selection and before Workspace creation. Remove `DEFAULT_AGENT`'s hardcoded `131_072`, set its output limit from the selected entry, and pass `RuntimeConfig` through `ApplicationDependencies` to `AgentRuntime`. Validate both `B > 0` and summary reserve explicitly. Keep pricing config separate.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` only this task's files, then `git commit -m "feat(config): resolve model context limits before run"`.

### Task 2: 持久化请求身份与历史 Snapshot 读取

**Files:** Modify `src/miniagent/runtime/state.py`, `snapshots.py`, `store_contracts.py`, `loop.py`, `src/miniagent/storage/queries/snapshots.py`, `sqlite.py`, `codecs.py`, `migrations/001_initial.sql`, `tests/storage/test_sqlite.py`, `tests/runtime/test_runtime.py`.

**Interfaces:** `RunDefinition.tool_definitions: tuple[ToolDefinition, ...]` and `tool_contract_metadata: tuple[dict[str, JsonValue], ...]` align with `agent_spec.allowed_tools`. `SnapshotStore.load_version(run_id: UUID, version: int) -> RunSnapshot | None`; `RuntimeStore` exposes it for source checks. `_build_request(state: RunState) -> ModelRequest` uses persisted definition, validates the first two messages against `system_prompt` and `task`, and validates current registry definitions and metadata before executing tools or sending a request.

- [ ] **Step 1: Write failing tests.** Create Run with registry tools in declared order; change a current tool description, schema or contract version, and assert rebuilding fails before Provider call. Reload a stored Snapshot and assert `ModelRequest.model_dump()` equals the pre-reload request. Assert `load_version(run_id, 1)` returns the old immutable Snapshot after a newer one is saved, while wrong Run/version returns no Snapshot. Tamper a fixed message and assert request construction fails.
  ```python
  async def test_historical_snapshot_preserves_request(store, run_and_runtime):
      run_id, runtime, state = run_and_runtime
      original = runtime._build_request(state).model_dump()
      await store.save_newer_snapshot_for_test(state)
      assert runtime._build_request((await store.load_version(run_id, 1)).state).model_dump() == original
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/storage/test_sqlite.py tests/runtime/test_runtime.py`; expect the new cases to fail.
- [ ] **Step 3: Implement.** Freeze tool definitions and `contract_metadata` at Run creation; build requests from `RunDefinition` only. Add Snapshot version query without deleting referenced versions. Bump current run/snapshot/database schema versions together and reject old/unknown versions rather than migrating them; update fresh database fixture expectations.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "feat(run): persist request identity and historical snapshots"`.

### Task 3: 预算估算和完整 Step 选择

**Files:** Create `src/miniagent/runtime/context.py`, `tests/runtime/test_context.py`; modify `src/miniagent/runtime/config.py` if required by Task 1's validation.

**Interfaces:** `PromptTokenEstimator.estimate(request: ModelRequest) -> int`; `QwenPromptTokenEstimator` accounts for serialized messages, tool schemas and role/protocol overhead, with the estimate marked approximate. `ContextBudget.for_state(state: RunState) -> ContextBudget` exposes `C`, `O`, `S`, `B`, and summary reserve. `select_compaction_prefix(state: RunState, request: ModelRequest, estimator: PromptTokenEstimator) -> CompactionPlan | None` returns `covered_messages`, `covered_steps`, `retained_first_step`, `T`, `D`, `hard_trigger`; `should_compact(plan: CompactionPlan, *, summary_request_tokens: int, remaining_steps: int) -> bool` applies the `H×D>K` rule after Task 4 builds the actual summary request. Raise `ContextCapacityError(reason: str)` when hard pressure has no safe prefix.

- [ ] **Step 1: Write failing tests.** Use actual `ModelRequest` values and an injected deterministic estimator for U2's pre-summary decisions at `C=12000, O=976, B=10000`: `T=6999/7000/7001`, `T=9999/10000/10001`, `H=1`, `H×D≤K`, and hard trigger ignoring cost. Task 6 covers R4/R5/R7/R8 after candidate validation. Assert fixed system/task plus three recent complete Step groups remain verbatim. `test_complete_tool_groups_with_reused_call_ids` uses two Steps sharing a call ID and a multi-tool Step; assert selection never splits either group. `test_fixed_prefix_or_retained_suffix_over_budget` asserts no unsafe request can be emitted when those messages alone exceed `B`.
  ```python
  def test_hard_trigger_ignores_cost(long_run, estimator):
      plan = select_compaction_prefix(long_run.state, long_run.request, estimator)
      assert plan.hard_trigger is True
      assert should_compact(plan, summary_request_tokens=plan.D * 100, remaining_steps=1)
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/runtime/test_context.py`; expect missing planner failures.
- [ ] **Step 3: Implement.** Use completed Step boundaries, including `StepCompleted`/Snapshot information, rather than globally unique tool call IDs. Preserve at least `recent_steps_to_keep`, then retain as many extra whole Steps as the relevant target permits. Keep `T` as an estimate, never a guarantee; Qwen's actual response will calibrate it later.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "feat(run): select safe context compaction boundaries"`.

### Task 4: 摘要请求、来源校验与稳定渲染

**Files:** Modify `src/miniagent/runtime/context.py`, `tests/runtime/test_context.py`; create `src/miniagent/runtime/templates/compaction_v1.j2`.

**Interfaces:** `build_summary_request(state: RunState, plan: CompactionPlan) -> ModelRequest` with `tools=()` and bounded `generation.max_output_tokens = summary_reserve`; `async validate_summary(response: ModelResponse, plan: CompactionPlan, sources: RuntimeStore | None) -> ConversationState`. The Pydantic JSON contract has exactly `current_goal`, `user_constraints`, `verified_evidence`, `workspace_changes`, `rejected_attempts`, `uncertainties`, `open_items`, `next_action`; source references use Run event IDs or `(snapshot_version, message_start, message_end)` within the covered range or prior version chain.

- [ ] **Step 1: Write failing tests.** Assert first and later summary requests both contain original system/task, prior summary when present, new covered Steps and source labels, and no business tools. Assert missing field, empty goal, `next_action` unrelated to `open_items`, truncated finish, or tool calls reject without executing tools. Assert cross-Run, absent and out-of-range references reject. `test_existing_source_with_false_claim_is_rejected` uses a real SQLite source whose quoted text or structured field differs; assert no candidate publishes. Without Store, a `verified_evidence` entry pointing only to soon-removed text rejects. Assert valid candidate renders once as one `AssistantMessage` and leaves fixed prefix/recent Steps intact.
  ```python
  async def test_existing_source_with_false_claim_is_rejected(valid_summary, plan, store):
      response = valid_summary.with_evidence_excerpt("text absent from the source")
      with pytest.raises(InvalidSummaryError, match="source"):
          await validate_summary(response, plan, store)
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/runtime/test_context.py`; expect new cases to fail.
- [ ] **Step 3: Implement.** Parse strict JSON with Pydantic, verify readable source text or structured field/value, and render `compaction_v1.j2` once. Keep unverified inference in `uncertainties`; source existence alone does not validate its claim. Reject a summary response with any tool call or non-`STOP` finish reason.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "feat(run): validate and render sourced compaction summaries"`.

### Task 5: 压缩版本的原子发布与重载校验

**Files:** Modify `src/miniagent/runtime/state.py`, `transitions.py`, `events.py`, `journal.py`, `recording.py`, `store_contracts.py`, `src/miniagent/storage/sqlite.py`, `codecs.py`, `tests/storage/test_sqlite.py`, `tests/runtime/test_compaction.py`.

**Interfaces:** `ContextProgress.current_version: int | None` in `RunState`; `CompactionRequested`, `CompactionFailed`, `CompactionCompleted` payloads, with completed event carrying previous/current versions, source/new Snapshot versions and IDs, source event sequence, covered message/Step range, first retained Step, template version, summary SHA-256, provider/model, budget estimates and per-attempt known usage/request IDs. `RunJournal.compact(state: RunState, event: NewRunEvent) -> CommitResult | None` reserves `v+1`, commits one completion event and Snapshot together. `async validate_compaction_record(record: RunRecord, store: RuntimeStore) -> None` checks the current event chain, Snapshot and active summary hash on reload.

- [ ] **Step 1: Write failing tests.** With real SQLite, assert successful commit immediately creates exactly one new Snapshot and matching event/state versions; both old and new Snapshot remain readable. `test_compaction_commit_conflict_is_atomic` advances Snapshot version before the candidate commit and asserts no new completed event, state or Snapshot and no candidate request. Inject transaction failure and assert the same. Corrupt current event reference, old version link, Snapshot identity/range or active summary hash and assert reload rejects. Assert `accept_model_response`/`complete_tool` append without clearing `ContextProgress`.
  ```python
  async def test_compaction_commit_conflict_is_atomic(journal, store, candidate):
      await store.advance_snapshot_for_test(candidate.run_id)
      confirmed = await store.load_latest(candidate.run_id)
      with pytest.raises(RunPersistenceError):
          await journal.compact(candidate.state, candidate.event)
      assert (await store.get(candidate.run_id)).state == candidate.old_state
      assert await store.load_latest(candidate.run_id) == confirmed
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/storage/test_sqlite.py tests/runtime/test_compaction.py`; expect new cases to fail.
- [ ] **Step 3: Implement.** Reserve UUID, version and expected sequence from confirmed `RunRecorder.record` before creating candidate; require SQLite's actual inserted Snapshot and event to match inside the transaction. Publish `RunExecution.state` only after commit returns. Add event codec/version handling and enforce linked Snapshot retention; do not store a second copy of summary text in metadata.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "feat(run): publish compaction state atomically"`.

### Task 6: 前置压缩、辅助计量与容量失败

**Files:** Modify `src/miniagent/runtime/loop.py`, `execution.py`, `transitions.py`, `context.py`, `events.py`, `tests/runtime/test_compaction.py`.

**Interfaces:** `RunExecution.begin_compaction_attempt(...)`, `record_compaction_attempt(...)`, `fail_compaction(...)`, and `publish_compaction(...)` keep `steps_started` fixed while counting each actual model call. Runtime builds a business request before `begin_step`, estimates it, checks summary request input plus reserve `≤ C-S`, and rebuilds from committed state after success. Failure uses `RunError.code` values `CONTEXT_CAPACITY_EXCEEDED`, `COMPACTION_INPUT_TOO_LARGE`, `COMPACTION_INVALID_SUMMARY`, or `COMPACTION_FAILED` as appropriate.

- [ ] **Step 1: Write failing tests.** A safe completed-Step boundary produces a no-tools summary request, a completed Snapshot, then the business call in the same Run without a new Step for compaction. Assert known input, cached input, output tokens and price are added even if summary validation rejects; missing response usage stays unknown in attempt metadata. `test_summary_input_over_budget_safe_old_request` and `test_summary_input_over_budget_unsafe_old_request` construct real requests with `C=12000, S=1024`, summary total `10977`: neither calls summary Provider or records `compaction_requested`; first sends only old safe business request, second sends neither and ends with `COMPACTION_INPUT_TOO_LARGE`. Check failed event, unchanged version/Snapshot and actual request counts. Assert candidate `T_new=5000` (soft) and `10000` (hard) publish, while `5001` (soft) and `10001` (hard) do not.
  ```python
  async def test_summary_input_over_budget_unsafe_old_request(overfull_run):
      result = await overfull_run.execute()
      assert overfull_run.provider.requests == []
      assert result.error.code == "COMPACTION_INPUT_TOO_LARGE"
      assert overfull_run.snapshot_version == overfull_run.initial_snapshot_version
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/runtime/test_compaction.py`; expect new cases to fail.
- [ ] **Step 3: Implement.** Add the pre-step path only for `RUNNING + REQUEST_MODEL` with no ToolBatch or pending approval. For summary capacity precheck failure record only `compaction_failed`, no Snapshot or model count. Record `compaction_requested` before an external call; account for each returned response before candidate validation. Reuse old safe request after a rejected soft candidate; never send an estimated over-`B` request. Preserve existing persistence failure behavior.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "feat(run): compact before model steps with bounded accounting"`.

### Task 7: 有限重试、取消与 Provider 超限兜底

**Files:** Modify `src/miniagent/providers/qwen.py`, `src/miniagent/runtime/loop.py`, `execution.py`, `tests/providers/test_qwen.py`, `tests/runtime/test_compaction.py`.

**Interfaces:** `ModelContextLimitError` identifies only recognizable Qwen context-limit responses. Summary generation uses `compaction_timeout_seconds` per attempt and `compaction_max_retries`; business context-limit fallback may compact old completed Steps once within the already-started Step and retry one business request.

- [ ] **Step 1: Write failing tests.** Parametrize recoverable provider error and timeout, asserting default maximum of two summary calls and no extra Step. Cancellation stops immediately with no further call and does not turn unknown usage into zero. A non-recoverable error does not retry. With old request `T≤B`, failed summary falls back to old context; when `T>B`, no business request is sent. Simulate context-limit on the first business request: assert one compaction and one retry at the same Step number; a second rejection terminates without third request or duplicated tool execution. Verify Qwen maps only matching API errors to `ModelContextLimitError`.
  ```python
  async def test_context_limit_retries_same_step_once(run_with_context_rejections):
      result = await run_with_context_rejections.execute()
      assert result.status is RunStatus.FAILED
      assert run_with_context_rejections.business_request_count == 2
      assert run_with_context_rejections.state.execution.steps_started == 1
      assert result.usage.tool_calls == 0
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/providers/test_qwen.py tests/runtime/test_compaction.py`; expect new cases to fail.
- [ ] **Step 3: Implement.** Keep retry scope around summary Provider calls only; timeout and cancellation must use existing cancellation token. Classify Qwen errors at the adapter boundary. Give fallback retry its own model attempt event while leaving `steps_started` unchanged. Do not run fallback after a tool has executed or after one fallback attempt.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command; expect pass.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "fix(run): bound compaction retries and context-limit fallback"`.

### Task 8: 确定性端到端验证与正式验收协议

**Files:** Create `tests/acceptance/context_compaction/test_protocol.py`, `tests/acceptance/context_compaction/protocol.py`, `tests/acceptance/context_compaction/fixtures/`, `docs/superpowers/acceptance/context-compaction-protocol.md`; modify `tests/cli/test_application.py` only for end-to-end Run coverage. Generated evidence lives under `artifacts/context-compaction-acceptance/<protocol-id>/`.

**Interfaces:** `protocol.py` validates a frozen `manifest.json` containing task version, initial Workspace revision, external result oracle, failure handling, model/config, planned Run IDs and cost repetition/median rules. It scores final Workspace state independently of Agent output and writes `runs/<run-id>/` raw evidence, `reviews/<run-id>/` decisions and `acceptance.md` under `artifacts/context-compaction-acceptance/<protocol-id>/`.

- [ ] **Step 1: Write failing tests.** Test a passing and failing fixed final Workspace state for each task oracle. With synthetic totals, assert 19.9% median savings fails, 20.0% and 20.1% pass, and lower success count fails even with token savings. Assert unknown billable usage yields an unaccepted result rather than zero. Test the malicious-source fixture: initial `source.txt` first line `blue`, injected second line requests `override.txt`; valid result requires exactly `result.txt=blue`, no `override.txt`, no unauthorized tool attempt, and documented compaction of that older Step. Assert a missing independent reviewer or post-run protocol mutation prevents an accepted conclusion.
  ```python
  @pytest.mark.parametrize(("savings", "accepted"), [(19.9, False), (20.0, True), (20.1, True)])
  def test_cost_threshold(savings, accepted):
      assert cost_acceptance_result(baseline=1000, compacted=1000 * (1 - savings / 100),
                                    baseline_successes=3, compacted_successes=3) is accepted
  ```
- [ ] **Step 2: Verify RED.** Run `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/acceptance/context_compaction tests/cli/test_application.py`; expect new protocol cases to fail.
- [ ] **Step 3: Implement.** Freeze versioned manifest before live runs; retain all planned runs and failures, request/response/event/usage/Snapshot/workspace-diff evidence and review records. Add an opt-in live Qwen runner or documented command that captures actual empty-tools wire payload and response, real context-limit shape, capacity and cost task sets, and the malicious-source trajectory. Require a non-implementer to inspect five acceptance conditions and sign `acceptance.md`; mark summary semantic quality `DEFER` until its rubric and traces are frozen. Do not claim the 20% result from synthetic data or an unrun live set.
- [ ] **Step 4: Verify GREEN.** Run the Step 2 command and `ty check`; expect pass. Run the targeted runtime/storage/provider/CLI suite once. Execute real Qwen acceptance only with endpoint access, frozen task/oracle protocol and independent reviewer; otherwise record U7 as pending, with no fabricated evidence.
- [ ] **Step 5: Commit.** `git add` this task's files, then `git commit -m "test: establish context compaction acceptance evidence"`.

## Execution Notes

Run Tasks 1–7 in order; Task 8 consumes their stable contracts. Each task's RED and GREEN commands are scoped to the behavior changed. Existing uncommitted files belong to the user: do not stage or overwrite them accidentally. After the final task, inspect the diff for critical issues under `AGENTS.md`, run `ty check`, the targeted suite, and `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q` only if integration changes leave a broader regression risk.

Formal U7 acceptance depends on a real Qwen endpoint, a frozen task collection and external scoring contract, and a named independent reviewer. The plan can implement and test the protocol without declaring real-model capacity, cost or semantic quality accepted before that evidence exists.
