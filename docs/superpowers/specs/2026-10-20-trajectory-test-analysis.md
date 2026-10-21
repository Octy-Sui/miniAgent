# Trajectory Test Analysis

日期：2026-10-20。状态：需求测试分析；测试未实现、未执行。本文将上一轮对话中的 T01–T20 分析落盘，补充风险、判据、证据组合及与 Test Design 的追踪。

## 1. 范围、依据与结论

**Mode：Design；Strategy pass：Obligation；Decision：PARTIAL。** 核心要求可以建立可证伪的测试义务；本结论不是产品验收结论，也不是已执行测试的通过率。架构相关的范围与保真度为候选，待架构状态确认后冻结 Portfolio；不因该确认尚未登记而停止测试设计。

用户两次提供了相同架构文件名。仓库只有一份该文件，本文以它为主，并采用它明确引用的功能需求作为补充，不臆造第二份架构。

| 代号 | 来源 | 权威范围 |
| --- | --- | --- |
| D | [Trajectory 功能需求与设计](2026-10-20-trajectory-design.md) | 产品意图、配置、验收、D1–D9 决策 |
| A | [Trajectory 架构设计](2026-10-20-trajectory-architecture-design.md) | 采集、事务、恢复、流式写入、固定边界和资源约束 |
| R3 | [第三轮架构评审](2026-10-20-trajectory-architecture-critique-round-3.md) | PROCEED 咨询结论，不代替用户接受或实现验证 |
| TD | [Trajectory Test Design](2026-10-20-trajectory-test-design.md) | 本分析的场景、数据、故障窗口与预期结果 |
| P | [现有实施计划](../plans/2026-10-20-trajectory.md) | 仅定位待对齐项；不作为预期结果的来源 |

AC1–AC13 是 D§11 原有编号的本地引用写法；T01–T20 沿用上轮分析 ID。A§n、D§n 是章节引用。当前 HEAD 为 `02b42a9b24b4f0d8f4fbae27c20e735fba7fb53a`，输入含未提交内容，不能把 HEAD 当作完整输入版本。输入内容指纹见 TD§2。

A 首页仍写“修订待 review”，R3 已给出 PROCEED；记录这一状态差异，不修改原文、不宣称用户已经确认全部修订。A 与 D 的架构细节冲突按 A 推荐方案设计，并保留待确认状态：业务响应一次提交；结构不可信直接导出失败；结果用独立 TrajectoryExportOutcome；Runtime 显式必需持久化依赖。

范围：始终保存完整规范化请求/最终响应、原始工具结果和命令双流；有界观察；恢复、压缩与历史消费者；最终可选 trajectory JSON；CLI 收尾与当前契约消费者。

不纳入：HTTP wire/SDK 内部 retry、模型 reasoning 正文、训练格式、确定性重放、历史 export 命令、bundle、自动脱敏、旧数据迁移、GC、专用 UI、通用 RunTrace 实现。完整指已收到且能够提交的事实；不承诺强杀前内存内容或未生成内容。

## 2. 风险台账

优先级按影响和明确失效窗口划分，不虚构生产概率。P0 表示可能破坏恢复安全、数据完整性或秘密边界，P1 表示产品功能、配置或可操作性。P0 不等于发生频率高。

| 风险 / 义务 | 失败模式 | 影响 | 暴露条件 | 风险证据与信心 | 优先级 / 建议责任组件 |
| --- | --- | --- | --- | --- | --- |
| 提交与结算；T03–06 | 已收到响应丢失、重复结算、未发布摘要生效 | 复盘失真、费用错误、恢复错误 | 拒绝响应、事务取消、摘要重试/恢复 | A§5 明确；频率未知 | P0 / runtime、providers、storage |
| 工具保真与归属；T07–10 | 原结果被替换；原字节截断；许可误当执行 | 错误执行证据、模型观察不可解释 | 错调用 ID、大输出、多命令、无下一次模型请求 | A§6.1 明确；频率未知 | P0 / tools、runtime、env |
| 持久化与取消；T11–12 | 证据失败后继续；锁外发布；假 complete | 未知副作用、竞态与不可信成品 | write/finish/commit 故障、重复取消 | A§6.3 明确；线程/OS 语义需真实边界 | P0 / artifacts、env、cli |
| 历史资源与安全；T14–15 | 全量保留正文；丢旧执行链/来源校验 | 内存失控、不安全 replay | 长历史、多压缩版本、分页交界 | A§7.1 明确；实际规模未知 | P0 / runtime、storage、cli、dashboard |
| 边界与内容；T16、20 | summary 混边界；结构损坏仍发布；秘密泄漏 | 错误判断或秘密暴露 | 任意序列化、越界来源、非法事件 | A§8 明确；不扩大为通用安全审计 | P0 / trajectory、providers |
| 入口与契约；T01–02、13 | 开关关闭采集、入口分歧、缺依赖晚失败 | 功能不可用、契约退化 | CLI 覆盖、resume、直接 API | D§5、A§7、9 明确 | P1 / cli、runtime、dashboard |
| 目录与收尾；T17–19 | 孤立附件当成功；导出失败覆盖主结果 | 误报成功、错误清理、无产物假 URI | 超限、超时、异常与提前返回 | A§8–9 明确 | P1 / artifacts、cli、trajectory |

