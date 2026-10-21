# Trajectory 功能需求与设计

日期：2026-10-20

状态：用户已确认完整证据保存、排除推理过程及不升级 schema_version，其余选择采用推荐方案；尚未进入实施计划或代码实现。本次修订不提交。

## 1. 意图、已知要求与假设

用户已明确：默认不导出 trajectory，运行时通过配置参数启用；无论是否启用导出，Event 始终保存完整模型输入、模型最终输出、工具执行命令和输出等运行证据；不保存模型推理过程内容。项目尚未上线，现有 schema_version 不变，不设计历史格式兼容迁移。其余设计选择采用本文推荐方案。

用途假设：使用者首先是 miniAgent 开发者，需要离开实时进程、甚至在 Workspace 销毁后，复盘一次 Run：模型看到了什么、返回了什么、提出了哪些工具调用、工具实际执行了什么、观察到了什么、为何终止、独立验证是否通过，以及消耗多少。本文按此用途设计；训练数据生产不是首版验收目标。

成功标准：拿到一个导出文件即可阅读模型与工具交互主线；每项事实可关联原始 Run、Event、Step、attempt 与 Span；压缩、重试和跨进程 resume 不造成历史混淆；缺失信息明确表示，不能根据最终状态补造历史。

本轮仅修订设计，不修改运行代码、不编写实施计划、不提交。第 13 节汇总用户要求与采用的推荐决策，便于核对。

## 2. 当前实现与需求缺口

依据当前代码，而非 roadmap：

- `runtime/events.py` 已覆盖 Run、Step、模型、工具、策略、审批、压缩、评测、中断和 resume；Event sequence 是 Run 内的事实顺序。
- `runtime/state.py` 保存当前 conversation；`runtime/snapshots.py` 保存指定 Event 边界的 Run State。Snapshot 中的 conversation 不等于完整模型请求历史。
- `ModelRequested` 主要包含 provider、model、attempt；`ModelResponded` 主要包含 finish reason 与工具调用数量。业务请求的逐次 usage、cost、provider request ID 和完整请求/响应尚未作为这些事件保存。
- `runtime/loop.py` 构造业务及摘要模型请求；`runtime/execution.py` 和 journal 持久化运行事实。响应可能在 usage 或业务校验阶段被拒绝，不能只记录被接受的 AssistantMessage。
- `ToolEnded` 已可带有有界 `ToolResult`；实际加入 conversation 的 `ToolMessage` 还需要明确保存，不能日后用新版 renderer 推算。
- `models/messages.py` 的 AssistantMessage 不含 reasoning 文本；provider 当前只映射 reasoning token 数。不能承诺从已有存储恢复模型思考文本。
- `runtime/tracing.py` 的 `RunTrace` 是未接入的 Span 聚合契约。Dashboard 独立推导 Span，并已有缺失/冲突处理；不能把现有 `RunTrace` 当作已可导出的完整交互记录。
- ArtifactStore 已提供不可变内容、manifest、hash 与按 Run 查询；CLI 已有 finalization 与输出路径。

结论：导出最后一个 Snapshot 或直接序列化现有 `RunTrace` 都不足以达到本功能目标。

## 3. Trajectory 的意义与边界

Trajectory 是某个 Run 在明确已提交边界上的、用于复盘的交互与执行证据导出。它属于现有 Trace Artifact 的产品用途，不替代 Run Event、Run Snapshot 或恢复状态。

它回答三类问题：

1. 行为：模型依据哪些上下文发出了什么动作，Runtime 是否接受，工具是否获准并执行，模型收到什么观察。
2. 结果：候选输出、最终输出、终止依据与 Evaluation 分别是什么；中断和未知执行结果在哪里。
3. 效率：业务模型与摘要模型分别产生多少已确认 usage/cost，哪些请求结果或账单未知。

首版不包含：从 trajectory 恢复/执行 Run、确定性重放、训练数据转换、跨 Run 对比、远端 telemetry、实时文件流、专用查看器、OTel 协议、完整代码仓库或 Workspace 打包。也不顺带实现通用 RunTrace 聚合器。

