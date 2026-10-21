# Trajectory Test Design

日期：2026-10-20。状态：测试设计稿，未实现、未执行；不是发布批准或产品验证结论。

依据：[Test Analysis](2026-10-20-trajectory-test-analysis.md)（TA）、[功能需求](2026-10-20-trajectory-design.md)（D）、[架构设计](2026-10-20-trajectory-architecture-design.md)（A）。AC1–AC13 对应 D§11 编号，T01–T20 沿用 TA。用户重复提供的架构文件按一份处理，功能需求由其显式引用补入。

## 1. 测试设计计划

| Unit / 义务 | Source | Logic | Technique | 选择原因 / 候选最小范围 |
| --- | --- | --- | --- | --- |
| U01 / T01 | AC1、D§5 | 开关只改变导出 | 变形关系 | 对照真实存储的成对 Run |
| U02 / T02 | AC2、D§5 | 三态覆盖与冻结继承 | 决策表 | 小规则表＋入口装配 |
| U03 / T03 | AC3、5、13、A§5.1 | 响应接受与计费条件 | 决策表 | 真实 journal；外部 provider stub |
| U04 / T04 | AC5、7、A§5.2 | 业务提交与恢复 | 状态转换 | 真实事务及跨进程恢复 |
| U05 / T05 | AC6、A§5.2 | 摘要 attempt 与发布不同步 | 状态转换 | 真 Snapshot 与 journal |
| U06 / T06 | AC5、13、A§5.3 | 协议失败分类 | 等价分区 | provider 适配器＋外部 fixture |
| U07 / T07 | AC4、A§6.1 | 原结果/归属与大小阈值 | 等价分区＋BVA | ToolRunner、真实 ArtifactStore |
| U08 / T08 | AC4、A§6.1 | 许可与实际执行身份 | 状态条件分区 | Workspace 执行边界 |
| U09 / T09 | AC4、A§6.1–2 | 字节保真与预览 | 数据分区＋属性 | 真 Local pipe / Docker guest |
| U10 / T10 | AC4、A§6.1 | 观察原子提交 | 结果分区＋故障 | 工具完成到 conversation |
| U11 / T11 | AC10、A§6.3 | 必需证据故障 | 故障分区 | 实际文件/SQLite 边界 |
| U12 / T12 | AC10、A§6.3 | 发布与取消状态 | 状态转换 | 真实线程 I/O、锁与进程 |
| U13 / T13 | AC9、A§7 | 当前契约消费者 | 契约分区 | 公开调用者与读取端 |
| U14 / T14 | A§7.1、10 | 有界历史读取 | 规模实验＋结构约束 | 全受治理消费者 |
| U15 / T15 | AC7、12、A§7.1 | 坏来源与旧执行链 | 无效分区 | 实际压缩/resume/诊断 |
| U16 / T16 | AC7、12、A§8.1、8.3 | 结构可信与证据完整 | 决策表 | 固定 N 的投影 |
| U17 / T17 | AC8、A§8.2 | 目录/附件/verdict 分类 | 等价分区 | manifest 与交付 |
| U18 / T18 | AC10、A§8.4 | 字节上限与总预算 | BVA＋时间条件 | 编码/发布边界 |
| U19 / T19 | AC3、7、8、10、A§9 | 收尾规则与用户旅程 | 决策表＋场景 | application/CLI 跨进程 |
| U20 / T20 | AC11、13、A§5.3、8.3 | 禁止字段与允许正文 | 哨兵分区 | 采集、存储、导出全链 |

规则、状态、字节边界、数据分类分别建模：仅 happy path 不能观察提交中取消；仅状态路径会漏掉大小等值、错误 ID 和禁止字段。已知竞态不做 pairwise 缩减。T14 是资源实验，不虚称黑盒用例可单独证明对象生命周期；需要运行测量与实现约束核查两类证据。

## 2. 来源、假设与执行前缺口

本稿由 Codex 在本会话基于规范生成，AI 角色为分析、设计和文档编写。先从 D/A 提取 oracle，再参考现有计划定位缺口；没有从新功能实现推导 expected。人类编辑记录：本次未提供。当前 HEAD 为 `02b42a9b24b4f0d8f4fbae27c20e735fba7fb53a`；输入含工作区修改，SHA-256：

```text
D  5041c49975278986bd114ad1568ff5b14158e79419ca5ddf6b8aeae2c594adaa
A  f4ae74eb64edbd15469a1b04404633c60119e899038c9fb05c6710f2f96a20ce
```

| ID | 假设 / 未决项 | 影响 |
| --- | --- | --- |
| ASM-1 | 表内每个显式 variant 是独立测试实例；每次只破坏一个条件，除标记 robustness 的组合 | 避免缺陷遮蔽；实例 ID 为 TC-Uxx-nn[variant] |
| ASM-2 | A 与 D 细节不同时采用 A 推荐方案；状态仍为待最终接受的候选 Portfolio | 全部，不擅自修改源文档 |
| ASM-3 | 资源规模、token 数与单价是合成测试数据，不是生产容量/定价要求 | U03–05、U09、U14 |
| Q-1 | A 状态与 R3 结论未统一；待架构负责人登记最终接受状态 | 冻结 Portfolio，不阻碍编写场景 |
| Q-2 | 真实 Docker、宿主平台、执行负责人尚未核实/指派 | U08–09、U12、U19 的环境证据 |
| Q-3 | 实际事务后段/文件发布屏障未实现验证 | 未命中窗口不得计入提交/竞态验证 |
| Q-4 | 资源数值阈值、墙钟误差未定义 | 记录趋势；量化性能验收 DEFER |
| Q-5 | 部分新错误枚举/issue code 尚待契约实现 | 验证结构化原因语义，不编造字符串；实现前登记具体码 |
| Q-6 | 实现后的独立验证者未指派 | 文档独立核查不能替代实现验收 |

## 3. 公共夹具、动作与观测

### 3.1 夹具

- F：每例独立的真实 SQLite 文件、FileArtifactStore、临时 Git Workspace、Run UUID；固定 UTC 导出时刻；其他 fixture 均派生于合法 F。进程恢复用新连接，不能依靠外层测试事务 rollback 制造“持久化”。
- M：受控 provider stub，按公开 ModelRequest/ModelResponse 契约提供返回，外部日志记录调用输入与 attempt。代表业务返回/异常，不模拟 SDK 重试或网络收费。Qwen 另用实际适配器消费当前 SDK/协议 fixture 作为后盾；不在线调用真实模型。
- C：无 cache 计费 fixture，input=10、output=5，输入每百万单价1、输出每百万单价2，每次已确认费用手算 `0.000020`；两次为 `0.000040`。金额按 Decimal 数值核对，JSON 必须为十进制字符串，不强制未承诺的尾零格式。缺 usage、无 pricing 和计算失败分别保留原因，不填零。
- B：预先生成的 bytes：ASCII、`A中🙂B` 的 UTF-8、`00 ff 80` 二进制、空流、独立 stdout/stderr 标记。预期 hash 从输入 bytes 独立计算。大流用可重放生成器，不先在被测采集进程构造全部 bytes。
- H：合法完整历史，包含多 Step、两个压缩版本、一次重试、旧执行链、当前工具结果、patch/evaluation refs。损坏 fixture 一次改变一项；原始 SQLite/manifest 保留副本供对照。
- S：每个禁止字段独立合成哨兵；允许正文用不同哨兵。不得把同一哨兵同时放在正文与秘密字段后再做全局“不出现”断言。

