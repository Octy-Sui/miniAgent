# Trajectory 功能需求与设计

日期：2026-10-20

状态：待用户集中 review 的设计草案；本文所有推荐选择均为草案采用值，尚未获实现批准。

## 1. 意图、已知要求与假设

用户已明确：默认不导出 trajectory；运行时通过配置参数启用；先确定 trajectory 的意义和保存内容；设计中优先采用推荐选项，集中在最终文稿 review 决策。

用途假设：使用者首先是 miniAgent 开发者，需要离开实时进程、甚至在 Workspace 销毁后，复盘一次 Run：模型看到了什么、返回了什么、提出了哪些工具调用、工具实际执行了什么、观察到了什么、为何终止、独立验证是否通过，以及消耗多少。本文按此用途设计；训练数据生产不是首版验收目标。

成功标准：拿到一个导出文件即可阅读模型与工具交互主线；每项事实可关联原始 Run、Event、Step、attempt 与 Span；压缩、重试和跨进程 resume 不造成历史混淆；缺失信息明确表示，不能根据最终状态补造历史。

本轮仅交付设计，不修改运行代码、不编写实施计划。用户可在第 13 节集中确认或调整推荐决策。

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
| 开启时补齐 journal 证据，收尾生成 JSON Artifact | 与已有事实同事务保存；跨 resume 连续；导出格式独立演进 | 开启时 SQLite 内容增加，需要兼容扩展 Event | 推荐，本文采用 |
| 仅导出现有 Event/Snapshot | 改动小，可读历史 Run | 缺少逐次准确模型输入与被拒绝响应，无法满足交互复盘 | 不采用 |
| 运行时另写 JSONL | 可实时消费，崩溃后可能留下文件前缀 | 增加事实提交与文件写入的一致性问题，resume/并发/半行处理复杂 | 首版不采用 |

首版优先复用既有提交路径。模型请求完整保存，不新增消息字典、内容寻址数据库或独立采集进程。重复上下文会增加空间，这是明确接受的首版成本；压缩算法和去重存储由实际需求推动。

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
- 启用同时意味着补齐后续完整交互证据并在收尾导出；不是只在最后决定是否复制一份文件。
- 解析后的值映射到冻结的 RuntimeConfig `trajectory_enabled`。Runtime 用于采集门控，application 用同一个冻结值决定导出，不保存两份独立开关。
- resume 继承原 Run 的冻结值，首版不提供中途切换。开启的 Run 恢复后继续采集并生成新导出；关闭的 Run 不能补造过去未采集的请求。
- `resume --dry-run` 不导出、不写 Artifact，不采集新的交互。
- 默认关闭时，不增加完整模型请求/响应等可选证据，不创建 trajectory Artifact，也不增加后台导出任务；现有 Event/Snapshot 保存行为照常保留。
- 输出位置复用 `artifact_root`，CLI 显示 `Trajectory: <uri>`。首版不增加自定义目标文件路径、格式选择和详情等级选项。
- 直接使用 AgentRuntime 的调用者可设置同一 RuntimeConfig 开关采集；自动 Artifact 导出属于 CLI application 装配职责，Runtime 不自行创建 ArtifactStore。

## 6. 具体保存内容