## 4. 方案比较

| 方案 | 收益 | 代价与限制 | 选择 |
| --- | --- | --- | --- |
| 始终保存完整 journal 证据，按开关在收尾生成 JSON Artifact | 与已有事实同事务保存；跨 resume 连续；导出格式独立演进 | 所有 Run 的持久化内容增加，需要扩展 Event 契约 | 推荐并按用户要求修订，本文采用 |
| 仅导出现有 Event/Snapshot | 改动小，可读历史 Run | 缺少逐次准确模型输入与被拒绝响应，无法满足交互复盘 | 不采用 |
| 运行时另写 JSONL | 可实时消费，崩溃后可能留下文件前缀 | 增加事实提交与文件写入的一致性问题，resume/并发/半行处理复杂 | 首版不采用 |

首版优先复用既有提交路径。完整运行证据始终保存，trajectory 开关仅控制是否生成导出文件。模型请求完整保存，不新增消息字典、内容寻址数据库或独立采集进程。重复上下文和完整工具输出会增加空间，这是明确接受的成本；压缩算法和去重存储由实际需求推动。

## 5. 配置与入口

推荐接口：

```text
miniagent run "task" --trajectory
miniagent run "task" --no-trajectory
miniagent resume --run-id <uuid>
uv run python trigger --instance-id <id> --trajectory
```

- `miniagent` 的直接运行入口与 `miniagent run` 行为一致；`trigger` 透传同一选项。
- Settings 增加 `export_trajectory: bool = False`；按现有 Settings 习惯读取环境变量 `EXPORT_TRAJECTORY` 和 `.env`。CLI 使用可区分“未提供”的 `--trajectory/--no-trajectory`，优先级为显式 CLI > Settings > false。
- 开关仅控制收尾时是否生成 trajectory Artifact，不控制 Event 内容或工具完整输出的保存。
- 解析后的值保存到冻结的 CLIApplicationDefinition `export_trajectory`，由 application 决定导出。Runtime 不增加 trajectory 采集开关，直接调用 AgentRuntime 也始终保存完整运行证据。
- resume 继承原 Run 的冻结导出值，首版不提供中途切换。无论原 Run 是否导出，恢复后都继续保存完整证据；仅开启导出的 Run 在收尾生成新文件。
- `resume --dry-run` 不导出、不写 Artifact，不采集新的交互。
- 默认关闭时，不创建 trajectory Artifact，也不增加后台导出任务；模型输入/最终输出、工具命令与完整输出等 Event 证据照常保存。
- 输出位置复用 `artifact_root`，CLI 显示 `Trajectory: <uri>`。首版不增加自定义目标文件路径、格式选择和详情等级选项。
- 自动 trajectory Artifact 导出属于 CLI application 装配职责；完整工具输出使用注入的持久化能力保存，Runtime 不自行创建 ArtifactStore。直接调用者也必须提供满足完整证据保存契约的持久化依赖。

## 6. 具体保存内容

