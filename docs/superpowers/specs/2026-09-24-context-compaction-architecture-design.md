# Context Compaction 架构设计

## 1. 设计目标与现状

本设计落实已确认的 [Feature Requirements](2026-09-24-context-compaction-design.md)：长任务接近模型窗口上限时继续执行；在可比较的长任务中减少计入摘要调用后的总计费 token；保存可供未来跨进程恢复准确重建的压缩状态。首版支持当前 Qwen Provider，不实现跨进程恢复执行器、手动压缩命令或 Provider 原生压缩。

当前路径为 `AgentRuntime` → `RunExecution` → `RunJournal` → `RunRecorder` → SQLite。业务请求由 `AgentRuntime._build_request` 从 `ConversationState.messages` 构造。QwenProvider 使用兼容 OpenAI 的 Chat Completions 接口。完成 Step 时已有包含完整 `RunState` 的不可变 Snapshot；跨进程恢复尚未接通。

## 2. 方案选择

| 方案 | 优点 | 代价或不适配点 |
| --- | --- | --- |
| **Runtime 编排，Context 模块处理压缩** | 可以检查完整业务请求、保持 Step 语义，并通过现有事务提交新状态。 | Runtime 边界增加预算与摘要编排。 |
| Provider 内部压缩 | 以后可能利用 Provider 原生上下文表示。 | 当前 Qwen 契约没有原生压缩项；Provider 无法原子提交 Run 状态和 Snapshot。 |
| Recovery Controller 编排 | 将上下文压力与其他恢复决策集中。 | Recovery 编排尚未接通，会使常规上下文维护依赖另一个未完成子系统。 |

选用第一种方案。Claude Code 公开文档说明，对话压缩后仍需重新提供有效的固定指令和部分项目上下文；OpenAI 官方资料说明，压缩后的窗口是后续请求的新起点，后续消息向其追加。这些是 MiniAgent 的设计参考，不推断其私有摘要算法，也不将 OpenAI 的不透明压缩项当作 Qwen 文本消息使用。

## 3. 组件职责

| 组件 | 职责 | 依赖 |
| --- | --- | --- |
| `AgentRuntime` | 终止检查后、`begin_step` 前调用 `_build_request`，请求 Context 做预算判断，按需编排压缩；从已提交状态再次调用 `_build_request`；处理同一 Step 的一次超限重试。 | Context 模块、`RunExecution`、Provider |
| Context 模块 | 估算完整请求、按保留量下限选择已完成的整组 Step、判断软触发的预计 token 回收、构造无业务工具的摘要请求、校验摘要与来源，并返回候选 `ConversationState`。不执行工具，不直接写库。 | Message/Request 契约、模型相关 token 估算器 |
| `RunExecution` / `RunJournal` | 记录压缩尝试及已知用量，通过 `RunRecorder.commit(snapshot=True)` 发布候选状态、完成事件和 Snapshot。提交成功后才切换内存状态。 | Runtime Store 契约 |
| `QwenProvider` | 继续提供模型生成；把可识别的上下文超限响应转换为专门错误。不决定删除哪些历史。 | Qwen Chat Completions API |
| SQLite Snapshot Store | 保留被压缩版本引用的不可变历史 Snapshot，增加按 Run ID 和版本读取的能力。 | 现有 Snapshot 表及版本校验 |

`AgentRuntime._build_request` 仍是**业务** `ModelRequest` 的唯一拼装入口，工具定义和生成参数不移入 Context 模块。Context 接收已拼装的请求用于预算判断，仅生成候选活动消息。摘要请求独立构造，`tools=()`、输出有界；QwenProvider 对空工具集合不提供可调用工具。首版只有一种压缩实现，不增加策略插件体系。

创建 Run 时，Runtime 按 `agent_spec.allowed_tools` 的顺序从 `ToolRegistry` 取得有效 `ToolDefinition` 与各工具的 `contract_metadata`，随 `RunDefinition` 保存。业务 `_build_request` 使用已保存的 `agent_spec`、工具定义和活动消息，运行期间不再从可变注册表重新拼装工具 Schema；工具执行仍由当前 `ToolRegistry` 负责。未来恢复入口在发送模型请求或执行工具前，须确认当前注册表能提供所有已保存工具，且工具定义与契约元数据逐项一致；不一致则明确拒绝恢复。只保存名称或摘要哈希不能重建相同的 `ModelRequest`，因此这里保存实际定义并用契约元数据检查执行兼容性。