| 内容 | 保存要求 | 复盘价值 |
| --- | --- | --- |
| 文件身份 | trajectory schema version、Run ID、trace ID、导出时间、miniAgent 版本、已提交边界 sequence、最后观察状态 | 明确文件对应哪次 Run、覆盖到哪里 |
| 任务与有效定义 | 完整 task、Agent 指令和身份、provider/model、generation、工具定义与 schema、Runtime 预算、模型容量/输出限制、冻结定价、Workspace 类型与 base revision、Evaluator 类型；benchmark instance/task digest 若有 | 解释行为条件，区分 pass-through 与独立验证 |
| 模型请求 | 每次实际传给 `ModelProvider.generate()` 的规范化 ModelRequest：完整有序 messages、tools、generation、model；业务或摘要用途、attempt ID、关联 Step（若有） | 确定模型可见输入，而非推测最终 conversation |
| 模型响应 | Runtime 收到的完整规范化 ModelResponse，含文本、tool call ID/name/arguments、finish reason、usage、provider request ID、latency、结构化 error；业务接受/拒绝结果及理由 | 保留失败、超长、缺 usage 等未进入正常对话的响应 |
| 请求费用 | 每次已计算 RequestCost、currency；Run 累计 usage/cost、unconfirmed 请求数 | 拆分业务与摘要成本，避免把未知费用算作零 |
| 工具交互 | 原始规范化调用参数、顺序、策略与审批结果、执行 attempt/身份/结束证据、有界 ToolResult、实际追加的 ToolMessage、duration、error、截断标记 | 区分提出动作、许可、执行结果和模型观察 |
| 压缩 | 触发原因、来源 Snapshot/version/sequence、covered messages、每次摘要请求和响应/错误/usage/cost、已发布 Task Memory 与版本关联 | 解释模型上下文变化，保留压缩前历史 |
| 生命周期 | 按 sequence 排列的 Run/Step/operation 事件，重试、中断、suspension、resume attempt、未知结果；Span ID 与父 ID | 保留因果关系，避免把恢复误认为新 Run |
| 输出与验证 | candidate output、final output、termination decision/reason、Evaluation 类型/结果/错误、已保存报告和 patch 的 ArtifactRef | 区分模型作答、Runtime 终态与独立验证 |
| 附件目录 | 依赖 Artifact 的 ID/type/name/media type/size/hash 与原引用 URI | 关联补丁、报告和已有完整日志，不复制附件正文 |

保存的是 provider-independent 规范化协议内容，不承诺 HTTP wire payload、provider 隐式默认值或 SDK 内部重试细节。完整请求意味着 messages 不作二次摘要；规范化 ToolCall 参数不等于原始 JSON 字节。

不新增 reasoning 文本采集。已有 reasoning token 数照常保存；模型公开返回在普通 content 中的解释照常保留。没有提供的内容不能推断或标成已保存。

工具结果遵守现有 ToolOutputLimits，不为 trajectory 绕过限制重新读取文件或重新执行命令。模型实际看到的截断输出就是需要保存的观察；完整 stdout/stderr 仅在已有 Artifact 时引用。没有后续模型请求的最后一个工具观察也必须可读。

### 6.1 明确不保存的内容

不序列化 Settings、凭据、HTTP header、API key、完整环境变量、恢复锁或执行权限令牌。定义采用允许字段列表，只导出复盘所需字段，不复制整个 recovery context；provider endpoint 仅保留已有非凭据身份标识。

task、指令、模型文本与工具结果按实际内容保存，可能包含代码、路径或用户输入的敏感文本。首版不对这些正文自动脱敏，以免破坏复盘准确性；文件声明 `content_policy: faithful`。这不构成可直接公开分享的导出模式。分享脱敏和附件打包是后续独立能力。

## 7. 证据采集与持久化

在已有模型/工具/压缩事件中加入可选且有类型的证据字段，不引入与 Event 平行的业务记录表：

- 请求证据与对应请求开始事件在同一 journal commit 中保存，提交成功后才允许调用 provider。摘要请求沿用摘要 operation attempt identity，不能只用可重复的重试次数关联。
- 请求事件表示“已准备并获准进入调用边界”，不证明远端收到请求。提交后、发送前的取消仍可能留下没有响应的 attempt；不得导出为模型已完成。
- 收到规范化响应后，先保证它进入持久化提交路径，再处理可能拒绝它的 usage/termination 分支。响应证据与其结果事件一同提交；必要时可以先提交响应观察，再提交状态决策，不能只记录被业务接受的响应。
- provider 抛异常且没有 ModelResponse 时，只记录已知错误和 operation 未确认结果；不合成空响应、零 usage 或伪造响应时间。
- 工具完成时，将实际渲染并用于追加 conversation 的 ToolMessage 与已有 ToolEnded/状态变化原子提交；denied/timeout/error 也适用。有 ToolResult 却未产生观察时明确记录观察缺失原因。
- 摘要响应在 Task Memory 校验/发布前保存，因此无效摘要和重试失败仍可解释。已发布摘要从 compaction commit 的事实关联，不用导出时重新调用摘要模型。
- 关闭时这些可选正文证据均缺省，不改变模型调用和工具行为。