没有事故频率与负责人指派证据，因此 P×I 数值和具名 owner 均未确认，不宣称已完成定量风险评估。表中组件是建议承接方；缓解措施是下列证据义务，未替负责人接受风险。

## 3. Oracle 与证据原则

1. 持久化正确性由新连接/新进程读到的 Event、State、Snapshot、manifest 与正文判定，不由“commit 被调用”判定。取消异常不等于回滚；写入中的取消必须先排空确认事务结果。
2. 模型请求对照外部 provider stub 实际收到的规范化请求；费用使用手算 Decimal fixture。响应、结算与业务 disposition 必须属于同一已提交事实，不从最终状态倒推缺失响应。
3. 工具原字节对照预先生成的源 bytes 与独立 hash，不能只比较两个由被测实现产生的 hash。原 ToolResult 与有效有界结果、ToolMessage 分别核验。
4. 取消使用握手屏障和外部生命周期观测；不以 sleep 猜窗口。新进程锁竞争用于证明锁未提前释放；无写入完成证据时不以“暂时没看到文件”证明后台任务结束。
5. 固定 N 后所有来源不得越界；sequence 顺序优于 timestamp。coverage.complete 不等于 Run 成功、终态或附件可达。
6. 不精确匹配未承诺的诊断措辞。明确缺失原因、结构化 issue、原异常/退出码保持才是 oracle；需要具体枚举而尚未定稿时登记缺口。
7. 内存实验分离数据准备、采集、附件校验进程。结构性约束与增长趋势共同提供证据，不引入无依据 RSS/延迟合格线。

假通过风险包括：使用生产计费函数产生 expected、只断言 not null、只检查 mock 调用、同源 hash 自洽、宽泛 raises、未命中故障窗口、只测 exporter 分页。假失败风险包括：直接比较合法变化的 UUID/时间、把未知远端结果当损坏、把允许的单 Event 峰值或紧凑索引增长当无界历史正文缓存。

## 4. 测试义务与候选证据组合

所有“证据”均为待取得证据；不是已有测试执行结果。TD 的同编号 U01–U20 分别承接 T01–T20。

