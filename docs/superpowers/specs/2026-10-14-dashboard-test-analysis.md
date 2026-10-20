# 只读 Trace Dashboard：Test Analysis

分析日期：2026-10-19

依据：两份设计的最终 review 工作区版本，而非其旧提交版本。

- D：[Dashboard 详细功能设计](2026-10-14-dashboard-design.md)，尤其 §13 D01–D16。
- A：[Dashboard 架构设计](2026-10-14-dashboard-architecture-design.md)，尤其 §4–13、§15 A1–A11。

状态：设计阶段测试分析；未实现或执行 Dashboard 测试，不构成实施授权、发布结论或现有测试套件审计。

Review修订：按 [Test Strategy Audit](2026-10-14-dashboard-test-strategy-audit.md) F1～F5同步TA-17/18/21/24的oracle、数据及 [Test Design](2026-10-14-dashboard-test-design.md) 场景引用；TA-09补限额可达性说明。原audit保留修订前NOT-ADEQUATE快照，本稿修订尚未独立复核。

## 1. 分析范围与结论

Subject 是本机单用户只读 Dashboard 首版：Run 列表、Run 详情、Workspace，以及附属 Event/Snapshot 来源和 Artifact 预览。采用 **Design / Combined pass**：D 与 A 已确认，不重新询问既定产品选择；同时提取风险义务并确定所需测试范围与保真度。

策略结论为 **PARTIAL（修订稿作者自评，待复核）**。F1～F5已在设计层增加确认场景/对照或改写观测协议；D01–D16仍映射到拟议证据路径，并发协调、时间测量、浏览器支持面及真实OS环境局部DEFER。该自评不撤销audit的原NOT-ADEQUATE，不表示验收已通过。未运行测试本身不等同策略不足，也不能把设计映射当作执行证据。

优先保障三类结果：不产生业务写入或不安全读取；不把未知/缺失/冲突事实展示为验证通过；不混合不同页面边界的状态、事件和来源。其次保障资源边界、历史证据解释和交互可用性。

不纳入本轮：Compare、Checkpoint、Recoverable、恢复执行/Workspace、审批操作、任务执行、完整模型请求记录、目标测试全集复核、生产者格式变更、Phoenix/Langfuse 导入同步、公开托管。测试不调用模型、Docker、真实 benchmark 或活动 Workspace，不以新增上述能力消除证据缺口。

本文使用 TA-01～TA-29 作为分析义务 ID，不替代 D01–D16，也不是逐条测试用例。一个义务可需要多个场景；同一场景也可为多个验收提供独立断言。

## 2. 测试依据与判定规则

### 2.1 事实来源

| 事实 | 权威来源与限制 |
| --- | --- |
| Run 状态、reason、usage、配置 | 同读取事务内的 RunRecord；非终态仅是最后记录状态，不证明进程存活 |
| 顺序、工具关联、Span 与审批 | 持久化 Event，sequence 排序；scope/类型/父类别遵循领域契约，不补造结束事件 |
| Task Memory | 固定模板版本摘要、完整渲染消息 hash、CompactionCompleted 与指定 Snapshot/Event 来源；只有 VerifiedEvidence 有精确逐项来源 |
| Artifact | 同 Run 的合法 manifest、受限内容路径、同描述符校验的大小与 SHA-256；文件发现时间独立于 DB 边界 |
| 独立评测 | 同边界 Run/Event + 匹配 config/summary/report/patch 的完整证据链；当前报告适配 harness_version=5.0.2；不声称复核目标全集 |
| 费用 | 持久化 Decimal/cost_usd 与 priced_separately；不按当前价格重算，不重复加摘要费用 |
| 错误与资源 | A§12 固定限制及 HTTP 语义；处理预算是主动检查截止时间，不是任意内核 I/O 的硬实时上限 |

代码抽查仅用于确认当前接入边界：DB user_version 与 Run/Event/Snapshot schema 为 2，manifest 为 1；现有 FileArtifactStore.list_for_run 会校验全部内容，不能代表 Dashboard 的逐项隔离读取。代码和已有测试不替代产品验收。

### 2.2 全局 oracle

- **未知不是结论**：未记录、未配置、未完成加载/校验、已确认缺失、证据不一致/冲突分别有可见标记。只断言“没有通过标签”不充分，还需断言正确问题类型和其他事实仍可见。
- **边界完整**：同页 Run、usage、当前摘要、首批 Event、as_of_sequence、Snapshot 上界来自同一读取单元；分页和来源不得越界。主页面切换/整体刷新建立新边界，文件发现单独显示时间。
- **内容一致**：hash、claim、身份比较针对原始值；展示针对脱敏值。测试 oracle 不调用被测投影或脱敏函数计算期望值，使用独立构造的原文、hash 与预期展示。
- **副作用可观察**：请求前后比较逻辑表、schema/user_version、目录和 Artifact 字节；同时观察 SQL 无写语句、无 initialize/migration 路径。无业务变化不要求 WAL/SHM 字节不变。
- **失败可定位**：422/404/409/503/403/500 不互相替代，不返回空成功页。响应、HTML 属性、片段、游标、验证错误和日志均在泄露检查面内。
- **时间可解释**：游标有效期 30 分钟；轮询 5 秒、失败退避 10/20/30 秒、成功恢复 5 秒。边界的精确时间比较及计时容差在测试设计中固定，不临时放宽，也不引入新产品 SLA。

## 3. 风险台账

排序表示分析优先级，不是量化分数。Exposure 只描述触发条件；没有实际使用频次或线上缺陷数据，不能称为低发生率。