| 内容 | 保存要求 | 复盘价值 |
| --- | --- | --- |
| 文件身份 | trajectory schema version、Run ID、trace ID、导出时间、miniAgent 版本、已提交边界 sequence、最后观察状态 | 明确文件对应哪次 Run、覆盖到哪里 |
| 任务与有效定义 | 完整 task、Agent 指令和身份、provider/model、generation、工具定义与 schema、Runtime 预算、模型容量/输出限制、冻结定价、Workspace 类型与 base revision、Evaluator 类型；benchmark instance/task digest 若有 | 解释行为条件，区分 pass-through 与独立验证 |
| 模型请求 | 每次实际传给 `ModelProvider.generate()` 的规范化 ModelRequest：完整有序 messages、tools、generation、model；业务或摘要用途、attempt ID、关联 Step（若有） | 确定模型可见输入，而非推测最终 conversation |
| 模型响应 | 每次调用收到的完整最终响应内容，含未经二次截断的文本、tool call ID/name/arguments、finish reason、usage、provider request ID、latency、结构化 error；业务接受/拒绝结果及理由；不含推理过程文本 | 保留正常、失败、超长、缺 usage 等响应，不限于 Run 最后一次作答 |
| 请求费用 | 每次已计算 RequestCost、currency；Run 累计 usage/cost、unconfirmed 请求数 | 拆分业务与摘要成本，避免把未知费用算作零 |
| 工具交互 | 完整调用参数与实际执行命令、执行目录、顺序、策略与审批结果、执行 attempt/身份/结束证据、完整原始工具结果及 stdout/stderr、返回码、有界 ToolResult、实际追加的 ToolMessage、duration、error、观察截断标记；大正文由 Event 引用持久化 Artifact | 区分完整执行事实、许可和模型实际收到的有界观察 |
| 压缩 | 触发原因、来源 Snapshot/version/sequence、covered messages、每次摘要请求和响应/错误/usage/cost、已发布 Task Memory 与版本关联 | 解释模型上下文变化，保留压缩前历史 |
| 生命周期 | 按 sequence 排列的 Run/Step/operation 事件，重试、中断、suspension、resume attempt、未知结果；Span ID 与父 ID | 保留因果关系，避免把恢复误认为新 Run |
| 输出与验证 | candidate output、final output、termination decision/reason、Evaluation 类型/结果/错误、已保存报告和 patch 的 ArtifactRef | 区分模型作答、Runtime 终态与独立验证 |
| 附件目录 | 依赖 Artifact 的 ID/type/name/media type/size/hash 与原引用 URI | 关联补丁、报告和已有完整日志，不复制附件正文 |

保存的是 provider-independent 规范化协议内容，不承诺 HTTP wire payload、provider 隐式默认值或 SDK 内部重试细节。完整请求意味着 messages 不作二次摘要；规范化 ToolCall 参数不等于原始 JSON 字节。

模型最终输出指每次模型调用的最终响应内容，包括业务响应和摘要响应，不仅是 Run 的 final_output。完整保存 Runtime 收到的最终内容，不做二次摘要或截断；provider 因输出上限结束时保存实际收到的全部内容和 finish reason，不声称包含未生成的后续文本。

不保存模型推理过程文本，包括 provider 单独提供的 reasoning/thinking 字段或推理流；不为了本功能请求或采集这些内容。已有 reasoning token 数作为 usage 指标照常保存。最终 content 中面向用户的解释属于最终响应正文，照常保留，不凭文本猜测并删改内容。

完整工具执行记录与模型可见观察分开保存：在任何面向模型的截断之前持久化原始工具结果和完整 stdout/stderr，Event 保存正文或带 hash/size 的 ArtifactRef。输出较大时必须保存完整正文 Artifact，不能仅在原有 Artifact 恰好存在时引用。此行为不受 trajectory 导出开关影响。

ToolOutputLimits 继续约束加入 conversation 的 ToolResult/ToolMessage；模型实际看到的截断观察、截断标记与完整执行输出都必须可追溯。完整输出不得通过事后重新读取文件或重新执行命令补取；执行过程中尚未收到或因强制终止无法确认的输出明确标记缺失。没有后续模型请求的最后一个工具观察也必须可读。trajectory JSON 中大正文附件仍仅引用，保持首版不打包附件的推荐选择。

### 6.1 明确不保存的内容

不序列化 Settings、凭据、HTTP header、API key、完整环境变量、恢复锁或执行权限令牌。定义采用允许字段列表，只导出复盘所需字段，不复制整个 recovery context；provider endpoint 仅保留已有非凭据身份标识。

task、指令、模型文本与工具结果按实际内容保存，可能包含代码、路径或用户输入的敏感文本。首版不对这些正文自动脱敏，以免破坏复盘准确性；文件声明 `content_policy: faithful`。这不构成可直接公开分享的导出模式。分享脱敏和附件打包是后续独立能力。

## 7. 证据采集与持久化

