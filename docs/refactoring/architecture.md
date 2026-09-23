# 目标文件结构与数据契约

本文件是目标，不代表当前实现。执行顺序见 [tasks.md](tasks.md)，范围与不变量见 [plan.md](plan.md)。不维护旧数据或旧 Python 接口兼容。

## A. 依赖方向

```text
cli → runtime + 具体 Provider/Workspace/SQLite/Artifact 实现
runtime → agents/models/tools/policy/metering/evaluation/environments 的契约
storage → runtime.store_contracts + 功能数据契约
providers → models.protocol
tools 实现 → environments.contracts
models 消息 → tools.contracts
environments → artifacts.contracts
```

`tools.contracts` 不反向导入模型协议；`policy.contracts/approvals` 不导入 Runtime；`runtime.store_contracts` 不导入 Storage。内部使用明确的定义文件导入，包 `__init__.py` 只提供少量稳定导出，不能靠扩大重导出解决循环依赖。

## B. 逐模块文件清单

下列各目录均包含 `__init__.py`；未单列说明的 `__init__.py` 不承载实现或资源初始化。

### agents

| 文件 | 内容及约束 |
| --- | --- |
| `spec.py` | `AgentSpec(name, system_prompt, model, generation, allowed_tools)`；嵌套配置冻结，工具许可用 tuple；不拥有模型参数定义 |
| `default_agent.py` | 默认配置构造及现有简短提示词；调用者不会共享可修改的配置 |
| `prompts/default.jinja` | 后续提示词变复杂时才建立，本次不创建 |

### models

| 文件 | 内容及约束 |
| --- | --- |
| `config.py` | `ModelTarget(provider, model)`、`GenerationOptions(temperature, max_output_tokens)` |
| `messages.py` | System/User/Assistant/Tool 消息及按 role 判别的 `Message` 联合；Tool 消息必须有关联 ID，其他角色不接受无关字段 |
| `protocol.py` | `ModelRequest`、`ModelResponse`、`ModelUsage`、`ModelError`、`FinishReason`、`ModelProvider` |

模型 usage 保留缓存 Token、推理 Token 与总数一致性。Provider 特定字段只在 adapter 内出现。无工具调用的 assistant 可以有文本；工具调用 assistant 可以无文本，不能错误拒绝合法响应。

### providers

| 文件 | 内容及约束 |
| --- | --- |
| `qwen.py` | SDK 调用、请求/消息/工具声明转换、JSON 参数解析、响应 usage/结束原因归一化 |

不访问 RunState、审批记录或存储。将现有 Provider 协议移到 models 后，不在 providers 保留旧定义副本。

### tools

| 文件 | 内容及约束 |
| --- | --- |
| `contracts.py` | ToolDefinition/Call/Result/Status/Error、参数错误、输出完整性及限制；引用 WorkspaceDelta 和 ArtifactRef |
| `base.py` | 严格 `ToolArguments`、泛型工具接口；工具输入模型和执行方法；稳定工具契约版本 |
| `registry.py` | 注册条目、PreparedToolCall、严格校验、许可列表检查、definition 快照；收拢异构工具的类型适配 |
| `execution_result.py` | 小型公共转换函数，复制 duration/artifacts/workspace_delta/截断标记，不决定每个工具的业务错误 |
| `rendering.py` | 完整 ToolResult → 有界模型文本；不原地修改结果 |
| `read_file.py` | ReadFileArguments/Tool、路径和解码处理、文件观察结果 |
| `shell.py` | ShellArguments/Tool、命令构造、退出状态与工具语义映射 |

参数只有模型输入边界是原始 JSON；prepare 后保留参数对象。异构 registry 可用私有绑定对象把具体工具与具体参数一起封装，对 Runtime 仅暴露 `invoke(tool_call_id)` 和生成策略输入所需的规范 JSON，不把 `Any` 传播到工具实现。不要让用户提供的工具名执行任意动态导入。

稳定工具契约摘要使用逻辑名称、显式版本、参数 schema 和影响执行的选项，不使用 Python 模块路径；否则本次搬文件本身就会改变授权条件。摘要格式变化需同步 policy revision。

### environments