| 风险 / 对应义务 | 可观察失败模式 | 影响 | Exposure | 风险依据 / 证据置信度 |
| --- | --- | --- | --- | --- |
| K1；TA-01、27 | GET 启动迁移、修改 Run/文件或装配执行器 | 损害原运行与证据、破坏既有 CLI | 所有启动和读取/错误路径 | D§1、10、12、D16；约束明确，当前发生频率未知 |
| K2；TA-13～16、23 | 越界/跨 Run 文件读取、FIFO 阻塞、脚本执行或秘密泄露 | 本机文件与凭证暴露、服务不可用 | 不可信 manifest、日志、摘要、参数及文件替换 | D08、D14；A§9、11；威胁面明确，未提供攻击历史 |
| K3；TA-04、05、20、21 | 缺证据或错关联仍显示独立通过/历史验证类型 | 开发者误判修复和验证质量 | Local succeeded、混合 attempt、冲突/损坏/未知报告 | D02、D09、A§10.2；产品 oracle 明确 |
| K4；TA-02、07、22、29 | 新 Run 状态混旧事件、旧响应覆盖新筛选、越界来源 | 时间线和结束原因无法解释 | 活跃写入、翻页、整页刷新与延迟响应 | D13、A§5–6、11.4；并发协议明确，窗口需可观测 |
| K5；TA-08～12 | Tool/Span/compaction 串联、假结束、来源存在但不支持 claim | 错误归因、把摘要当作证据 | 重复 call ID、跨页 scope、多次压缩与坏来源 | D03～D06、A§7–8；规则明确，无既有 Dashboard 证据 |
| K6；TA-17、18、24、25 | 先全量取正文再限流，取消后释放名额而后台仍运行 | 阻塞服务、扩大内存/线程/文件负担 | 大字段、深 JSON、目录扫描、并发请求与取消 | D15、A§12；限额明确，环境测量未落实 |
| K7；TA-06、19 | 费用/Token 重算重复、补丁误选或解析失败当空补丁 | 成本和代码变更解释错误 | 历史价格、子集 Token、多 patch/binary/不支持 diff | D07、D11～D12；算术和选择规则明确 |
| K8；TA-03、26、28 | 筛选分页漏/重、无 JS 不可浏览、键盘/刷新丢位置 | 诊断和演示主路径受阻 | 插入新 Run、相同创建时间、长文本、失败轮询 | D01、D13～D15、D§4；浏览器范围与人工评价需落实 |

## 4. 风险到证据的义务矩阵

Evidence 列是应采集的证据及依据，均为**拟议**，不是已实现或已执行结果。Scope / fidelity 给出最小 SUT，Dependencies 与 Data / environment 说明真实边界；Stimulus 写入 Technique 或 Oracle。没有独立测试价值的私有调用细节不进入断言。