| Risk / criterion | Failure mode | Oracle | Technique | Scope / fidelity | Dependencies | Data / environment | Evidence | Residual blind spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 / AC1 | 关闭导出丢采集 | 开关两侧证据口径相同，仅开启侧有 trajectory | 变形关系 | 装配 Run | 真 journal/store；provider stub | 成对隔离 Run | D§5；TD U01 | 单对行为不证明异常路径 |
| T02 / AC2 | false 被覆盖、resume 改值 | CLI > Settings > false；三入口一致；resume 继承；dry-run 零写入 | 决策表 | CLI→冻结定义 | 真解析/入口 | CLI 三态×Settings 两态 | D§5；TD U02 | 环境变量/.env 污染 |
| T03 / AC3、5、13 | 请求不同或拒绝响应丢失 | 请求等于 generate 输入；提交失败不调用；响应与 disposition 同 commit | 分区、决策表 | Runtime 提交边界 | 真 SQLite；provider stub | 正常/拒绝/缺 usage/无 pricing | A§5.1；TD U03 | 意外异常提交前窗口允许丢失 |
| T04 / AC5、7 | 重复消息/收费/attempt | 以实际 commit 为准；提交后不重复；未提交保持未知，新调用新 attempt | 状态转换 | 跨进程恢复 | 真事务、进程、锁 | 提交前/中/后中断 | A§5.2；TD U04 | 远端是否收费未知 |
| T05 / AC5、6、13 | 未发布摘要生效、失败返回丢证据 | attempt 独立，协议/usage/校验失败证据保留，发布才改 Task Memory | 状态转换、返回分区 | 压缩恢复 | 真 Snapshot/journal | 缺 usage、协议失败、无效摘要、来源失败、预算失败、重试 | A§5.2；TD U05 | 摘要语义质量不在此验收 |
| T06 / AC5、13 | 非法协议丢证据/执行工具 | 白名单失败证据；无伪 ModelResponse；未验证 usage 不计费 | 等价分区、协议契约 | provider 适配器 | 外部 SDK/传输 fixture | malformed args/usage、连接失败 | A§5.3；TD U06 | 外部 fixture 漂移 |
| T07 / AC4 | 校验覆盖 raw_result | 原值保留，外层归当前 attempt；合成观察另存；非结果不 repr/pickle | 分区＋BVA | ToolRunner→store | 真运行链/store | 错 ID/错误码、64 KiB 边界 | A§6.1；TD U07 | 工具上游已丢内容不可恢复 |
| T08 / AC4 | 许可当启动、归属错误 | 放行前命令身份已提交；未启动明确；多执行与内部命令归属准确 | 状态条件 | Workspace 边界 | 真 Local/Docker | 许可失败、多执行、诊断命令 | A§4、6.1；TD U08 | start_unknown 仍未知 |
| T09 / AC4 | 双流字节丢失/混控制帧 | 分别 byte/hash/size 一致；预览有界；空流不同于未启动；不补执行 | 数据分区、属性 | 输出接收 | 真 pipe/guest/store | 二进制、跨 chunk、多流、大输出 | A§6.1–2；TD U09 | 不承诺跨流交错 |
| T10 / AC4 | 保存观察不等于实际消息 | 精确 ToolMessage 与 conversation 同提交；最后一次观察可读 | 原子性、示例 | 工具完成 | 真 journal/renderer | 正常/拒绝/超时/error/无观察 | A§6.1；TD U10 | 未提交观察允许缺失 |
| T11 / AC10 | 必需保存失败被吞 | 停止后续业务、收敛执行；孤立附件不当已提交；不重跑 | 故障注入 | 存储/执行交界 | 真 store；外部故障 | write/finish/commit | A§6.3；TD U11 | DB 同坏时失败 Event 不保证 |
| T12 / AC10 | 取消后后台发布/假 EOF | partial/complete 准确；发布前 abort，发布中 drain；已发布返回 ref；锁外无注册 | 状态转换、竞态 | writer/put 收尾 | 真线程 I/O、锁 | 准备/发布中/发布后取消和重复信号 | A§6.3；TD U12 | 强杀/syscall 不保证硬实时 |
| T13 / AC9 | 调用者当前契约不一致 | 依赖构造期必需；版本不变；hash/身份检查；当前 Run 可恢复 | 契约、分区 | 当前消费者集合 | Runtime/store/resume/Dashboard | 缺依赖、当前/不匹配格式 | A§7；TD U13 | 不要求旧开发数据可读 |
| T14 / A§7.1 | 历史正文常驻 | 页≤16、固定 N、释放 payload；正文规模增长不造成同比驻留 | 资源趋势、生成数据 | 全历史消费者 | 真 SQLite、独立进程 | 导出关闭、正文/事件数独立变化 | A§7.1；TD U14 | 元数据增长和单对象峰值允许 |
| T15 / AC7、12 | 分页遗漏安全校验 | 跨页缺口、坏来源、旧未知链、错身份继续拒绝 | 无效分区 | 压缩/resume/诊断 | 真存储 | 每次一个损坏条件 | A§7.1；TD U15 | 仅接口调用次数不足 |
| T16 / AC3、7、12 | 新旧边界混合、定义遗漏或损坏发布 | 1..N 连续；summary 来源冻结；有效定义白名单完整且取冻结值；结构冲突失败；合法缺证据 partial | 决策表、字段契约 | 导出投影 | 真 SQLite/store | 非默认冻结定义、越界/错 Run/partial/未知 | A§8.1、8.3；TD U16 | complete 不等于成功 |
| T17 / AC8 | 目录读正文/孤立附件当成功 | manifest-only；排除 trajectory；registered_only；patch 保留校验；verdict 类型准确 | 分区、故障 | manifest/finalization | 真文件 store、评测记录 | 缺普通正文、坏 patch、不同 evaluator | A§8.2；TD U17 | 不保证附件可达 |
| T18 / AC10 | 超限注册、预算重置 | 最终 UTF-8≤64 MiB；超限无成品；预算 min 且不重置 | BVA、时间条件 | 编码/发布 | 增量编码、真 store | 转义/目录开销、边界±1 byte | A§8.4；TD U18 | 上限不等于 RSS |
| T19 / AC3、7、8、10 | 导出覆盖主结果或漏出口 | 安全可读且启用时一次；原退出/异常/清理保持；注册才显示 URI；resume 不覆盖旧文件 | 规则矩阵、旅程 | application/CLI | 真进程/SQLite/文件；provider stub | 所有出口、Workspace 销毁 | A§9；TD U19 | 强杀不保证产物 |
| T20 / AC11、13 | reasoning/秘密泄漏或正文被删 | 禁止字段不采集/导出；最终 content、普通解释、reasoning 计数保留；faithful | 哨兵分区 | provider采集/投影 | 真映射与投影 | 每字段独立哨兵 | A§5.3、8.3；TD U20 | 用户正文可含敏感内容 |

## 5. 范围与成本选择

