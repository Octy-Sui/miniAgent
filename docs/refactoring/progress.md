# 重构执行进度

本文件是本次重构的唯一进度源；方案见 [plan.md](plan.md)。不要从对话或其他 HANDOFF 推断完成状态。

## 当前检查点

- 阶段：重构完成；T00—T19 全部验收。
- 方案核对 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`。
- 代码执行基线：T00 `105 passed, 1 skipped`；当前完整测试 `125 passed, 1 skipped`。
- 当前任务：无。
- 下一任务：无；后续能力清单见 T19 执行记录。
- 下一具体动作：无。
- 本轮测试：最终完整测试 `126 passed, 1 skipped`；`ty check`、CLI help、wheel build/SQL 资源和 diff 检查通过。
- 当前关键约束：不考虑旧数据、旧导入或字段兼容；不删除真实本地数据库。

## 任务状态

合法状态：TODO / ACTIVE / BLOCKED / DONE。BLOCKED 必须写具体阻塞及已做检查；DONE 必须有验证记录。仅修改文档中的勾选不能代替实际执行。

| ID | 状态 | 实际结果/验证记录 |
| --- | --- | --- |
| T00 | DONE | HEAD `d72dbd55a6184c292e7904330ad0e89ef28df886`；`105 passed, 1 skipped`，ty/help 通过；调用与行为基线见执行记录 |
| T01 | DONE | Artifact/Workspace/Tool 契约已归位，旧定义已删除；`34 passed, 1 skipped`，ty/独立导入/diff 通过 |
| T02 | DONE | 判别消息、GenerationOptions、ModelProvider/ModelUsage 已迁移；相关测试 61+27 passed，ty/独立导入/diff 通过 |
| T03 | DONE | Metering/Evaluation/Runtime 基础类型已拆分；相关测试 51+37 passed，ty/独立导入/diff 通过 |
| T04 | DONE | 运行/持久化 Spec 共用 Options，恢复契约归位且 schema 一次切换；`55 passed, 1 skipped`，ty/diff 通过 |
| T05 | DONE | Store 契约归 Runtime、ApprovalRecord/StoredApproval 分层完成；完整测试 `110 passed, 1 skipped`，ty/diff 通过 |
| T06 | DONE | Policy Operation/规则/审批已拆分，revision v3；完整测试 `111 passed, 1 skipped`，ty/diff 通过 |
| T07 | DONE | 泛型 Tool、Registry 私有绑定和 Prepared.invoke 完成；相关测试 57+40 passed，ty/diff 通过 |
| T08 | DONE | duration/artifact/delta/截断证据转换完成；`37 passed`，ty/diff 通过 |
| T09 | DONE | 类型化事件、预分配 event_id 与直接审批关联完成；完整测试 `116 passed, 1 skipped` |
| T10 | DONE | TraceContext 仅保留 scope 生命周期；相关测试与类型检查通过 |
| T11 | DONE | 组合 RunState 与具名纯迁移完成；完整测试 `116 passed, 1 skipped` |
| T12 | DONE | State 历史移除，ToolBatch 前缀游标完成；完整测试 `116 passed, 1 skipped` |
| T13 | DONE | SQLite codecs/queries 拆分，事务唯一归 sqlite.py；完整测试 `116 passed, 1 skipped` |
| T14 | DONE | Artifact 身份、原子 manifest 和 Run 查询完成；完整测试 `120 passed, 1 skipped` |
| T15 | DONE | CLI settings/approvals/rendering 归位；相关测试 `26 passed` |
| T16 | DONE | Application 收尾编排与结构化失败完成；相关测试 `21 passed` |
| T17 | DONE | 真实共享 process helper 抽取；`15 passed, 1 skipped` |
| T18 | DONE | 测试按领域归位；完整测试 `125 passed, 1 skipped`，ty/import/diff 通过 |
| T19 | DONE | 导航与最终门禁完成；完整测试 `126 passed, 1 skipped`，wheel 含初始 SQL |

## 已确定决定

| ID | 决定 | 来源 |
| --- | --- | --- |
| D01 | 项目未上线，不做数据兼容；不保留旧 Python API 转发层 | 用户 2026-09-22 最新要求及本方案 |
| D02 | 旧数据库明确拒绝，不自动清空；测试使用临时数据 | 数据安全边界 |
| D03 | 不增加预算执行、跨进程恢复、Trace 导出或新评估器 | 将结构重构与新能力区分 |
| D04 | 原子提交和提交后发布不变；本次不采用 event sourcing 框架 | 已实现 Runtime 不变量 |
| D05 | Artifact manifest 发布完整元数据，提供 Run 查询；CLI 收尾与 Run 终态分离 | architecture B |
| D06 | 旧 docs/HANDOFF.md 不作为本项目的执行清单，不覆盖它 | 避免误续接其他任务 |

## 待决问题与阻塞

暂无已确认阻塞。若实现中发现不可由现有约束解决的产品语义，写明：问题、受影响卡片、可选项、建议、可继续的独立工作。不要因为普通文件命名或 import 路径选择反复请求确认。

## 执行记录模板

每卡完成或中断都追加以下记录；失败的验证不要被后来成功结果覆盖，注明修正即可。

```text
任务/子步：Txx / Txx.a
状态：ACTIVE | BLOCKED | DONE
开始 HEAD：
结束 HEAD：（未提交则写“未提交”，并列实际变更文件）
目标与实际范围：
已完成：
关键契约/决策：
文件及消费者：
验证命令 → 退出结果/通过失败跳过数：
未验证与原因：
已知问题：
下一动作：（具体文件 + 函数/类 + 操作，不写“继续重构”）
完成条件还差：
```

## 测试路径映射

| 旧路径 | 新路径 |
| --- | --- |
| `tests/test_application.py` | `tests/cli/test_application.py` |
| `tests/test_cli.py` | `tests/cli/test_main.py` |
| `tests/test_cli_bootstrap.py` | `tests/cli/test_bootstrap.py` |
| `tests/test_artifacts.py` | `tests/artifacts/test_file_store.py` |
| `tests/test_local_workspace.py` | `tests/environments/test_local.py` |
| `tests/test_docker_workspace.py` | `tests/environments/test_docker.py` |
| `tests/test_policy.py` | `tests/policy/test_policy.py` |
| `tests/test_pricing.py` | `tests/metering/test_pricing.py` |
| `tests/test_protocol_models.py` | `tests/models/test_protocol.py` |
| `tests/test_runtime.py` | `tests/runtime/test_runtime.py` |
| `tests/test_sqlite_storage.py` | `tests/storage/test_sqlite.py` |
| `tests/test_tool_contracts.py` | `tests/tools/test_contracts.py` |

## 工作树归属

- 用户已有改动：未跟踪的 `docs/refactoring/`，为用户指定的重构方案文档；执行期间按方案更新其中 `progress.md`，其余内容保留。
- T00 不修改生产代码；当前任务修改仅为 `docs/refactoring/progress.md` 的执行记录。
- 开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`。