所有 fixture 有测试/worker 独立目录及容器名，固定随机 seed（若生成）、可复现字节/hash、输入版本和清理记录。真实 Docker 使用隔离的开发任务上下文；不能用缺 task context 的普通 CLI Docker 运行代替。无环境记未执行，不以 stub 通过冒充真实 Docker。

### 3.2 公共动作和观测

表中 Given/When/Then 是可实施测试规格，不是生产 hook 名称。每例先构造合法前置状态，只施加指定 stimulus，再由外部边界或新连接检查 outcome。持久化类用例检查 Event、State/Snapshot、Artifact 引用三者关系；只检查事件数量不够。

`NO-NEXT`：指定故障之后无新的业务 provider 请求、工具执行或 evaluator 调用；允许既有监督/排空及该故障允许的收尾。`NO-EXPORT`：无新增 run_trajectory manifest/CLI URI，允许架构规定的未注册正文或孤立附件。`UNCHANGED`：原结果/异常语义、退出码及既有 Workspace 清理/保留策略与同场景关闭导出的对照一致。

跨进程中断步骤：建立握手 → 确认实际窗口 → 发信号或终止父进程 → 等监督/事务收敛（若父进程存活）→ 新进程 resume → 新连接查询 → 对照外部调用和命令副作用日志。正常测试不以强杀绕过应验证的 drain。

| 屏障 | 语义位置 | 需要保留的证明 |
| --- | --- | --- |
| B-request | 请求已提交、尚未实际调用 provider | DB 可读请求；外部 provider 日志为空 |
| B-response | provider 已返回、业务响应尚未提交 | 外部返回记录；DB 只有请求 |
| B-tx | 事务已开始写入、最终 commit 尚未决定 | 实际 SQLite 写入/锁/故障证据；不得用入口前抛错替代 |
| B-attempt | 摘要响应/费用已提交，Task Memory 未发布 | DB 可见 attempt，Snapshot 仍为旧版本 |
| B-command | 命令身份提交后、实际工作命令未放行 | 身份事实与外部副作用日志 |
| B-body | 正文已发布、manifest 未发布 | 正文路径存在，manifest 不存在 |
| B-publish | 有限原子发布已开始 | 文件边界握手，发布 drain 与 RunLock 时间线 |

只允许外部 provider、文件系统、数据库驱动/连接、OS/guest 协议等边界提供故障或观测。优先利用现有公共契约；不 patch Runtime 私有方法或以调用次数替代业务 oracle。难以可靠到达的窗口记录 Q-3，不让测试通过即声称窗口已覆盖。

## 4. 具体 Test Design

### U01：开关与证据采集

变形关系 MR1：相同受控业务输入/输出，将 export 从 false 改为 true，移除合法变化的身份/时间后，完整请求、响应、原工具证据和观察的语义相同；产物差异仅为 trajectory 及其独立收尾诊断。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U01-01 | MR1；T01/AC1 | F、M，大于预览限制的工具双流 | 分别关闭/开启导出执行同一脚本 | 两侧均可读取完整模型/工具证据和有界观察；仅 true 有 trajectory；按关联关系比较，不要求 UUID 相同 |
| TC-U01-02 | 默认/显式 false；T01/AC1 | 独立环境，未设置开关或环境 false（两变体） | 执行同一 Run | 完整采集仍在；无 trajectory、无后台导出 |

### U02：配置决策表

完整规则空间：CLI={未提供,true,false} × Settings={false,true}，6 列，无折叠。Settings 的默认/环境/.env 来源另验证，不把来源当成不同布尔语义。

| 条件/动作 | R1 | R2 | R3 | R4 | R5 | R6 |
| --- | --- | --- | --- | --- | --- | --- |
| CLI | 未提供 | 未提供 | true | true | false | false |
| Settings | false | true | false | true | false | true |
| 冻结 export_trajectory | false | true | true | true | false | false |
| TC-U02-01 variant | r1 | r2 | r3 | r4 | r5 | r6 |

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U02-01 | R1–R6；T02/AC2 | 每列独立 F；入口={直接,run,trigger} | 运行对应入口 | 冻结值及实际导出行为等于表；18 个独立实例，不宣称 pairwise |
| TC-U02-02 | 来源分区；T02/AC2 | default、env true/false、.env true/false 独立变体；无其他配置源 | 不给 CLI flag | 默认 false；显式值按其值生效 |
| TC-U02-03 | 冻结继承；T02/AC2、7 | 原 Run 导出 false/true；当前 Settings 反转 | 新进程 resume | 继承原值，继续完整采集；不被当前 Settings 改写 |
| TC-U02-04 | 只读/非法切换；T02/AC2 | 当前 Run；dry-run 或 resume 加不支持的切换 flag，分别运行 | 调用 CLI | dry-run DB/manifest/业务调用不变；不支持 flag 按 CLI 解析拒绝，无写入 |

### U03：业务请求与响应

下表是受约束扩展决策表：usage 缺失时 pricing 不影响费用结论，折叠为 `—`；只对可规范化 ModelResponse 适用。协议无效独立放 U06。业务合法性之外的意外程序异常不合并为预期拒绝。

| 条件/动作 | R1 | R2 | R3 | R4 | R5 | R6 |
| --- | --- | --- | --- | --- | --- | --- |
| usage | 有效 | 有效 | 缺失 | 有效 | 有效 | 有效 |
| pricing | 有 | 无 | — | 有 | 有 | 有但可预期计算失败 |
| 业务判定 | 接受 | 接受 | 缺 usage 拒绝 | finish reason 不一致 | 重复 tool call ID | 失败 disposition |
| 响应完整保存 | 是 | 是 | 是 | 是 | 是 | 是 |
| 费用 | C | null＋无 pricing | null＋缺 usage | 已确认费用 C | 已确认费用 C | null＋计算失败原因 |
| 后续 | 正常推进 | 正常推进 | 不执行拒绝响应中的工具/评估 | 同左 | 同左 | 同左 |