| Risk / criterion | Failure mode | Oracle | Technique | Scope / fidelity | Dependencies | Data / environment | Evidence | Residual blind spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TA-01；K1；D16；A§3、5 | 读取或异常路径写业务数据、迁移/建空 DB、装配 Runtime | 成功/参数错/内容错/取消前后所有逻辑表、schema 与 Artifact 树相同；缺 DB 不创建文件/目录；SQL 无 DDL/DML/initialize/migration；ro+query_only，正常 WAL 可读 | 副作用差分、启动/故障分区 | CLI/app + 真实 SQLite/文件及 SQL 观测 | 实际只读连接、预先由生产存储创建的库 | 无并发业务写入的隔离目录；含错误路由、空/缺库 | D16；逻辑转储、树摘要、语句/外部执行记录 | 只比较表不能证明从未尝试写；需结合观测；WAL/SHM 允许协调变化 |
| TA-02；K4；D13；A§5.2 | 单页混提交边界或分页读入新状态 | 第二连接在受控窗口原子提交新 State/Event/Snapshot；首屏仅完整旧或新组；旧分页 ≤ 原上界且保留旧概览/边界状态；刷新整体使用新组 | 事务竞争、状态转换 | 查询/页面编排 + 真实 WAL SQLite | 真实第二 writer、不可变 Event/Snapshot | 临时库，确定性提交握手；DB 事务结束后才文件/渲染 | 页面 read_at/sequence/version/状态对应组及提交时间线 | 没有公开可协调边界时局部 DEFER；不能靠 sleep 碰窗口 |
| TA-03；K8；D01；A§6.1 | 同时插入导致重复、LIKE 通配符误匹配、计数与页面不一致 | created_at DESC/run_id DESC，严格下一位置；同时间戳不同 ID 不漏重；插入更前 Run 不重现既有行；[from,to)、from≥to 拒绝；%、_、escape 字符字面匹配；同事务筛选计数正确 | 等价类、边界、排序不变量 | list_runs + 真实 SQL/HTTP | SQLite JSON/LIKE 与时间 codec | 页大小 10/25/50/100、空结果、模型/状态多选/unknown、task 200 字符边界 | 行 ID 顺序/计数/游标及参数响应；D01 | 跨请求状态成员和计数允许变化；不要求冻结整个集合 |
| TA-04；K3；D02；D§5–6 | Local succeeded 推断 resolved、缺字段变零、陈旧非终态当存活/失败 | Runtime 终态、Evaluation 及 Artifact 分开；历史验证类型未记录；默认 pass-through 只作装配说明；非终态显示最后记录时间，不擅改状态；列表仅用已存摘要，否则详情查看证据 | 事实组合决策表 | Run/list 纯投影 + 模板集成 | 真实领域值/页面模型 | 各已有状态/reason，Local/unknown、缺模型/usage、过期更新时间、candidate/final 不同 | 独立事实和缺失标记，任务全文/有界错误；D02 | 不验证实际进程存活；没有持久化字段的历史不可补造 |
| TA-05；K3；D02、D09；A§6.1、10 | task 猜 instance、Workspace kind 当 evaluator 证据 | 有/无 benchmark 风格 task 不改变未知 instance/evaluator；Local 无 SWE-bench 卡片，unknown 不推断；配置模型/Workspace 来源为已定义字段 | 输入变换、等价类 | 页面投影 + 实际 SQL 标量提取 | 固定 JSON path、实际 schema | 自由 task 看似 instance；缺/坏 recovery_context；同 task 不同元数据 | 对照页面/列表字段及问题标记 | 默认装配说明不证明历史 Run |
| TA-06；K7；D11–D12；A§7.3 | 子集 Token/摘要成本重复、float 重算、百分比/时长误导 | 独立 Decimal 算例保持持久化分项与 priced_separately；total 不再加 cached/reasoning；摘要只说明贡献；计费完整性未证明；容量与累计量分开；未配置上限无百分比，已配 step 限制达限/超额保真；wall 与 active 分开，负时间不可算 | 算术算例、边界、变换不变量 | Run/usage 纯投影 + 渲染 | RunUsage/config/已记录压缩事件 | 精细 Decimal、零/缺失、阈值前/等于/后、时钟倒退、非终态 read_at | 确切展示值/文案；改变当前价格不改变历史值 | 无新增 time/token/cost 硬预算引擎；展示舍入规则需固定 |
| TA-07；K4；D03、D05；A§4、6.2 | 仅筛当前页、坏行重复、定位受筛选或越界 | SQL 全体已提交范围筛选；稀疏匹配仍成页；无匹配结束；匹配坏行保留 envelope 并推进；顺序按 sequence；定位绕过筛选，目标前后各≤20；Snapshot 同时满足 version/sequence 上界及 Run 归属 | 分区、边界、序列不变量 | queries/routes + 真实 SQLite | 实际 codec、固定 payload 路径 | 时间戳逆序、连续坏行、未知类型、远离首批的目标、错 Run/未来 Snapshot | 有序 ID、占位、下一位置、404/422 适用响应 | 未知 payload 不冒充失败；范围校验不能以只校验 ID 替代 |
| TA-08；K5；D03–D04；A§6.2、7.1 | 跨 Step 串 Tool、deny 当已执行、success+截断变失败 | 按 Run/Step/call ID 关联，冲突不挑一条；execution status 与 completeness 独立；仅失败精确包含 A§6.2 各已知分支，approved/PolicyDecided deny/未知状态不误入；shell exit 仅命令状态 | 决策表、关联反例 | Event/Tool 纯投影 + 筛选 SQL backstop | 实际 Event/ToolResult 类型 | 跨 Step/Run 同 call ID；重复/缺结束；各失败谓词、denied 与 truncated | 关联身份、双标记、筛选 ID 集合；D03–D04 | 未持久化参数/模型 IO 明确未记录，不由 conversation 还原 |
| TA-09；K5；D05；A§7.2 | 跨页/被筛掉起止被误报缺失、伪造 Span 时长 | 7 类 scope 按 span ID/类型/父约束补查；跨页仍配对，重复起止计数不被 LIMIT 隐藏；无结束按边界状态 open 或 incomplete；重复/缺父/非法层级/倒时分别提示；超限为 not_loaded；缺根仍有 Event | 状态/结构分区、分页不变量 | SpanView 投影 + scope SQL 集成 | tracing 父类别契约、实际历史表 | 筛掉开始、跨页结束、异常父链/大量scope、矛盾RunFinished；64/65和1000/1001先核对实际读取路径可达性 | 配对来源/问题/记录间隔与查询范围；TD U6与B15/B16 | 局部SpanView不等于全RunTrace；有限父类别层级不能伪造合法64层链，异常路径提前拒绝时记录限额不可达；不累加重叠区间 |
| TA-10；K5；D06；A§8 | 从任意 assistant JSON 提取、hash 错对象、当前历史版本错位 | 仅固定模板 v1+CompactionSummary；hash 是完整原渲染消息 UTF-8；当前版本、previous/new Snapshot/sequence/保留消息与覆盖 Step 一致；历史从对应 new Snapshot 读；无摘要明确未生成 | 版本/形状分区、hash 反例 | compaction 纯投影 + 精确 Snapshot 查询 | 实际模板/summary/EventSource/SnapshotSource 契约 | 合法当前/历史、JSON 后缀 hash、错误前缀/版本、错 Snapshot/覆盖区间 | 摘要值、版本链接、无摘要/证据不一致标记 | 不调用全历史 Runtime 校验器，不据当前 conversation 补旧历史 |
| TA-11；K5；D06；A§8 | 链接存在即判证据成立、未加载误报缺失、普通字段伪造逐项来源 | 同 Run、sequence/version/消息/祖先范围合法；excerpt 等于实际叶文本且 claim=excerpt，field_path 指实际值且 claim 匹配规范 JSON；missing/不一致与 not_loaded 分开；仅 VerifiedEvidence 精确跳转，其余指承载 Snapshot | 来源决策表、内容反例、链边界 | 来源校验投影 + 真实按 ID/版本读取 | 真实不可变 Event/Snapshot | 有链接但 claim 错、错 leaf/path、越界、断链、32/33 版、缺/超限来源 | 精确 source、所用边界、问题类型；D06 | 超限部分不宣称通过或损坏；摘要自身不是重新验证 |
| TA-12；K5；D06、D11；A§8 | 多次压缩共享 span/attempt 串组或重复计费 | first_requested_sequence 标一次压缩；组内 attempt 正确；AttemptRecorded 优先、结束列表只补缺，同次不重复；冲突/缺起止保留未关联事实 | 序列模型、去重不变量 | compaction 纯投影 | 实际压缩事件类型 | 同 Run 两次 attempt=1、多 retry、记录/列表重复与矛盾、缺起止 | 分组 ID、尝试列表与独立 Decimal 和；D06/D11 | 生产者未保存的尝试无法补造 |
| TA-13；K2；D08；A§9.1 | 一个坏 manifest 阻断全部、扫描未完却称唯一/最新 | UUID 位置分页每批≤100，仅登记元信息；身份/schema/大小异常逐项保留；Event 引用可精确补查页外项；完整发现才确认唯一；部分扫描/多个 patch 不猜默认；晚发布可重发现 | 目录分区、分页/发布序列 | metadata Reader + 真实文件目录 | 生产 FileArtifactStore 生成 manifest，之后构造坏项 | 100/101 项、坏 JSON/ID/版本、1 MiB 边界、早晚 UUID、扫描截止 | 完整/部分发现标记、页外引用、逐项问题 | UUID 顺序不等于创建时间；流式发现仍可能扫描目录，受预算约束 |
| TA-14；K2；D08；A§9.2 | 跨 Run、URI/path 逃逸、metadata.files 触发宿主读 | 只按 Run+Artifact ID 找 manifest；file URI authority 为空、布局匹配 hash；Run/ID/hash/大小相符；非法登记预览409，未登记404；网络 URI/metadata 路径无读取或下载 | 路径/身份决策表 | 受限 Reader + HTTP + 真实文件 | dir_fd、实际 URI/manifest schema | 编码路径、绝对/相对逃逸、错 hash 前缀、跨 Run/错 ref、根外哨兵 | 响应、哨兵未进入输出、外部访问观测；D08 | 功能规则不等同任意 hostile host 安全认证 |
| TA-15；K2；D08；A§9.2 | 内部 symlink 跟随、TOCTOU 换文件、FIFO 卡住 | 根以下全部 symlink 拒绝；中间节点为目录、末级普通文件；同 FD fstat/hash/read；受控替换只得安全已开内容或拒绝，不得读替换内容；FIFO 非阻塞拒绝且其他请求可继续 | 真实文件类型/竞争故障 | Reader/HTTP + 真实 OS 文件描述符 | O_NOFOLLOW/dir_fd、实际 symlink/FIFO/socket | manifest/content/中间节点各处攻击；受控 rename 屏障；独立监督进程 | 文件身份/内容、错误状态、监督超时与回收、并发读结果 | OS 支持面待环境落实；无法稳定协调则相应竞争证据 DEFER，不能 mock open 后称通过 |
| TA-16；K2；D08、D14–D15；A§9.3、11.3 | 截断前脱敏漏秘密、字节边界损坏、旧游标读新内容 | 先大小/hash 校验完整允许文本，再脱敏后 UTF-8 字节分页；每段≤256 KiB且≤2000行，next offset 严格前进，无损连接为同一脱敏视图；已知秘密不出任一段，长行字符安全续段；每次预览重新校验；游标绑定 hash/策略版本 | 边界、连接不变量、替换反例 | redaction/preview + 真实文件/HTTP | 实际 SHA-256、UTF-8 与签名游标 | 私钥/Authorization 跨切点、中文/emoji、超长单行、同 ID 换内容、非法 UTF-8/二进制 | 各段字节/行数、连接结果、标记与409/422 | 无任意原始下载；脱敏不识别任意未知秘密；不保证复制 patch 可应用 |
| TA-17；K6；D15；A§9、12 | 超大正文先读后检查、超大 artifact 仍 hash、批量加载日志 | SQL先envelope/长度/预算再取正文，单请求原始结构化合计≤64MiB，超限只元信息/未完成；Artifact登记与fstat双重大小门禁，实际>20MiB即无content read/mmap/hash输入，即使登记size未超限；只保留必要文本，元信息不打开全部内容 | 字节边界、读取观测 | 查询/Reader/页面真实集成 | SQLite LENGTH 字节语义、真实文件和 SQL/I/O 观测 | 各大小阈值前/等/后、多字节、合计超额；实际M+1且登记M-1、两者均在限额内但大小不符的独立对照 | SQL正文返回范围与实际内容I/O记录；TD B01～10、TC-U9-13～14、TC-U14-09；对应F3修订 | HTML/对象内存不等于原始字节；Q-8可延期HTTP优先级，但不延期虚报size时的零内容读取oracle |
| TA-18；K6；D09、D15；A§9–12 | 坏/重复键/深 JSON 原样展示、字符串括号误算深度 | 坏/重复键/真正深度>64只元信息无原文；浅层字符串/对象键含大量括号及转义不误报深度；合法未知结构统一脱敏可预览但不判评测；先词法预检再递归解码 | 语法分区、深度边界、定向生成 | 受限解析/脱敏 + HTTP | 实际 JSON parser与词法预检查 | 顶层/嵌套重复键、深64/65；单层对象值65对方括号、转义引号/反斜杠、键内括号/转义三个合法正例，均带秘密与可读对照 | 无原文/原异常、浅层正文确可见且无depth问题；TD TC-U9-05、10～12与B18；对应F4修订 | HTTP200不能区分预览与问题页；深度计数仍待定；无需以通用fuzz替代明确词法反例 |
| TA-19；K7；D07；A§10.1 | 多 patch 默认误选、解析失败当空补丁、metadata 当事实 | 匹配评测证据优先，否则仅完整发现唯一合法 patch 默认；多 attempt/关联冲突需选择；增删/rename/引号空格/binary可解释；不支持格式有界原文，完整解析后才判断 files 不一致 | 选择决策表、diff 格式分区 | workspace/diff 纯投影 + Reader 集成 | 真实已登记 patch 文件 | 多 patch、页外引用、无活动 Workspace、metadata/diff 差异、不完整解析 | 默认选择依据、文件列表/原文/问题；D07 | 不运行 Git/应用 patch，不证明可应用性或评测通过 |
| TA-20；K3；D09；A§10.2 | 不同 Run/attempt/instance/patch 混成通过 | 同边界 succeeded+passed Event、事件引用、config/最终summary/report/已验hash patch 全链一致才显示已记录独立评测通过；缺必要项不完整，明确矛盾冲突；日志缺失不自动推翻已完整 verdict 链 | 证据链决策表、逐条件反证 | evaluation 纯投影 + 真实文件/DB集成 | 当前生产者 config/summary 与manifest/ref | 完整链；逐一替换 Run/attempt/instance/patch/hash/submission；collecting/cancelled/failed；多summary及页外引用 | 关联图、独立三事实、正确问题与通过标签 | 不靠数量/关键词/最新文件名补证据；缺失与冲突并存时组合呈现需定稿 |
| TA-21；K3；D09；A§10.2 | report 自证目标覆盖、未知 harness/JSON 放宽成功 | 仅当前形状+5.0.2；唯一instance key、严格bool/必要字段、真实对象与list[str]；resolved=true、非空patch、成功apply、无infra_failure，tests_status组内无重复/交集/已记录失败；未知形状/版本仅脱敏预览；不由报告补目标全集，通过仍注明覆盖无法复核并称报告记录的测试 | 报告分区、契约/反证 | report 适配器 + manifest/HTTP backstop | 真实生产者结构样本；独立预期 | 保留未知版本/形状、错顶层key、组内重复/交集/失败等语义反例；C_good对照；五bool逐字段同义字符串/数字/null、四必需bool缺失、infra_failure缺省有效；已知容器错形状/缺字段/非字符串元素，各自重建合法hash/ref | 消费者adapter与真实Workspace入口的字段问题/禁通过及合法对照；TD TC-U12-13～16、U12-E1～E9补充既有语义反例；对应F1修订 | 上游StrictBool/parser测试不能证明Dashboard实际接线；不从report反推目标；无法复核全集仍是已确认范围 |
| TA-22；K4；D13、D15；A§11.4 | 旧请求覆盖新筛选、轮询混边界/丢位置或重读日志 | 请求代次拒绝旧响应；详情整体切边界并保筛选/锚点/展开，远处锚点仅定位窗口；主页面新边界、来源旧边界；Workspace刷新元信息保留已开日志内容/时间，显式重载才hash；晚发布手动发现 | 交互状态转换、受控响应乱序 | 实际模板/HTMX/本地JS + 浏览器 | 真ASGI、SQLite/文件；浏览器传输调度只控制延迟/失败 | 旧慢新快、锚点不在首批/不匹配筛选、日志改动/晚发布 | DOM身份/边界/位置、网络请求、预览read_at；D13 | 不重复纯计算分支；无可观测代次/定位证据时测试设计须落实 |
| TA-23；K2；D14；A§11.2–11.3 | HTML/script/URI 可执行，输入/路径/搜索词泄露 | 全页/片段/属性/错误/日志统一脱敏和转义；普通 token秘密遮盖、仅schema白名单统计token保留；链接只受控ID/相对路由；Host精确端口、Origin同源、cross-site拒绝403，无Origin导航可用；CSP/no-store/nosniff/referrer/frame规则生效 | 攻击输入分区、跨表面哨兵、浏览器执行backstop | HTTP/模板/日志集成 + 浏览器小场景 | 真实Jinja/中间件/本地资源 | task/payload/metadata/summary/diff/错误/Pydantic input和ctx；主机尾缀、错端口/Origin、无Origin | 响应及日志无秘密/宿主路径、无脚本副作用、响应头；D14 | 已知功能安全契约有oracle；完整安全/公开演示数据审查另交专项 |
| TA-24；K6；D15；A§12 | async timeout结束但SQL/线程继续、取消后超卖并发 | 真实busy按1秒策略后503且不无限重试；SQL先确认实际开始并独立记录t0/5秒预算，越deadline后验证中断/503/真实任务完成；实际decode/projection/hash/redaction在线程且在名额区间；最多2重任务、第三快速503，取消HTTP不早释名额、工作完成后可再读 | 确定性故障、并发/取消序列、实际阶段线程归属观测及截止前/后对照 | 真实工作线程/SQLite/ASGI服务 | 实际锁竞争、受控时钟/数据，实际受限任务 | 真实锁、实际昂贵SQL、目录截止、2占用+第三个/取消、正常有界重计算；G-SQL开始屏障与G-STAGE阶段记录 | 实际线程ID/名额持有区间、语句开始/截止/中断/工作完成；TD TC-U14-05/07～08/11～12；对应F2/F5修订 | 暂停时health不证明计算线程；接口/计时Q-4/5可DEFER，但已确认SQL跨deadline仍运行是产品FAIL；不保证内核慢IO硬5秒 |
| TA-25；K6；D15；A§12 | 不同异常吞为200空页、原始codec异常泄露 | 启动结构错失败；422参数、404实体不存在、409登记冲突、200超限/单坏项、503资源、403同源、500物理损坏/未知错误；HEAD同规则无body；每类正确导航/问题/错误ID | 错误分类表、真实故障注入 | CLI/HTTP + 实际 DB/文件 | schema检查、真实锁/坏文件、受控外部IO失败 | 缺表/列/JSON能力、单行新schema、物理坏DB、权限错、参数错 | exit/status/固定code、无原文/凭证/空成功 | 未知异常服务端诊断仍须脱敏；不mock异常类替代真实故障主证据 |
| TA-26；K8；D§4、D13–D15 | 自动轮询默认开、隐藏/终态仍请求、失败清内容 | 默认手动；启用后5秒，隐藏暂停、终态停止；连续失败10/20/30封顶，成功回5；原内容/read_at保留；终态手动可读新Artifact；URL保筛选、空结果可清除 | 刷新状态模型、受控时间 | 浏览器交互小场景 + 真服务冒烟 | 真HTMX/JS定时器；传输层受控失败 | 显隐/终态转换、连续失败后成功、请求中改筛选 | 请求时间序列、DOM/read_at、刷新入口 | 精确定时用受控时钟，真浏览器仅验证接线；不靠长sleep或重复E2E |
| TA-27；K1；D10、D16；A§11.1、14 | extra强制依赖、读取.env/凭证、旧入口被改 | 最小安装无Web依赖时原miniagent TASK可解析运行，dashboard缺extra有安装说明；有extra无凭证/.env仍启动且显式路径优先；仅database/artifacts/port/help，port1–65535；127.0.0.1单worker不自动浏览器；无Compare/Checkpoint/恢复/experiments路由入口 | 包装/CLI契约、配置分区 | 真实wheel/隔离安装/子进程+HTTP | Typer、optional依赖、本地打包资源 | 最小/extra两种安装；含恶意.env/无key；port0/1/65535/65536 | help/exit/启动监听、路由方法表、旧调用结果、资源可取 | 只跑--help不足以证明旧TASK解析；原运行可用确定性外部provider边界替身 |
| TA-28；K8；D§4、13 | 三页和来源链不可浏览、无JS失效、中文/时区/键盘含混 | 一条列表→详情→Event/Snapshot来源→Workspace→预览主路径；断网后本地资源可用；无JS为普通GET/UTC/手动刷新；有JS本地时区+UTC提示；状态文字与颜色、键盘焦点、长diff/移动表格横滚可用 | 组装旅程、人工探索 | 真实浏览器+打包应用 | 真实Jinja/HTMX/CSS、合成持久化证据 | 长中文/长行/空结果/无压缩/坏单项，已销毁Workspace | DOM/请求/焦点及人工记录；D三页面DoD | 无浏览器/辅助技术支持矩阵或正式WCAG级别，不声称全面兼容或无障碍认证 |
| TA-29；K4；D13、D15；A§4、6.3 | 伪造/错用游标越界读取、参数绕过、游标泄露搜索词 | 用途/版本/签名/Run/规范化筛选/完整位置/上界/状态/时间校验；Artifact绑定ID/hash/策略/位置；篡改、过期、重启后旧令牌及非法范围均422并提示重载；游标≤4KiB且无秘密/原始搜索词/路径；未知参数/排序/过滤值、非法UUID/offset拒绝 | 签名向量、语义决策表、参数/时间边界 | cursor纯契约 + 各用途真实HTTP/SQL/文件backstop | 标准HMAC-SHA256、实际进程随机密钥与参数模型 | 合法签名非法payload、用途互换、上界未来、服务重启、超长/损坏编码 | 独立向量比对、422、实际返回范围；D13/D15 | constant-time比较需实现复核；临界时间语义局部DEFER；签名不是授权 |