采集后的证据属于已持久化 Event。SQLite/journal 写入失败仍遵循既有 RunPersistenceError 语义，停止后续执行，不允许悄悄丢弃证据后继续宣称记录完整。这与最终 JSON 导出失败是不同故障。

### 7.1 版本兼容

新的 trajectory JSON 从 `schema_version: 1` 开始，独立于内部 Event/Snapshot 版本。

当前内部 Event/Snapshot 为 v3。新增证据的 Event 写入采用 v4，并提供 v3/v4 显式解码；新配置进入 Snapshot 后，新 Snapshot 写入采用 v4，保留 v3 解码。旧 v3 数据解码时 trajectory 开关为 false、可选证据为空，不能就地改写旧 Event。若冻结 application 定义本身无需新增字段，不为此单独升级其版本。

必须同步更新 Runtime、storage、resume 和 Dashboard 的版本读取入口。旧版本解码适配不能改变冻结定义原有的 hash 校验口径，不能仅因补入默认字段而拒绝旧 Run。新程序继续读取旧数据；旧程序读取新版本应清楚报告版本不支持，不承诺旧程序继续恢复新 Run。

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
| `events` | 1 到 through_sequence 的有序 Event；包含可选采集证据和原始关联标识，不重排为时间顺序 |
| `artifacts` | 截至导出前已注册且属于该 Run 的附件目录，排除所有 trajectory Artifact |

首版以 events 为规范时间线，不再复制一份 messages/steps/attempts 树。请求中的 messages 已足以独立解释一次模型输入；工具观察和完整响应仍能在原事件中直接找到。Span 父子关系不要求时间区间包含；首版不输出猜测补全的 TraceSpan 列表。

`coverage.complete` 表示既定边界内应采集的证据均存在且 Event 连续，不表示 Run 已成功或到达终态。失败或 suspension 可以有完整的边界导出；缺少规范化响应是已知中断事实时，不等于事件损坏。预期应存在却丢失的证据、未知版本或 sequence 缺口才产生对应 issue，不能以空数组掩盖。

对已经结束但请求无确认结果的情况，文件可完整记录“结果未知”；summary 中 unconfirmed 不归零。跨 resume 的 sequence/attempt/span 全部保留原值，不重编号。

金额采用十进制字符串；缺失值为 null 或明确 issue，不能替换为 0。Artifact 的 hash 标识已注册内容，本次导出不读取全部附件验证可达性，因此附件目录不保证外部附件仍可读取。

## 9. 收尾、导出与错误语义

导出位于 CLI application finalization：先完成已有候选/诊断补丁等持久化动作，再导出 trajectory，再形成 ApplicationResult 和做适用的 Workspace 清理。文件包含 Runtime 结果与已提交的补丁/评测事实；不承诺包含之后发生的 Workspace 清理结果。

成功、失败、取消或 suspension，只要存在 Run 且可以安全读取已提交事实，都尝试导出；bootstrap 在 Run 创建前失败时没有文件。执行异常时，在保留原异常的前提下尝试导出已提交前缀。不得为了导出发起新的业务操作，也不得延迟原有停止/资源清理义务。

一致性要求：在 Run 锁仍由当前执行持有时，以固定已提交 sequence 为边界读取同一边界的 record、events 与所需 Snapshot。所有关联来源必须不晚于边界；不能拿更新后的 summary 搭配较旧事件。正常路径没有并发 writer；异常路径无法证明边界一致时报告导出失败，不生成似乎完整的文件。

