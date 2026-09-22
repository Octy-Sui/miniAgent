# Runtime Loop Refactor

本文只记录本次 session 对 `src/miniagent/runtime/loop.py` 的分析、已完成重构、
目标架构和剩余执行计划。它不是 MiniAgent 的总体架构文档，也不扩展 recovery、
workspace、benchmark 等未在本次重构中处理的主题。

## 使用方式

后续继续该重构时：

1. 先阅读本文，不依赖历史对话恢复目标。
2. 对照代码确认“当前实现”仍然准确。
3. 一次只执行一个未完成批次，不把所有剩余批次合并为一次修改。
4. 每完成一个批次，立即更新本文的状态、实际文件和验证结果。
5. 代码与测试定义当前行为；本文定义本次重构的目标边界和续作顺序。

## 本次重构要解决的问题

原 `loop.py` 同时负责：

- Run 和 Step 编排；
- provider 调用、usage/cost 和模型响应分类；
- Tool Call 校验、Policy、Approval 和工具执行；
- cancellation 和 evaluation；
- Run State 修改；
- Run Event、span、snapshot 和 approval persistence change；
- terminal state、最终事件和 `RunResult` 构造。

文件曾达到 1297 行。核心问题不是 Agent Loop 本身过长，而是持久化和可观测性
横切逻辑侵入编排层，同时深层流程可以直接结束 Run，导致控制流、状态所有权和
事务边界不清晰。

本次还确认并修复了两个行为问题：

- 未配置 pricing 时，provider 返回的 token usage 没有累计。
- 未配置 Run Store 时，在等待 Approval 期间取消会因缺少 approval version 抛出
  `ValueError`，而不是返回 `CANCELLED`。

## 最终目标

### 职责结构

```text
AgentRuntime
├── run()                  只解释 RunOutcome，并在一个位置结束 Run
├── _run_step()            执行一个完整 Step，返回 RunOutcome
└── _evaluate()            执行 Evaluation，返回终止 Outcome

RunExecution               管理本次 Run 的状态和生命周期
├── RunState               durable facts
├── RunTraceContext        管理 Run/Step/Model/Tool/Approval span scope
└── RunJournal             原子提交 State + Events + Approval + Snapshot
    └── RunRecorder        底层 RuntimeStore 适配器

ToolCallRunner             prepare → policy → approval → execute
ApprovalCoordinator        审批业务，不感知 storage version 或持久化异常类型
```

“`AgentRuntime` 只负责编排”特指 `run()` 的控制流应保持精简；provider 调用、响应
分类等单步业务仍可以由 `AgentRuntime._run_step()` 协调，不要求把每个操作都创建成
独立 service。

### 目标控制流

`can_start_step()` 只有布尔结果，无法表达 max steps、token、cost 或 duration 等具体
终止原因。因此最终实现应返回明确的 `RunTermination`，而不是只使用布尔值。

目标形态如下，具体命名可以在实现时保持简洁：

```python
async def run(...) -> RunResult:
    execution = await RunExecution.start(...)

    try:
        while True:
            termination = execution.before_next_step()
            if termination is not None:
                outcome = TerminateRun(termination)
                break

            outcome = await self._run_step(execution)
            if isinstance(outcome, ContinueRun):
                continue
            if isinstance(outcome, EvaluateCandidate):
                outcome = await self._evaluate(execution, outcome)
            break
    except CancellationRequested:
        outcome = TerminateRun(cancelled_termination())

    return await execution.finish(outcome.termination)
```

正常路径和 cancellation 路径最终汇聚到同一个 `finish()` 调用点。`_run_step()`、
`_evaluate()`、`ToolCallRunner` 和 `ApprovalCoordinator` 都不能返回 `RunResult`，也
不能自行结束 Run。

### Outcome 类型

使用互斥类型表达控制流，避免多个 optional 字段组成非法状态：

```python
@dataclass(frozen=True, slots=True)
class ContinueRun:
    pass


@dataclass(frozen=True, slots=True)
class EvaluateCandidate:
    candidate_output: str


@dataclass(frozen=True, slots=True)
class TerminateRun:
    termination: RunTermination


RunOutcome = ContinueRun | EvaluateCandidate | TerminateRun
```

Evaluation 完成后只会返回 `TerminateRun`。Tool batch 可以保留自己的内部 outcome，
但必须由 `_run_step()` 翻译为统一的 `RunOutcome`。

### 状态所有权

除 `RunExecution` 外的组件不直接修改 `RunState`。需要通过语义方法完成修改，例如：

- `record_model_response(response, request_cost)`；
- `record_policy_decision(decision)`；
- `record_tool_result(result)`；
- `open_approval(request, decision)`；
- `resolve_approval(resolution)`；
- `complete_step(results)`。

这些方法负责组合状态更新、invariant 校验、事件生成和必要的 Journal 提交，避免
调用方分别修改 `messages`、`cursor`、`pending_tool_calls`、usage 或 approval lists。

### Trace 与 Journal 边界

`RunTraceContext` 只管理 span scope 和事件关联信息，不访问 Store。至少明确表达：

- Run scope；
- Step scope；
- Model scope；
- Tool scope；
- Approval scope；
- Tool execution scope。