## 执行记录

任务/子步：T00
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；仅更新 `docs/refactoring/progress.md`
目标与实际范围：建立当前代码、调用关系、行为及验证基线；不修改生产代码。
已完成：
- 确认 HEAD 与方案核对值一致；执行前 dirty 文件仅为用户新增且未跟踪的 `docs/refactoring/`。
- 确认 Bootstrap 构造 `SQLiteRunStore` 和恢复上下文，Runtime 经 `RunJournal`/`RunRecorder` 原子提交；`RunExecution` 已使用 `RunTraceContext`，不能按仓库旧导航误判为未接入。
- 旧文件消费者按模块记录：Artifact 旧定义由 artifacts、environments、runtime events/tracing 和 Docker 测试消费；Tool 旧定义由 environments、policy、providers、runtime、tools 及 workspace/policy/runtime/tool 测试消费；Model 旧定义由 providers、runtime 与 pricing/policy/runtime/CLI bootstrap 测试消费；Workspace 旧定义由 environments、runtime/Bootstrap/storage 及 workspace/storage 测试消费；pricing/usage 由 models/runtime 导出、execution/loop 与 pricing/runtime 测试消费；Provider 协议由 Bootstrap、Qwen、loop 及相关测试消费；persistence/evaluator/tool_output/policy models/storage models 的消费者与 `rg` 输出一致，分别集中在 runtime/storage/CLI 及对应测试。
- 行为基线：工具仅有 `read_file(path: strict str)` 与 `shell(command: strict str)`，schema 与参数模型严格相等且禁止额外字段；注册定义深拷贝，allowed tools 禁止重复/未知。Step 是一次模型请求加顺序工具批次。
- 当前事件由 TraceContext 产生 run/step/model/tool/tool-execution/approval/evaluation 的开始或完成事实；State、事件、审批变化和快照经 Journal 同一提交，候选 State/Trace 提交成功后发布。事件 schema v2，sequence/time 入库补齐；审批仍以批内 event index 关联。
- CLI 参数为必填 task、`--model`、`--workspace local|docker`、`--docker-image`；成功返回 0，普通失败 1，信号为 `128 + signal`，强制/键盘取消为 130。模型覆盖只替换 model 名。
- 价格输入来自 TOML（USD、billing unit、provider/model 唯一项），按 token 归一化 Decimal；配置 pricing 时缺 usage 终止为模型失败，无 pricing 时仍累计 usage。
- 默认策略：相对 Workspace read 允许，越界/非法 read 拒绝，Docker shell 允许，Local shell 需人工审批，未知工具拒绝；授权绑定 Run/Step/调用、参数摘要、Agent/工具/执行环境摘要和 policy revision，到期在提交事务内复核。
- 默认位置：运行库 `~/.miniagent/runs.sqlite3`、Artifact `~/.miniagent/artifacts`、Local Workspace `~/.miniagent/workspaces`；未读取这些真实数据。Bootstrap 会尝试导出 Patch；只有导出完成、已有 RunResult 且 Run 成功时销毁 Workspace，失败/取消/导出失败保留证据。
关键契约/决策：保留 `docs/runtime-loop-refactor.md` 已完成的单一 finish、RunState 单写入边界、候选提交后发布、无 Store/SQLite 业务终态一致，以及 cancellation token 与任务取消的既有差异。
文件及消费者：上述分组是 T01—T05 更新 import 与删除旧定义时的核对清单；精确消费者以执行时再次 `rg` 为准。
验证命令 → 退出结果/通过失败跳过数：
- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q` → 退出 0，`105 passed, 1 skipped in 2.29s`。
- `ty check` → 退出 0，`All checks passed!`。
- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent --help` → 退出 0，CLI 参数与上述基线一致。
未验证与原因：未运行真实模型或读取默认数据库，符合方案安全边界。
已知问题：无。
下一动作：T01 创建 `artifacts/contracts.py`、`environments/contracts.py`/`config.py`、`tools/contracts.py`/`base.py`/`rendering.py`，迁移直接消费者后删除旧定义。
完成条件还差：无。