| 文件 | 内容及约束 |
| --- | --- |
| `contracts.py` | Workspace/Backend、Command、ExecutionResult/Status、WorkspaceDelta、Patch、检查点 |
| `config.py` | WorkspaceKind、Local/DockerOptions、运行时 Spec、ResourceLimits、ContainerUserPolicy；公共路径校验 |
| `persistence.py` | EnvironmentReference、PersistedLocal/DockerSpec 联合、Workspace 恢复描述；规范化与无秘密序列化 |
| `process.py` | 仅 Local/Docker 已有且实际相同的流读取/stdin 辅助；不统一进程组终止与容器终止 |
| `local.py` | Local 创建/执行/checkpoint/restore/patch/destroy、主机路径边界与 BROKEN 状态 |
| `docker.py` | Docker 创建/资源隔离/执行/checkpoint/restore/patch/destroy、容器路径边界 |

运行 Spec = run_id + options + environment_values；持久化 Spec = run_id + 同一个 options + environment_refs。不得通过先 dump 运行配置再删除秘密字段构造持久化数据，使用显式白名单映射。

检查点若拆联合，Local/Docker 分别声明真正必需的字段；禁止为迁移省事给必需字段全部设 None。该变更只整理当前 checkpoint 功能，不接入自动恢复。

### policy

| 文件 | 内容及约束 |
| --- | --- |
| `contracts.py` | PolicyContext/Request/Evaluation/Decision、效果与风险、Operation 描述、PolicyEngine |
| `requests.py` | 已验证工具参数 → Operation；Agent/工具/执行条件摘要；唯一可信适配入口 |
| `rules.py` | 当前 read_file/shell/未知工具规则、规则优先级 |
| `engine.py` | 执行规则并合并决定；不读取 `arguments['path']` 等工具字段 |
| `approvals.py` | ApprovalRequest/Resolution/Outcome/Record/Handler；作用域、有效期、处置与关联校验 |

Operation 至少区分 WorkspaceRead、ShellExecution、UnknownOperation。新工具无适配时进入 Unknown，不能通过自报低风险获得权限。具体工具/参数到 Operation 的变化只落在 requests 和相关规则中；本次保留现有许可结果，不顺带设计新 Docker 信任策略。

ApprovalRecord 的业务部分属于 policy；其数据库行版本、request/resolution/invalidation 对应事件位置由 store_contracts 中的 `StoredApproval` 包装，不把 SQL/行版本塞回业务请求。

### metering

| 文件 | 内容及约束 |
| --- | --- |
| `usage.py` | RunUsage、Token/费用累计、不丢失无 pricing 时的模型 usage；不依赖 RunState |
| `pricing.py` | 价格输入、归一化价格、文件加载、RequestCost 和计算函数 |
| `budget.py` | 后续新增预算功能时建立；本次不创建、不暗中激活现有枚举对应功能 |

保留 Decimal、USD 和现有价格文件格式，避免结构重构同时改计价。是否嵌套费用分项以任务卡为准，默认保留已有公开计量字段。

### evaluation

| 文件 | 内容及约束 |
| --- | --- |
| `contracts.py` | CandidateOutput、EvaluationRequest/Plan/Check/Result/Error、Evaluator；计划表达命令与验证要求，不绑定 DockerSpec |
| `passthrough.py` | 现有直接通过实现 |

Runtime 在边界把 EvaluationError 映射为 RunError；Evaluation 不反向依赖 Runtime。执行环境通过评估器依赖提供。本次不实现命令评估器，不能把未运行的检查标为 passed。

### artifacts

| 文件 | 内容及约束 |
| --- | --- |
| `contracts.py` | ArtifactRef/Write/Record/Store、错误；完整记录包含 run_id、名称、类型、媒体类型、摘要、大小、metadata |
| `file.py` | 内容与 manifest 写入/读取、摘要校验、Run 范围查询 |

为保持现有调用形态，`put()` 仍返回 ArtifactRef；新增 `get_record(ref)` 和 `list_for_run(run_id)`。采用“先写内容，再原子发布 manifest”的顺序；manifest 是可查询记录的发布点。失败不可返回成功引用，孤立内容不视为已登记制品；同内容不同记录保留各自元数据。类型与名称不得造成根目录外写入。