CLI 配置链为 `Settings.model_limits_file`（默认 `config/model_limits.toml`，路径可由同名环境设置覆盖）→ `bootstrap` 按最终选定的 Provider/模型读取对应条目 → 将 `context_window_tokens` 放入 `RuntimeConfig`、`max_output_tokens` 放入 `AgentSpec.generation` → `ApplicationDependencies` 将配置传给 `AgentRuntime`。`--model` 先决定模型，再查配置文件；无匹配条目或配置无效时，在创建 Workspace 和 Run 前失败。`DEFAULT_AGENT` 不再硬编码 `max_output_tokens=131_072`，其业务输出上限由配置文件为默认模型提供。直接调用 Runtime 的客户端须提供同样的已解析配置；不能依赖 Runtime 猜测容量。配置文件读取一次后，Run 使用随定义持久化的值，不受运行中配置文件修改影响。

## 4. 状态与请求表示

`ConversationState.messages` 表示当前**活动消息**。第一次压缩前它包含完整原文；成功压缩后为：

```text
原 SystemMessage
原 UserMessage(task)
AssistantMessage(已渲染的历史摘要，属于资料而非指令)
保留的近期完整 Step
提交后追加的 assistant/tool 消息
```

系统指令与原始任务分别以 `RunDefinition.agent_spec.system_prompt` 和 `RunIdentity.task` 为准，压缩时保留原文。创建 Run 时按这两个字段构造活动消息的前两项；加载 Snapshot 和每次 `_build_request` 前，Runtime 校验前两项的类型、顺序和内容与定义完全相同，不一致就拒绝发送请求，不从摘要或当前 Agent 参数补写。摘要只使用普通 assistant 历史消息，不伪造 system 或 tool 消息。一次压缩后，已渲染摘要和保留边界保持不变；`accept_model_response` 与 `complete_tool` 继续向尾部追加。Runtime 不在每个 Step 滑动边界或刷新摘要。下一次触阈时，旧摘要与新增的完整 Step 一起生成新版摘要，被覆盖前缀才再次替换。

在 `RunState` 增加持久化的 `ContextProgress`，只保存当前压缩版本引用。对应的 `compaction_completed` 事件是每个版本的权威记录：Run ID、当前与前一压缩版本、来源 Snapshot 版本与事件序列、被覆盖的消息与 Step 范围、保留的第一个 Step、摘要模板版本与摘要哈希、摘要模型的 Provider 与模型名、预算估计、每次摘要调用的已知实际用量及新 Snapshot 版本。重试产生的调用分别记录，不把未知用量写成零。摘要正文只存于活动消息，不在元数据中重复存储。加载时按 `ContextProgress` 引用定位完成事件，校验同 Run、版本链、Snapshot 引用、范围和活动摘要哈希；缺失或不一致即报错。持久化对象不认识的 schema 版本仍明确报错。本产品尚未上线；首版直接采用新 Run/Snapshot 结构，不设计旧数据库迁移、旧 Snapshot 解码或历史版本兼容。已有开发数据库须重新创建后使用此结构。

正常压缩发生在已完成 Step 的安全边界，其最新 Snapshot 包含被替换的原文。若在 `StepStarted` 后、模型响应前遇到意外超限，来源仍是之前完成 Step 的安全 Snapshot；该 Step 尚无新工具结果。存储须保证被引用的历史 Snapshot 不被清理，并增加 `load_version(run_id, version)`。按版本和压缩事件中的范围重建原始片段。无 Run Store 时不另建被替换历史的内存副本；当前压缩调用可以读取尚未替换的活动消息，发布后仅保留新版活动上下文。需要原文才能继续使用的内容，其所在完整 Step 须留在活动原文后缀，不能只留下无法读取的引用；此模式不提供跨进程恢复或持久原文审计保证。

