**PROCEED — 主体架构可实施；无阻断项，建议补齐文件类型检查，并避免把默认 Evaluator 配置表述为历史事实。**

| # | 维度 | 评分 | 理由 |
|---|---|---|---|
| 1 | 需求与质量属性忠实度 | 🟢 | 三页面、只读、独立入口和延后范围明确；可信、一致、安全和资源约束均落实到读取行为。 |
| 2 | 边界质量 | 🟢 | 查询、文件读取、纯投影和展示职责清楚，不依赖 Runtime 装配或写入 Store 生命周期。 |
| 3 | 模式克制 | 🟢 | 独立查询解决写入生命周期耦合；明确拒绝读模型数据库、通用基类和完整 Trace 聚合。 |
| 4 | 数据与状态建模 | 🟡 | 数据来源、冻结边界和证据冲突处理充分，但 Local 的验证类型仍存在未经持久化证据支持的推断。 |
| 5 | 变化容忍与 YAGNI | 🟢 | 未来能力只标记所需生产者证据，没有提前建设恢复、Compare 或扩展框架。 |
| 6 | 故障与规模现实性 | 🟡 | 并发取消、部分损坏、晚发布和读取上限考虑充分，但非普通文件可能绕过现有时间预算。 |
| 7 | 决策记录与可读性 | 🟢 | 主要选择有理由与替代方案，并区分已确认选择、推荐选择及功能稿澄清。 |

### blocker

无。

### should-fix

1. **[should-fix][维度 6] 明确拒绝非普通文件，避免安全 Reader 永久阻塞。**\
   **锚点：**架构稿 §9.2 规定 `dir_fd/O_NOFOLLOW`、同描述符 `fstat/hash/读取`，§9.3 检查大小；没有明确要求文件类型必须为普通文件。§12 又明确承认不能打断内核中的阻塞操作，并让并发名额随实际任务完成释放。\
   **影响：**`O_NOFOLLOW` 不拒绝 FIFO。若 manifest 或内容位置被替换为 FIFO，阻塞式 `open()` 可在进入 `fstat` 和循环截止检查前挂住；两个内容请求即可长期占满重读取名额。大小上限不能解决这个问题。\
   **方向：**给 manifest 和内容统一补充“仅接受普通文件”的契约；打开过程应避免 FIFO 阻塞，再在同一描述符上检查文件类型。目录描述符也应明确要求目录类型。验收增加真实 FIFO 拒绝场景即可，无需增加新的读取抽象。

2. **[should-fix][维度 4] 将 Local 的默认装配说明与该 Run 的已记录验证类型分开。**\
   **锚点：**架构稿 §6.1 写道“Local 标注当前组合的 pass-through 属性”。仓库中 [bootstrap.py](/Users/octy/CodeBlocks/miniAgent/src/miniagent/cli/bootstrap.py:218) 确实默认装配 `PassThroughEvaluator`，但 [loop.py](/Users/octy/CodeBlocks/miniAgent/src/miniagent/runtime/loop.py:77) 和 [application.py](/Users/octy/CodeBlocks/miniAgent/src/miniagent/cli/application.py:65) 允许注入其他 Evaluator；`RunState` 与 `RunRecoveryContext` 没有持久化 Evaluator 类型。\
   **影响：**Workspace 类型只能证明运行环境，不能证明历史 Run 使用了哪个 Evaluator。若界面将 Local 直接解释为“本 Run 使用 pass-through”，便违反“每个展示结论都有持久化来源”的成功标准。\
   **方向：**允许说明“当前默认 Local 组合使用 pass-through”，但该 Run 的验证类型缺少证据时显示“未记录”。不必为首版增加生产者字段；继续禁止凭 Local succeeded 展示独立验证通过。

### nit

无。

总体而言，现有结构无需重设计；补齐上述两处边界即可保持其事实可信与有界读取承诺。