在已有模型/工具/压缩事件中加入有类型的完整证据字段，不引入与 Event 平行的业务记录表。执行路径按事件类型始终提供适用证据；字段允许缺省仅用于对应内容不适用或尚未产生结果的情况，不能由 trajectory 开关决定：

- 请求证据与对应请求开始事件在同一 journal commit 中保存，提交成功后才允许调用 provider。摘要请求沿用摘要 operation attempt identity，不能只用可重复的重试次数关联。
- 请求事件表示“已准备并获准进入调用边界”，不证明远端收到请求。提交后、发送前的取消仍可能留下没有响应的 attempt；不得导出为模型已完成。
- 收到规范化响应后，先保证它进入持久化提交路径，再处理可能拒绝它的 usage/termination 分支。响应证据与其结果事件一同提交；必要时可以先提交响应观察，再提交状态决策，不能只记录被业务接受的响应。
- provider 抛异常且没有 ModelResponse 时，只记录已知错误和 operation 未确认结果；不合成空响应、零 usage 或伪造响应时间。
- 工具执行适配层在截断前保存完整结果/输出；需要 Artifact 时先完成正文持久化和注册，再将引用与 ToolEnded/状态变化原子提交。保存失败不得用截断正文代替完整证据；正文已写入而 Event 提交失败时，不得据此宣称执行结果已提交。
- 工具完成时，将实际渲染并用于追加 conversation 的 ToolMessage 与已有 ToolEnded/状态变化原子提交；denied/timeout/error 也适用。未执行的工具不伪造命令执行输出；有 ToolResult 却未产生观察时明确记录观察缺失原因。
- 摘要响应在 Task Memory 校验/发布前保存，因此无效摘要和重试失败仍可解释。已发布摘要从 compaction commit 的事实关联，不用导出时重新调用摘要模型。
- 关闭 trajectory 导出时，上述持久化路径和完整内容不变，只跳过最终导出。

采集后的证据属于已持久化 Event，较大正文通过 Event 引用不可变 Artifact。SQLite/journal 写入失败仍遵循既有 RunPersistenceError 语义；必需的完整正文保存失败按证据持久化故障处理，停止后续业务执行，保留已经发生的执行事实与不确定性，不允许悄悄丢弃证据后继续宣称记录完整。这与最终 JSON 导出失败是不同故障。证据持久化仍须服从停止与清理 deadline，强制结束后的未提交部分不能承诺完整。

### 7.1 契约更新与版本约束

新的 trajectory JSON 从 `schema_version: 1` 开始，独立于内部 Event/Snapshot 版本。

项目尚未上线，直接扩展当前数据契约，现有 schema_version 保持不变：Event/Snapshot 仍为 3，CLIApplicationDefinition 仍为 1，新增完整证据字段和 export_trajectory。新引入的 trajectory JSON 使用自己的初始 schema_version，不构成现有版本升级。

同步更新 Runtime、storage、resume、Dashboard 的相关模型、读写逻辑及测试数据，保持当前契约一致。不增加多版本解码、历史数据迁移或旧程序兼容层；不要求兼容本次契约调整前的开发期 Run 数据，也不自动改写或删除这些数据。当前契约下的冻结定义 hash 校验、Run 身份和恢复安全约束继续保留。

## 8. JSON 文件契约

一个 UTF-8 JSON 文件，Artifact 类型 `run_trajectory`，media type `application/json`。顶层包含：

| 字段 | 语义 |
| --- | --- |
| `schema_version`, `kind` | `1` 与 `miniagent.trajectory` |
| `run_id`, `trace_id`, `exported_at`, `producer_version` | trace ID 沿用 `run_id.hex`，时间带 UTC 时区 |
| `boundary` | `through_sequence`、该边界对应 Run Status、`terminal`、`snapshot_version`（若有）、结束边界类型 `terminal/suspended/abnormal` |
| `coverage` | `complete/partial`、结构化 `issues`；显式声明未保存 wire payload/reasoning 文本和附件正文 |
| `definition` | 第 6 节允许导出的有效定义 |
| `summary` | candidate/final output、termination、evaluation、Run 累计 usage/cost、unconfirmed 数 |
| `events` | 1 到 through_sequence 的有序 Event；包含始终保存的完整交互证据或正文 ArtifactRef，以及原始关联标识，不重排为时间顺序 |
| `artifacts` | 截至导出前已注册且属于该 Run 的附件目录，排除所有 trajectory Artifact |