R6 的可到达输入需按实际公开计费契约构造；若所有计算异常均为意外程序错误，不伪造业务分支，登记 Q-5 并保留 A§4 的缺失原因契约测试。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U03-01 | 请求保真；T03/AC3 | 包含 system/task/历史/工具定义/generation 的请求 | 完成一次调用 | 请求 Event 等于 M 收到的完整规范化请求；provider 标识/attempt 对应 |
| TC-U03-02 | 请求 commit 失败；T03/AC5 | 请求事务实际写入后故障 | 执行请求路径 | 新连接无半提交开始/active operation；provider 日志为空 |
| TC-U03-03 | R1–R6；T03/AC5、13 | 每列独立响应，正文及工具参数有哨兵 | 返回并完成响应事务 | 响应原样、disposition、已确认 usage/cost 与 State 同边界；重复摘要字段一致；拒绝不执行动作 |
| TC-U03-04 | 意外异常；T03/A§5.1 | 响应已返回，判定过程意外异常可由公开边界诱发 | 传播异常 | 不要求抢救提交；无新增 pending response；保留已提交请求与原异常，不伪造响应成功 |

### U04：业务事务与恢复状态

状态是可观察的持久证据边界，不要求新增产品状态枚举：M0 无请求，M1 请求已提交但响应未提交，M2 响应/disposition/费用已原子提交。开始态 M0；M2 为本次 attempt 的提交完成态。resume 是整个 Run 的动作，M1 的新调用必须建立新 attempt。

| 状态 \ stimulus | 请求提交 | 响应事务提交 | 中断后恢复 |
| --- | --- | --- | --- |
| M0 | M1 / MT1 | 前置条件不成立；不直接注入非法内部调用 | 无本 attempt 结果可恢复 |
| M1 | 新 attempt 的决策由既有 planner，不能重用本 ID | M2 / MT2 | 保留本 attempt 未确认；允许继续时新 attempt / MT3 |
| M2 | 后续业务可新建 attempt，不能覆盖旧记录 | 重复结算禁止 / MI1 | 按已有 disposition，消息/收费不重复 / MT4 |

| ID / 路径 | Covers / Trace | Given | When | Then |
| --- | --- | --- | --- | --- |
| TC-U04-01 / MP1 | MT1→MT3；T04/AC7 | B-request | 中断并恢复到允许新请求的场景 | 旧 attempt 无响应且未确认；首次真实外部调用使用新 ID；不称远端已完成 |
| TC-U04-02 / MP2 | MT1→MT3；T04/AC5、7 | B-response，M 已返回 C | 终止进程后恢复 | 旧响应不在 DB；未知费用不填零；新请求新 ID；账本只累计后来已确认 C |
| TC-U04-03 / MP3 | MT2/MT4 或 MT3；T04/AC7 | B-tx，分别控制 rollback 和 commit 两结局 | 写入中取消，排空后恢复 | rollback 如 M1；commit 如 M2；不得从 CancelledError 推断回滚 |
| TC-U04-04 / MP4 | MT1→MT2→MT4、MI1；T04/AC7 | 响应已提交，尚未进入工具/评估 | 中断、恢复、再次只读检查 | 旧消息一次、C 一次；继续既定 disposition；不重请求已确认响应、不重复收费 |

每步都检查 Event/State/Snapshot，而非只看最后 Run status。目标是列出的 MT1–MT4 与 MI1；不是穷举全部 runtime 状态。

### U05：摘要 attempt 与发布

状态：S0 尚无摘要请求；S1 请求已提交；S2 响应/本次结算已提交但未发布；S3 Task Memory 发布；S4 本次校验失败已记录。S3/S4 为本 attempt 结果边界。

| 状态 \ stimulus | 请求 | 返回并记录 attempt | 校验/发布 | 恢复 |
| --- | --- | --- | --- | --- |
| S0 | S1 / ST1 | 不适用 | 不适用 | 沿既有状态 |
| S1 | 不重用 ID | S2 / ST2 | 前置事实未就绪 | 原 attempt 未确认；按既有规则新请求 |
| S2 | 重试必须新 ID | 不重复结算 | S3 / ST3 或 S4 / ST4 | 不把保存响应当已发布 / ST5 |
| S3 | 后续压缩新 ID | 不重复结算 | 不重复发布同版本 | 使用已提交版本 / ST6 |
| S4 | 新 attempt / ST7 | 不覆盖旧响应 | 本失败响应不发布 | 沿既有重试规则 |

| ID / 路径 | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U05-01 / SP1 | ST1→ST2→ST3；T05/AC6 | 合法摘要与 source Snapshot/version | 触发压缩及下一业务请求 | 摘要 request/response 同 attempt；前后请求分别保留旧/新上下文；发布关联准确 |
| TC-U05-02 / SP2 | ST4→ST7→ST2→ST3；T05/AC6 | 第一次为无效 JSON/来源无效/预算不合格，分别运行；第二次合法；每次 C | 重试成功 | 首次响应及失败仍可读；两个独立 attempt；累计 `0.000040`；仅第二个发布 |
| TC-U05-03 / SP3 | ST5；T05/AC6、7 | B-attempt | 中断恢复；fixture 选择既有规则要求重新摘要的路径 | 旧响应/费用保留，旧摘要不生效；新请求新 ID；两次确认费用各一次，不从旧响应新增本地恢复协议 |
| TC-U05-04 / SP4 | ST6；T05/AC6、7 | 已发布摘要 | 中断恢复 | 使用发布版本/hash/保留消息；不重复发布或结算；历史原请求/响应未复制进后续聚合 payload |
| TC-U05-05 | 摘要返回分区；T05/AC5、6、13 | 用途 summary；缺 usage、有效 usage 无 pricing、provider arguments/usage 规范化失败、无 response 连接错误，分别运行 | 执行摘要 attempt 并读取其持久证据 | 可规范化响应完整保留；费用缺失分别明确原因；协议失败走互斥白名单证据，未验证 usage 不计费；无 response 不伪造响应；这些失败事实不冒充已发布 Task Memory；若重试使用新 attempt，旧证据保留 |

### U06：provider 等价分区

分区按可观察协议结果划分，不把所有异常视为同类：E1 有效规范化；E2 收到响应但 arguments 无效；E3 usage 无效；E4 finish reason 无法规范化；E5 未收到 response 的连接错误。E4 若当前适配契约允许原字符串进入 ModelResponse，则进入 U03 的业务拒绝分支；不可任意选择以让测试通过，实施时须登记公开契约归属。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U06-01 | E1；T06/AC13 | 合法最终 content＋独立 reasoning | 实际 Qwen 适配器规范化 | ModelResponse 完整最终内容；无 reasoning 正文；reasoning token 计数保留 |
| TC-U06-02 | E2/E3/E4，独立变体；T06/AC5、13 | 原 arguments、finish reason、request ID、usage 原字段各有标识 | 适配器处理并经 Runtime 提交拒绝 | 无有效响应时走互斥失败证据；可用白名单字段保留；无 ToolRegistry 执行、无未验证账单 |
| TC-U06-03 | E5；T06/AC5 | 连接边界抛无 response 错误 | 执行请求 | 只有已知错误和未确认事实；无空 ModelResponse、伪响应时间、零 usage |
| TC-U06-04 | 禁止全量 dump；T06/AC13 | SDK 独立 reasoning/header/credential 哨兵 | 规范化失败 | 失败证据及错误字符串无禁止字段；不调用通用 SDK dump 采集证据 |