任务/子步：T01
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；变更为 `artifacts`、`environments`、`tools` 契约文件及其直接 import/测试，详见工作树。
目标与实际范围：将底层 Artifact、Workspace、Tool 类型归还所属模块，不改变运行行为或 JSON 形状。
已完成：
- 新建 `artifacts/contracts.py`，合并 Artifact 引用、写请求、Store 协议和错误；删除 `models/artifact.py` 与 `artifacts/store.py`。
- 新建 `environments/contracts.py` 与 `config.py`，分别承载执行/Workspace 契约和原样迁移的 Spec/配置；`WorkspaceDelta` 已归环境模块；删除 `environments/workspace.py`。
- 新建 `tools/contracts.py`、`base.py`，并将有界渲染迁到 `tools/rendering.py`；删除 `models/tool.py` 与 `runtime/tool_output.py`。
- 更新所有生产与测试 import；确认没有旧模块路径残留，依赖链为 tools → environments → artifacts。
关键契约/决策：保留所有字段、校验、序列化形状和工具执行签名；泛型化与契约摘要变化留给 T07。
验证命令 → 退出结果/通过失败跳过数：
- 定向 pytest（protocol_models、tool_contracts、local_workspace、docker_workspace）→ 退出 0，`34 passed, 1 skipped in 1.51s`。
- `ty check` → 退出 0，`All checks passed!`。
- 首次独立导入命令未设置 `UV_CACHE_DIR`，因沙箱无法写 `~/.cache/uv` 退出 2；改用规定缓存目录后退出 0。
- 旧 import `rg`、`git diff --check` → 退出 0，无残留/格式错误。
未验证与原因：本卡不改变运行行为，按任务卡未扩大到完整测试。
已知问题：无。
下一动作：T02 迁移 `models/model.py` 和 providers 协议，同时更新 Agent 配置快照及模型边界隔离测试。
完成条件还差：无。

任务/子步：T02
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；新增 models config/messages/protocol，迁移 agents/provider/runtime/CLI 与相关测试。
目标与实际范围：模型消息、生成配置和 Provider 协议一次切换，不保留旧导入路径。
已完成：
- `ModelTarget`/`GenerationOptions` 归 `models/config.py`；AgentSpec 及嵌套配置冻结，allowed_tools 为 tuple，ModelRequest 复用 GenerationOptions。
- 建立按 role 判别的 System/User/Assistant/Tool 消息联合；tool-call-only assistant 合法，ToolMessage 强制非空关联 ID，各角色拒绝无关字段。
- `ModelUsage`、ModelRequest/Response/Error、FinishReason、ModelProvider 归 `models/protocol.py`；Qwen 映射和参数 JSON 解析随实现迁移，providers 不保留协议副本。
- Runtime 初始 Agent/Config 和 Provider 请求建立深拷贝快照；新增恶意边界 fake 修改请求内工具参数而不影响已确认消息的用例。
- CLI `--model` 只替换模型名；新增测试确认 provider 和 GenerationOptions 保持。
关键契约/决策：AssistantMessage 响应类型明确；合法工具调用可无文本。删除 `models/model.py`，不加 alias。
验证命令 → 退出结果/通过失败跳过数：
- protocol/runtime/cli/cli_bootstrap/policy 定向测试 → `61 passed`。
- pricing/sqlite_storage 补充回归 → `27 passed`。
- `ty check`、独立导入、旧路径 `rg`、`git diff --check` → 退出 0。
未验证与原因：未调用真实 Qwen，方案禁止真实付费模型；adapter 由类型检查及现有边界覆盖。
已知问题：无。
下一动作：T03 新建 metering/evaluation 模块并把 RuntimeConfig、RunError/Result/TerminationReason 移出 state。
完成条件还差：无。

任务/子步：T03
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；新增 metering/evaluation、runtime config/result 并迁移直接消费者与测试。
目标与实际范围：独立计量、评估和 Runtime 基础结果类型，不新增预算或命令评估器。
已完成：
- pricing 与 usage/cost 累计迁入 metering；RunUsage 不再由 State 定义，未配置 pricing 时仍累计模型 usage，缓存价格计算保持。
- RuntimeConfig 迁入 `runtime/config.py`；RunError、TerminationReason、RunResult 迁入 `runtime/result.py`，State 仅引用这些契约并重建结果类型引用。
- Evaluation 契约迁入独立包，PassThrough 实现单独放置；新增 EvaluationError，Runtime 显式翻译为 RunError。
- CandidateOutput 显式承载文本与可选 Patch 引用；EvaluationPlan 删除 DockerWorkspaceSpec，只保留目标/回归命令，EvaluationRequest 仍携带 run_id 关联。
关键契约/决策：默认 evaluator 仍只透传候选文本；没有创建 budget.py 或命令评估器。
验证命令 → 退出结果/通过失败跳过数：
- pricing/protocol/runtime/CLI → `51 passed`；sqlite/bootstrap/policy 补充回归 → `37 passed`。
- `ty check`、独立导入、反向依赖 `rg`、`git diff --check` → 退出 0。
未验证与原因：无任务卡要求之外的真实模型/命令评估。
已知问题：无。
下一动作：T04 读取 persistence/Workspace Spec/Bootstrap/SQLite 恢复上下文，统一共享 Options 和白名单持久化映射。
完成条件还差：无。

