# Context Compaction QA Methodology 复审

## 范围与结论

本轮复审[功能需求](2026-09-24-context-compaction-design.md)、[架构设计](2026-09-24-context-compaction-architecture-design.md)、修订后的[测试分析](2026-09-29-context-compaction-test-analysis.md)和[测试设计](2026-09-29-context-compaction-test-design.md)。这是测试计划的审查，文中“设计覆盖”不代表测试已执行或功能已验收。

**结论：确定性测试设计已有明显改进，可以作为用例实施的依据；真实任务的独立验收计划仍不完整。** 尤其是最终任务结果的外部判定、独立验证责任与证据，以及真实模型处理恶意历史内容的验收样本，还需要落地。真实 Qwen 契约、成本比较和摘要质量均未取得执行证据；文档已正确将质量义务标为 `DEFER`。

## 上轮意见复核

| 上轮意见 | 本轮状态 | 依据 |
| --- | --- | --- |
| CLI 配置及数值边界不足 | 大部分已补 | [TC-U1-10～16](2026-09-29-context-compaction-test-design.md#L54)增加比例、保留量、重试、超时和持久化检查；`C` 与 `O` 的落点已纠正。仍有比例顺序反例未覆盖，见下文。 |
| U4 来源反例不足且覆盖数夸大 | 已在设计中补齐 | [TC-U4-10～13](2026-09-29-context-compaction-test-design.md#L130)增加工具调用、缺失与越界引用、结构化值不符；[13/13](2026-09-29-context-compaction-test-design.md#L135)明确是设计覆盖。 |
| Qwen 空工具请求缺真实边界检查 | 已安排，尚未执行 | [TC-U4-14](2026-09-29-context-compaction-test-design.md#L137)要求记录线上请求并向真实端点验证；[执行数为 0/1](2026-09-29-context-compaction-test-design.md#L208)。 |
| 成本样本可能事后筛选 | 处理规则已写入，协议未冻结 | [测试分析 §4](2026-09-29-context-compaction-test-analysis.md#L52)要求预先冻结任务和重复协议、保留失败样本；[Q-3](2026-09-29-context-compaction-test-design.md#L27)仍开放，正式比较尚不可执行。 |
| 摘要语义质量缺审阅标准 | 已明确延期，标准未冻结 | [Q-4 与 TC-U7-03](2026-09-29-context-compaction-test-design.md#L28)将语义质量列为 `DEFER`；不能据结构校验宣称质量通过。 |
| 独立验证与中文 AC 检查 | 仍开放 | 见以下发现。 |

## 尚存发现

### 高优先级

1. **真实任务的“完成”缺少独立的结果 oracle。** [TC-U7-01/02](2026-09-29-context-compaction-test-design.md#L194)以“完成任务”和“成功任务数”为判定条件，但 [Q-1](2026-09-29-context-compaction-test-design.md#L25)仍未冻结任务与评价合同，也未规定逐任务如何根据最终 Workspace 产物或可观察外部状态给出成功／失败。若由 Agent 的结束语或实现者临场判断，续行和成本两项结论无法独立复核。执行前应给每项任务固定输入、允许的最终状态、判分依据和失败处理，并保存最终 Workspace 差异与逐 Run 判定记录；评分依据应独立于被测 Agent 的自述。
2. **五项验收条件仍缺独立验证责任和证据索引。** [需求 §5](2026-09-24-context-compaction-design.md#L62)列出续行、成本、恢复、摘要质量、执行安全；[测试分析 §5](2026-09-29-context-compaction-test-analysis.md#L56)列出拟取得的证据，但没有逐项指定独立于实现者的验证角色、原始证据保存位置和复核记录。应建立五项条件到用例、oracle、原始产物、验证人和结论的映射。实现者运行自动测试可提供诊断，不能单独承担独立验收。

### 中优先级

3. **恶意历史内容的真实模型结果仍没有明确用例。** [测试分析](2026-09-29-context-compaction-test-analysis.md#L42)已识别“模型后续是否服从注入”需真实模型审阅；[TC-U7-04](2026-09-29-context-compaction-test-design.md#L197)只概述工具、审批和 Workspace 事实，未定义真实 Qwen 的固定恶意来源样本、预期合法任务结果及实际行为判定。增加至少一个冻结的样本，检查模型后续调用、审批、Workspace 变化和任务结果，并保留完整轨迹。受控 Provider 路径仍负责验证 Runtime 的权限边界。
4. **U1 比例顺序的越界反例尚缺。** [TC-U1-11](2026-09-29-context-compaction-test-design.md#L55)只检查 `target=trigger`；若校验器错误地接受 `target>trigger`，现有用例可能全部通过。补一个其余配置有效、仅 `target>trigger` 的拒绝用例。`B=0` 与摘要预留不足同时发生，因此 [TC-U1-07](2026-09-29-context-compaction-test-design.md#L51)的泛化“拒绝配置”结果也不能单独证明 `B>0` 校验；不要把它统计成该单一规则的独立证据。
5. **中文验收条件检查脚本仍有假通过。** 对[功能需求](2026-09-24-context-compaction-design.md)运行 `check-ac-testability.py` 返回成功，但 27 行被标为 `no requirement language detected; treated as context`。该脚本的英文规则不能据此证明中文 AC 可测试。当前应人工检查五项条件的刺激、可观察结果和判定来源；脚本适配中文前，不把返回码作为质量门禁证据。

## 验收证据安排

下表为应取得的证据，不表示已有测试结果。验证人和证据位置均待指定。

| 需求 §5 条件 | 现有用例 | 独立判定与原始证据 |
| --- | --- | --- |
| 续行 | U2、U3、U7-01 | 固定容量任务的外部成功判定；每次业务请求预算、Provider 响应和最终 Workspace 状态 |
| 成本 | U6、U7-02 | 冻结的配对与重复协议；每个计划 Run 的成功结果、业务和摘要调用用量、比较计算及失败记录 |
| 恢复契约 | U5 | 最新 Snapshot 重载前后完整请求、事件序列、版本与数据库状态差异 |
| 摘要质量 | U4、U7-03 | 冻结来源轨迹、人工审阅表、逐项遗漏／事实支持记录和争议裁定；当前为 `DEFER` |
| 执行安全 | U3、U6、U7-04 | 工具、审批、Step、Workspace 前后事实，以及真实模型恶意历史样本的完整行为轨迹 |

## 复审验证

逐项复核四份文档与上轮审查意见，检查了 U1、U4、U7 用例及证据状态；重跑中文 AC 检查脚本，观察到 27 行被当作上下文。未运行 Context Compaction 功能测试；文档当前也将 U7 执行覆盖标为 0/4。下一步先补齐独立任务 oracle 和验证记录，再冻结 Q-1、Q-3、Q-4，实施确定性用例并取得真实 Qwen 证据。