首版以 events 为规范时间线，不再复制一份 messages/steps/attempts 树。请求中的 messages 已足以独立解释一次模型输入；工具观察和完整响应仍能在原事件中直接找到。Span 父子关系不要求时间区间包含；首版不输出猜测补全的 TraceSpan 列表。

`coverage.complete` 表示既定边界内应采集的证据均存在且 Event 连续，不表示 Run 已成功或到达终态。失败或 suspension 可以有完整的边界导出；缺少规范化响应是已知中断事实时，不等于事件损坏。预期应存在却丢失的证据、未知版本或 sequence 缺口才产生对应 issue，不能以空数组掩盖。

对已经结束但请求无确认结果的情况，文件可完整记录“结果未知”；summary 中 unconfirmed 不归零。跨 resume 的 sequence/attempt/span 全部保留原值，不重编号。

金额采用十进制字符串；缺失值为 null 或明确 issue，不能替换为 0。Artifact 的 hash 标识已注册内容，本次导出不读取全部附件验证可达性，因此附件目录不保证外部附件仍可读取。

## 9. 收尾、导出与错误语义

导出位于 CLI application finalization：先完成已有候选/诊断补丁等持久化动作，再导出 trajectory，再形成 ApplicationResult 和做适用的 Workspace 清理。文件包含 Runtime 结果与已提交的补丁/评测事实；不承诺包含之后发生的 Workspace 清理结果。

启用导出时，成功、失败、取消或 suspension，只要存在 Run 且可以安全读取已提交事实，都尝试导出；bootstrap 在 Run 创建前失败时没有文件。执行异常时，在保留原异常的前提下尝试导出已提交前缀。关闭导出则不生成文件，但完整证据保存要求不变。不得为了导出发起新的业务操作，也不得延迟原有停止/资源清理义务。

一致性要求：在 Run 锁仍由当前执行持有时，以固定已提交 sequence 为边界读取同一边界的 record、events 与所需 Snapshot。所有关联来源必须不晚于边界；不能拿更新后的 summary 搭配较旧事件。正常路径没有并发 writer；异常路径无法证明边界一致时报告导出失败，不生成似乎完整的文件。

校验通过后通过 ArtifactStore.put 注册不可变内容，沿用现有存储的内容与 manifest 完整性保证；不自行覆盖已有 Artifact。只有注册成功，才在结果和 CLI 展示引用。每次 resume 收尾生成新的 Artifact，以各自 through_sequence 标明覆盖范围；此前文件保持原样。附件目录排除 trajectory，避免递归引用历史导出。

首版不增加导出完成 Event：导出产物由 Artifact manifest 发现，避免“导出事件又需要包含在自身导出中”的循环。

导出时间预算复用 `artifact_collection_timeout_seconds` 的配置值作为单次导出上限，并服从更早的 interruption cleanup deadline；不另加配置项。预算已耗尽时直接报告 skipped/interrupted。重复信号或强制结束不保证生成文件，不破坏已提交 journal 或锁释放纪律。

当前 ArtifactStore.put 接收完整 bytes，因此首版 JSON 最大 64 MiB（UTF-8 序列化后的大小）。导出器分页读取固定边界的 Event，逐项编码并累计字节，不能先加载整个事件历史或构建无限制的完整对象。累计超过上限即停止并报告失败，不截断成不完整 JSON，不把完整性问题藏成成功。只有完整内容在上限内时才向 put 提供 bytes。现有 SQLite 中单个请求记录本身的大小仍受 Runtime 上下文约束，不声称导出总内存精确等于文件上限。此限制仅针对 trajectory JSON，不允许据此截断或停止保存完整 Event/工具输出；大正文附件不计入 JSON 文件大小。完整证据始终保存带来的数据库和 Artifact 体积增长仍需接受。后续若真实 Run 经常触限，再单独设计流式导出/去重。