任务/子步：T04
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；Workspace config/persistence、recovery、Bootstrap、SQLite 当前格式和测试已更新。
目标与实际范围：统一 Workspace 运行/持久化配置与校验，持久化仅映射非敏感白名单。
已完成：
- 新增 LocalOptions/DockerOptions；运行 Spec 为 run_id + options + environment_values，持久化 Spec 为 run_id + 同一个 options + environment_refs。
- 路径/资源/Docker workdir 校验只由共享 Options 定义；Local/Docker 后端已迁移所有扁平字段读取。
- EnvironmentReference、Persisted Spec 与白名单映射迁到 `environments/persistence.py`；RunRecoveryContext 迁到 `runtime/recovery.py`。
- Bootstrap 只构造一次实际 WorkspaceSpec，并用同一对象创建 Workspace、持久化描述及策略执行条件。
- 当前格式一次切换：数据库 user_version 3、Run row v2、Snapshot v2、RecoveryContext v3；旧/未知版本明确拒绝，不迁移或清空旧库。
- 新增共享规范化 Options 的持久化测试；环境引用缺失、运行 Spec 误入恢复上下文和秘密值不落库继续覆盖。
关键契约/决策：未改变 checkpoint 结构，因当前 Local/Docker 必需字段和恢复语义无需为本卡强拆联合。
验证命令 → 退出结果/通过失败跳过数：Workspace/protocol/sqlite/bootstrap 定向测试 `55 passed, 1 skipped`；`ty check`、旧路径/扁平字段扫描、`git diff --check` 通过。
未验证与原因：Docker 集成测试因未配置 `MINIAGENT_DOCKER_IMAGE` 按既有条件跳过。
已知问题：无。
下一动作：T05 搬迁 storage models 中 Runtime 所有契约，并保证 Journal/Recorder 只引用 Runtime 包。
完成条件还差：无。

任务/子步：T05
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；新增 runtime/store_contracts，拆 policy ApprovalRecord 与 StoredApproval，删除 storage/models。
目标与实际范围：Runtime 拥有持久化契约，SQLite 仅实现；保留原子事务、CAS 和到期语义。
已完成：
- RunCommit/Record/CommitResult、RuntimeStore、审批/快照变更及边界异常迁入 `runtime/store_contracts.py`。
- `policy.approvals.ApprovalRecord` 仅保存业务请求/决定/处置；`StoredApproval` 组合业务记录与行版本、事件序列。
- Recorder/Journal 只引用 Runtime 契约；SQLite 直接实现并返回 StoredApproval；CLI 直接导入具体 SQLite 实现。
- 删除 `storage/models.py`，storage 包只稳定导出 SQLiteRunStore；Runtime 中没有 storage import。
关键契约/决策：审批批内 event index 暂按任务要求保留到 T09；未改变幂等、提交时到期检查和快照事务边界。
验证命令 → 退出结果/通过失败跳过数：runtime/policy/sqlite 定向 `56 passed`；事务门禁完整测试 `110 passed, 1 skipped`；`ty check`、依赖扫描、`git diff --check` 通过。
未验证与原因：Docker 集成仍因环境缺失跳过。
已知问题：无。
下一动作：T06 将工具参数适配集中为类型化 Operation，并把规则合并与审批协调分离。
完成条件还差：无。

任务/子步：T06
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；policy contracts/requests/rules/engine/approvals、Runtime/SQLite 当前格式和测试已更新。
目标与实际范围：类型化策略输入、规则与审批业务分离，默认决策结果保持。
已完成：
- WorkspaceRead/ShellExecution/UnknownOperation 与 Policy 通用契约归 `contracts.py`；工具参数到 Operation、摘要和 digest 集中在 `requests.py`。
- 具体规则迁 `rules.py`，通用 engine 仅合并 RuleMatch，不读取工具名或参数字典；未知工具不能凭自报风险获授权。
- Approval 请求/处置/业务记录归 `approvals.py`；Coordinator 继续只编排等待、取消、超时与 Run，并通过 PolicyEngine 重判业务合法性。
- policy revision 升为 `default-v3`，Policy/Approval schema v2；当前数据库格式同步为 user_version 4、Run v3、Snapshot v3、Approval row v2，旧格式拒绝。
关键契约/决策：为保持原非法 read 决策，UnknownOperation 带类型化 invalid_read_path 原因；未新增规则 DSL 或 Docker 信任策略。
验证命令 → 退出结果/通过失败跳过数：完整测试 `111 passed, 1 skipped`；`ty check`、禁止参数字典分支扫描、`git diff --check` 通过。
未验证与原因：Docker 集成按既有环境条件跳过。
已知问题：无。
下一动作：T07 让 Registry 在私有绑定边界保存具体参数类型，Prepared 调用同时提供规范 JSON 和同一已验证对象执行。
完成条件还差：无。