校验通过后通过 ArtifactStore.put 注册不可变内容，沿用现有存储的内容与 manifest 完整性保证；不自行覆盖已有 Artifact。只有注册成功，才在结果和 CLI 展示引用。每次 resume 收尾生成新的 Artifact，以各自 through_sequence 标明覆盖范围；此前文件保持原样。附件目录排除 trajectory，避免递归引用历史导出。

首版不增加导出完成 Event：导出产物由 Artifact manifest 发现，避免“导出事件又需要包含在自身导出中”的循环。

导出时间预算复用 `artifact_collection_timeout_seconds` 的配置值作为单次导出上限，并服从更早的 interruption cleanup deadline；不另加配置项。预算已耗尽时直接报告 skipped/interrupted。重复信号或强制结束不保证生成文件，不破坏已提交 journal 或锁释放纪律。

当前 ArtifactStore.put 接收完整 bytes，因此首版 JSON 最大 64 MiB（UTF-8 序列化后的大小）。导出器分页读取固定边界的 Event，逐项编码并累计字节，不能先加载整个事件历史或构建无限制的完整对象。到达上限后，只允许检查剩余内容是否仍可放入上限；超过即停止并报告失败，不截断成不完整 JSON，不把完整性问题藏成成功。只有完整内容在上限内时才向 put 提供 bytes。现有 SQLite 中单个请求记录本身的大小仍受 Runtime 上下文约束，不声称导出总内存精确等于文件上限。此限制不改变既有 Runtime 输入/工具输出限制；开启采集带来的数据库体积增长仍需接受。后续若真实 Run 经常触限，再单独设计流式 ArtifactStore/去重。

导出失败写入独立 `trajectory_export_errors`（新增有类型结果字段），不用现有影响成功判定的 finalization_errors：

- 已有 RunResult/RunSuspension、Evaluation verdict、原异常和现有退出码保持原有语义；trajectory 导出失败只打印明确的 stderr 诊断。
- 例如成功 Run 因磁盘满未导出时，仍按原成功行为返回，同时说明 `TRAJECTORY_EXPORT_FAILED`；失败 Run 的原失败原因不被覆盖。
- 不因 trajectory 导出失败而额外保留 Workspace，也不阻止已有清理；现有补丁/Artifact finalization 故障继续遵循原有规则。
- 不自动重跑模型/工具或无界重试。进程被强制结束后，首版没有独立历史导出命令；journal 中的证据仍保留供后续能力使用。

## 10. 组件职责与依赖

- `runtime`：冻结采集开关，在执行边界生成可选证据，沿用 journal 原子提交。新增 trajectory schema/projector 模块，负责将持久化事实变为有类型的导出对象；不依赖 CLI、Dashboard、Workspace 执行或 provider 网络客户端。
- `storage`：兼容新旧 Event/Snapshot 版本并保持序列与事务约束，不承担导出格式逻辑。优先复用现有分页读接口；仅在一致性读取确实缺能力时补足最小接口。
- `cli`：Settings/flag 装配、trigger 透传、resume 继承、固定边界读取协调、导出预算、Artifact 注册、结果诊断和 URI 展示。
- `artifacts`：复用现有不可变存储契约，不为首版增加 bundle/流式协议。
- `dashboard`：只做新版本读取兼容与既有敏感正文展示边界适配；不新增 trajectory 页面、下载按钮或复用其展示投影作为导出真相来源。

无需新依赖、后台服务、通用 observer 框架或全局日志 hook。规范化证据使用 Pydantic 模型；内部边界协调可沿用现有简单容器。

## 11. 验收与验证范围

后续实现必须以可观察内容验收，而不是只断言某个内部 hook 被调用：