T14 为 ArtifactRef 增加必需的 `artifact_id: UUID` 标识发布记录；`uri` 仍指向可读取的内容，`get_record` 按 artifact_id 定位并核对内容引用。不能只用内容摘要定位 metadata，否则同内容的不同记录无法区分。同步修改所有构造者及持久化格式，不提供旧引用兼容。

### runtime

| 文件 | 内容及约束 |
| --- | --- |
| `config.py` | 现有 RuntimeConfig；不增加尚未实现预算配置 |
| `state.py` | RunStatus、RunCursor、RunState 与状态子对象、不变量；不含完整历史 |
| `transitions.py` | 状态迁移表、纯函数具名更新；不执行 SQL，不直接写 Trace |
| `result.py` | RunError/TerminationReason/RunResult、从终态构造结果；避免与 state 的循环导入 |
| `loop.py` | AgentRuntime、模型/工具/评估主循环、唯一结束入口 |
| `execution.py` | 当前 Run 写入边界；候选 State/业务事件/Trace 的协调与提交后发布 |
| `tool_runner.py` | 串行 prepare → policy → approval → execute → complete |
| `approval.py` | 同进程等待、超时、取消、重新授权与 Run 终止的协调 |
| `termination.py` | 模型结束/工具失败/现有 Step 限制的判定 |
| `cancellation.py` | 取消令牌和可取消等待 |
| `recovery.py` | 已有恢复协议与 RunRecoveryContext；引用 environments 的恢复描述 |
| `events.py` | 类型化业务事实、事件信封、EventStore；构造和解码校验 |
| `tracing.py` | 已有 Trace/Span 模型与层级校验；无完整导出实现 |
| `trace_context.py` | Scope 生命周期、事件 scope 装饰；不定义审批/工具业务载荷 |
| `snapshots.py` | RunSnapshot/NewRunSnapshot、SnapshotStore；状态和事件位置的快照 |
| `store_contracts.py` | RuntimeStore、RunCommit/Record/CommitResult、StoredApproval、审批/快照持久化变更、边界错误 |
| `journal.py` | 生命周期原子提交、审批版本和事件引用管理、快照时机 |
| `recording.py` | 存储调用、已确认位置、输入冻结边界和持久化错误上下文 |

state/result 的互相引用必须用纯类型引用或单向工厂函数解决；不得借助包级重导出绕过。模型校验必须能在新进程独立导入后工作。

### storage

| 文件 | 内容及约束 |
| --- | --- |
| `sqlite.py` | SQLiteRunStore、连接/初始化/事务/线程等待、原子提交和幂等 |
| `codecs.py` | 仅当前版本对象与 JSON/行转换、格式版本拒绝、损坏数据翻译；无旧版本升级 |
| `migrations/001_initial.sql` | 新结构全量建表；本次可直接重写 |
| `migrations/__init__.py` | 当前 schema 资源加载，不新增历史迁移框架 |
| `queries/runs.py` | 当前 Run 记录读写 |
| `queries/events.py` | 事件读写、连续序列 |
| `queries/approvals.py` | 审批记录读写、作用域/CAS/到期条件 |
| `queries/snapshots.py` | 快照读写 |

所有 query 接收同一事务连接，不自行 commit、开启线程或创建新连接。保留提交前冻结以及取消后等待线程达到确定结果的约束。

### cli

| 文件 | 内容及约束 |
| --- | --- |
| `main.py` | Typer、参数、信号、退出码 |
| `settings.py` | 外部 Settings、原有环境变量名和默认配置位置 |
| `bootstrap.py` | 实现选择与对象装配、资源获取；不承载结果展示 |
| `application.py` | 运行、Patch 导出/登记、Workspace 收尾、ApplicationResult/FinalizationError |
| `approvals.py` | 终端 ApprovalHandler |
| `rendering.py` | 业务结果、费用、制品和收尾错误输出 |

ApplicationResult = RunResult + ArtifactRef 集合 + FinalizationError 集合。Runtime 终态不被收尾错误重写。成功 Run 但 Patch/登记/清理失败时，CLI 展示已有结果与错误并非零退出；已有信号退出码优先。证据未成功登记时保留 Workspace。初始化后装配失败也必须进入明确清理路径，不能因 try/finally 建得太晚泄漏资源。