任务/子步：T07
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；Tool base/registry/实现、Policy request 摘要、直接调用方与测试已更新。
目标与实际范围：验证后保留类型化参数，策略摘要和执行使用同一已验证参数快照。
已完成：
- Tool 泛型化，具体 ReadFile/Shell 直接接收各自 Arguments 模型；所有测试 fake 同步类型化。
- Registry 私有绑定参数模型、定义深拷贝、显式 contract_version、执行选项和实现；PreparedToolCall 仅暴露规范 JSON 与 `invoke()`。
- Runtime 不再取回 dict 交给工具，也不访问 Prepared 内部 Tool；严格验证继续拒绝额外字段和宽松转换。
- 稳定契约摘要使用逻辑名称、版本、schema、执行选项，不含 Python 模块路径；policy revision 升为 `default-v4`。
- 新增摘要快照与无 implementation 路径测试；外部修改定义、版本或 arguments_model 不影响注册快照。
关键契约/决策：异构 Tool 的 Any 只存在 registry 的私有绑定/入口类型收敛处，不传播到实现或 Runtime。
验证命令 → 退出结果/通过失败跳过数：tool/runtime/policy/workspace `57 passed, 1 skipped`；CLI bootstrap/protocol/sqlite 补充回归 `40 passed`；`ty check`、旧访问扫描、`git diff --check` 通过。
未验证与原因：Docker 集成依旧按环境条件跳过。
已知问题：无。
下一动作：T08 新建 `tools/execution_result.py`，让 Shell/ReadFile 所有结果分支复制 duration/artifacts/workspace_delta/output 截断证据。
完成条件还差：无。

任务/子步：T08
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；ToolResult、execution_result helper、ReadFile/Shell、rendering 与测试已更新。
目标与实际范围：保留 Workspace 已返回的执行证据，不声称新增 Workspace 采集能力。
已完成：
- ToolResult 新增明确可选 `duration_ms`；新 helper 统一复制 duration、artifacts、workspace_delta 和 stdout/stderr 截断标记。
- ReadFile/Shell 的所有 Workspace 执行后成功、错误、超时和拒绝分支均使用证据转换；执行前的静态越界拒绝不伪造证据。
- 有界渲染包含 duration，仍只修改深拷贝的 output 描述；新增断言证明原始 ToolResult 不变。
- fake Workspace 覆盖成功与失败转换，验证 Artifact、delta、duration 和截断证据不丢失。
关键契约/决策：保留 Shell 的 exit_code metrics 作为工具特有数据，但公共 duration 不再藏在 metrics。
验证命令 → 退出结果/通过失败跳过数：tool_contracts/runtime/local_workspace `37 passed`；`ty check`、`git diff --check` 通过。
未验证与原因：本卡未改变后端采集与持久化，按任务卡未扩大完整测试；完整 ToolResult 入库由 T09 完成。
已知问题：无。
下一动作：T09.a 枚举现有生产事件并定义判别载荷；NewRunEvent 预分配 event_id，Execution 生产 PolicyDecided/完整 ToolCompleted。
完成条件还差：无。

任务/子步：T09
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；类型化事件、稳定 event_id、审批事件引用及 SQLite 当前格式已更新。
目标与实际范围：事件载荷改为判别联合；策略决定和完整工具结果形成审计事实；审批提交使用稳定事件 ID。
已完成：
- 为全部实际生产事件定义必填载荷，删除未生产的旧枚举项；事件 type 只从 payload 派生。
- NewRunEvent 在构造时分配 event_id；SQLite 只补 sequence/occurred_at，并校验 type 列与 payload 一致。
- PolicyDecided 在工具执行前等待落库；ToolCompleted 保存完整 ToolResult，有界模型消息仍独立渲染。
- Create/Resolve/InvalidateApproval 改用事件 ID；RunCommit、Journal 与 SQLite 验证批内唯一引用、approval/run/policy call scope。
- 当前数据库格式升为 user_version 5、Event v3、Approval row v3；新格式直接解码，不兼容旧数据。
关键契约/决策：事件为审计事实但不承诺 event sourcing；审批表仍是授权查询事实，二者在同一事务提交。
验证命令 → 退出结果/通过失败跳过数：protocol/runtime/policy/sqlite 定向 `74 passed`；完整事务门禁 `116 passed, 1 skipped`；`ty check`、`git diff --check` 通过。
未验证与原因：Docker 集成按既有环境条件跳过。
已知问题：业务 payload 构造仍暂在 TraceContext，按计划由 T10 立即拆出。
下一动作：T10 将 TraceContext 收敛为 scope 生命周期与事件信封装饰，业务载荷全部由 Execution 构造。
完成条件还差：无。