| 候选范围 | 速度/可靠性 | 保真度与维护 | 使用决策 |
| --- | --- | --- | --- |
| 纯逻辑/编码 | 快、确定；无外部资源 | 能定位字段/阈值，不能证明事务/OS | 配置规则与字节计数优先用最小范围 |
| 真实临时 SQLite/FileArtifactStore | 局部、可重建、无网络 | 提供 commit、manifest、重开读取语义 | 提交、目录、原结果和投影的必要边界 |
| Local/Docker、子进程、锁 | 成本较高；需握手和独立资源 | 提供实际输出/取消/恢复语义 | 保留各自不可替代的边界后盾 |
| 全 CLI 旅程 | 慢、诊断粒度较粗 | 验证装配、退出码、清理和跨进程 | 仅关键组合，不复制所有分支 |
| 真实在线模型 | 不确定、有费用 | 对持久化规则无独立预期增益 | 不作为本功能确定性验收基础 |

不使用 pairwise 替代明确的高阶竞态；不为每项功能增加 E2E。暂不引入 fuzz、chaos 或大规模负载平台：已知输入分区、确定性故障和资源趋势更直接。若实现后出现解析广域风险或断言弱点，再有界补充生成式/变异验证，不预设覆盖率门槛。

## 6. 数据、环境、质量属性与缺口

使用合成数据；每个测试独立 DB、Artifact root、Workspace、Run ID、端口和容器。恢复测试必须真实 commit 后重开，不能由外层 rollback fixture 代替。共享不可变输入与 builder，不共享可变运行状态。provider double 表示规范化输入输出/故障，不能证明真实网络、SDK 重试或计费；Qwen 适配器使用当前协议来源 fixture 作为后盾。

| 质量面 | 验证义务或结论 |
| --- | --- |
| 功能/配置/可操作性 | T01–10、16–19；独立 stderr 诊断、URI 与离线复盘 |
| 并发/恢复/完整性 | T04–05、11–12、15–16；真实事务、锁、发布 |
| 兼容性 | T13；当前契约消费者；不扩展旧数据兼容承诺 |
| 资源/容量 | T09、14、18；内存趋势和最终 JSON 上限；无通用吞吐/延迟 SLO |
| 隐私与安全 | T20 及 T08/15 归属安全；不自动脱敏，不宣称完成通用安全审计 |
| 易用性 | 人工复盘 charter；无专用 UI，无新增无障碍合规目标 |
| 横切机制覆盖 | TD U14 显式枚举受治理消费者；共享生成数据验证每个消费者，不以 exporter 一项替代 |

缺口：架构状态尚未统一；真实 Docker 环境未核实；故障屏障需证明命中实际写入/发布阶段；资源数值阈值与测量误差未定义；执行负责人和独立实现验证者未指派。它们不改变已明确的预期结果，但相应执行/量化结论应为 DEFER。

已接受的设计限制仍保留：业务响应提交前丢失窗口、强杀无法提交的字节、未知远端费用、单个巨大对象峰值、64 MiB 超限无导出、附件引用不打包且不验证可达性。不要把这些限制改写成必须消除的实现缺陷。

## 7. 交接、完成条件与证据状态

- P0：相关规则、提交窗口及真实边界证据全部取得，未解决的高影响 oracle/环境缺口不计通过；隔离或跳过测试不计证据。
- P1：配置规则、产物内容、诊断和关键旅程均有结果；任何保留差距需具名责任人决定，本文不授权豁免。
- 所有自动化目标均为设计建议，未实现。数值资源性能门槛为 DEFER；人工复盘不以自动字符串检查替代。
- 实施计划优先对齐 T07 的“业务校验替换之前捕获”，及 T14–15 的全部历史消费者，不仅 exporter。本次不修改实施计划。
- Portfolio 确认后交接执行阶段与 CI/CD；需求映射和执行证据交接 traceability；资源/重复覆盖成本交接 optimization。本分析不制定流水线触发器或发布门禁。

```text
Subject:              Trajectory 完整证据采集、恢复及可选导出
Mode:                 Design
Strategy pass:        Obligation
Decision:             PARTIAL
Test basis:           D§5–11、D1–D9；A§4–10
Architecture basis:   R3 PROCEED；A 首页仍标修订待 review
Quality risks:        提交恢复、保真、发布竞态、历史资源、白名单
Manual evaluation:    Workspace 销毁后的离线复盘
Adequacy signals:     T01–T20、AC1–13 已映射；无实现执行证据
Handoffs:             Portfolio、CI/CD、traceability、optimization
Residual risk:        明示设计限制；环境/屏障/数值性能/责任人待落实
Next action:          用 TD 场景对齐当前架构和实施计划后冻结 Portfolio
Verification:         文档设计与独立文档核查；运行时测试未运行（仅文档）
```