## C. RunState 和历史的分工

| 子对象 | 数据 |
| --- | --- |
| RunIdentity | run_id、task、created_at |
| RunDefinition | 独立且不可变的 AgentSpec/RuntimeConfig |
| RunLifecycle | status、started_at、updated_at、finished_at、终止信息 |
| ConversationState | 当前模型上下文消息；本次不实现压缩 |
| ExecutionProgress | steps_started、连续失败数、cursor、当前 ToolBatch |
| ToolBatch | 当前响应 ToolCall tuple、next_index；按已完成前缀推进 |
| RunUsage | 计量模块定义的累计值 |
| PendingApprovalRef | approval_id、step_number、call_index、tool_call_id；无需复制完整审批历史 |
| RecoveryProgress | 当前检查点引用、attempts；不接入自动恢复 |

删除全 Run 的 completed_tool_call_ids 集合；调用关联用 `(run_id, step_number, call_index)`，provider call ID 保留为模型相关 ID。同一 Step 重复 call ID 继续拒绝，不擅自假设不同 Step 的 provider ID 全局唯一。

消息和配置采用明确的持有权：外部传入对象不得直接成为可变运行状态；给 Provider 的请求也不能让其修改已确认 State。Pydantic frozen 是浅冻结，嵌套 list/dict 仍需 tuple、拷贝或边界快照；不要自行引入不兼容 JSON 的“不可变 dict 框架”。

事件是审计历史，RunState/Snapshot 是当前恢复事实。本次不承诺单靠事件完整重放，也不引入 event sourcing 框架。

## D. 事件与提交契约

1. 使用 `Annotated[... , Field(discriminator='type')]` 的类型化载荷联合，每种事实规定字段和校验。
2. 信封保存 run_id、event_id、sequence、occurred_at、schema_version、scope；载荷的 type 是唯一事件类型来源，不再维护可不一致的两份 type。
3. 业务事件在 execution/相关功能边界构造，TraceContext 只生成/添加 scope。
4. NewRunEvent 在入库前分配 event_id；审批变更直接引用该 ID。Journal 不再按 payload 字符串寻找数组位置。
5. Storage 在同一提交中验证被引用事件存在、类型/approval_id/run_id 匹配，之后分配连续 sequence。
6. ToolEnd 收尾 ToolStart，正常完成时保存完整结构化 ToolResult；中途终止时保存终止状态和原因，不生成重复的 ToolCompleted。模型消息保存有界渲染。PolicyDecided 保存决定；Approval 表保存可查询审批记录。审批表与事件属于同一原子提交，不宣称只有一份物理数据。
7. 新事件必须覆盖当前所有实际生产者。只有旧日志兼容用途的枚举可删除；尚未接入的事件不需要空 payload 占位类型。

数据有意存在多份时：事件为审计事实，审批记录为授权查询事实，RunState 为执行位置；一次事务生成这些表示。修改任一表示不应要求另一个模块自行猜测字段关系。

## E. 旧文件去向

| 旧位置 | 新位置 |
| --- | --- |
| models/artifact.py、artifacts/store.py | artifacts/contracts.py |
| models/tool.py | tools/contracts.py；WorkspaceDelta → environments/contracts.py |
| models/model.py | models/messages.py、protocol.py |
| models/settings.py | cli/settings.py |
| models/pricing.py、runtime/usage.py | metering/pricing.py、usage.py |
| providers/__init__.py 中的协议 | models/protocol.py |
| tools/__init__.py 中的类 | tools/base.py |
| environments/workspace.py | environments/contracts.py、config.py |
| runtime/persistence.py | environments/persistence.py；RunRecoveryContext → runtime/recovery.py |
| runtime/evaluator.py | evaluation/contracts.py、passthrough.py |
| runtime/tool_output.py | tools/rendering.py |
| policy/models.py | policy/contracts.py、approvals.py |
| storage/models.py | runtime/store_contracts.py；审批业务记录 → policy/approvals.py |
| runtime/state.py 的配置/usage/结果 | runtime/config.py、metering/usage.py、runtime/result.py |

旧文件迁移完成就删除，不添加兼容转发文件。无需为所有类型另建一个文件；每个文件应围绕共同变化原因组织。