### U07：原始工具结果与 64 KiB 边界

分区：E1 合法匹配结果；E2 可序列化 ToolResult 但 ID 不匹配；E3 可序列化但错误码违反业务约束；E4 非 ToolResult 任意对象；E5 工具抛异常。E2/E3 的 schema 本身必须合法；不能在对象构造时就失败而声称覆盖 ToolRunner 的拒绝。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U07-01 | E1；T07/AC4 | 原正文含超出模型观察上限的文本 | 完成调用 | raw 保留完整；有效 result/ToolMessage 有界，原大正文不在旧 result 字段重复内联 |
| TC-U07-02 | E2/E3 × inline/artifact；T07/AC4 | 错 ID 或非法错误码；小/大两个大小等级 | 完成调用 | 原错误字段/正文不变；外层归预期 Run/Step/call/attempt；模型只收 INVALID_TOOL_RESULT；错误 ID 不选目录或恢复归属 |
| TC-U07-03 | E4；T07/AC4 | 任意对象，repr/reduce 若调用则在外部测试日志留痕 | 返回该对象 | unsupported_result_type、合成错误观察，无原 ToolResult；日志证明未调用任意 repr/pickle |
| TC-U07-04 | E5；T07/AC4 | 工具抛允许传播/翻译的异常 | 执行 | 按既有错误契约，无伪造 raw ToolResult |

边界库存：实际原 ToolResult 的 UTF-8 JSON 大小 n；有效可表示大小内 n≤65,536 内联，n≥65,537 Artifact；两分区均为合法业务结果，步长1 byte。空 JSON 不是合法 ToolResult，不凭空增加 n=0 用例。

| ID | Covers / Trace | Given / When | Then |
| --- | --- | --- | --- |
| TC-U07-05 | BV 65,535；T07/A§6.1 | 调整 ASCII padding，使完整合法序列化结果恰为该大小，再执行 | 内联且内容完整 |
| TC-U07-06 | BV 65,536；T07/A§6.1 | 同上，恰好等值 | 内联且内容完整 |
| TC-U07-07 | BV 65,537；T07/A§6.1 | 同上，超过1 byte | tool_result Artifact，重开读回等于原值；旧 result 不重复正文 |
| TC-U07-08 | 编码单位；T07/A§6.1 | 多字节字符与需转义字符构成超阈值 JSON，字符数低于阈值 | 按最终 UTF-8 JSON bytes 选择 Artifact，不按字符数或裸正文大小 |

### U08：命令事实与执行归属

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U08-01 | 已描述未放行；T08/AC4 | B-command；Local/Docker 两变体 | 取消且不放行 | 命令 argv/cwd/身份可查；未观察开始则 not_started/start_unknown，不能标已执行；无完成空流 |
| TC-U08-02 | writer 创建失败；T08/AC4、10 | 合法命令会向外部日志追加标记；创建 writer 故障 | 请求执行 | 不放行；无副作用标记；持久化故障而非普通工具失败 |
| TC-U08-03 | 多执行；T08/AC4 | 一个工具依次执行两个工作命令 | 成功完成 | 同 tool attempt 下两个独立 execution_id，argv/cwd/stream refs 各自对应 |
| TC-U08-04 | 非 Agent 命令；T08/AC4 | Workspace 生命周期、patch、evaluator、diagnostic 四变体 | 分别触发 | 保持各自 operation 归属；不伪装 Agent Tool Call；无 launcher 控制参数/完整 env |

### U09：完整输出与有界观察

每种数据各有独立失效机理，不互相声明等价：字节损坏、字符边界、空流/未启动、双 pipe 饥饿、Docker 控制帧。所有流使用相同“收到 bytes 与证据 bytes 相等”的属性；完整性以实际 EOF 与持久化为前提。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U09-01 | 大 stdout/stderr；T09/AC4 | 两流各大于配置预览上限，内容独立；Local/Docker | 执行一次 | 两个完整 Artifact bytes/hash/size 匹配 B；预览有界且截断标记真实；外部执行日志只有一次 |
| TC-U09-02 | 二进制/跨 chunk；T09/AC4 | `00 ff 80`、`A中🙂B` 在字符中间分 chunk，独立变体 | 写入接收路径 | 正文字节无损；替换解码只影响预览；hash 按 bytes |
| TC-U09-03 | 空流；T09/AC4 | 已启动命令双流为空、仅 stderr 有内容，独立变体 | 等真实 EOF 完成 | 两 writer 均有已完成 ref；空流 size=0；不同于未启动 |
| TC-U09-04 | 双流背压；T09/AC4 | 子进程交替大量写 stdout/stderr，store 慢速受控边界 | 排空并结束 | 无单流堵塞造成的无限等待；收到字节均保存；没有无限 chunk 队列 |
| TC-U09-05 | Docker 协议；T09/AC4 | 合法控制帧与业务 data 混合；另例无效 frame | guest 接收 | 合法只存解码业务字节；无效帧沿协议错误处理，不当 stdout；真实 Docker 后盾另保留 |
| TC-U09-06 | 禁止补取；T09/AC4 | read_file 读取后源文件改变；shell 有一次性外部标记 | 完成证据保存 | 保存首次实际收到内容，不重新读文件或执行命令；read_file 原截断语义保持 |

### U10：精确观察

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U10-01 | 正常/denied/invalid args/timeout/error；T10/AC4 | 各独立工具结果，之后不再调用模型 | 完成允许的工具结束事务 | Event.observation 与实际 conversation ToolMessage 字段/正文完全相同；无下一请求仍可读；未执行无伪命令输出 |
| TC-U10-02 | 事务失败；T10/AC4 | 工具返回后 ToolEnded/State 事务实际后段失败 | 重开 DB | 不出现仅 ToolMessage 或仅 ToolEnded 的半提交；大正文可孤立但不是已提交观察 |
| TC-U10-03 | 未产生观察；T10/AC4 | 原结果可保存但观察未产生的已定义失败路径 | 收敛并读取 | 若有已提交结束事实则给明确 missing reason；不事后用新 renderer 补造 ToolMessage |

### U11：必需持久化故障

