# 重构任务卡

执行前阅读 [plan.md](plan.md)，状态只维护在 [progress.md](progress.md)。本文的“完成”是验收要求，不是已完成声明。所有任务初始为 TODO。

## 任务顺序

采用下表顺序串行执行。每卡都依赖前一卡 DONE；表中列出额外需要重新核对的关键产物。不要因某卡看起来独立而提前修改共享契约。

| ID | 交付物 | 关键前置产物 | 性质 |
| --- | --- | --- | --- |
| T00 | 当前基线与调用/行为清单 | 当前代码 | 核对 |
| T01 | 底层 Artifact/Workspace/Tool 类型归位 | T00 基线 | 结构迁移 |
| T02 | 模型消息、生成配置、Provider 协议 | T01 工具声明 | 契约重构 |
| T03 | Metering/Evaluation/Runtime 基础类型 | T02 模型协议 | 结构迁移 |
| T04 | Workspace 配置与持久化统一 | T01 环境契约 | 重构及校验修复 |
| T05 | Runtime 自有存储契约 | T03、T04 | 依赖反转 |
| T06 | Policy 规则与审批契约分离 | T05 审批存储包装 | 结构迁移 |
| T07 | 类型化工具执行边界 | T01、T06 | 契约重构 |
| T08 | 完整工具执行证据 | T07 | 信息丢失修复 |
| T09 | 类型化事件及事件 ID 关联 | T05、T06、T08 | 契约重构 |
| T10 | Trace scope 与业务事件分离 | T09 | 职责拆分 |
| T11 | 组合式 RunState 与具名迁移 | T03、T09、T10 | 状态重构 |
| T12 | 历史移出状态、当前调用进度 | T11、T09 | 数据归属重构 |
| T13 | SQLite 查询拆分与当前 schema | T05、T12 | 存储整理 |
| T14 | Artifact 元数据及 Run 查询 | T01 | 信息丢失修复 |
| T15 | CLI 配置、审批交互和展示拆分 | T03、T06 | 结构迁移 |
| T16 | 应用收尾与结构化最终产物 | T14、T15 | 收尾行为完善 |
| T17 | 共享进程辅助函数 | T04 | 有条件去重 |
| T18 | 测试归位与边界验证 | T00—T17 | 验证整理 |
| T19 | 最终门禁、导航与交付 | T18 | 验收 |

每卡默认可以修改“范围”中定义及其所有直接调用方的 import/构造；不得顺带重写未列功能。旧文件在该卡所有消费者迁移后删除，不保留 alias/shim。

## T00 — 记录可复核基线

**必读**：AGENTS.md、plan、architecture、当前 progress；runtime/loop、execution、journal、recording；cli/bootstrap；tests/test_runtime.py；docs/runtime-loop-refactor.md 的最终不变量。

**操作**：

1. 记录 HEAD 和 dirty 文件；不要覆盖用户改动。核实存储/Trace 当前接入状态。
2. 用 `rg` 列出 architecture 旧文件映射中的实际消费者，保存到 progress 的 T00 记录，可按模块列路径而非复制全部搜索输出。
3. 执行当前完整测试、ty 和 CLI help，记录真实结果，不照抄旧文档的通过数。
4. 建立行为基线：tool 名称/参数 schema、事件生产者、CLI flags/退出码、价格输入、审批决定、Workspace 清理条件。
5. 记录默认数据库/Artifact 位置，但不读取真实数据库内容、不展示凭证。

**验收**：progress 可让新执行者定位基线、命令、已有失败和具体下一动作。基线失败只做安全诊断，明确是否影响当前卡；不把旧 HANDOFF 的全部遗留项自动加入本项目。

## T01 — 底层类型按所属模块迁移

**范围/必读**：models/artifact、models/tool、artifacts/store/file、environments/workspace、tools/__init__、tools/registry、对应包导出；tests/test_protocol_models.py、test_tool_contracts.py。

**操作**：

1. 新建 artifacts/contracts.py，合并现有 ArtifactRef、ArtifactWrite、ArtifactStore、错误；暂不改变存储行为。
2. 新建 environments/contracts.py，迁入 Workspace/Backend、Command、ExecutionResult、Patch、Checkpoint；WorkspaceDelta 迁到这里。
3. Workspace 配置先原样迁入 environments/config.py；字段重组留 T04。
4. 新建 tools/contracts.py 和 base.py，迁入工具类型、基类；runtime/tool_output.py 迁为 tools/rendering.py。
5. 更新全部生产调用和测试 import。确保链条为 tools → environments → artifacts，不出现 environments → tools。
6. 删除迁完的旧文件/定义；精简 __init__，新进程分别导入这些契约。

