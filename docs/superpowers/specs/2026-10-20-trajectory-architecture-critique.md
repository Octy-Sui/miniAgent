**REVISE — 主体边界合理，但完整输出的有界持久化路径与 Artifact 取消后的写入生命周期尚未闭合；建议补齐后再进入实施计划。此结论是建议，不是审批门禁。**

| # | 维度 | 评价 |
|---|---|---|
| 1 | 需求与质量属性保真 | 🟡 已明确完整性、可追溯性、停止纪律；provider 归一化失败时的响应保存范围仍有歧义。 |
| 2 | 边界质量 | 🟢 Event 为事实来源、Runtime 采集、CLI 导出、Artifact 保存正文的职责清楚；现有 application 锁覆盖范围支持该方向。 |
| 3 | 模式克制 | 🟢 不新增业务事实表、后台采集器、通用 observer、RunTrace 聚合器或重复消息树，结构与需求匹配。 |
| 4 | 数据与状态建模 | 🟡 固定 sequence、未知结果和冻结定义约束明确；附件目录接口与声明行为不一致。 |
| 5 | 变化容忍度 | 🟢 历史导出、bundle、脱敏和训练转换均作为独立后续需求，无明显提前泛化。 |
| 6 | 故障与规模现实性 | 🔴 大输出仍缺少可行的有界写入机制；现有 Artifact 写线程不能靠取消 coroutine 停止。 |
| 7 | 决策记录与可读性 | 🟢 方案比较、版本约束、失败语义及 D1–D9 决策完整，容易据此评审。 |

### Blocker

**B1 — 完整工具输出要求缺少可实现的有界资源路径。**

- **维度：** 失败与规模现实性。
- **设计锚点：** `docs/superpowers/specs/2026-10-20-trajectory-design.md:96`、`:114`、`:181` 要求截断前完整保存、大正文 Artifact，并将大输出资源使用留给实施计划验证。
- **代码事实：** `src/miniagent/environments/process.py:283` 的 `read_stream()` 直接丢弃超过 limit 的字节；取消 limit 则无限扩展内存。`src/miniagent/environments/docker_exec.py:312` 同样在解码业务输出时丢弃超限字节。`src/miniagent/artifacts/contracts.py` 的 `ArtifactStore.put()` 只接受完整 `bytes`。
- **影响：** 单纯在工具适配层增加保存逻辑拿不到完整输出；取消现有限制会把任意规模 shell 输出转化为无限内存需求。JSON 的 64 MiB 上限不保护这条始终开启的采集路径。
- **方向：** 在设计中选定最小的流式落盘路径：在 Local/Docker 首次接收字节的位置同时维持有界观察缓冲区和完整输出 spool；Artifact 提供最小的文件/流注册能力，明确磁盘失败、取消时已接收前缀及暂存文件的归属。无需完整实现设计，但不能把该关键结构选择推迟为资源测试。

**B2 — 复用现有 ArtifactStore 不足以兑现导出截止时间与锁释放纪律。**

- **维度：** 失败与规模现实性。
- **设计锚点：** 设计 `:158–164` 要求在锁内完成一致读取和 Artifact 注册，超时、重复信号不得破坏锁纪律。
- **代码事实：** `src/miniagent/artifacts/file.py:45` 直接 `await asyncio.to_thread(self._publish, ...)`；`:85–86` 在线程中先写内容再写 manifest。取消等待不会终止该线程。相比之下，`src/miniagent/storage/sqlite.py:339` 明确 shield 并 drain 已开始的写入。`src/miniagent/cli/execution_scope.py:18` 在 scope 退出时释放 RunLock。
- **影响：** 导出 timeout 返回失败之后，后台线程仍可能注册 Artifact；锁释放后 resume 可开始，而旧执行仍在修改该 Run 的附件目录。既无法把“超时停止”当作没有成品，也无法仅靠现有 store 保证全部写入在锁内结束。
- **方向：** 明确 Artifact 写入的取消/提交协议及所有权：区分准备内容和发布 manifest，规定已开始发布如何收敛、何时可以释放锁、截止时间耗尽后允许留下何种产物。不要仅用 `asyncio.timeout` 包住当前 `put()`。

### Should-fix

**S1 — provider 归一化拒绝与“每次收到的最终响应”之间存在缺口。**

- **维度：** 需求保真、数据建模。
- **设计锚点：** 设计 `:82`、`:92`、`:202` 承诺保存每次完整最终内容；`:112–113` 只在收到 `ModelResponse` 后保存，provider 抛异常则仅记录错误。
- **代码事实：** `src/miniagent/providers/qwen.py:142–156` 在构造 `ModelResponse` 前解析工具 arguments；`:72–79` 对非法 JSON 或非 object 抛异常。`:169` 的 `ModelUsage` 构造同样可能验证失败。此时远端响应已经收到，但正文、其他正常 tool calls、usage 和 request ID 尚未交给 Runtime。
- **影响：** 一个 arguments 畸形的 tool call 可使同一响应中其余已收到内容全部丢失。将其标为“没有 ModelResponse”符合当前技术边界，却可能违背用户对完整最终响应的理解。
- **方向：** 显式确定范围。若这些响应也属于应保留证据，增加 provider-independent 的“响应观察/归一化失败证据”，允许保存未成功解析的 arguments 表示和允许字段，不保存 SDK 原对象或 reasoning；若确实排除，必须在成功标准和 coverage 中明确这一缺口。

**S2 — 附件目录的“不读取正文”承诺与现有公开接口冲突。**

- **维度：** 数据建模、故障与规模现实性。
- **设计锚点：** 设计 `:150` 声明导出不读取全部附件验证可达性；`:181` 复用现有 Artifact 存储。
- **代码事实：** `src/miniagent/artifacts/file.py:66–75` 的 `list_for_run()` 验证每个附件；其 `_validate_content()` 调用 `_read_content()`，通过 `Path.read_bytes()` 读取完整正文并校验 hash。因此历史 trajectory 也会先被读取，才有机会由导出器排除。
- **影响：** 随完整工具日志增大，导出会产生与 JSON 上限无关的内存和 I/O；一个丢失的附件也会让目录读取失败，与“不保证外部附件仍可读取”的契约不符。
- **方向：** 明确增加最小的 manifest-only 查询接口，或带清晰语义的查询模式；保持现有校验型接口行为，避免影响其他消费者。

### Nit

无。未发现需要阻止方案的 schema-version 选择或通用架构风格问题；版本不升级是明确的用户约束。

**总结：保留当前 Event 主线与 CLI 导出的总体方案，先补齐完整输出 spool、Artifact 取消提交协议和 provider 归一化失败范围。**