1. 不带配置运行、环境显式 false：没有 trajectory Artifact，也没有新增完整交互证据；现有业务结果相同。
2. Settings true、CLI false 和 CLI true 的优先级正确；直接入口、run、trigger 一致；dry-run 无写入。
3. 一个真实 journal + ArtifactStore 的成功 Run，文件可解析且注册 hash/size 匹配；能读出 task、实际模型输入、AssistantMessage、工具参数、结果与准确 ToolMessage、最终输出和费用。
4. 有界工具输出保存真实截断标记；最后一次工具观察无需等下次模型调用才能出现；不读取未保存的完整日志。
5. provider 错误、缺 usage、异常 finish reason、被业务拒绝响应分别保留已知内容，不伪造成功/usage/cost；导出不再次调用 provider。
6. 压缩前后请求上下文不同且均保留；摘要无效/重试有独立 attempt 关联；旧证据不会被新 Task Memory 替代。
7. suspension 导出到固定 sequence；resume 后 Run ID 不变、序列连续、attempt 不重复，生成新文件且旧文件不变；未知 shell 结果仍阻止原有不安全 replay。
8. patch/Evaluation refs 与准确 verdict 可关联；pass-through 不被标成 SWE-bench resolved；已销毁 Workspace 不影响主线内容阅读。
9. v3 旧 Event/Snapshot/Run 继续可读可恢复，开关默认关闭；v4 由 Runtime/storage/resume/Dashboard 一致解码；不认识的新版本明确失败。
10. 64 MiB 边界内成功，超限不注册成品；存储失败、超时、重复信号、异常退出不覆盖原 Run 结果/异常/退出码，不制造成功 URI，不打破停止与锁纪律。
11. 导出不包含 Settings 凭据/HTTP headers/完整 recovery context；正文保持原样，coverage 的 faithful 声明准确。
12. 固定边界所有 Event 序列连续且与 summary 一致；故意损坏的来源必须报错或明确 partial issue，不能通过补全记录掩盖。

组合验证优先使用真实临时 SQLite、真实 ArtifactStore，以及只在 provider 等外部边界使用的可控 fake。本轮是文档修改，不运行运行时测试。

## 12. 后续能力的独立边界

历史 Run 主动导出、JSONL streaming、附件 bundle、脱敏分享、训练数据转换、reasoning 显式采集和 Run 对比均需各自需求依据，不作为首版隐藏扩展点。尤其是“未开启采集的旧 Run 导出”只能承诺有限证据，不能升级成完整交互历史。

## 13. 集中 review 的推荐决策

以下均已按推荐值写入全文，用户可一次性确认或逐项改动；不是实现中的未定分支。

| ID | 待 review 的选择 | 草案采用的推荐值 | 主要取舍 |
| --- | --- | --- | --- |
| D1 | trajectory 首要意义 | 调试、复盘与行为分析，保留动作和观察 | 暂不交付训练/重放协议 |
| D2 | 开启方式与 resume | 默认 false；CLI/Settings 开启；resume 继承不可切换 | 保证一次 Run 采集口径一致 |
| D3 | 保存深度 | 规范化完整模型输入/响应、精确工具观察，含摘要请求；不新增 reasoning 文本 | 可解释交互，数据库重复上下文增加 |
| D4 | 来源与格式 | 可选证据随 Event 原子提交，收尾 JSON Artifact | 复用 journal，首版无实时文件流 |
| D5 | 失败和中断 | 尽力导出已提交边界；导出失败独立报告，不改变原退出码 | 使用者需检查 URI/诊断，不能仅凭 exit 0 推断已导出 |
| D6 | 内容与携带性 | 正文忠实保存；配置凭据不导出；附件仅引用 | 主线可离线读，附件不保证随文件可用；不适合未经检查公开分享 |
| D7 | 大文件与超时 | 64 MiB 硬上限；复用 artifact collection timeout；超限失败不截断 | 控制首版内存与功能范围，暂不支持大型 bundle |
| D8 | 首版接口范围 | run/直接入口/trigger 启用与 resume 自动延续；无历史 export 命令、专用 UI | 集中完成一个端到端能力 |

下一阶段仅在本文获批准后进入实施计划；实施计划和执行方式另行 review。