故障分区按失败位置区分，不能只测试一个通用 “storage error”。F1 write；F2 finish/正文发布；F3 manifest 发布；F4 Artifact 成功后 Event commit；F5 文件与 DB 同时失败（robustness）。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U11-01 | F1/F2/F3；T11/AC10 | 活跃命令继续产出；逐个外部故障 | 保存失败 | NO-NEXT；传播证据持久化错误；按监督规则停止/确认执行链；有预算可存失败事实 |
| TC-U11-02 | F4；T11/AC10 | 完整正文与 manifest 成功 | Event 事务失败 | 附件可保留但不成为已提交结果；不重跑；导出若列出则 registered_only |
| TC-U11-03 | F5 robustness；T11/AC10 | 文件写失败后 DB 也不可写 | 收敛 | 报已知错误、停止后续业务；不要求失败 Event 一定存在，不用截断结果冒充完整 |
| TC-U11-04 | raw_result 保存失败；T11/AC10 | 合法大 ToolResult 的 Artifact 保存失败 | ToolRunner 处理 | 不降级为 INVALID_TOOL_RESULT 后继续；保持持久化故障语义 |

### U12：取消与发布状态

writer 状态为测试观测阶段，不要求产品新增状态：W0 准备/写入，W1 已进入有限原子发布，W2 manifest 已发布，W3 已取消/未注册。W0 为开始态，W2/W3 为该次操作结果；仅正文已存在仍属未注册。

| 状态 \ stimulus | 进入发布（有预算） | 取消/截止 | 发布成功 |
| --- | --- | --- | --- |
| W0 | W1 / WT1 | W3，abort 不启动新发布 / WT2 | 不直接跨越发布 |
| W1 | 已在发布，不重复启动 | drain，确认后 W2 或 W3 / WT3 | W2 / WT4 |
| W2 | 不再次发布同次操作 | 保留 ref，exported / WT5 | 已发布结果保持 |
| W3 | 本次已结束；再次 finish 行为未规定，不测试猜测契约 | 不启动后台注册 | 不允许迟到注册 |

| ID / 路径 | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U12-01 / WP1 | WT2；T12/AC10 | W0，writer/trajectory put 两变体 | 取消或预算耗尽 | abort；无新增 manifest；无锁外后台发布；已收前缀若不能 finish 则明确缺失 |
| TC-U12-02 / WP2 | WT1→WT3→WT4；T12/AC10 | B-publish，store 成功结局 | 取消，再次信号，释放屏障 | drain 确认 manifest；结果 exported/ref，原中断继续；另一进程此前不能取得锁，此后无遗留注册 |
| TC-U12-03 / WP3 | WT5；T12/AC10 | W2 | 取消 | 不报“没有成品”；ref 可读；不破坏原清理/中断语义 |
| TC-U12-04 / WP4 | WT3→W3；T12/AC10 | B-body 后 manifest 发布故障 | 取消并收敛 | 可有未注册正文，但无成功 URI；失败/跳过，不冒称 exported |
| TC-U12-05 | 流 completeness；T12/AC10 | timeout/取消导致提前停止 drain；另例已真实 EOF 且全部写入 | 分别收尾 | 前者 partial＋原因/字节数/EOF=false；后者可 complete；进程退出不自动等于流完整 |

上述路径覆盖 WT1–WT5 及禁止迟到注册；不声称覆盖未定义的 writer 重入 API。

### U13：当前契约与消费者

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U13-01 | 缺 store/artifact store；T13/AC9 | 直接构造 Runtime，分别缺一个必需依赖 | 构造 | 执行前失败；无 provider/工具；不自动创建 DB 或回退内存 |
| TC-U13-02 | 当前契约 round trip；T13/AC9 | 新 Run 含完整证据，合法冻结 hash | storage 写/读、resume、Dashboard 展示 | Event/Snapshot=3，application=1，trajectory=1；当前内容可读与恢复，正文展示边界保持 |
| TC-U13-03 | 定义/身份不匹配；T13/AC9 | 当前格式，分别篡改 hash 或 Run 身份 | resume | 原安全拒绝仍生效；不得以同版本号绕过检查 |
| TC-U13-04 | 旧开发期不匹配；T13/AC9 | 保存旧格式只读 fixture 及原 bytes | 尝试读取 | 若不兼容则明确当前契约不匹配；不静默补字段宣称完整，不迁移/删除/改写原数据 |
| TC-U13-05 | 装配调用者；T13/AC9 | CLI bootstrap/resume、直接调用、自定义 Workspace/store fixture | 走各公开构造与调用契约 | 所需依赖一致且输出 writer 使用注入 store；类型检查补充调用者漏改，不代替行为验证 |
| TC-U13-06 | Snapshot 职责；T13/A§4 | 多个已提交请求/拒绝响应/完整工具输出，后续继续产生 Snapshot | 读取后续 State/Snapshot 与对应 Event/Artifact | Snapshot 只有当前 Working Context/disposition 等恢复所需内容，不额外复制完整证据历史列表；原完整证据仍可从 Event/Artifact 读取；不把正常当前上下文误判为历史复制 |

### U14：历史资源与受治理消费者

受治理集合 G 由 A§7.1 及实际调用者审计共同维护；新增调用者必须显式加入，不能只有固定手写清单而不检查遗漏。

| G ID | 消费者 / stimulus | 必需保留 / 禁止保留 | 最小可观察业务结果 |
| --- | --- | --- | --- |
| G1 | context 来源链和多 EventSource/SnapshotSource 校验 | 紧凑来源索引、单事件/所需 Snapshot；不每条 evidence 全历史重扫 | 合法引用通过，越界/错字段拒绝 |
| G2 | validate_compaction_record 恢复链 | 当前 source/new 两份 Snapshot，不整链缓存 | 版本/hash/范围一致通过，坏链拒绝 |
| G3 | CLI resume inspect、history_facts | 必要身份/权限/结束/中断/有界当前结果 | 保持 planner 结论和 sequence 连续性 |
| G4 | loop.resume、prepare_request | 匹配 ResumeStarted 计数、压缩选择紧凑字段 | 唯一恢复开始匹配、选择一致 |
| G5 | require_operation_ended、record_component_fact | 从开始 sequence 到 N 的身份/结束折叠 | 结束身份正确才允许后继事实 |
| G6 | diagnostic finalization | 含旧未知链的紧凑 facts | 旧未确认链仍阻止诊断 |
| G7 | Dashboard 及其他 list_events/等价 SQL 调用者 | 有界页面/单正文，Span 不缓存全部 payload | 当前契约显示与敏感正文边界保持 |
| G8 | list_tool_results、CompactionFailed/Completed 聚合 | 有效有界结果；引用 attempt/费用，不重复完整请求 | 恢复结果正确，聚合无巨型 raw 副本 |

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U14-01 | G1–G8，逐消费者；T14/A§7.1 | H，导出关闭；完整 Event 数=15/16/17/32/33，独立实例 | 执行对应 stimulus | 完整 payload 每页≤16；固定 N；业务结果正确；不因筛选有间隔而误报缺口 |
| TC-U14-02 | 正文规模轴；T14/A§7.1 | 固定合法事件数128，历史请求正文分别16/64/256 KiB；单对象仍在合法上下文预算；仅实验数据 | 独立进程分别执行 G1–G8 | 记录峰值/存活对象/读取量；排除全历史正文驻留与跨页缓存；数值趋势证据不单独转成固定阈值 verdict |
| TC-U14-03 | 数量规模轴；T14/A§7.1 | 单事件正文固定，合法历史数128/512/2048；准备进程分离 | 同上 | 允许必要紧凑元数据增长；不保留全部 Event/Snapshot；附对象/查询审计区分两种增长 |
| TC-U14-04 | 多引用/SQL 筛选；T14/A§7.1 | 同次摘要多 evidence；混合大量模型事件与少量压缩事件 | 校验来源、取有界有效结果 | 来源索引同次共享，定点查事件；类型筛选在 SQL 取行执行；不解码全部 payload 再筛，不读取 raw 大正文以获取有效结果 |
| TC-U14-05 | 单对象限制；T14/A§7.1 | 一个很大但合法 Event/Snapshot | 执行相关查询 | 不截断内容规避峰值；报告该对象峰值，不误判为历史累积 |