## 5. 验收追踪与技术取舍

### 5.1 D01–D16 覆盖入口

| 验收 | 分析义务 | 验收 | 分析义务 |
| --- | --- | --- | --- |
| D01 | TA-03 | D09 | TA-05、18、20、21 |
| D02 | TA-04、05 | D10 | TA-27 |
| D03 | TA-07、08 | D11 | TA-06、12 |
| D04 | TA-08 | D12 | TA-06 |
| D05 | TA-07、09 | D13 | TA-02、22、26、29 |
| D06 | TA-10、11、12 | D14 | TA-16、23 |
| D07 | TA-19、28 | D15 | TA-16～18、22～26、29 |
| D08 | TA-13～16 | D16 | TA-01、27 |

A1/A2 的依赖隔离由 TA-01/27 承接；A3/A11 的页面边界由 TA-02/07/22 承接；A4 游标由 TA-29 和下面的共通契约承接；A5 配置由 TA-27；A6/A7 来源由 TA-09～11；A8 由 TA-20/21；A9 由 TA-14～16；A10 由 TA-17/18/24。此映射是分析追踪，不证明实现或运行状态。

### 5.2 游标与跨子系统规则

以下不是额外独立功能，而是 TA-03、07、09、16、22 共用的参数边界。使用一组参数化契约样本覆盖适用用途，避免每条路由复制相同签名测试。