导出失败写入独立 `trajectory_export_errors`（新增有类型结果字段），不用现有影响成功判定的 finalization_errors：

- 已有 RunResult/RunSuspension、Evaluation verdict、原异常和现有退出码保持原有语义；trajectory 导出失败只打印明确的 stderr 诊断。
- 例如成功 Run 因磁盘满未导出时，仍按原成功行为返回，同时说明 `TRAJECTORY_EXPORT_FAILED`；失败 Run 的原失败原因不被覆盖。
- 不因 trajectory 导出失败而额外保留 Workspace，也不阻止已有清理；现有补丁/Artifact finalization 故障继续遵循原有规则。
- 不自动重跑模型/工具或无界重试。进程被强制结束后，首版没有独立历史导出命令；journal 中的证据仍保留供后续能力使用。

## 10. 组件职责与依赖

- `runtime`：始终在执行边界生成完整证据，沿用 journal 原子提交，不感知 trajectory 导出开关。新增 trajectory schema/projector 模块，负责将持久化事实变为有类型的导出对象；不依赖 CLI、Dashboard、Workspace 执行或 provider 网络客户端。
- `storage`：按扩展后的当前 Event/Snapshot 契约读写，保持原 schema_version、序列与事务约束，不承担导出格式逻辑或新增历史迁移。优先复用现有分页读接口；仅在一致性读取确实缺能力时补足最小接口。
- `cli`：Settings/flag 装配、trigger 透传、resume 继承、固定边界读取协调、导出预算、Artifact 注册、结果诊断和 URI 展示。
- `tools/env`：在面向模型的截断之前捕获并持久化完整工具结果、执行命令与输出，将完整记录和有界模型观察分别交付；不重新执行工具补取证据。
- `artifacts`：复用现有不可变存储契约保存完整工具输出和 trajectory，不增加 bundle 格式。完整工具输出的保存路径需要在实施计划中验证大输出资源使用，不能沿用只返回截断内容的捕获路径。
- `dashboard`：只做扩展后的当前契约读取与既有敏感正文展示边界适配；不新增 trajectory 页面、下载按钮或复用其展示投影作为导出真相来源。

无需新依赖、后台服务、通用 observer 框架或全局日志 hook。规范化证据使用 Pydantic 模型；内部边界协调可沿用现有简单容器。

## 11. 验收与验证范围

后续实现必须以可观察内容验收，而不是只断言某个内部 hook 被调用：

1. 不带配置运行、环境显式 false：没有 trajectory Artifact，但 Event 仍完整保存模型输入、最终响应、工具命令与输出；同一行为在开启和关闭导出时持久化内容口径一致。
2. Settings true、CLI false 和 CLI true 的优先级正确；直接入口、run、trigger 一致；dry-run 无写入。
3. 一个真实 journal + ArtifactStore 的成功 Run，文件可解析且注册 hash/size 匹配；能读出 task、实际模型输入、AssistantMessage、工具参数、结果与准确 ToolMessage、最终输出和费用。
4. 工具输出超过 ToolOutputLimits 时，Event 正文或其 ArtifactRef 能读取完整原始结果/stdout/stderr；模型观察仍有界且标记真实截断。关闭导出同样成立；最后一次观察无需等下次模型调用才能出现；不重新执行或事后读取日志补造输出。
5. provider 错误、缺 usage、异常 finish reason、被业务拒绝响应分别保留已知内容，不伪造成功/usage/cost；导出不再次调用 provider。
6. 压缩前后请求上下文不同且均保留；摘要无效/重试有独立 attempt 关联；旧证据不会被新 Task Memory 替代。
7. suspension 导出到固定 sequence；resume 后 Run ID 不变、序列连续、attempt 不重复，生成新文件且旧文件不变；未知 shell 结果仍阻止原有不安全 replay。
8. patch/Evaluation refs 与准确 verdict 可关联；pass-through 不被标成 SWE-bench resolved；已销毁 Workspace 不影响主线内容阅读。
9. Event/Snapshot schema_version 仍为 3，application 定义仍为 1；Runtime/storage/resume/Dashboard 对扩展后的当前契约读写一致。当前契约创建的 Run 可正常恢复，完整证据继续保存；不要求兼容调整前的开发期数据，不新增历史迁移或多版本解码。
10. 64 MiB 边界内成功，超限不注册 trajectory 成品，完整证据仍保留；导出存储失败、超时、重复信号、异常退出不覆盖原 Run 结果/异常/退出码，不制造成功 URI，不打破停止与锁纪律。完整工具正文保存失败则按证据持久化故障停止后续业务，不得混同于非致命的导出失败。
11. 导出不包含 Settings 凭据/HTTP headers/完整 recovery context；正文保持原样，coverage 的 faithful 声明准确。
12. 固定边界所有 Event 序列连续且与 summary 一致；故意损坏的来源必须报错或明确 partial issue，不能通过补全记录掩盖。
13. 每次业务/摘要模型调用的最终正文和工具调用完整保存，包括未被业务接受的响应；provider 另行返回的 reasoning/thinking 字段或推理流均不保存。reasoning token 指标与最终正文中的普通解释仍保留。