结构核查读取实现/查询计划，资源实验另记录 Python/OS/SQLite 版本、输入总 bytes、最大对象、索引条数、peak RSS 和测量误差。没有明确阈值前，资源量化结论为 DEFER；发现 read-all/无限缓存可直接判违反架构约束。8 MiB/64 MiB shell 流另外测量，不在采集进程读全量 Artifact 验证 hash。

### U15：分页安全反例

分区按不同拒绝规则列出，均单独修改合法 H；不能将所有坏字段放同一 fixture。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U15-01 | sequence 缺口；T15/AC12 | 分别删除第16或17条事件 | 完整历史 resume/检查 | 拒绝不连续；类型筛选不能掩盖缺口 |
| TC-U15-02 | 压缩链；T15/AC6、12 | 重复版本、断链、循环三变体 | 校验/恢复压缩 | 各自拒绝，不因紧凑索引覆盖键而通过 |
| TC-U15-03 | EventSource；T15/AC12 | 跨 Run、sequence>N、不允许 Step、field/excerpt 不符四变体 | 校验证据 | 拒绝对应无效来源；不全历史搜索后误认相似文本 |
| TC-U15-04 | SnapshotSource；T15/AC12 | 缺 Snapshot、hash 错、版本错、保留消息错四变体 | 校验发布/恢复 | 按原安全规则拒绝，不能补造来源 |
| TC-U15-05 | 旧未知链；T15/AC7 | 未确认 shell 链在较早页，不是当前 active_operation | resume 和诊断，独立变体 | 阻止不安全 replay/诊断；外部副作用日志不新增命令 |
| TC-U15-06 | 结束身份；T15/AC7 | operation 结束记录存在但 execution identity 不匹配 | 确认结束并尝试后继 | 不作为结束证明；NO-NEXT |
| TC-U15-07 | 恢复/压缩选择；T15/A§7.1 | 重复同 resume_attempt 的开始、匹配 source version 的压缩选择冲突，各独立例 | loop 恢复/prepare request | 拒绝不唯一或不一致；不因只保留“最新一条”放过 |
| TC-U15-08 | dry-run 陈旧；T15/A§7.1 | 读取中另一受控合法 writer 改变记录，走 dry-run 契约允许的检测路径 | 完成 inspection | 报视图陈旧，不拼接新旧边界；dry-run 本身不写 |

### U16：固定边界与 coverage 决策

规则空间：结构可信 Y/N × 应有证据齐全 Y/N × Run 成功 Y/N，共8组合。结构不可信两种证据状态与成功状态全部折叠为 R1；结构可信时成功状态不改变 coverage，保留 R2/R3。未知远端结果若被完整解释属于“应有证据齐全”，不是缺损。

| 条件/动作 | R1 | R2 | R3 |
| --- | --- | --- | --- |
| 结构可信 | N | Y | Y |
| 应有证据齐全 | — | Y | N |
| Run 成功 | — | — | — |
| 导出 | 失败、不发布 | complete | partial＋结构化 issues |

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U16-01 | R2；T16/AC7、12 | terminal failed/suspended 两种合法完整边界；含已解释未知请求 | 导出 | complete 不冒称成功/终态；unconfirmed 不归零；原 sequence/attempt/span 保留 |
| TC-U16-02 | R3；T16/AC12 | 类型合法但应有响应/输出缺失或 partial stream，独立例 | 导出 | partial，issue 含语义 code、sequence、attempt、原因；无空数组吞问题 |
| TC-U16-03 | R1；T16/AC12 | sequence 缺口、解码失败、错 Run、attempt 冲突、summary 边界不一致，独立例 | 导出 | 全部直接失败且 NO-EXPORT，不发布“partial”掩盖结构不可信 |
| TC-U16-04 | 固定 N；T16/AC12 | record 冻结 N；存在>N 的查询数据；Snapshot source>N 另例 | 按边界读取 | 只含1..N、summary 来自冻结 State；越界 Snapshot 不被接受；不能混新 summary |
| TC-U16-05 | 顺序/结构；T16/AC3、12 | timestamp 非单调但 sequence 连续；合法 artifact refs | 导出解析 | 按 sequence；schema=1、kind、run_id.hex、UTC；identity 字段在顶层；不复制 messages/steps 树、不伪造 TraceSpan |
| TC-U16-06 | 有效定义正向完整性；T16/AC3、D§6、A§8.3 | 冻结 task、Agent身份/指令、provider/model、generation、工具定义/schema、预算、容量/输出限制、定价、Workspace类型/base revision、Evaluator均设非默认唯一值；benchmark身份存在/不适用两变体；当前进程配置与冻结值不同 | 导出并逐字段对照独立的允许字段fixture | 所有适用白名单字段存在且等于冻结有效定义，不以最新 Settings/default 替代；benchmark instance/task digest 适用时完整，不适用时不伪造；producer_version 存在；禁止字段仍由 U20 验证 |

### U17：附件目录、patch 与 Evaluation

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U17-01 | manifest-only；T17/AC8 | 普通正文已删除/损坏，manifest 合法 | 分页列目录/导出 | 元数据仍可列，注明未验证可达性；不读正文；hash/size 是注册时事实 |
| TC-U17-02 | 目录归属；T17/AC8 | 被≤N事件引用、未引用、其他 Run、历史 trajectory 四类记录 | 导出 | 本 Run 被引用项对应；未引用为 registered_only；其他 Run 和所有 trajectory 不进入目录 |
| TC-U17-03 | patch 完整性；T17/AC8 | candidate patch 正文损坏 | 既有 patch finalization | 原交付校验失败，不能因 manifest-only 优化而成功；不读取无关大工具正文 |
| TC-U17-04 | evaluator 分类；T17/AC8 | pass-through、SWE-bench resolved/unresolved/error 独立已知结果 fixture | 投影 | 类型、verdict、报告/patch ref 准确；pass-through 不变成 resolved；不重跑 evaluator |
| TC-U17-05 | 稳定分页；T17/A§8.2 | 本 Run 多页 manifest，无并发注册；含相邻 trajectory 项 | 遍历全部页 | 稳定顺序，无漏/重；结构坏或归属异常 manifest 不静默认领 |