任务/子步：T10
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；TraceContext 与 Execution 事件生产职责已拆分。
目标与实际范围：Trace 只维护 scope 生命周期和信封关联，业务事件由 Execution 显式构造。
已完成：
- TraceContext 仅导入 EventPayload/NewRunEvent；不再知道模型、审批、工具结果或终止字段。
- Execution 构造全部 payload，再显式交给 run/step/model/tool/execution/approval scope 装饰。
- 模型异常、工具执行取消和审批等待终止时按活动层级关闭 scope，并保持结束事件顺序。
- 所有 scope 变化继续在 deepcopy 候选 Trace 上完成，Journal 成功后才发布；提交失败不污染已确认 scope。
关键契约/决策：ToolCorrelation 属于 Trace 关联信息，只保存调用索引/ID/名称，不构造或解释业务 payload。
验证命令 → 退出结果/通过失败跳过数：protocol/runtime/sqlite 定向 `60 passed`；完整事务门禁 `116 passed, 1 skipped`；`ty check`、`git diff --check` 通过；业务 payload 名扫描无结果（`rg` 以 1 表示无匹配）。
未验证与原因：Docker 集成按既有条件跳过。
已知问题：无。
下一动作：T11 建立组合式 RunState 与 `transitions.py` 具名纯更新函数，移除 Execution 的通用 update_state 调用。
完成条件还差：无。

任务/子步：T11
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；组合式 RunState、具名 transitions、全部生产读取方与存储格式已迁移。
目标与实际范围：RunState 按职责组合，Execution 只能通过具名纯函数产生后继状态。
已完成：
- 新增 RunIdentity、RunDefinition、RunLifecycle、ConversationState、ExecutionProgress、ApprovalProgress、PendingApprovalRef、RecoveryProgress。
- 状态机表与 prepare/start/begin_step/accept response/complete tool/wait-resolve approval/begin evaluation/finish 等纯函数移入 `transitions.py`。
- 删除公共通用 update_state/transition；Execution 不再能用任意字段名更新状态，scope 和 state 均只在提交成功后发布。
- RunDefinition 与输入消息在创建边界深拷贝；子对象 frozen，更新只重建实际改变的部分后做完整不变量验证。
- lifecycle transition_count 与 event sequence/snapshot version 明确分离；当前格式升为数据库 user_version 6、Run row v4、Snapshot v4。
关键契约/决策：T11 暂保策略/审批历史及旧 pending tool fields，严格按任务卡留给 T12；无扁平兼容属性。
验证命令 → 退出结果/通过失败跳过数：runtime/protocol/policy/sqlite 定向 `74 passed`；完整事务门禁 `116 passed, 1 skipped`；`ty check`、`git diff --check` 通过；旧通用更新接口扫描无结果（`rg` 退出 1）。
未验证与原因：Docker 集成按既有条件跳过。
已知问题：无。
下一动作：T12 删除 State 历史数组，以 ToolBatch(call tuple + next_index) 表示当前批次并用事件/StoredApproval 查询历史。
完成条件还差：无。

任务/子步：T12
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；RunState 历史移除，ToolBatch 前缀游标和调用链已接入。
目标与实际范围：State 只保存当前恢复事实，历史由事件/StoredApproval 承担；工具批次按当前位置推进。
已完成：
- 删除 policy_decisions、approval requests/resolutions、全 Run completed_tool_call_ids 和 pending_tool_calls 字段。
- 新增 ToolBatch(calls tuple, next_index)；同 Step provider call ID 必须唯一，游标不得越界。
- 完成 ToolResult、追加 ToolMessage 与推进 next_index 在同一候选 State/Journal 提交中完成；批次全部完成后才清除。
- PendingApprovalRef 仅保留 approval/run step/call 关联；无 Store 审批继续由活动 Execution/Coordinator 持有所需对象。
- PolicyDecision、审批请求/结果/失效及 ToolCompleted 均已有事件审计落点；审批查询继续使用 StoredApproval。
- 当前格式升为数据库 user_version 7、Run row v5、Snapshot v5。
关键契约/决策：工具关联以当前 Run+Step+call_index 为执行位置；不同 Step 不使用 provider call ID 做全局去重。
验证命令 → 退出结果/通过失败跳过数：runtime/policy/sqlite 定向 `58 passed`；完整事务门禁 `116 passed, 1 skipped`；`ty check`、`git diff --check` 通过。
未验证与原因：Docker 集成按既有条件跳过。
已知问题：无。
下一动作：T13 将 SQLite codecs 与 runs/events/approvals/snapshots queries 从事务协调器拆出。
完成条件还差：无。

任务/子步：T13
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；SQLite codecs 与 connection-scoped queries 已拆分。
目标与实际范围：SQLiteRunStore 保留唯一连接/事务协调、CAS 和幂等；模型格式与表级 SQL 各自归位。
已完成：
- `storage/codecs.py` 集中 Run/Event/Snapshot/Approval/Recovery JSON 编解码、版本拒绝、列与 payload 一致性校验。
- 新增 queries/runs、events、approvals、snapshots；读写函数均接收调用方 connection，不自行 commit 或打开连接。
- sqlite.py 保留 BEGIN IMMEDIATE、回滚、输入冻结、线程 shield、审批到期/幂等编排和连续 sequence 分配。
- initial SQL 保持当前 user_version 7，旧/未知版本只拒绝，不迁移、删除或覆盖。
关键契约/决策：query 层不拥有事务；codec 层不执行 SQL。
验证命令 → 退出结果/通过失败跳过数：sqlite/runtime 定向 `44 passed`；完整事务门禁 `116 passed, 1 skipped`；`ty check`、`git diff --check` 通过；query commit 扫描无结果（`rg` 退出 1）。
未验证与原因：Docker 集成按既有条件跳过。
已知问题：无。
下一动作：T14 为 ArtifactRef 增加 artifact_id，并实现原子 manifest、get_record/list_for_run 与 Patch/Checkpoint 元数据往返。
完成条件还差：无。