## 5. 预算与摘要流水线

预算估算以 `_build_request` 返回的**完整**业务请求为输入，包括工具 Schema、角色和协议开销。配置文件 `config/model_limits.toml` 为每个 `(provider, model)` 提供唯一的正整数 `context_window_tokens` 和 `max_output_tokens`，两项均必填；与 `config/model_pricing.toml` 分开维护。首版默认 Agent 对应条目使用 `context_window_tokens = 32768`、`max_output_tokens = 4096` 作为保守的运行预算，属于产品配置而非 Provider 规格声明，须在真实 Qwen 容量验收中校准。其他模型只在有对应配置条目时可选择。`RuntimeConfig.context_window_tokens` 与 `AgentSpec.generation.max_output_tokens` 随 `RunDefinition` 持久化；即使 `GenerationOptions` 类型允许空值，启动 Run 前也必须完成解析并拒绝空值。设 `C = context_window_tokens`，`O = agent_spec.generation.max_output_tokens`，`S = max(1024, ceil(0.05 × C))` 为估算误差余量，业务输入硬预算 `B = C - O - S`。配置加载时校验 `B > 0`，且按本节公式计算的摘要输出预留至少为 256；否则在创建 Workspace 前拒绝配置。独立的 `PromptTokenEstimator` 适配 Qwen 的估算，不改变 `ModelProvider.generate`；后续以真实 `ModelUsage.input_tokens` 校正估算，但不把估算当作 Provider 保证。

**具体触发规则**：`RuntimeConfig` 还保存 `compaction_trigger_ratio`（默认 0.70）和 `compaction_target_ratio`（默认 0.50），创建 Run 时校验 `0 < compaction_target_ratio < compaction_trigger_ratio < 1`。每次准备下一业务请求，先计算符合近期 Step 保留量下限的候选前缀。`T > B` 是硬阻断：只要存在可压缩的前缀就尝试压缩，不经过成本判断；压缩后的完整业务请求只需满足 `T_new ≤ B`，否则明确失败，不发送超预算请求。`T ≥ compaction_trigger_ratio × B` 且 `T ≤ B` 时，只在有可压缩前缀且预计能回收压缩 token 成本时软触发。设 `D` 为候选被替换前缀的估计 token 数减去摘要输出预留，`K` 为单次摘要请求的估计输入与输出 token 之和，`H = min(3, max_steps - steps_started)`；仅当 `H ≥ 2`、`D > 0` 且 `H × D > K` 时软触发。这只是决定是否尝试压缩的筛选器，假设后续最多 `H` 次请求各取得近似节省；20% 成本目标仍由第 8 节的固定任务集合验收。软触发的摘要候选须使 `T_new ≤ compaction_target_ratio × B`；达不到该目标但旧请求仍满足 `T ≤ B` 时继续旧版。硬触发只要求 `T_new ≤ B`，因为此时续行优先于压缩幅度。完成一次压缩后摘要与保留边界固定，只有后续新增内容再次达到触发条件才生成新版，因此不会每个 Step 刷新摘要。70% 与 50% 是首版初值，须经真实 Qwen 容量和成本验收校准，而非 Provider 保证值。

`RuntimeConfig` 增加随 Run 定义持久化的 `recent_steps_to_keep`，默认 3，配置值至少为 1。Context 只能选择位于最近 `recent_steps_to_keep` 个已完成 Step 之前的完整 Step 前缀；这些近期 Step 始终保持原文，且在满足相应触发预算的候选中保留尽可能多的额外完整 Step。assistant 的工具调用与对应的全部 ToolMessage 必须成组保留或成组替换。保留量下限不因目标预算而放宽：软触发无法达到 `compaction_target_ratio × B` 时跳过；硬阻断无法达到 `B` 时明确报告容量或压缩失败。固定内容或最小可保留后缀已超出硬预算时，明确报告容量失败。摘要请求也必须满足“估算输入＋摘要输出预留 ≤ `C - S`”；摘要输出预留为 `min(2048, floor(0.15 × B))` 个 token，不足 256 个 token 的配置视为无效。