**不做**：工具泛型化、消息联合、结果字段变化、Artifact manifest。

**验证**：protocol_models、tool_contracts、local_workspace、docker_workspace 定向测试，ty、diff 检查。

**验收**：类型只有一个定义；功能与序列化形状不变；模型/环境契约导入不加载 CLI 或 Runtime 执行器。

## T02 — 模型协议与 Agent 配置

**范围/必读**：agents/spec/default、models/model、providers/__init__/qwen、runtime/loop/termination/execution、CLI model override、protocol_models/runtime/provider 相关测试。

**操作**：

1. ModelTarget 移到 models/config.py；建立 GenerationOptions；AgentSpec 与 ModelRequest 使用同一个参数类型。
2. 建立按 role 判别的 Message 联合，逐个迁移构造位置：默认消息、模型响应、工具回传和测试 fake。
3. Usage 明确命名为 ModelUsage；ModelProvider 移到 models/protocol.py；更新 Qwen 映射和 imports。
4. AgentSpec/model/generation 使用 frozen 模型，allowed_tools 用 tuple。构造 Run 时建立独立配置快照。
5. Provider 接收的消息不能共享可修改的已确认 State 内层对象；补充外部边界 fake 修改请求不影响 State 的行为用例。
6. 更新 CLI model override，仅替换模型名，保留 provider 与其他生成参数。

**不做**：增加模型参数、新 Provider、Jinja、改变 finish reason 决策。

**验证**：protocol_models、runtime、cli、cli_bootstrap、policy 测试；新增必要消息组合测试；ty。

**验收**：合法 tool-call-only assistant 正常；无关联 ID 的 tool 消息在模型边界拒绝；默认 Agent 无跨 Run 可变共享；所有构造方迁移，无旧别名。

## T03 — 独立计量、评估和 Runtime 基础类型

**范围/必读**：models/pricing、runtime/usage/state/evaluator/termination/loop/recovery、tests/test_pricing.py、test_runtime.py、test_protocol_models.py。

**操作**：

1. 创建 metering/pricing.py、usage.py；迁移价格、RequestCost、RunUsage、计算及累加逻辑，解除对 RunState 的依赖。
2. RuntimeConfig 迁到 runtime/config.py；RunError、TerminationReason、RunResult 迁到 result.py；处理 state/result 导入方向。
3. 评估契约迁入 evaluation/contracts.py，默认实现迁入 passthrough.py；独立 EvaluationError，Runtime 显式翻译。
4. CandidateOutput 明确承载文本及可选 Patch 引用；同步 loop 和 evaluator，不要求默认流程提前生成 Patch。
5. EvaluationPlan 移除 Docker 特定装配字段，保留命令和验证要求；评估器配置拥有环境依赖。更新原有作用域测试，保留 Run 请求关联，不能直接删掉作用域保护。

**不做**：预算执行、多币种、价格格式变更、命令评估器。

**验证**：pricing、protocol_models、runtime、cli；覆盖无 pricing 累计 usage、缓存费用和评估失败。

**验收**：metering/evaluation 无 Runtime 反向依赖；默认费用、终态和 CLI 数字保持。

## T04 — 统一 Workspace 运行与持久化配置

**范围/必读**：environments/config/contracts/local/docker、runtime/persistence/recovery、cli/bootstrap、SQLite recovery context 使用处。

**操作**：

1. LocalOptions/DockerOptions 提取非敏感字段；运行 Spec 与持久化 Spec 组合相同 Options。
2. 所有 workdir 路径规则定义一次；两种 Spec 同时通过共享类型执行校验与规范化。
3. EnvironmentReference 和 persist_workspace_spec 迁入 environments/persistence.py；只做白名单字段映射。
4. RunRecoveryContext 迁入 runtime/recovery.py，引用 Workspace 恢复描述；更新 Snapshot、Store 和 Bootstrap。
5. Bootstrap 只构造一次实际 WorkspaceSpec，以它创建 Workspace、构造持久化描述和策略执行条件；禁止二次重建而得到不同配置。
6. 检查点如改为联合，同步 create/restore/recover 全部消费者和作用域校验；保留现有可执行后端功能。

**验证**：local_workspace、docker_workspace、protocol_models、sqlite_storage、cli_bootstrap；两种 Spec 的非法/规范化 workdir、环境引用缺失、秘密值不序列化。