任务/子步：T14
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；Artifact 记录身份、manifest、查询和元数据生产者已实现。
目标与实际范围：内容与发布记录分离；同内容的不同 Run/metadata 保留独立记录身份。
已完成：
- ArtifactRef 新增必需 artifact_id；新增 ArtifactRecord、get_record(ref)、list_for_run(run_id)。
- FileArtifactStore 先原子写内容，再原子发布 manifest；manifest 是可查询发布点，重建 Store 后仍可读取。
- 内容按 sha256 存储可复用，记录按 Run/artifact_id 独立；读取核验 manifest/ref、根目录、大小和摘要。
- type/name 必须是安全路径段；损坏 manifest、越界 URI、内容变更均明确拒绝。
- Patch 保存 files/base_revision；Local/Docker Checkpoint manifest 保存 checkpoint/workspace/revision/base/workdir/backend/image 元数据。
- Artifact/Event/Recovery/Snapshot 当前格式同步升级：数据库 user_version 8、Event v4、RecoveryContext v4、Snapshot v6。
关键契约/决策：孤立内容不算发布记录；put 仍返回轻量 ArtifactRef，不把文件路径当记录身份。
验证命令 → 退出结果/通过失败跳过数：artifact/workspace/bootstrap/sqlite/tool 定向 `54 passed, 1 skipped`；完整测试 `120 passed, 1 skipped`；`ty check`、`git diff --check` 通过。
未验证与原因：Docker 集成因环境缺失跳过，但 Docker 元数据构造与类型检查已覆盖。
已知问题：无。
下一动作：T15 将 Settings、终端审批处理和结果展示从 main/bootstrap 拆入 CLI 所属模块，保持 flags/退出码/输出。
完成条件还差：无。

任务/子步：T15
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；CLI settings/approvals/rendering 已按表示层拆分，旧 models/settings 删除。
目标与实际范围：CLI 配置、终端交互与输出归 CLI；main 保留 Typer/信号/退出码，bootstrap 保留装配。
已完成：
- Settings 迁 `cli/settings.py`，环境变量、默认路径和配置来源不变；models 不再拥有应用配置。
- ConsoleApprovalHandler 迁 `cli/approvals.py`，提示内容、默认拒绝和 actor 语义不变。
- RunResult/费用/Patch 展示迁 `cli/rendering.py`；signal exit code 仍由 main 决定并优先传入。
- main 仅保留 Typer 命令、model override、信号和退出控制；bootstrap 继续选择具体 provider/workspace/store。
验证命令 → 退出结果/通过失败跳过数：cli/bootstrap/policy `26 passed`；`ty check` 与 `miniagent --help` 退出 0，CLI flags/默认展示不变。
未验证与原因：本卡不改变 Runtime/存储行为，按任务卡未重复完整事务门禁。
已知问题：当前 bootstrap contextmanager 仍混合运行后 Patch/cleanup，按计划由 T16 application 层接管。
下一动作：T16 引入 ApplicationResult/FinalizationError，将 run→patch→登记→cleanup 显式编排并保留已有 RunResult。
完成条件还差：无。

任务/子步：T16
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；应用层收尾、结构化错误和 Workspace 生命周期已显式编排。
目标与实际范围：Application 持有运行依赖并编排 run → patch export → artifact registration → cleanup；Runtime 结果和收尾结果分离。
已完成：
- 新增 ApplicationResult、FinalizationError 和 ApplicationDependencies；bootstrap 只装配并移交显式资源所有权。
- 普通 patch 导出、artifact 登记及 destroy 异常转为分阶段错误，成功 RunResult 不丢失；任务级异常继续传播并附加收尾诊断。
- 仅成功且证据登记无错误时销毁 Workspace；运行失败、导出/登记失败均保留诊断现场。
- Workspace 创建后的装配失败路径立即清理；CLI 对成功 Run 的收尾错误输出业务结果后以非零退出，信号退出码仍优先。
- Patch 通过 ArtifactStore manifest 按 Run 查询，不修改已终止 Runtime State，也不增加伪 Step。
验证命令 → 退出结果/通过失败跳过数：application/cli/bootstrap/artifact 定向 `21 passed`；`ty check`、`git diff --check` 通过。
未验证与原因：Docker 实际容器生命周期留在既有条件测试，本卡用 Local Workspace 和边界 fake 覆盖所有收尾阶段。
已知问题：无。
下一动作：T17 对照 Local/Docker 进程读取、stdin、截断、超时和取消语义，只抽取真正相同的部分。
完成条件还差：无。