摘要请求每次都包含来自 `RunDefinition.agent_spec.system_prompt` 的有效系统指令原文、来自 `RunIdentity.task` 的原始任务原文，以及当前适用的固定约束；用户硬约束从原始任务和已提交的上一版摘要、新覆盖的完整 Step 中提取，保留各自来源。前两项即使在首次压缩、没有上一版摘要时也必须提供。请求还包含上一版已提交摘要、新覆盖的完整 Step、有界来源标签和带版本号的 Jinja 模板，不携带业务工具。摘要模板明确要求以固定指令和原始任务理解来源，不在摘要中复制或改写 system 指令。所有这些输入都计入摘要请求的容量估算，超预算时按本节失败规则处理。模型以 JSON 输出以下字段；列表字段无适用内容时使用空列表，不能省略或编造事实：

| 字段 | 必须包含的内容 |
| --- | --- |
| `current_goal` | 原始任务在当前阶段的具体目标和进度；原始任务原文仍单独保留。 |
| `user_constraints` | 仍有效的用户硬约束及其来源；不复制或改写 system 指令。 |
| `verified_evidence` | 可直接核对的观察、对应 Event ID 或 Snapshot 消息范围、来源原文摘录或结构化结果字段，以及观察时的代码状态；测试结果须含命令、退出状态和适用范围。 |
| `workspace_changes` | 已修改的相对路径、改动目的、是否已验证；未知状态明确标记。 |
| `rejected_attempts` | 已尝试方法、失败证据、适用条件和允许重试的条件，避免将一次超时写成永久失败。 |
| `uncertainties` | 未经验证的推断、过时或冲突的信息，以及需要回读的来源。 |
| `open_items` | 尚未完成的工作、阻塞原因与可执行的后续动作。 |
| `next_action` | 下一次模型调用应优先执行的具体动作，并指向对应的 `open_items` 条目。 |

Runtime 用 Pydantic 校验 JSON 结构、必需字段、无工具调用、完整结束原因，以及压缩后业务请求的大小。有 Store 时，摘要中的 Event ID 和 Snapshot 消息范围须属于同一 Run，真实存在于已提交的事件或被引用历史 Snapshot 中，且落在本次被覆盖范围或前一版摘要的来源链内。每条 `verified_evidence` 还须带有有界、可逐字核对的来源摘录，或明确的结构化结果字段和值；Runtime 读取对应来源并校验摘录或字段值确实存在。模型应将仅有推断线索的内容写入 `uncertainties`；摘录不匹配、结果字段缺失或来源无法读取时，Runtime 拒绝该候选压缩，不自动改写摘要。无 Store 时，来源范围在替换前对当前活动消息核验；发布后不能回读的来源不得列入 `verified_evidence`，需要回读的完整 Step 须留在活动原文后缀。`current_goal`、`next_action` 和 `open_items` 不得为空；其他列表可为空，`next_action` 必须对应一个未完成条目。通过后只用固定版本模板渲染一次，并作为 `AssistantMessage` 保存；总摘要受上述输出上限约束。逐字核对仍不能证明观察解释正确，真实任务轨迹验收负责检查语义忠实性。

正常触发须早于旧窗口填满。首版只构造一个可容纳的摘要输入，失败时可按第 7 节重试，不实现分块归纳或中间摘要合并；若摘要输入本身超出预算，则在调用 Provider 前记录独立的摘要输入容量失败原因，不生成摘要调用用量，也不改变压缩版本。若旧业务请求仍在硬预算内，继续旧版；否则以该原因结束 Run，不能发送超预算的业务请求。这样避免在未定义来源映射与合并预算的情况下发布多段摘要，也不静默删消息。

容量预检失败只记录带独立原因的 `compaction_failed` 事件，不记录表示已发起 Provider 调用的 `compaction_requested`；失败事件不生成 Snapshot，也不增加模型请求和 token 用量。预检通过后才进入以下外部调用时序。

## 6. Runtime 与持久化时序