**验收**：增加 Docker 非敏感选项不再修改 runtime 中的后端字段定义；恢复上下文往返新数据库正常。

## T05 — 持久化契约归 Runtime 所有

**范围/必读**：storage/models/__init__/sqlite、runtime/recording/journal/snapshots/events、policy/models/approvals。

**操作**：

1. 把 RunCommit/Record/CommitResult、RuntimeStore、提交变更和边界异常迁入 runtime/store_contracts.py。
2. ApprovalRecord 拆为 policy 业务记录与 StoredApproval 持久化包装；不改变字段关系、幂等或到期语义。
3. Runtime 的 Journal/Recorder 仅引用本包契约；SQLite 实现契约；CLI 从具体 sqlite 文件导入实现。
4. 清理 Runtime 对 storage 的所有导入，删除 storage/models.py；不保留两套异常类。
5. 暂时保留现有审批 event index，T09 一次替换为 event ID。

**验证**：runtime、policy、sqlite_storage；事务门禁、ty；新进程分别导入 store_contracts 和 sqlite。

**验收**：`rg 'from miniagent.storage|import miniagent.storage' src/miniagent/runtime` 无结果；状态/审批/快照原子提交和失败后状态不变。

## T06 — 策略输入、规则与审批业务分离

**范围/必读**：policy 全部文件、runtime/tool_runner/approval、tools policy_metadata、tests/test_policy.py。

**操作**：

1. 按 architecture 拆 contracts、requests、rules、engine、approvals。
2. requests 将 read_file/shell 转为 WorkspaceRead/ShellExecution；未知工具映射 UnknownOperation。
3. engine 合并规则结果；具体规则只接收类型化 Operation 和可信上下文，不读取工具参数键。
4. 摘要构造集中到 requests，调用方不各自拼 JSON；为工具显式契约版本定义稳定输入，T07 完成 registry 对接。
5. ApprovalCoordinator 只保留等待/取消/超时/Run 协调；业务合法性调用 policy，数据库到期防竞态检查继续保留。
6. 显式增加 policy revision；不复用旧摘要或旧授权。

**不做**：改变默认 allow/deny/approval 结果；执行旧 HANDOFF 的新 Docker 安全策略；增加规则 DSL。

**验证**：policy、runtime、sqlite_storage、cli；审批拒绝、无 handler、过期、不同 Run、参数/策略变化后重判。

**验收**：通用 engine 无工具名字和参数分支；未知 Operation 不自动授权；调用摘要绑定仍完整。

## T07 — 类型化工具与 Prepared 调用

**范围/必读**：tools/base/registry/read_file/shell、runtime/tool_runner、policy/requests、测试中的自定义工具。

**操作**：

1. 工具定义使用 `Tool[ArgumentsT]`，具体工具 execute 接收其参数对象。
2. Registry 在注册时把参数模型、definition、逻辑版本和工具实现绑定；prepare 返回已绑定调用，不再把参数变回 `dict[str, Any]` 供工具执行。
3. 对异构工具列表的不可避免类型收敛只放在 registry/base 私有绑定边界，调用执行器保持简单；不得铺开 cast/ignore。
4. Prepared 调用暴露规范 JSON 供策略摘要和审计，实际执行使用同一份已校验参数快照。
5. 工具契约摘要去掉 `type.__module__`，加入逻辑名称、版本、schema 和超时等执行选项；更新 policy revision 和测试。
6. 迁移所有 fake 工具及工具注册测试；严格验证继续禁止额外参数和宽松类型转换。

**验证**：tool_contracts、runtime、policy、local_workspace、docker_workspace；ty。

**验收**：参数改名由具体工具类型检查发现；策略评估与执行参数一致；definition 外部修改不影响注册快照。

## T08 — 保留工具执行证据

**范围/必读**：environments/ExecutionResult、tools/contracts/execution_result/read_file/shell/rendering、runtime/tool_runner/execution。

**操作**：

1. ToolResult 增加明确的可选 duration_ms，保留 artifacts/workspace_delta/output；不靠私有 metrics 键表达公共字段。
2. 提取小型证据复制函数；Shell/ReadFile 每个返回分支均保留已经获得的证据。
3. 保持工具特有错误分类；超时/失败也不能无故丢掉已收集输出和制品引用。
4. 渲染仍有界，完整结果不原地截断。修正遗漏字段清单以符合新字段。
5. Runtime 已有结果验证同步新字段；持久化完整结果在 T09 接入。