嵌套 scope 使用对象表达，不使用 `execution_active=True` 配合 nullable span ID 的组合。

`RunJournal` 是生命周期事务门面，包装现有 `RunRecorder`。它负责：

- State、Events、Approval Changes 和 Snapshot 的原子提交；
- approval expected version 和 event index；
- storage conflict/expiry 到 runtime 语义的翻译；
- 无 Store 模式与持久化模式保持相同业务结果。

Run Event 是审计和恢复相关事实，不能改成 best-effort Event Bus。通用 AOP、装饰器或
OpenTelemetry 也不能替代 Journal 的事务职责。

## 最终实现

本次 session 规划的 6 个批次已经全部完成，没有遗留的执行批次。

### 完成状态

- [x] `run()` 只解释 `RunOutcome`，且只有一个 `execution.finish()` 调用点。
- [x] `_run_step()` 负责一个完整 Step，返回 `ContinueRun`、
  `EvaluateCandidate` 或 `TerminateRun`。
- [x] `_evaluate()` 返回 `TerminateRun`，不再生成 `RunResult`。
- [x] `RunExecution.start(...)` 是异步 factory，负责初始状态迁移和首个提交。
- [x] `RunExecution.before_next_step()` 返回具体的 step limit termination。
- [x] `loop.py`、`tool_runner.py` 和 `approval.py` 不直接修改运行中的 `RunState`。
- [x] `RunTraceContext` 管理 Run、Step、Model、Tool、Approval 和 Tool Execution
  scope，以及 Event 的 step/span/parent 关联。
- [x] `RunJournal` 包装 `RunRecorder`，管理 snapshot、approval change、event index、
  expected version 和 storage expiry 翻译。
- [x] `ApprovalCoordinator` 不再依赖 storage change、approval version 或 storage
  exception，只处理 runtime 级的 `ApprovalCommitExpired` 语义。
- [x] 无 Store 和 SQLite Store 模式保持相同业务终止结果。
- [x] 无 pricing 时仍累计 Model Usage。
- [x] 无 Store 时 Approval 等待期间取消正常返回 `CANCELLED`。
- [x] State 和 Trace 通过候选对象提交后发布；Journal 失败不会留下半完成的
  cursor、usage 或活动 scope。
- [x] `transition()` 返回完整验证的新 `RunState`，不再原地修改调用方持有的状态。

### 最终文件职责

| 文件 | 最终职责 |
|---|---|
| `runtime/loop.py` | Run 装配、Outcome 编排、单步 provider/tool 协调和 Evaluation |
| `runtime/execution.py` | Run State 唯一写入边界，协调 TraceContext、Journal 和最终结果 |
| `runtime/trace_context.py` | scope 生命周期、span ID 和 Run Event 关联信息 |
| `runtime/journal.py` | 生命周期事务、Approval 乐观锁和 storage error 翻译 |
| `runtime/recording.py` | RuntimeStore 的低层 create/commit 适配器 |
| `runtime/tool_runner.py` | Tool prepare、policy、approval、execute 和结果验证 |
| `runtime/approval.py` | Approval request、resolution、expiry 和 policy revalidation |
| `runtime/cancellation.py` | Cancellation token 和 cancellable await |
| `runtime/usage.py` | Model usage 和 cost 累计 |

### 已完成批次记录

1. 统一 `RunOutcome`，提取 `_run_step()`，收敛单一 finish 调用点。
2. 收紧 `RunState` 所有权，消除其他 runtime 组件的直接写入。
3. 提取 `RunTraceContext`；测试过程中修正了 Approval 提交失败前过早关闭 scope 的
   事务时序问题。
4. 建立 `RunJournal`，隐藏 storage change、approval version 和底层 expiry error。
5. 收敛异步启动、step limit、连续工具失败判断和第一阶段临时接口。
6. 将 State/Trace 更新改为候选对象事务：完整验证候选状态，Journal 提交成功后再
   替换执行上下文；模型响应、工具步骤、Approval 和最终收尾使用同一发布顺序。

如后续需求改变本文的职责边界，应先更新“最终目标”，再新增独立执行批次；不要把
新需求追记为本次 session 未完成项。

## 每批通用验证

影响 runtime 行为的批次至少执行：

```bash
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q \
  tests/test_runtime.py tests/test_policy.py
ty check
git diff --check
```

涉及 Journal、Approval 原子提交或 Event/Span 的批次还必须执行完整测试：

```bash
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
```

最终批次执行真实 CLI 验证：

```bash
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache \
  uv run miniagent 'model qwen的providers实现路径是？' \
  --model deepseek-v4-flash-0731
```

该命令可能触发本地 shell Approval，需要交互式终端确认。

## 本次 session 已验证的基线

第一阶段重构完成后已经验证：

- 完整测试：`103 passed, 1 skipped`；
- 类型检查：`ty check` 通过；
- `git diff --check` 通过；
- 上述真实 CLI 命令在交互批准 Tool Calls 后成功完成；
- CLI 正确返回 `src/miniagent/providers/qwen.py`，并输出 token usage 和 cost。

后续实现不能只依赖这份历史结果；每个批次仍须重新执行对应验证。
