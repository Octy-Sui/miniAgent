# Context Compaction Test Strategy 复审

## 结论

**PARTIAL。** 修改后的[测试分析](2026-09-29-context-compaction-test-analysis.md)和[测试设计](2026-09-29-context-compaction-test-design.md)已补齐上轮指出的配置断言、U4 来源反例，并为 Qwen 空工具请求安排真实端点检查。确定性决策、SQLite 事务和 Provider 语义分别采用足以观察相应故障的范围，没有明显需要再复制一套端到端分支测试的理由。

仍有一条明确的架构失败路径未落到用例：**摘要请求自身超预算**。真实任务的成功判定、成本比较协议和摘要语义审阅材料尚未冻结；其中摘要语义质量义务维持 **DEFER**。本文审查的是证据方案，不把设计用例数当作执行结果。

## 上轮意见复核

| 上轮缺口 | 本轮状态 |
| --- | --- |
| U1 配置数值、持久化和 `C` 的错误落点 | [TC-U1-01、10～16](2026-09-29-context-compaction-test-design.md#L45)已改为从 Run 定义及预算决策验证 `C`，增加主要无效值和非默认配置；比例倒序反例仍缺。 |
| U4 无效来源与工具调用反例不足 | [TC-U4-10～13](2026-09-29-context-compaction-test-design.md#L130)已补，13/13 明确是设计等价类数。 |
| Qwen 空工具契约未验证 | [TC-U4-14](2026-09-29-context-compaction-test-design.md#L137)已规定真实适配器和端点刺激、请求负载及响应证据；尚未执行。 |
| U7 真实任务、成本重复和人工质量标准未冻结 | [测试分析 §4](2026-09-29-context-compaction-test-analysis.md#L48)禁止正式运行后删样本、补跑替换失败；[Q-1/Q-3/Q-4](2026-09-29-context-compaction-test-design.md#L25)仍开放。质量义务已正确标为 DEFER。 |

## 仍需修正的证据缺口

1. **高：摘要请求自身的容量失败没有可证伪用例。** [架构 §5](2026-09-24-context-compaction-architecture-design.md#L59)要求摘要请求满足“估算输入＋摘要输出预留 ≤ `C-S`”；[同节](2026-09-24-context-compaction-architecture-design.md#L76)要求超预算时记录独立失败原因，并根据旧业务请求是否安全而续行或结束；[架构测试义务](2026-09-24-context-compaction-architecture-design.md#L115)也点名该路径。当前 U2 只测业务请求 `T/T_new`，U6 只列 Provider 错误、摘要校验失败和提交失败。加入两个受控估算场景：摘要请求过大且旧业务请求仍在 `B` 内时，不调用摘要 Provider、保留旧版并续行；旧业务请求也超 `B` 时，不调用任何超预算 Provider 请求，以可区分的摘要容量失败结束。断言请求次数、失败原因、版本与用量状态。这样可直接检验摘要输入预算和回退条件。
2. **高：U7 的任务成功 oracle 仍未形成可执行合同。** [需求 §5](2026-09-24-context-compaction-design.md#L64)要求完成任务且成本组通过率不降；[TC-U7-01/02](2026-09-29-context-compaction-test-design.md#L194)写了“完成任务／成功任务数”，但 [Q-1](2026-09-29-context-compaction-test-design.md#L25)表明任务与评价合同未冻结。每项任务应在正式运行前固定输入、允许的最终 Workspace 结果、失败判定与外部评分材料。Agent 的完成声明不能充当任务通过的 oracle。成本比较的重复次数、失败处理和汇总口径依 [Q-3](2026-09-29-context-compaction-test-design.md#L27)冻结后再执行。
3. **中：阈值配置与执行安全各有一个未实例化的反例。** [架构 §5](2026-09-24-context-compaction-architecture-design.md#L57)要求 `target<trigger`，而 [TC-U1-11](2026-09-29-context-compaction-test-design.md#L55)只测等值；增加仅 `target>trigger` 的拒绝场景。另[测试分析](2026-09-29-context-compaction-test-analysis.md#L42)识别了真实模型可能服从恶意历史指令的盲点，但 [TC-U7-04](2026-09-29-context-compaction-test-design.md#L197)尚未给出固定的真实 Qwen 恶意来源样本及任务结果 oracle。受控 Provider 可证明 Runtime 的工具与审批边界；真实模型样本用于检查后续行为，不能互相替代。
4. **中：U1 的 `B=0` 断言不可单独证明硬预算规则。** [TC-U1-07](2026-09-29-context-compaction-test-design.md#L51)只要求“拒绝配置”，而该输入也违反摘要预留至少 256 的规则；即使实现漏掉 `B>0` 校验，仍可能因后一条规则而通过。保留它作为无效配置样本即可，不应计作 `B>0` 的独立证据；若确需证明该内部校验，应选择能观察具体拒绝原因的契约或审查实现逻辑。

## 风险到最小证据组合

下表的“证据”均指计划取得的产物，不表示测试已运行。

| 风险／准则 | 失败模式 | Oracle | 技术 | 范围／保真度 | 依赖 | 数据／环境 | 证据 | 剩余盲点 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FR-1/FR-6 预算及配置 | 错误限制或越界请求进入 Provider | 持久化配置与完整请求估算符合阈值；超 `B` 的业务请求数为零 | 分区、边界、决策表 | CLI bootstrap 与 Runtime 决策；真实构造器 | 真实 TOML/估算器，受控 Provider | 隔离配置及临时 Workspace，阈值邻点 | U1/U2 请求与拒绝记录 | Qwen 实际 token 与估算偏差 |
| FR-2 摘要输入容量 | 摘要请求自身超 `C-S` 仍发出，或错误回退 | 估算输入加预留越界时摘要调用数为零；按旧请求安全性续行或失败 | 容量边界、两分支故障路径 | Context + Runtime 请求路径 | 真实摘要请求构造与估算器，受控 Provider | 合成固定前缀和长历史；旧业务请求安全／不安全各一例 | **缺用例**；需请求计数、原因、状态差异 | 真实 Qwen 计数须端点校准 |
| FR-1/FR-5 Step 与执行安全 | 工具组拆散、审批或 Workspace 被改变 | 成对消息、近期 Step 原文、计数与状态前后相同 | 状态路径、关键旅程 | 真实 Runtime 状态与工具边界 | 受控 Provider、合成安全工具 | 完整旧 Step、待决审批、超限兜底 | U3/U6 拟验证状态与调用记录 | 恶意历史对真实模型的影响未实例化 |
| FR-2/FR-4 摘要来源 | 无效引用或错误结构化事实发布 | 真实 SQLite 来源可读且支持所记录摘录／字段；无效候选不发布 | 无效分区、来源反例 | 验证器 + 真实 SQLite | 受控摘要响应 | 独立 Event/Snapshot 与最小反例 | U4 13 类设计，人工语义核对拟议 | 逐字匹配无法证明解释成立 |
| FR-2 Provider 契约 | 空工具请求被拒或暴露业务工具 | 实际负载无可调用工具，真实 Qwen 接受并返回完整无工具响应 | 真实边界契约 | Qwen 适配器到端点 | 真实 Qwen | 固定模型和最小摘要请求 | TC-U4-14，执行 0/1 | 端点版本漂移 |
| FR-4/FR-5 发布与重建 | 状态、事件、Snapshot 部分提交或重载请求不同 | 事务全有／全无；重载 `ModelRequest` 逐字段相同 | 故障注入、重建契约 | Runtime + 真实 SQLite | 临时 SQLite，受控 Provider | 版本冲突、事务失败、重载 | U5 六路径拟议 | 跨进程恢复执行器不在首版 |
| FR-3/FR-6 用量、重试、取消 | 漏计、无界重试或取消后调用 | 已知用量守恒、未知保持未知、调用上限与终止状态正确 | 决策表、故障注入 | Runtime + 真实计量逻辑 | 受控异步 Provider；真实错误形态回查 | 可恢复错误、超时、取消和二次超限 | U6 九规则拟议 | Provider 未返回的账单无法推断 |
| 需求 §5 真实任务 | 续行失败、成本未降、摘要失真 | 外部任务结果、全调用 token；固定来源轨迹的人工逐项判断 | 配对任务、真实边界、人工审阅 | 真实 Qwen 与独立 Workspace | 真实 Provider | 版本固定任务及重复协议 | U7 框架；执行 0/4，质量 DEFER | 任务 oracle、协议与审阅表尚未冻结 |

## 审查记录与下一步

| 字段 | 结果 |
| --- | --- |
| Subject | 首版 Qwen Context Compaction 的 FR-1～FR-6 与需求 §5 验收 |
| Mode | Audit |
| Strategy pass | Combined；以当前稳定的架构设计为依据 |
| Decision | PARTIAL；摘要语义质量义务为 DEFER |
| Test basis | [功能需求](2026-09-24-context-compaction-design.md)、[测试分析](2026-09-29-context-compaction-test-analysis.md)、[测试设计](2026-09-29-context-compaction-test-design.md) |
| Architecture basis | [架构设计](2026-09-24-context-compaction-architecture-design.md)；首版尚未实现 |
| Quality risks | 超预算调用、摘要失真、部分提交、执行权限变化、成本失控、Provider 漂移 |
| Manual evaluation | U7 任务结果及摘要语义审阅；样本与规则尚未冻结 |
| Adequacy signals | 风险映射和可证伪断言；U4 等计数仅表示设计覆盖，未提供执行证据 |
| Handoffs | 用例实施交实现者；流水线安排和执行证据状态交相应负责方，不由本审查判定 |
| Residual risk | Qwen token／错误／账单行为、模型随机性、无 Store 的历史不可回读 |
| Next action | 先补摘要请求超预算的双分支用例，并冻结真实任务的外部成功 oracle |
| Verification | 只读复核四份材料、现有适配器边界及用例映射；未运行功能测试，因功能尚未实现 |

已设计的受控 Provider、真实 SQLite 和真实 Qwen 检查各自针对不同失效机制，应保留这些差异化证据；不以覆盖百分比、用例数或一次成功端点调用宣称整体充分。