**验证**：tool_contracts、runtime、local_workspace；用 Workspace 边界 fake 返回带耗时/制品/变更的结果，验证成功和失败转换；验证渲染后原始值不变。

**验收**：Workspace 已提供的信息无转换丢失；不假装 Workspace 已实现原本没有的差异采集或完整输出归档。

## T09 — 类型化业务事件与稳定关联

**范围/必读**：runtime/events/execution/journal/trace_context/store_contracts、storage/sqlite、当前所有 `NewRunEvent` 生产者和测试。

**建议子步**：T09.a 载荷与生产者；T09.b 存储/审批关联；两步未全部验收前 T09 不 DONE。

**操作**：

1. 枚举当前实际产生的事件，逐个定义载荷；保留实际需要的开始/完成/取消分支，删除纯旧日志兼容项。
2. 新信封/载荷结构采用 architecture D；NewRunEvent 分配 event_id，入库只补 sequence/time，不改变 ID。
3. 审批 change 用 request_event_id/resolution_event_id/invalidation_event_id 代替数组 index。
4. Journal 与 SQLite 验证 ID 属于本批提交且载荷 scope 匹配；移除 `.payload.get('approval_id')` 和事件下标猜测。
5. 增加 PolicyDecided；ToolEnd 保存完整结构化 ToolResult，Trace/模型文本不充当唯一结果记录。
6. 事件表 payload JSON 与 type 列必须来自同一载荷，解码检查不一致数据；新格式直接使用，不写旧解码器。

PolicyDecision 记录改为可等待的持久化操作，同步更新 tool_runner 调用；策略决定在工具执行前确认落库。不能保留仅修改内存的 record_policy_decision，再在 T12 删除其唯一存储位置。无 Store 时仍返回相同决定。

**验证**：protocol_models、runtime、policy、sqlite_storage；事务门禁、ty。

**验收**：缺失载荷必需字段立即拒绝；错误审批事件引用导致整个提交回滚；重开数据库可取回完整 ToolResult；事件序列连续。

## T10 — Trace 只管理关联信息

**范围/必读**：runtime/trace_context/tracing/execution/events、runtime 中 Scope 失败测试。

**操作**：

1. 把审批、模型、工具和终止的业务载荷构造从 TraceContext 移到 Execution 或所属功能边界。
2. TraceContext 保留 scope 建立/关闭、step/span/parent 关联；调用显式传入业务事件，不使用任意 payload 合并。
3. 完成、异常、取消时仍关闭已有活动 scope，保留当前层级与事件顺序。
4. 所有 scope 变化在候选 Trace 上进行，Journal 成功后才发布。

**验证**：protocol_models、runtime、sqlite_storage；事务门禁；覆盖模型异常、执行工具中取消、审批提交失败。

**验收**：调整审批业务字段不需要修改 trace_context；Trace 不变成可忽略的异步事件总线。

## T11 — 组合式状态和具名迁移

**范围/必读**：runtime/state/transitions/execution/result/config、所有 RunState 构造者、snapshots/store/codecs、runtime/protocol/storage 测试。

**建议子步**：T11.a 子对象及构造；T11.b 所有读取与迁移；T11.c 不变量与隔离验证。暂时保留历史字段，T12 再删除。

**操作**：

1. 按 architecture C 建立身份、定义、生命周期、会话、执行、审批引用、恢复子对象。
2. 迁入状态机转换表；提供 begin_step、accept_model_response、complete_tool、wait/resolve_approval、begin_evaluation、finish 等纯更新函数。
3. Execution 只通过这些具名操作创建后继状态；通用 `**changes: Any` 不再作为跨文件公共更新接口。
4. 同步所有读取方、RunResult 工厂、Snapshot 和 SQLite；新格式一次切换，不保留扁平兼容属性。
5. 在请求边界保证外部对象不改变已确认状态；仅对改动的子结构构造新值，不能以优化拷贝为由引入共享可变别名。
6. 明确区分 lifecycle transition 计数（如仍需要）、event sequence、snapshot version；存储并发只使用明确提交位置，不能混用三种版本。

**验证**：runtime、policy、protocol_models、sqlite_storage、cli_bootstrap；事务门禁、ty。

**验收**：非法组合在边界拒绝；失败提交不改变状态/usage/scope；外部修改构造输入无效；没有所有模块都可任意改字段的入口。

## T12 — 历史数据移出 RunState

**范围/必读**：T11 状态、runtime/execution/approval/journal、T09 事件、store_contracts、SQLite 查询。

