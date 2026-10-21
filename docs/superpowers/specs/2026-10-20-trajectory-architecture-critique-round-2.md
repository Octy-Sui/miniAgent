**REVISE — 方案总体克制，但完整证据进入 Event 后，现有压缩与恢复路径仍会全量加载历史；必须补齐这些消费者的资源边界。**

| 维度 | 评分 | 理由 |
| --- | --- | --- |
| 1. 需求与质量属性保真度 | 🟡 | 目标、非目标和三项主要质量属性明确，但“有界内存”的落实仅覆盖输出捕获与导出，遗漏正常压缩和恢复。 |
| 2. 边界质量（内聚与耦合） | 🟢 | Runtime、执行环境、ArtifactStore 与导出模块职责清楚，复用公共契约，没有另建事实中心。 |
| 3. 模式克制（基于实际约束） | 🟢 | 流式 writer 和元数据分页都有现存缺口支撑；明确拒绝双写服务、通用投影框架和后台任务。 |
| 4. 数据与状态建模 | 🟡 | Event、Artifact、State 和 Snapshot 的权威性及提交边界清晰，但工具返回值被拒绝前的证据保存顺序尚未封闭。 |
| 5. 变化容忍度（YAGNI） | 🟢 | 导出格式独立成小包合理，历史导出、bundle、去重及通用恢复机制均未提前建设。 |
| 6. 故障与规模现实性 | 🔴 | 取消、发布及孤立 Artifact 处理较完整，但新增大 Event 会使现有全量历史读取成为正常执行和恢复的内存风险。 |
| 7. 决策记录与可读性 | 🟢 | 主要选择有理由、替代方案及残余风险说明；业务响应一次提交的丢失窗口也已明确接受。 |

### Blocker

**B1 — 完整请求进入 Event 后，现有压缩与恢复消费者仍全量读取历史。**

- **维度：** 需求与质量属性保真度；故障与规模现实性。
- **设计锚点：** `docs/superpowers/specs/2026-10-20-trajectory-architecture-design.md:10` 将有界内存列为主要质量属性；`:88` 将完整 ModelRequest 放进每次请求事件；`:185` 仅为导出器规定分页；`:219` 接受单条 Event 的内存峰值，但没有覆盖其他历史读取者。
- **已核实的仓库事实：**
  - `src/miniagent/runtime/context.py:317` 在压缩来源链检查时全量加载 Event。
  - `src/miniagent/runtime/context.py:364` 在验证单个 EventSource 时再次全量加载 Event。
  - `src/miniagent/runtime/context.py:469` 在压缩恢复校验时全量加载 Event。
  - `src/miniagent/cli/resume.py:131` 将整个 Run 的 Event 物化为 tuple。
  - `src/miniagent/storage/sqlite.py:160` 的 `list_events` 默认 `limit=None`，返回完整的 `list[RunEvent]`。
- **为什么重要：** 原来较小的历史元数据将包含每次重复保存的完整模型上下文。压缩限制当前 conversation，却不能限制这些消费者载入的累计历史；单个请求有界也不意味着全部请求之和有界。关闭 trajectory 同样受影响，64 MiB 导出上限也无法保护这些路径。结果可能是仍能继续保存的 Run，在下一次压缩或 resume 时耗尽内存；EventSource 验证还可能反复支付全历史解码成本。
- **建议方向：** 在架构范围中明确审计并更新既有 Event 消费者。使用分页折叠、按事件类型筛选或按 ID 定位等最小读取能力，让恢复和压缩只保留其实际需要的事实；不能分页读取后再累积完整 payload 列表。补充长历史、重复完整上下文下的压缩与 resume 资源验证，不需要引入第二套证据存储或消息去重。

### Should-fix

**S1 — “原始 ToolResult”必须在返回值业务校验替换之前保存。**

- **维度：** 数据与状态建模。
- **设计锚点：** `docs/superpowers/specs/2026-10-20-trajectory-architecture-design.md:92` 要求 ToolEnded 表达实际原始 ToolResult；`:147` 只规定“调用完成后、渲染前”保存，没有明确与返回值校验的先后关系。
- **已核实的仓库事实：** `src/miniagent/runtime/tool_runner.py:227` 会将 `tool_call_id` 不匹配的 ToolResult 替换成合成错误；`:233` 对不符合错误码规范的 ToolResult 也执行替换。两者都发生在工具调用返回之后、conversation 渲染之前。
- **为什么重要：** 如果保存点放在现有 `_execute_prepared` 返回之后，保存下来的将是合成的 `INVALID_TOOL_RESULT`，原工具正文及错误内容已经丢失。这种实现满足目前文字描述的时序，却无法兑现“实际原始 ToolResult”的证据要求。模型响应已经区分原始证据和接受结论，工具返回值需要同样明确的最小规则。
- **建议方向：** 明确先捕获合法 ToolResult 对象的原始值，再验证其调用归属与错误码，并分别保存拒绝原因及真正交给模型的结果。证据归属使用当前执行 attempt，不能信任被拒绝结果自带的 ID。无需承诺序列化任意非法 Python 返回对象；增加“返回有效 ToolResult，但调用 ID 错误”的验收案例即可封闭此缺口。

### Nit

无。

**结论：保留当前总体结构，补齐大 Event 的既有消费路径及工具返回值校验前的保存顺序，再进入实施计划。**