- Run 列表/Event/来源边界/Artifact 游标分别校验用途、版本、规范化筛选摘要、完整位置、Run、上下界、边界状态及时间；Artifact 额外绑定 ID/hash/脱敏策略/位置。
- 篡改单个字段/签名、错 Run/用途/筛选、未来已提交上界、非法位置、4 KiB 上下边界、30 分钟临界与过期、服务重启后均有明确拒绝路径；验证失败返回 422 和重新加载入口。
- 签名需要与标准 HMAC-SHA256 的独立已知向量比对，并核对 constant-time 比较；只做 encode/decode round trip 会漏掉两端共享错误。浏览器拿到的payload不能含原始task搜索词、秘密、宿主路径；游标不承担授权。
- 按游标用途执行真实 SQL/文件 backstop，验证“签名合法但语义非法”仍拒绝。游标到期瞬间 `<`/`<=`、未来签发容忍策略若实现设计未明确，局部 DEFER，不能由测试作者任意选定。

横切规则按治理集合验证：展示脱敏集合为 task/output/payload/summary/metadata/diff/log/错误与访问日志；只读集合为启动、完整页、片段、来源、预览、HEAD及各错误路径；工作线程/资源治理集合为SQL、目录、正文解码、投影、hash、脱敏。使用代表性不可信内容和参数化集合验证每个入口确实到达机制，再用机制自身反例验证强度；不能只测redaction函数后声称所有页面安全。