**操作**：

1. 确认每种被删除事实都有审计落点：全部 PolicyDecision、审批请求/结果/失效、工具完成结果。
2. 删除 state.policy_decisions/approval_requests/approval_resolutions；当前审批只保留作用域引用。无 Store 模式由活动执行上下文持有所需决定/请求，不能为了查历史强迫配置 SQLite。
3. pending_tool_calls + 全 Run completed ID 集合改为当前 ToolBatch(call tuple, next_index)；完成一个结果与推进 next_index 原子提交。
4. 工具关联使用 Run+Step+索引，保持同 Step 重复 provider call ID 拒绝；不同 Step 不误认为已完成调用。
5. 批次完成清除当前 ToolBatch；终止时保留必要诊断但不能留下非法 pending approval。
6. 使用事件和 StoredApproval 查询历史，CLI/测试不得继续读取删除字段。

**验证**：runtime、policy、sqlite_storage；事务门禁；覆盖一个响应多个工具、第二工具失败、审批中取消、重复 ID 跨 Step、无 Store 授权。

**验收**：长历史不会通过审批/策略数组再次复制进每个 State；新数据库可查询这些历史；不声称消息历史已被压缩。

## T13 — SQLite 实现按读写职责整理

**范围/必读**：storage/sqlite、当前 initial SQL、store_contracts、全部 SQLite 测试。

**操作**：

1. codecs.py 接管当前模型 JSON 编解码、未知格式与损坏数据错误；没有旧版本升级分支。
2. 按 runs/events/approvals/snapshots 提取 queries 函数；全部接受调用方 connection。
3. sqlite.py 保留唯一事务入口、事件序列 CAS、输入冻结、线程等待和幂等；查询函数不得自行 commit。
4. 重写 initial SQL 与当前版本标记一致；旧版本只清楚拒绝，不删除、不自动覆盖。
5. 检查 Run/审批/Event/Snapshot 的数据库约束与模型作用域约束一致，必要查询索引保留。

**验证**：sqlite_storage、runtime；事务门禁；真实临时 SQLite 覆盖并发冲突、回滚、过期、幂等、重开、取消确定性、损坏格式。

**验收**：RunCommit 仍是一笔事务；构造/排队后的原输入变动不影响落库；代码拆分不新增数据库连接穿透事务。

## T14 — Artifact 元数据落盘与查询

**范围/必读**：artifacts/contracts/file、cli 中 persist_workspace_patch、Local/Docker checkpoint 调用及测试。

**操作**：

1. 加入 ArtifactRecord 和 get_record/list_for_run；保持 put 返回 ArtifactRef，消费者无需猜文件路径。
2. 设计当前格式 manifest，显式保存 metadata、run_id、类型、名称与内容引用；用原子替换发布记录。
3. 顺序为内容写入成功 → manifest 发布 → 返回引用；失败不返回伪成功。
4. 同内容不同 Run/metadata 有独立记录身份；列表只返回完整已发布记录。
5. 校验记录/内容摘要、根目录范围及 type/name 路径；错误不被静默忽略。
6. 覆盖 Patch 的 files/base_revision 和 Checkpoint 元数据往返。

按 architecture 为引用加入 artifact_id，更新手工构造引用的工具/测试及当前存储格式；同内容记录有不同 artifact_id，get_record 不以内容 URI 作为唯一记录键。

**验证**：新增 artifacts 定向测试，local_workspace、docker_workspace、cli_bootstrap；重建 store 对象后仍能按 Run 查询；同内容不同元数据不互相覆盖。

**验收**：metadata 不再丢失；外部读者不依赖 BootstrappedAgent 对象；不扩展垃圾回收或远程存储。

## T15 — CLI 配置与表示层拆分

**范围/必读**：models/settings、cli/main/bootstrap、policy ApprovalHandler、tests/test_cli.py/test_cli_bootstrap.py。

**操作**：

1. Settings 迁 cli/settings.py；保持环境变量名称、默认值和现有配置来源。
2. ConsoleApprovalHandler 迁 cli/approvals.py；结果/费用/制品展示迁 rendering.py。
3. main.py 保留 Typer、信号与退出码；bootstrap 保留具体实现装配。
4. 不改变 CLI 选项、默认 provider、model override 和价格显示；application 生命周期留 T16。

**验证**：cli、cli_bootstrap、policy；CLI help、ty。

**验收**：models 不再拥有数据库/Workspace Settings；仅调用 CLI help 不创建 Workspace、不打开真实数据库、不调用模型。