```text
已有完成 Step 的 Snapshot
  → 终止与取消检查
  → _build_request(当前状态)
  → Context 预算判断并选择安全前缀
  → 构造完整摘要请求并检查估算输入＋输出预留 ≤ C-S
  → 记录 compaction_requested 事件
  → Provider 生成无工具摘要，记录已知用量
  → Context 校验并渲染候选活动消息
  → RunJournal/RunRecorder 预定新 Snapshot 版本与 ID，构造候选状态和完成事件
  → RunJournal.compact：一次 RunCommit(新状态 + 完成事件 + Snapshot)
  → 提交成功后切换内存状态
  → _build_request(已提交状态)
  → begin_step，正常业务模型请求
```

请求事件在外部调用前写入，不改活动消息，也不生成 Snapshot。候选摘要不合格时写失败事件及已知用量，不生成压缩 Snapshot。成功摘要**立即**生成新 Snapshot，不等待下一个 Step 完成。`RunJournal` 通过 `RunRecorder` 读取其已确认 `RunRecord.latest_snapshot_version = v`，在构造候选 `ContextProgress` 和 `compaction_completed` 前预定新 Snapshot ID 与版本 `v + 1`；两者引用同一预定版本，`SnapshotChange.expected_version = v`。完成事件的预期序列为已确认的 `last_event_sequence + 1`，该提交只追加这一条完成事件。SQLite 在同一事务内校验实际最新版本仍为 `v`，并校验实际插入的 Snapshot ID、版本和提交后事件序列与候选状态、完成事件一致；冲突则整笔提交失败，不能在提交后静默改写事件或状态。无 Store 的运行不声称有持久 Snapshot 版本。现有 SQLite 事务已能一起提交 Run 状态、事件和可选 Snapshot；`RunJournal.compact` 只是该路径的专用入口。完成事件按第 4 节的版本记录契约保存来源、模型、预算、Provider 请求 ID 与每次调用的实测用量，并与新版活动状态和 Snapshot 原子提交。未来恢复以最后已提交版本为准，校验 Run ID、版本、事件序列、引用完整性与固定消息前缀；已提交摘要按原样复用。

若 Provider 已返回而完成提交前进程中断，旧活动上下文仍为权威状态。已记录的请求事件能表明一次未完成尝试，但实际计费可能未知，未来重试可能再次付费。数据库提交失败沿现有持久化错误路径终止，不继续发业务请求。本设计不声称外部模型调用恰好计费一次。

## 7. 失败、计量与执行安全

`RuntimeConfig` 增加随 Run 定义持久化的 `compaction_timeout_seconds`（默认 60 秒，正数）和 `compaction_max_retries`（默认 1 次，非负整数）。每次摘要 Provider 调用均受单次超时和 Run 取消令牌约束；仅可恢复的 Provider 错误或超时允许重试，最多调用 `1 + compaction_max_retries` 次，取消立即停止。当前 Runtime 没有 Run 总截止时间；若以后引入，总截止时间还须限制每次调用和重试，不能由压缩绕过。每次摘要调用计入 `model_requests` 和 Run 用量，但不增加 `steps_started`。即使摘要被拒绝，已返回的输入、缓存输入和输出 token 及价格仍计入 `RunUsage`。调用中断且没有响应时记录用量未知，不冒充零。压缩事件将摘要调用与业务生成区分，以供 Trace 和费用报告使用。

QwenProvider 在边界将可识别的上下文超限错误转换为专门异常。若预检漏判，Runtime 可压缩**旧的已完成 Step**，在已经开始的同一 Step 内重试业务请求一次。重试有独立模型尝试事件或 Span，不再次增加 `steps_started`，也不重复已完成的工具调用。其他 Provider 错误沿既有模型失败路径。摘要失败但旧请求仍在硬预算内时可沿用旧版；无法安全发送时，以明确容量或压缩失败原因结束 Run。未提交候选绝不发送给业务模型。

来源原文和摘要均为历史资料，不授予工具权限、不改变审批、不修改 Workspace，也不覆盖 Agent 系统指令。来源引用只证明可追溯，不能自动证明内容为真；未验证推断须保留标记。

## 8. 验证设计