### 5.3 最小充分组合

| 候选范围 | 速度 / 利用率 | 维护性 / 可靠性 | 特有信心 | 选择 |
| --- | --- | --- | --- | --- |
| 纯投影、决策表、独立状态/算术模型 | 快、低资源 | 确定性好，按公开输入输出 | 配对/来源内容/证据链/费用/选择规则 | 主承接逻辑分支；不证明SQL、文件或HTML语义 |
| SQL+ASGI+真实Artifact | 中等、本机无需网络 | 每测试独立临时数据，诊断明确 | SQLite隔离/JSON/排序、HTTP参数/错误、生产者数据兼容与逐项隔离 | 主承接跨边界证据；不能用fake DB替代 |
| 真实OS/工作线程/已组装服务 | 较慢、限少数并发场景 | 需握手/监督/可靠清理 | WAL竞争、FIFO、描述符替换、后台取消、event loop响应 | 必须保留；进程内投影不能替代 |
| 浏览器单一跨页主路径+交互小场景 | 较慢、成本较高 | 易受布局和时间影响，固定选择器与受控调度 | 打包资源、HTML/JS组装、请求代次/锚点/安全执行 | 主路径仅一条；轮询/乱序独立小场景不复制业务组合 |
| 真实模型/Docker/SWE-bench重跑 | 最慢、有外部成本 | 结果随机且风险不相关 | 模型/评估执行能力 | 首版不要求；从持久化结构样本测试消费契约 |

评测、Patch默认选择与失败筛选使用决策表，已知多条件规则不能以pairwise替代。分页/脱敏可用排序、连接、插入不重复等性质检查扩大数据输入，不强制新属性测试依赖。diff/JSON/URI从已知反例和有界定向生成开始；只有出现未建模崩溃/挂起再扩大fuzz。浏览器不承担每种报告组合与Decimal计算。