### U18：64 MiB 与预算

大小库存：n 是最终 UTF-8 JSON bytes，包含转义、标点、metadata 和目录。n≤67,108,864 可注册，n≥67,108,865 超限失败，步长1 byte；只构造合法完整 JSON，n=0 不属于合法导出。预览上限与完整证据采集不受此阈值控制。

| ID | Covers / Trace | Given / When | Then |
| --- | --- | --- | --- |
| TC-U18-01 | BV 67,108,863；T18/AC10 | 用合法 ASCII padding 使最终 JSON 精确达到大小，执行导出 | 成品可解析，manifest hash/size 正确 |
| TC-U18-02 | BV 67,108,864；T18/AC10 | 同上等值 | 成功，不误用 `<` |
| TC-U18-03 | BV 67,108,865；T18/AC10 | 同上超过1 byte | NO-EXPORT；不截断 JSON；完整 Event/正文仍保留 |
| TC-U18-04 | 编码开销；T18/AC10 | 裸正文未超限，但转义或多页目录使最终超限，两变体 | 失败，无成功 URI；按最终 bytes 而非原文长度 |
| TC-U18-05 | 总预算；T18/A§8.4 | 设置可控总预算，多个页每页均未超单页时间，但累计超过；公共时钟或实际边界握手 | 不重置预算；编码让出执行权；截止后不启动新发布 |
| TC-U18-06 | 较早 deadline；T18/A§8.4 | export 预算早于 cleanup、cleanup 早于 export、开始时已耗尽三变体 | 取较早者；已耗尽直接 skipped/interrupted；不新建发布 |
| TC-U18-07 | 截止与已发布；T18/A§8.4 | 发布在 deadline 前已开始，截止时尚未完成 | 按 U12 排空并确认；manifest 成功则 exported；不要求 syscall 硬实时打断 |

时间契约使用相对顺序，不把未经定义的 δ=1ms 当产品精度。测量误差未确认时，只确认阶段禁止/允许动作；墙钟定量结论 DEFER。

### U19：收尾规则、错误独立性与关键旅程

入口条件空间 enabled×Run_exists×dry_run×safe_to_read 为16组合；按优先规则折叠：dry-run 无导出；关闭无导出；无 Run 无导出；其余按 safe_to_read。不能把 disabled、skipped 和 failed 的原因合并为一个外部结果。

| 条件/动作 | R1 | R2 | R3 | R4 | R5 |
| --- | --- | --- | --- | --- | --- |
| dry-run | Y | N | N | N | N |
| enabled | — | N | Y | Y | Y |
| Run exists | — | — | N | Y | Y |
| 安全读取 | — | — | — | N | Y |
| 文件行为 | 不导出、不写入 | disabled | 无文件 | skipped＋原因 | 尝试一次 |

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U19-01 | R1–R5；T19/AC2、10 | 每列独立；无 Run 用 bootstrap 失败；unsafe 用锁/DB 状态不可信 | 收尾 | 遵守表；不触发模型、工具、诊断命令补事实；无伪 URI |
| TC-U19-02 | R5出口集合；T19/AC10 | 正常成功、失败、suspension、patch interruption、RunPersistenceError、ManagedExecutionError 六独立变体，均确保边界可安全读 | 开启导出收尾 | 每路径尝试一次；只做原异常允许 finalization；原结果/异常/traceback/退出码保持 |
| TC-U19-03 | 导出失败；T19/AC10 | 原 Run 成功/失败/抛异常 × export 存储失败/超时，6实例 | 收尾并与关闭导出同场景比较 | UNCHANGED；独立 failed/skipped 诊断；不写影响成功的 finalization_errors；不额外保留 Workspace |
| TC-U19-04 | 正常旅程；T19/AC3、8 | F、M、C，一次工具调用、最终响应、已知评测及 patch | 真实 CLI 执行，清理 Workspace，重开 artifact | JSON 可解析；输入、完整响应、参数、raw/observation、final、费用、verdict 可关联；hash/size 对照实际 bytes；主线阅读不依赖 Workspace |
| TC-U19-05 | suspension→resume；T19/AC7 | 同 Run，首次 suspension 已导出并保存旧 bytes/hash；新进程 resume | 第二次收尾 | Run ID不变、sequence连续、attempt不复用；新 Artifact；旧文件字节不变；目录排除旧 trajectory；无 export 完成 Event |
| TC-U19-06 | 异常无 ApplicationResult；T19/AC10 | 原异常与导出失败并存 | CLI 捕获呈现 | 原异常/退出码/traceback 语义保留，stderr 可见独立导出诊断；不能仅将诊断藏在未显示的 note |
| TC-U19-07 | 收尾顺序；T19/AC8、10 | 真实锁、patch/diagnostic允许路径、Workspace可观察清理 | 收尾 | patch允许动作完成→journal排空→持锁固定N/导出→原清理；不在 destroy 后取证、不重复 patch；锁内无并发注册 |

宽范围只保留正常、恢复和异常收尾的装配价值；详细规则在较小范围验证。Docker 真实旅程使用既有内部任务上下文；无需为所有配置值重复真实 Docker。

### U20：内容边界与 faithful

禁止分区：独立 reasoning/thinking/推理流，Settings/API key/header，完整 env/recovery_context，执行 permission token，helper/launcher 控制材料。允许分区：task/指令/最终 content 普通解释/工具正文中的忠实内容，reasoning token 数，非秘密因果身份与许可结果。不同字段逐项独立哨兵，不能将“正文含敏感样式”当禁止字段。

| ID | Covers / Trace | Given / data | When | Then |
| --- | --- | --- | --- | --- |
| TC-U20-01 | 禁止分区，逐字段；T20/AC11、13 | S 放在独立禁止字段，允许正文无该 S；正常/规范化失败两路径 | 采集并导出 | DB/Event/正文 Artifact 不新增 reasoning 正文；导出和诊断无凭据/控制材料；不任意 dump SDK/Settings/recovery context |
| TC-U20-02 | 允许正文；T20/AC11、13 | 最终 content 含普通解释、task/工具正文含合成敏感样式 | 执行/导出 | 正文原样；不凭内容猜测 reasoning 后删改；content_policy faithful 声明准确 |
| TC-U20-03 | token/缺失声明；T20/AC13 | reasoning token 数有效，最终正文非空 | 导出 | 计数保留；coverage 声明 wire/reasoning正文/附件正文未包含，不能宣称完整附件包 |

## 5. 设计覆盖与残余风险

以下是设计覆盖，不是执行覆盖。编号区间表示该单元全部已列用例；variant 必须展开，不能以一个实例通过代表全部。没有自动化代码，本稿不提供测试通过率。