组合验证优先使用真实临时 SQLite、真实 ArtifactStore，以及只在 provider 等外部边界使用的可控 fake。本轮是文档修改，不运行运行时测试。

## 12. 后续能力的独立边界

历史 Run 主动导出、JSONL streaming、附件 bundle、脱敏分享、训练数据转换和 Run 对比均需各自需求依据，不作为首版隐藏扩展点。按本设计创建的 Run 即使未开启导出也有完整持久化证据，但首版仍不增加独立历史导出命令。开发期旧数据的迁移或补全不在本次范围内。模型推理过程保存不在本设计范围内。

## 13. 修订后的决策汇总

用户已要求始终完整保存 Event、仅保存模型最终输出而不保存推理过程，并保持现有 schema_version 不变；其余选择采用推荐方案。以下值已统一写入全文，不是实现中的未定分支。

| ID | 选择 | 修订后采用值 | 主要取舍 |
| --- | --- | --- | --- |
| D1 | trajectory 首要意义 | 调试、复盘与行为分析，保留动作和观察 | 暂不交付训练/重放协议 |
| D2 | 导出方式与 resume | 默认不导出；CLI/Settings 开启；application 冻结导出值，resume 继承不可切换 | 开关只影响导出，Event 始终完整保存 |
| D3 | 保存深度 | 始终保存完整规范化模型输入、每次调用的完整最终响应、工具命令/完整输出及精确模型观察，含摘要调用；不保存推理过程文本 | 所有 Run 的存储量增加；大正文由 Event 引用 Artifact |
| D4 | 来源与格式 | 完整证据随 Event 原子提交或引用持久化正文，按导出开关在收尾生成 JSON Artifact | 复用 journal，首版无实时文件流 |
| D5 | 失败和中断 | 尽力导出已提交边界；导出失败独立报告，不改变原退出码 | 使用者需检查 URI/诊断，不能仅凭 exit 0 推断已导出 |
| D6 | 内容与携带性 | 正文忠实保存；配置凭据不导出；附件仅引用 | 主线可离线读，附件不保证随文件可用；不适合未经检查公开分享 |
| D7 | 大文件与超时 | 64 MiB 硬上限；复用 artifact collection timeout；超限失败不截断 | 控制首版内存与功能范围，暂不支持大型 bundle |
| D8 | 首版接口范围 | run/直接入口/trigger 启用与 resume 自动延续；无历史 export 命令、专用 UI | 集中完成一个端到端能力 |
| D9 | schema_version 与兼容范围 | 现有版本不变，直接扩展当前契约，不新增历史迁移或多版本解码 | 项目未上线，不要求兼容调整前的开发期数据 |

下一阶段仅在本文获批准后进入实施计划；实施计划和执行方式另行 review。