不要求通用chaos、全量benchmark、压力/soak或统一覆盖率/层级比例。有限资源约束的证据足够针对当前风险；尚无吞吐、峰值RSS、并发用户或P95目标，不能发明负载验收阈值。选择性mutation可用于确认关键oracle能检测：去掉Run归属/claim校验、把not_loaded改missing、删除失败谓词、重复累计摘要费用、允许未知report通过、先分页后脱敏。无需设置mutation百分比。

### 5.4 Review响应与设计追踪

| Finding | 同步义务 | Test Design入口 | 关键修订 / 残余 |
| --- | --- | --- | --- |
| F1 | TA-21 | TC-U12-13～16、U12-E1～E9 | 已知类型的字符串/数字/null/缺字段/错容器与list元素反例，经合法完整性消费链验证；原生bool及infra_failure缺省有效对照。不是仅测false语义或producerparser |
| F2 | TA-24 | TC-U14-11、G-STAGE；原TC-U14-07仅证明门禁 | 实际重阶段thread_id、请求与名额区间直接观测；暂停health不再作线程归属oracle。Q-4设施仍待落实 |
| F3 | TA-17 | TC-U9-13～14、TC-U14-09及B10 | 小登记/实际超限时零内容read/mmap/hash输入；等大小及限额内不符对照。顶层状态Q-8不影响零读取oracle |
| F4 | TA-18 | TC-U9-10～12及B18 | 三个浅层字符串/转义/键内括号正例必须有脱敏正文、无深度问题；真深层无原文。深度计数细节仍待定 |
| F5 | TA-24 | 修订G-SQL、TC-U14-05/12 | 查询开始为前置、中断为后置；跨截止仍活动且需监督回收记产品FAIL，不因无handler记DEFER。Q-4/5只限设施/测量缺口 |

每项均为设计响应，不表示已执行或已复核关闭。逐项修订见TD§4.4；后续review仅重评这些受影响行及映射，不重新选择已确认产品方案。

## 6. 数据、依赖与环境

### 6.1 真实依赖与替身

- SQLite 使用项目支持的真实版本/JSON能力，独立临时DB，生产 schema 初始化在测试准备阶段完成；被测 Dashboard 仅ro/query_only。不mock查询、codec、事务、hash、文件安全打开、锁或线程行为。
- Artifact合法样本由真实FileArtifactStore生产，保存类型/metadata/ref的实际格式；在持久化边界构造损坏、未知和攻击样本。生产Store的批量拒绝语义只可作基础契约，不能充当Dashboard隔离Reader。
- 纯投影直接使用真实领域值和可验证输入容器。对报告oracle独立列出证据条件，不能把被测adapter或parse_instance_report的结果反用为预期；原完整目标校验器的测试不证明当前无目标清单的消费策略。
- 浏览器可在网络传输边界提供503/延迟/乱序响应；仅表示失败/排序，不证明真实SQLite busy或服务线程停止。真实锁/并发后盾由TA-02/24补充；至少一条主路径使用实际应用响应。
- 时间/随机服务密钥用明确配置边界或受控测试时钟控制临界条件，再保留真实计时/进程重启接线证据。若公开测试边界尚不存在，将屏障设计交给后续测试设计；不patch私有实现来制造通过。
- Provider/Workspace/Evaluator应该完全不进入Dashboard依赖图。旧CLI兼容所需外部Provider可用遵循实际协议的确定性stub；其目的只在旧解析与装配回归，不验证模型能力。

### 6.2 数据设计与复现

每测试独立Run/attempt/Artifact UUID、临时DB/根目录、端口和进程；共享不可变样本/builder，不共享可变库。回收仅限本测试资源，取消后等待后台真实任务完成，避免泄露连接/FD/线程影响后续案例。

关键数据族：

- 同created_at多Run、包含 `%`/`_`/escape/中文task、多状态/unknown字段、稀疏SQL筛选和坏行穿插；序列与时间戳刻意不一致。
- 同call ID跨Step/Run、跨分页scope、重复起止/错父、根缺失、多次压缩共享span且attempt重用、claim有链接但不被内容支持。
- 完整与逐项破坏的评测链、同attempt多summary、相似但不同patch/submission、未知harness、报告组重复/互斥反例；不复制私有目标测试或宿主准备数据。
- hash/size不符、manifest损坏、未知类型、各层symlink/FIFO/socket、路径替换、晚发布、部分目录发现；Workspace目录故意不存在。
- 秘密只用可检索合成哨兵，覆盖结构化敏感键、赋值/Bearer/Authorization、已知前缀、私钥跨分段、数字token秘密与合法usage统计白名单；不放真实密钥。
- 每个A§12限制在实际可达路径上设计邻界；字节限制按UTF-8字节，字符限制按HTTP契约字符，条目/层数按定义计。Run页大小为枚举，不用连续数值边界代替合法集合。Span父链/scope上限先分析有限父类别及200事件页约束：异常历史提前被拒绝时记录边界不可达，不为测试放宽领域契约或声称合法64层链通过。

样本记录schema/模板/harness版本、原始bytes及hash、Run/Event/Snapshot关系、预期问题类型和独立计算说明。生成式反例保存seed与最小失败输入；竞争案例保存初始状态、外部握手、提交/打开/响应时间线与实际FD身份，不只留截图。

### 6.3 环境与计时

查询和投影不依赖模型、Docker、CDN或Node。包装检查需要最小安装与dashboard extra两种隔离环境，实际wheel携带templates/static，并验证缺Web依赖的友好提示。无凭证、无.env及存在不应读取的.env均为必要输入。

文件安全与WAL在实际目标OS运行；项目当前使用macOS，Linux作为拟议兼容验证环境，产品是否承诺及具体版本应在测试设计中登记。不能在一个OS上mock O_NOFOLLOW/FIFO后宣称另一个已验证。真实可用环境及负责人尚未指定。