- 使用真实 SQLite Store，只在外部模型边界使用 FakeProvider，覆盖首次和连续压缩、近期 Step 保留量下限、完整 Step 配对、历史 Snapshot 按版本读取、取消、单次超时和有限重试、无效摘要、已知与未知用量、同一 Step 的一次超限重试。
- 在压缩提交前及提交中注入故障，验证新状态、完成事件和 Snapshot 全部生效或全部不生效，且 Runtime 从不发送未提交摘要。
- 重新加载压缩提交后的 Snapshot，校验活动消息前两项与持久化的 system prompt 和原始 task 完全一致，再用其中持久化的 `RunDefinition.agent_spec` 和有效工具定义调用 `_build_request` 构造下一次请求，与提交时的请求逐项比较；篡改固定消息，或替换当前注册表的工具描述、Schema、契约版本后均须拒绝恢复。校验当前版本引用、完成事件与历史 Snapshot 的关系，验证后续 Step 只追加消息，直至再次触阈。
- 覆盖首次压缩及再次压缩的摘要请求输入：均含原始任务、有效固定指令及其来源；固定内容计入摘要预算。注入 Snapshot 版本冲突，验证候选状态、完成事件和新 Snapshot 一起回滚。
- 覆盖 CLI 默认模型及 `--model` 覆盖后从 `config/model_limits.toml` 选取正确条目、配置路径覆盖、缺少或重复模型条目、无效 `context_window_tokens` 或 `max_output_tokens`、`B ≤ 0`、摘要预留无法容纳及阈值顺序错误；验证配置错误在 Workspace 和 Run 创建前报告，提交后请求使用持久化的两项配置。
- 覆盖软阈值下可回收与不可回收的成本估计、剩余 Step 不足两次，以及硬预算触发不受成本判断抑制；硬触发压缩后位于 `0.50 × B` 与 `B` 之间的请求应可续行，超过 `B` 则失败。
- 覆盖摘要请求自身超预算、无 Store 的原文保留与不可回读引用，以及不存在或越出来源范围的 Event ID 和 Snapshot 消息引用；对真实存在但不支持摘要断言的来源、缺失或不匹配的摘录，验证只能归入 `uncertainties` 或拒绝发布。
- 摘要容量预检失败时验证仅有带容量原因的失败事件、没有 `compaction_requested`，并检查事件序列、Snapshot 版本和用量与实际 Provider 调用一致。
- 将摘要请求超预算分成旧业务请求仍在 `B` 内和已超 `B` 两条路径；两条均断言摘要 Provider 调用数为零、失败原因与版本／用量状态，后一条还断言业务 Provider 调用数为零。单独检查 `compaction_target_ratio > compaction_trigger_ratio` 被拒绝；`B=0` 同时违反摘要预留下限，不能只凭泛化拒绝结果证明 `B>0` 条件。
- 用版本固定的真实 Qwen 容量组和成本组完成 Feature Requirements 的验收。成本组计入所有摘要及业务请求，要求总计费 token 中位数至少降低 20%、任务通过率不下降；同时报告按配置价格计算的实际费用和 `cached_input_tokens`。OpenAI 的前缀缓存机制不能证明 Qwen 的实际命中率。
- 正式运行前冻结逐任务输入、允许的最终 Workspace／外部状态、独立判分步骤、失败处理和成本重复协议；保存每次运行的最终差异及判分记录，由非实现者复核任务成功数和成本计算。Agent 的完成声明不充当任务 oracle。
- 按固定清单审阅真实轨迹中的目标、硬约束、未完成事项、证据来源与无依据推断；另以固定的恶意历史工具结果检查真实 Qwen 的后续工具、审批、Workspace 与任务结果。受控 Provider 的 Runtime 权限测试与真实模型行为检查分别保留，不能只检查摘要结构合法。

## 9. 公开参考

- [Claude Code：上下文窗口](https://code.claude.com/docs/en/context-window)：压缩对话历史时，部分指令和项目上下文重新注入。
- [OpenAI Docs：Compaction](https://developers.openai.com/api/docs/guides/compaction)：独立压缩返回下一轮的完整上下文窗口，此后追加新消息。
- [OpenAI Docs：Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)：稳定前缀有利于复用，压缩改写旧前缀可能降低复用，需衡量总体成本。