| Unit | 技术覆盖目标 | 已设计映射 | 尚未证明 |
| --- | --- | --- | --- |
| U01 | MR1＋默认/false | TC-U01-01～02 | 真实运行 |
| U02 | 决策表6/6列；三入口各列；来源/继承/只读 | TC-U02-01～04 | 环境隔离与入口装配 |
| U03 | R1–R6＋请求提交失败 | TC-U03-01～04 | R6可到达性；真实原子性 |
| U04 | MT1–MT4、MI1；4条路径 | TC-U04-01～04 | B-tx实际命中 |
| U05 | ST1–ST7；4条路径＋摘要返回分区 | TC-U05-01～05 | 现有恢复策略下fixture可达 |
| U06 | E1–E5 | TC-U06-01～04 | E4契约归属需登记 |
| U07 | E1–E5；BVA三点3/3＋编码单位 | TC-U07-01～08 | 实际编码和拒绝前捕获 |
| U08 | 未放行/创建失败/多执行/内部命令 | TC-U08-01～04 | Local/Docker实际窗口 |
| U09 | 六类输出风险 | TC-U09-01～06 | 真实guest/背压/字节证据 |
| U10 | 结果分区＋原子失败＋无观察 | TC-U10-01～03 | 实际conversation同事务 |
| U11 | F1–F5＋raw持久化 | TC-U11-01～04 | 文件和DB故障位置 |
| U12 | WT1–WT5及禁止迟到注册 | TC-U12-01～05 | 线程drain和锁时间线 |
| U13 | 缺依赖/当前/不匹配/调用者/Snapshot职责 | TC-U13-01～06 | 调用者集合完整性 |
| U14 | G1–G8；页边界与双规模轴 | TC-U14-01～05 | 数值性能判据DEFER；结构/趋势未执行 |
| U15 | 八组独立安全规则 | TC-U15-01～08 | 每个variant实际拒绝 |
| U16 | 折叠表3/3列＋顺序/边界/有效定义 | TC-U16-01～06 | 固定边界实现 |
| U17 | 附件/评测/分页类别 | TC-U17-01～05 | 真实manifest-only及patch校验 |
| U18 | BVA三点3/3＋编码/预算 | TC-U18-01～07 | 实际阈值、时钟误差 |
| U19 | 折叠表5/5列＋六出口＋关键旅程 | TC-U19-01～07 | 所有提前返回/异常路径接线 |
| U20 | 禁止/允许/计数声明 | TC-U20-01～03 | 不等于完整安全审计 |

| 需求验收编号 | 主要设计单元 |
| --- | --- |
| AC1 | U01 |
| AC2 | U02、U19 |
| AC3 | U03、U16、U19 |
| AC4 | U07–10 |
| AC5 | U03–06 |
| AC6 | U05、U15 |
| AC7 | U04–05、U15–16、U19 |
| AC8 | U17、U19 |
| AC9 | U13 |
| AC10 | U11–12、U18–19 |
| AC11 | U20 |
| AC12 | U15–16 |
| AC13 | U03、U05–06、U20 |

不承诺所有状态路径或跨参数组合全覆盖。表中覆盖数字仅针对列出的决策规则/BVA/转换集合；真实执行证据为零。保留 TA 的设计限制，不把未知远端计费、提交前内存丢失或外部附件不可达自动判成测试失败。

## 6. 人工复盘、可靠性与交接

人工 charter：开发者在 Workspace 销毁后，只凭 JSON 和可选 Artifact root，复盘成功、业务拒绝、摘要失败后重试、suspension/resume 四类产物。建议每类20分钟为活动时间盒，不是产品性能阈值。问题：模型实际输入是什么；原结果与观察为何不同；哪个摘要生效；费用哪些确认；未知执行在哪里；评测是否独立。记录 event/attempt 定位、误解、缺失和附件不可达是否被正确理解。负责人待指派；无法回答必须回到相应义务，不用“文件可打开”代替复盘验收。

自动化采用 pytest 和仓库测试层次，不新增依赖；真实确定性 owned collaborators 默认保留。只在外部边界使用 stub/fake/故障装置，且明确省略的真实语义。全套运行不作为本次文档工作的必要检查。

实现后断言强度核查可选择少量风险导向变异：阈值 `<`/`<=`、删除拒绝响应保存、丢弃旧未确认链、让取消跳过 drain、让导出错误写 finalization_errors。必须由相应行为断言检测，不以 mutation 分数作为产品验收阈值。当前未运行变异测试。

竞态失败保留原结果，最多一次诊断重跑；禁止重跑到绿。确认为 flaky 时隔离需具名 owner、原因、到期日与保留信号，隔离测试不计通过。失败窗口未命中、环境缺失和真实产品失败分别记录，不能统一标 pass 或简单 skip 后宣称完成。

交接顺序：确认架构状态→落实 Q 项/公开契约→对齐实施计划（尤其 U07、U14–15）→实现与真实边界执行→独立实现验证。测试证据至少包含输入指纹、实现 revision、TC/variant、环境、屏障证明、DB/Artifact/外部行为摘要、结果与未决项；不保存真实凭据或客户数据。流水线触发器和发布豁免由相应负责人决定。

## 7. 文档核查记录

本稿与 TA 由同一编写会话生成；自检仅检查链接、ID、映射和文档结构，不能称独立验证。按 qa-methodology，由独立上下文审阅者 `/root/review_trajectory_tests` 在 2026-10-20 只读核查 D/A 与两份文档；未继承编写会话，未审查实现、未运行测试。初审未发现阻断级预期矛盾，提出两项中等覆盖缺口：

| Finding | 核查发现 | 处置与依据 | 独立复核 |
| --- | --- | --- | --- |
| DOC-F1 | 只有禁止字段与主线检查，遗漏允许定义字段仍可能通过 | substantiated；新增 TC-U16-06 逐字段核对冻结有效定义及 benchmark 有/无分区，同步 TA T16；依据 D§6、A§8.3 | 已确认解决 |
| DOC-F2 | 业务缺 usage/协议失败不证明摘要专用提交路径 | substantiated；新增 TC-U05-05，覆盖摘要缺 usage、无 pricing、协议失败、无 response；同步 TA T05；依据 D§6–7、A§5.2–5.3 | 已确认解决 |

编写者另补 TC-U13-06，验证 Snapshot 不额外复制历史完整证据；独立复核确认与 A§4 一致，未发现新增预期矛盾。复核范围仅这些修订，不把初审咨询意见当作产品批准。

文档机械检查：100 个唯一 TC 规格（variant 另展开）、20 个义务到单元映射、AC1–AC13 映射、相对链接、输入 SHA-256、代码块闭合及尾随空白均已核对。没有运行 pytest、类型检查或运行时测试，因为本次只新增两份文档。该文档核查不代替未来实现测试，也不表示当前产品满足这些场景。