竞争用第二连接、进程/管道握手或公开读取生命周期观察协调，不靠固定sleep赌窗口。时间断言分离产品策略值、测量时钟、调度容差和测试监督上限；5秒主动预算不等同请求必须在5秒硬截止。busy/预算/并发测试须证明底层任务确实结束或仍占名额，HTTP返回503不是唯一oracle。禁止重跑到绿；环境不可用、测试夹具错误、产品失败分别记录，跳过或隔离不算证据。

## 7. 基础证据与未决项

### 7.1 当前可复用材料

以下是只读抽查，不是全量portfolio audit，本轮未执行这些测试；未发现dashboard产品包或专用测试目录。

| 材料 | 可复用基础 | 不能证明的首版义务 |
| --- | --- | --- |
| tests/storage/test_sqlite.py | 实际持久化重开、sequence、原子State/Event/Snapshot、schema拒绝与scope | Dashboard mode=ro生命周期、SQL筛选/预算、跨HTTP页面边界及占位降级 |
| tests/artifacts/test_file_store.py | manifest/content生产格式、metadata重开、hash篡改拒绝 | 单坏项隔离、流式目录、FD安全、FIFO、脱敏分页；现有批量坏项拒绝不能冒充隔离通过 |
| tests/runtime/test_compaction.py、runtime/context.py与模板 | 摘要结构、hash及来源语义依据 | 有界来源消费、not_loaded区别、不同压缩组关联、页面来源权限/边界 |
| tests/evaluation/test_swebench_report.py | 当前报告字段与有目标清单时的严格覆盖规则 | 无目标清单的Dashboard证据链与免责声明；不可用report自身生成目标去复用通过路径 |
| tests/cli/test_main.py | 原任务入口与输出/信号行为基线 | 独立console script、extra可选安装、无凭证/不读.env、loopback及静态打包 |

### 7.2 质量扫描与局部 DEFER

| 方面 | 可确定的测试义务 | 局部未决 / 移交 |
| --- | --- | --- |
| 功能/完整性/并发 | D01–D16与TA矩阵；证据分层、签名边界、损坏隔离；已按F1～F5补场景/对照/协议 | TA-02/15/24的实际协调接口、G-STAGE线程记录及G-SQL开始屏障仍需落实；不接受内部monkeypatch/竞速sleep，已观测行为失败不能降为DEFER |
| 兼容/版本 | 只读检查当前DB结构，单行未知schema隔离；manifest1、模板1、harness5.0.2；旧CLI保留 | 不要求迁移；后续生产者版本变化仅重评受影响义务 |
| 性能/资源 | A§12固定字节/批次/深度/时间/并发限制 | 实际计时容差、任务停止观测和OS环境负责人DEFER；无吞吐/RSS SLA，不能新设阈值 |
| 安全/隐私 | 已写明Host/Origin、CSP、FD路径边界、脱敏与日志限制有客观oracle | 完整威胁模型、未知秘密识别或公开演示数据审查交安全/数据专项；不阻塞已明确规则的分析 |
| UX/可访问性 | 中文文字状态、键盘、横滚、JS/无JS时区、刷新保位有可观察义务 | 浏览器/辅助技术支持矩阵、正式WCAG等级与视觉可用性评价负责人未给出；不宣称认证 |
| 证据解释 | 缺失、冲突、未加载、未配置区分；三个独立事实 | 缺失与冲突同时存在的组合文案、金额舍入精度、游标到期瞬间和未来签发规则，实施/测试设计应明确，未定部分DEFER |
| 可运维/安装 | 缺DB/结构不支持失败、extra安装说明、500错误ID、503可重试、本地资源离线可用 | 不扩展生产探针、公开部署、CI门禁或审计保存政策 |

没有凭据支持实际使用频次、既往Dashboard缺陷率或flake率。人工探索需保留具体数据、浏览器、动作、预期与发现：重点审读“已记录独立评测通过”“完整目标覆盖无法复核”“未完成校验”“最后记录状态”是否容易误解；用键盘完成来源往返并查看长diff。人工评价不由DOM存在性取代。负责人和时间盒由测试设计阶段登记。

## 8. 充分性与移交

```text
Subject:              本机只读Trace Dashboard三页面与附属来源/Artifact预览
Mode:                 Design
Strategy pass:        Combined
Decision:             PARTIAL（修订稿作者自评，待复核）；原audit的NOT-ADEQUATE保留为历史，局部DEFER仍在
Test basis:           D§1–15、D01–D16；A§4–13、A1–A11及最终review澄清
Architecture basis:   两份2026-10-19已确认的工作区文档；首版FastAPI/Jinja/HTMX直接只读消费
Quality risks:        业务写入/不安全读取 > 错误验证结论/秘密泄露 > 边界混用/来源串联 > 资源失控 > 解释与交互
Manual evaluation:    证据与成本/非终态文案审读、键盘来源往返、长diff/窄屏/时区探索
Adequacy signals:     D01–D16映射、横切治理集合、独立oracle、真实SQL/文件/浏览器后盾及显式DEFER
Handoffs:             测试设计细化TA义务、时序屏障/边界测量与环境；Portfolio字段落实后交shift-left选最早位置
                      CI/CD负责触发/门禁/证据保留与失败行动；traceability负责实现与执行状态
                      安全/可访问性/UX专项承接策略外评价；optimization仅在实际数据出现后优化成本和flake
Residual risk:        测试未实现/未执行，环境及负责人未知；未知秘密、完整目标覆盖和未持久化历史不在首版保证内
Next action:          对TA-17/18/21/24及TD§4.4的F1～F5响应重新review，再落实Q-4/Q-5的观测与测量环境
Verification:         核对review与报告/资源契约，同步设计ID映射、oracle及可达性；检查两份文档结构/链接/diff
                      未实现或执行测试；本轮文档修订不运行runtime tests，不自动改变原审计结论
```

当前没有风险接受记录，未决项不视为已接受。后续只重评架构、oracle或环境变化影响的行；不得用测试数量、覆盖率或已有绿色suite将本分析升级为ADEQUATE。实施计划、测试实现及测试执行分别作为后续工作，不在本文中预报通过。