## T16 — 应用级收尾和结果

**范围/必读**：cli/application/bootstrap/main/rendering、artifacts 查询、runtime RunResult、Workspace destroy/patch 行为。

**建议子步**：T16.a 生命周期迁移保行为；T16.b 结构化收尾错误和验收。

**操作**：

1. 加入 ApplicationResult、FinalizationError；应用层显式编排 run → patch export → artifact registration → cleanup。
2. Bootstrap 返回运行需要的依赖及资源持有者，不通过 contextmanager 退出时改隐藏字段来交付结果。
3. Workspace 获取后立即进入资源保护范围；后续 Provider/价格/Store 装配失败有确定清理行为。
4. 普通收尾异常转换为带阶段与错误码的 FinalizationError，保留原 RunResult；任务级取消/主异常仍传播，附加收尾诊断，不能被覆盖。
5. 只有符合原成功清理条件且证据登记成功时销毁；失败、取消或导出失败保留可诊断 Workspace。不增加后台清理。
6. 成功 Run 有收尾错误时 CLI 非零退出并打印业务结果与错误；信号退出码优先。正常成功路径输出保持。
7. Patch 记录由 ArtifactStore 按 Run 查询，不为 Patch 元数据修改已终止的 Runtime 状态，也不在终止后伪造新执行 Step。

**验证**：cli、cli_bootstrap、artifact 测试；覆盖成功/模型失败/取消/装配失败/patch export 失败/manifest 失败/destroy 失败。

**验收**：已有业务结果不丢失，收尾失败不伪装成功；制品持久可查；没有资源创建到 try/finally 之间的泄漏窗口。

## T17 — 仅抽取真实共享的进程操作

**范围/必读**：environments/local/docker 中 stream/stdin helpers、对应后端测试。

**操作**：

1. 对照两实现输入/返回值、截断、编码、异常、取消语义。
2. 将真正相同的流读取/stdin 函数迁 process.py，保持显式参数。
3. Local 进程组终止与 Docker 容器终止保留各自实现；不建立通用 Backend 基类。
4. 若没有值得共享的实现，记录证据并不创建 process.py；该结论也可验收 DONE。

**验证**：local_workspace、docker_workspace；截断、超时和取消测试，ty。

**验收**：没有为减少重复而改变后端隔离行为。

## T18 — 测试归位与架构检查

**范围/必读**：现有 tests、architecture 所有模块边界、T00 原始行为清单。

**操作**：

1. 将测试按 agents/models/providers/tools/environments/policy/metering/evaluation/artifacts/runtime/storage/cli 归位；按行为分文件，不按实现函数一一建测试。
2. 共享 fixture 只放真实共享内容；外部边界 fake 保留清晰归属，不新建庞大测试工具框架。
3. 在 progress 更新原测试路径到新路径映射，修正本方案后续命令引用。
4. 通过 import 搜索和独立导入验证无禁止反向依赖、旧模块引用、通用 Any 更新接口。
5. 对照 T00 行为清单解释每项差异：纯路径变化、任务卡授权改进或未解决问题；未解释差异不得验收。

**验证**：完整测试、ty、diff；不增加只复述目录名或 Python 框架行为的测试。

**验收**：测试集合未因迁移丢失；新增测试针对契约/失败路径；没有 skip 来掩盖回归。

## T19 — 总体验收与文档收尾

**范围/必读**：全部 progress 记录、architecture、实际源码树、repository-map、AGENTS 的导航块。

**操作**：

1. 执行 plan 最终门禁，记录结果和跳过项；wheel 包含 SQL 等运行资源。
2. 以架构所有权检查定义和消费者：新工具无需改 SQLite、新计费无需改 Workspace、存储替换不需改 loop、Workspace 选项不在 Runtime 重复定义。
3. 清理已无引用的旧文件、旧导出与临时步骤；不得删除用户数据或无关工作树改动。
4. 更新 docs/repository-map.md、AGENTS 导航块及本次改变到的实现设计文档；保留历史重构文档的完成记录，必要时添加指向新结构的说明。
5. 不修改 README；领域术语未变化不修改 CONTEXT。若确需修改领域定义，先按适用 skill 流程另行处理。
6. progress 写最终结构、验证、明确后续能力清单；所有必做卡 DONE 才称本次重构完成。

**验收**：无未解释失败；不把预算、跨进程恢复、Trace 导出、命令评估器写成已实现；提供面向接手者的简洁完成报告。