任务/子步：T17
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；共享流读取、stdin 写入与输出解码已抽取，后端终止策略保持独立。
目标与实际范围：仅合并 Local/Docker 字节流层完全相同的行为，不统一进程启动、隔离或终止。
已完成：
- 新增 environments/process.py，集中持续排空但限量保留的 stream reader、容忍对端提前关闭的 stdin writer 和 UTF-8 replacement 解码。
- stdin pipe 不可用错误作为显式参数传入，保留各后端异常类型与原错误文本。
- Local 继续创建独立进程组并杀整个进程组；Docker CLI 进程和容器 kill 继续分层处理，取消与 BROKEN 状态语义未合并。
验证命令 → 退出结果/通过失败跳过数：local/docker workspace `15 passed, 1 skipped`；`ty check`、`git diff --check` 通过。
未验证与原因：真实 Docker 集成因 `MINIAGENT_DOCKER_IMAGE` 未配置跳过；脚本化 Docker 超时、kill、截断路径通过。
已知问题：无。
下一动作：T18 整理测试归属并执行架构引用/旧接口/行为清单核对。
完成条件还差：无。

任务/子步：T18
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；12 个平铺测试模块已迁入对应领域目录。
目标与实际范围：测试按行为所有权归 agents/models/providers/tools/environments/policy/metering/evaluation/artifacts/runtime/storage/cli 体系导航；当前没有独立 Agent、Provider 或 Evaluation 专属行为文件，不为目录完整性创建空测试。
已完成：
- CLI、Artifact、Environment、Policy、Metering、Model、Runtime、Storage、Tool 测试分别归入领域目录，pytest 收集数量未减少。
- progress 增加全部旧路径到新路径映射；最终命令统一使用递归完整测试或新路径。
- 独立导入各层代表模块通过；生产源码无 Runtime→Storage、旧模块 import、通用 update_state/transition 接口或功能层→CLI/Storage 反向依赖。
- 对照 T00：工具许可/严格参数、Step 串行、价格计算、默认策略、CLI 原 flags 与退出码均保持；事件 schema/关联、State 历史、Artifact manifest 和收尾错误/清理差异分别由 T09/T12/T14/T16 授权，未发现未解释差异。
验证命令 → 退出结果/通过失败跳过数：完整测试 `125 passed, 1 skipped`；`ty check`、13 个代表模块独立导入、禁止引用扫描、`git diff --check` 通过。
未验证与原因：真实 Docker 集成仍因 `MINIAGENT_DOCKER_IMAGE` 未配置跳过；未调用真实付费模型。
已知问题：无。
下一动作：T19 更新 repository-map/AGENTS，核对最终结构、wheel SQL 资源与所有门禁。
完成条件还差：无。

任务/子步：T19
状态：DONE
开始 HEAD：`d72dbd55a6184c292e7904330ad0e89ef28df886`
结束 HEAD：未提交；最终源码、测试、导航和重构记录均已验收。
目标与实际范围：完成模块所有权、发布资源、行为门禁和接手文档核验，不新增计划外能力。
已完成：
- `docs/repository-map.md` 与 AGENTS 导航改为实际 Application→Runtime/SQLite→Artifact 收尾链，清除旧文件与旧测试路径。
- 所有 architecture 必做文件存在且旧实现文件/导出已删除；Runtime 无 Storage 实现依赖，功能层无 CLI 反向依赖。
- 所有权检查通过：新增工具只触及 tools/policy 适配而不需 SQLite；新增计费不需 Workspace；替换存储只需实现 runtime store contract；Workspace options 只在 environments/CLI 定义。
- wheel 从 sdist 成功构建，包含 `miniagent/storage/migrations/001_initial.sql` 及 queries/codecs；构建产物输出到 `/private/tmp`，未污染工作树。
- T00 对照无未解释回归；新增直接测试证明任务取消传播、收尾诊断附注及 Workspace 保留。
关键契约/决策：旧数据库和旧 Python API 不兼容；未上线的最终格式统一作为首个正式版本，数据库及 Run/Event/Snapshot/Approval/Recovery schema 均为 1。
最终结构：契约分别归 models/tools/environments/policy/evaluation/artifacts/runtime；metering 独立；storage 仅实现 Runtime store contract；CLI Application 独占应用收尾。
验证命令 → 退出结果/通过失败跳过数：完整测试 `126 passed, 1 skipped`；`ty check`、`miniagent --help`、`uv build`、wheel SQL 清单、独立导入、禁止引用扫描和 `git diff --check` 全部通过。
未验证与原因：真实 Docker 集成因 `MINIAGENT_DOCKER_IMAGE` 未配置跳过；按方案未调用真实付费模型，也未读取或迁移用户数据库。
已知问题：无本次重构遗留问题。
后续能力（明确未实现）：跨进程恢复/审批恢复、预算执行、Trace 聚合导出与查询、命令评估器、新 Provider/工具、并行工具和 UI。
下一动作：无；可由维护者审查并按仓库提交规范拆分或提交工作树。
完成条件还差：无。

验收后修正（2026-09-23）：
- 发现执行过程中错误地把未发布格式的内部改动累计为 schema 2—8；这与“不保留旧数据兼容、最终格式作为首次发布”的决定冲突。
- 数据库 user_version 以及 Run/Event/Snapshot/Approval/Recovery/Policy/Artifact 持久化对象现统一为 schema 1；未知版本仍明确拒绝，不自动迁移或删除开发数据。
- 定向 storage/policy/models/runtime 测试 `74 passed`；完整测试 `126 passed, 1 skipped`；`ty check`、`git diff --check` 通过。重新构建 wheel 并确认包内 `001_initial.sql` 设置 `PRAGMA user_version = 1`。
