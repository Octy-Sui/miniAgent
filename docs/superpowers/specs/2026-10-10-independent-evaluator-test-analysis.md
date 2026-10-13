# Independent Evaluator Test Analysis

日期：2026-10-10

Review 修订：2026-10-13；同步 test design 的五项 review 处理，未执行测试。

## 1. 分析范围与结论

**Subject：** 首版 Docker Workspace 的 SWE-bench 独立校验，包括准备门槛、唯一候选提交、外部执行、可信报告、证据交付、终态事务和资源生命周期；同时验证 Local passthrough 的兼容性。

**Mode / Strategy pass：Design / Obligation。Decision：PARTIAL，架构相关组合与未决 oracle 为 DEFER。** 已确定的需求可以建立测试义务与反例。架构仍是“完整评审稿，等待用户集中审阅”，D1–D3 和运行默认预算尚未批准，因此本次不采用 Combined pass，不把候选 scope/fidelity 当作最终 Portfolio，不宣称已有覆盖或验收通过。

本文完成测试分析，不实现测试、不编写产品代码，也不批准架构。第 4 节列出基于当前架构稿的候选证据路径；规格批准后，仅对受决策影响的行完成 Portfolio pass。

### 1.1 Test basis 与来源优先级

| 来源 | 本次用途 | 限制 |
| --- | --- | --- |
| [需求设计 R](2026-10-10-independent-evaluator-design.md) §1–9 | 范围、行为与验收依据 | 没有规范化 FR/AC ID，以章节追踪 |
| [架构规格 A](2026-10-10-independent-evaluator-architecture.md) §1、5–15 | 已确认门槛，以及待批准的接口、错误与证据细化 | D1–D3 仍开放，其余推荐方案亦不等于批准 |
| [架构 review](../../../architecture-critique.md) | 识别提交取消、Docker daemon、证据来源与内存成本风险 | PROCEED 不是产品验收或规格批准 |
| 当前 evaluation/cli/runtime/artifacts/environments 代码及相关测试 | 确认已有边界和可复用基础证据 | 代码不是新需求的 oracle；只读核对，未执行 |

本文 `TA-01`～`TA-25` 是局部测试义务 ID，不冒充原需求 ID。引用 `R§n` / `A§n` 分别指上述两份文档。

已确认的逐任务就绪门槛优先于 R§4、§9 的全量启动门槛：全局 `ready=false` 时，满足全部逐任务校验的任务仍可执行。25 项全量就绪仍是完整冻结实验前提。D1 默认禁止拉取、D2 允许未单独预检的覆盖镜像、D3 取消引用通过 manifests 追溯，均只按 A 的推荐方案列候选，不视为已确认。

不纳入本次：批量 runner、自动修复/重试、跨进程恢复、通用命令 Evaluator、Windows、强离线隔离、全宿主配额、GC。候选实际能否修复任务由独立评测判定；Agent 的最终文本不参与成功 oracle。

## 2. 风险台账

优先级按可信判定、证据完整性与取消有界性排序，不将影响、暴露和证据信心合并为分数。没有生产事故、发生率或流量材料，暴露描述是触发条件，不代表已知概率。

| 优先级 / 义务 | 可观察失败模式 | 影响 | 暴露条件 | 风险依据与信心 |
| --- | --- | --- | --- | --- |
| 高 / TA-01～04 | 不支持或未就绪任务启动；Local 意外依赖 harness；运行中换验证规则 | 错题、验证基准失真、兼容性回归 | 缺上下文、manifest 更新、混合预检状态、损坏数据 | R§3–4、A§5–6；契约明确，宿主数据状态仅为文档快照 |
| 高 / TA-05、06、20 | 检查、执行和删除指向不同 daemon；误删其他 Run/Agent 容器 | 资源泄漏、他人任务中断、评测来源不可信 | context 与环境冲突、TLS 缺失、daemon 更换、相似容器名 | A§6.1、§11；明确外部兼容性风险，真实连接尚未验证 |
| 高 / TA-07～11 | patch 在终态后生成、重复提交、转换改字节或旧报告关联新候选 | 交付内容未经验证、误判成功 | finalization、发布中取消、CRLF/binary diff、缓存或 symlink | R§5–7、A§7–9；强不变量明确 |
| 高 / TA-12～15 | 退出 0/`resolved=true` 掩盖漏测、错题、执行错误或证据缺失 | 错误进入 SUCCEEDED | 部分报告、宽松类型转换、多重故障、日志丢失 | R§7、A§9–10；报告局部规则仍需固定样本解释 |
| 高 / TA-08、18～21 | 提交 finally 无界、超时不回收子进程、取消转 FAILED | 用户无法停止、资源残留、错误终态 | attempt 前取消、子进程拒绝 TERM、Docker 不响应、重复信号 | R§8、A§7.1、§11；预算原则明确，建议数值未批准 |
| 高 / TA-16、17 | 成功事件没有引用、事务部分提交、收集损坏时虚构交付 refs | 无法追溯判定，交付虚假证据 | SQLite 冲突、磁盘损坏、list_for_run 超时、patch 发布竞争 | A§10；SQLite 事务已有基础，新增引用路径未实现 |
| 中 / TA-22 | 多份日志同时进入内存、每一步重置预算 | OOM、超时放大、交付失败 | 大日志、多 Run、取消后的线程仍读文件 | A§10、§14；可分析资源行为，无绝对内存指标 |
| 高 / TA-23、24 | gold/隐藏数据进入 Agent、非独立容器验证、自定义镜像冒充预检 | 验证污染、评测结果不可比较 | 输入/挂载配置、覆盖镜像、实际 image ID 不可见 | R§4–5、A§6、§10–14；边界明确，安全专项不能仅凭功能测试认证 |
| 高 / TA-25 | 受控 harness 全绿却真实 5.0.2 不兼容 | 整条功能在真实环境不可用 | 参数、路径、报告、镜像和 Docker SDK 语义漂移 | R§9、A§13；真实集成与全量 preflight 均未在本轮执行 |

## 3. Oracle 与可测试性缺口

每项 oracle 由冻结的需求/契约和独立预期数据提供。测试 fixture 先保存预期规则和结果，再构造输入；不得调用被测解析器生成“预期结果”。成功条件必须检查完整判定链，单独断言非空 report、退出 0、artifact 数量或 `resolved=true` 都不足。

### 3.1 核心判定与状态关系

| 刺激 / 条件 | 可观察预期 | 不可替代的证据 |
| --- | --- | --- |
| 所选任务通过全部准备检查，其他任务未预检且全局 ready=false | 允许所选任务进入 Runtime | 选中记录、预检报告摘要、Provider 首次请求；不是只测 ready 布尔值 |
| 缺上下文、不支持 ID、所选任务未就绪或数据包损坏 | Runtime/模型未启动；明确准备错误 | Provider 外部边界调用记录、Workspace 未创建；不伪造 FAILED Run |
| 提交空 patch、导出/登记失败 | FAILED / EVALUATION_FAILED；无 harness attempt | 错误码、提交状态、目录及外部启动记录 |
| 完整可信通过报告、进程 0、必要证据登记和评测清理均完成 | SUCCEEDED / TASK_VERIFIED | 同一 patch bytes、报告测试集合、已登记 refs、容器不存在 |
| 有效 unresolved | FAILED / EVALUATION_FAILED，候选失败 | 完整原报告与 TASK_UNRESOLVED；不能统一当作 infra failure |
| 进程非零，即使存在 resolved 报告 | 不成功，进程失败为主 | exit status、报告保留、多重故障诊断 |
| 用户在提交/评测/清理阶段取消 | CANCELLED / CANCEL_REQUESTED，有界退出 | 清理资源身份、诊断、无伪造 EvaluationCompleted；取消不转换一般失败 |
| Evaluator 通过，随后 Application 收集或销毁失败 | 保留既有 SUCCEEDED，附 finalization error、CLI 非零 | SQLite 既有终态不改写；收集失败保留 Workspace |

“必要证据登记与评测清理”发生在 Evaluator 返回前，可阻止 Run 成功；“Application 收集与 Agent Workspace 销毁”发生在终态后，只改变交付错误与 CLI 结果。两类失败必须分开分析。

### 3.2 DEFER 项与需固定的材料

| 缺口 | 为什么影响 oracle / portfolio | 所需材料与责任移交 |
| --- | --- | --- |
| D1–D3、规格整体批准与预算默认值 | 镜像缺失/覆盖任务能否启动、取消 SQLite 引用义务和时限不同 | 功能负责人集中确认 A§15；本次不重新索取批准、不进入实现 |
| 严格 tests_status 判定细节 | A§9 禁止遗漏/重复/规则外必需证据，但未逐一定义额外非必需测试、清单跨组重叠、失败键缺失与空必需集合的接受范围 | 规格负责人用固定 5.0.2 report 样本确认字段、集合关系及 valid-unresolved 与 invalid 的分界；这些精确分类暂 DEFER，拒绝漏测/矛盾通过的义务不变 |
| harness 测试超时标记 | 不能用任意日志字符串分类 EVALUATION_TEST_TIMEOUT | 从固定 5.0.2 源码与真实超时产物提取标记/路径；冻结反例。此前只能可靠断言不成功，不能断言该精确错误码 |
| 全量数据准备来源验证 | 运行时不读源 Parquet，不能靠 bootstrap 证明准备时核对了 revision/原始字段 | 数据准备负责人提供两源摘要、25 项字段比较、解析后的测试清单、gold preflight 报告及生成数据摘要 |
| 峰值内存与时间测量规则 | “顺序读取”不提供绝对 RSS 数值；scheduler 与单文件不可中断 I/O 不提供无误差墙钟上限 | Portfolio 时冻结文件规模、宿主/解释器、测量方法、时钟容差与资源隔离；不虚构 MB 或秒阈值 |
| 真实宿主数据/镜像可用性 | A§2.2 的 7 项预检是设计快照，不能作为本轮当前执行证据 | 真正执行前只读核验所选开发任务 manifest、摘要、harness 和镜像；本次未访问或更新该目录 |

上述局部 DEFER 不阻止其他明确义务的分析；也不能把未确认细节自动设成实现默认。

## 4. 测试义务与候选证据组合

以下 Evidence 全是**拟取得证据**，未实现、未执行或验收。Scope、依赖与环境是当前架构稿下的候选，待 Portfolio pass 确认。每行的数据/环境栏明确 stimulus；测试级别名称不能替代具体边界。

| Risk / criterion | Failure mode | Oracle | Technique | Scope / fidelity | Dependencies | Data / environment | Evidence | Residual blind spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TA-01；R§3、5、9 / A§5–6：装配与输入 | Docker 缺任务回退 passthrough；Local 做外部预检；自由文本被当 ID | Docker 合法结构化上下文才启用独立评测；非法输入在 Workspace/模型前失败；Local 无评测数据/executable 仍使用 passthrough；多余字段/空值拒绝 | 等价分区、配置决策表 | 真实 Settings/Bootstrap 到 Runtime 入口 | 真实装配；仅 Provider/宿主 CLI 外部边界受控 | 临时配置；Local/Docker × 缺失/支持/不支持 ID；issue 与镜像刻意含另一 ID | 准备错误、Provider 请求、Workspace 生命周期、Local 最终结果 | 不证明 Docker daemon/harness 真兼容 |
| TA-02；R§1、4 / A§5.3、6：冻结清单 | 重复/清单外 ID、分组错、规则摘要错仍运行；从 cwd 临时文件加载 | 支持集合精确 25、分组 5/20；摘要、唯一性、字段规则全部满足；非仓库 cwd/安装包也能取得安全清单 | 分区、集合反例、安装资源契约 | 数据 loader + 包资源读取 | 真实 Path/JSON/Pydantic/hash | 合成完整 25 项元数据；一次改一个 ID/组/摘要/字段；列表与 JSON 列表字符串；隐藏字段注入 | 错误码、校验后记录、包资源安全字段检查 | 不证明合成任务的真实验证规则 |
| TA-03；R§4 / A§1、6：逐任务就绪 | ready=false 拒绝就绪任务；取消/未运行当 passed；报告路径越界 | 所选 status=passed、严格 resolved=true、报告摘要/身份及 harness 版本合法才启动；整包错全拒绝、未预检只拒绝所选 | 决策表、状态分区、路径反例 | Bootstrap + 真实文件预检 | 真实摘要/报告校验；受控版本进程 | 同包就绪/失败/取消/未运行记录；缺/坏/逃逸预检报告；actual/manifest 版本分别变异 | 模型零调用或允许启动、准备错误、所选报告关联 | 覆盖镜像门槛按 D2 DEFER；预检不保证未来候选通过 |
| TA-04；R§4 / A§6：规则快照与准备流程 | Run 中途读新 tasks；自动下载或执行 gold；准备时静默接受新版规则 | 绑定后单任务输入与已验证快照一致，共享源只读；准备工具遇冻结字段差异中止且不标就绪；gold 不进入正常记录 | 状态转换、字段差异反例、真实文件边界 | Runtime 数据快照；另行准备流程契约 | 真实文件、摘要；准备流程依赖固定外部环境 | bootstrap 后替换共享 tasks/manifest；准备输入逐项改 repo/base/test lists 等；相同列表两种编码 | 前后源摘要、task.json/配置摘要、准备差异报告 | 本轮未审计准备脚本；两源真实性需真实准备证据 |
| TA-05；A§6.1：连接选择与冻结 | CLI 用 A、SDK 用 B；动态 context 替换 endpoint；私钥进入 artifact | 显式 context 优先 HOST，其次 HOST，再当前 context；所有评测 argv/env 使用冻结 endpoint/TLS；父环境不变，不存证书内容 | 决策表、协议契约 | 连接预检到 harness/cleanup 外部进程 | 真实配置解析；受控 Docker/harness 可执行文件 | A/B 冲突；Unix/TCP/验证 TLS；准备后改 context；TLS 启用/移除 | 全部 argv/env、冻结配置、artifact 非敏感字段 | 受控 CLI 不能证明 SDK/TLS 握手；需真实边界补强 |
| TA-06；A§6.1、11：非法连接与 daemon 身份 | SSH/npipe/缺证书静默回退；新 daemon 被删除容器 | 非法/不支持连接拒绝；daemon ID 缺失准备失败；执行前变化拒绝启动，清理前变化停止删除并记录残留 | 分区、故障注入、状态转换 | Bootstrap + Evaluator 清理 | 外部受控 CLI；真实路径与证书可读检查 | 损坏 inspect、SkipTLSVerify、DOCKER_TLS 歧义、材料缺失；daemon A→B | 稳定错误、零删除记录、残留身份 | 连接断开不能保证清理，需人工处置；真实 daemon 替换非必需常规测试 |
| TA-07；R§5–6 / A§7、12：唯一提交 | 多次导出、交付另一 patch、只凭 patch_ref=None 重试 | 同 Run 一次提交尝试、稳定 submission_id；harness patch 与交付 ref bytes 相同；request text/run/task/plan 保留；空/失败状态不重试 | 状态转换、不变量、真实 Git 示例 | Submission→Evaluator→Application | 真实 Git Workspace/FileArtifactStore；受控 harness/Provider | 非空/空/空格；提交后修改 Workspace；模型提前失败触发诊断导出；P/E/F 各再次显式正式提交，分别恢复改动或故障条件 | patch metadata、manifest、predictions、最终 bytes 与提交状态；重复提交身份不变、真实 records 无新增及外部 Git/Docker 无新导出记录 | 非空空格候选可被 harness 拒绝，不作为 EMPTY_PATCH；并发正式提交若允许需另明契约 |
| TA-08；A§7.1、11：提交预算及 attempt 前取消 | finally/锁/Git/put 各拿完整预算；清理错误覆盖取消；diff 已生成仍冒险提交 | 全阶段共享总 deadline，清理在总预算内预留；耗尽停止后续；取消仍 CANCELLED，无 attempt；正常超时/清理失败明确失败 | 时间边界、状态路径、故障注入 | 公开 Local/Docker export_patch + Submission + Runtime | 真实 Workspace；受控 Git/Docker 外部进程；真实 store | 锁等待、Git 卡住、清理拒绝响应、生成 diff 后清理失败、登记阶段取消；无参数调用 | 单调时间线、PID/index/容器诊断、无 harness 记录、Agent 保留 | 配置值/测量容差待定；不可中断单文件 I/O/线程不能强杀 |
| TA-09；R§5、7 / A§7、10：发布中取消与关联 | put 已发布但内存无 ref 后重复提交；diagnostic 强制要求 attempt | 已发布 patch 可在成功 manifest 汇总中按 submission_id 恢复并去重；不再导出；patch 可无 attempt；evaluation config/summary 对应原 record 的 submission/ref/hash | 状态路径、发布竞争实验 | Submission + 真实 FileArtifactStore + Application | 真实 manifest/content；可控外部文件系统发布实验 | put 进行中取消，以公开 manifest 出现作为同步点；未发布与已发布分别留证 | 唯一 patch record、身份关联、取消与收集诊断 | 不 patch 内部 _publish；无法稳定控制发布窗口则该精确路径保留实验缺口，不能用 fake 声称证明线程语义 |
| TA-10；R§6 / A§8.2：JSONL 保真 | strip、换行转换、截断、手工转义或传 patch 路径 | 一条 JSON 对象、一条物理行、末尾 LF；恰好三字段；反序列化后 UTF-8 bytes 等于原 artifact；非法 UTF-8失败 | 分区、往返属性、真实 diff 示例 | Evaluator 输入生成公开路径 | 真实 ArtifactStore/Git/标准 JSON；受控 harness 读取 | 多行/CRLF/Unicode/引号/反斜杠/无末尾换行/重命名/mode/binary；非 UTF-8 artifact | predictions 原 bytes、独立 JSON 解码与逐字节比较 | 无损不代表 diff 可应用；property 框架非必需，可先用确定性 corpus |
| TA-11；R§3、5、7 / A§8–9：独立目录与报告绑定 | 缓存旧报告、汇总 report 冒充实例 report、symlink 逃逸、模型名注入 | UUID attempt 不复用；argv/cwd/固定实例路径正确；predictions 摘要与 patch.diff 同候选；越界/错误关联拒绝；模型用 safe key、原名留配置 | 路径分区、反例、进程契约 | Evaluator + 实际文件树 | 受控 harness；真实 Path/JSON/hash/store | 相同 Run 两次独立评测；恶意模型名；旧/错目录报告；外部 symlink；缺/不同 patch.diff | attempt 目录、argv、拒绝错误、config 与 patch bytes | 报告无签名；路径/摘要关联不是抵抗恶意宿主的证明 |
| TA-12；R§7 / A§9：可信通过 | 0+resolved 直接通过；bool 宽松转换；漏测/失败被忽略 | 退出0、唯一正确任务、必需 patch_exists/patch_is_None/patch_successfully_applied/resolved 字段存在且严格合法、可选 infra_failure 若存在为 false、规则测试覆盖完整无失败才 passed；任何必要条件反例都不成功 | 完整决策规则的单条件反例、集合分区 | 真实 Evaluator 判定到 Runtime | 受控外部进程写真实报告/patch.diff/日志；真实校验 | 基准通过报告每次只改一项；字符串/整数 bool、逐项删除必需布尔字段或必需测试成功结构、仅删除可选 infra_failure 的合法样本、遗漏/重复/跨组错误测试、失败列表非空 | verdict、稳定错误、报告原 artifact、Run 状态 | tests_status 精确额外测试/重叠规则 DEFER；必需通过结构缺失只固定不成功义务，未定精确分类继续 DEFER；不能只用 pairwise 替代已知条件反例 |
| TA-13；R§7 / A§9：执行异常与候选失败 | 非零 resolved 成功；unresolved 都当 infra；假 timeout 关键词改错误 | 有效 unresolved 是候选失败；非零优先进程失败；缺/坏报告不成功；timeout 仅按冻结版本标记分类 | 错误分区、多故障决策表 | Evaluator 外部执行及错误映射 | 真实 subprocess/文件；受控 executable | 启动不存在、非零+完整报告、0+unresolved/缺/坏报告、伪 timeout 文本 | 主错误、附诊断、retryable=false、保存报告/日志 | EVALUATION_TEST_TIMEOUT 精确标记待样本确认；不新增重试 |
| TA-14；R§7 / A§10：必要证据与可追溯配置 | 只存摘要；缺一必要日志仍成功；image ID 冒充实际 | 成功登记 patch/predictions/config/summary/report/stdout/stderr/test_output/instance_log；失败存已产生文件；ref/hash/快照/实际连接/镜像来源可复核；摘要≤4096字符 | 必需项缺失反例、完整性检查 | Evaluator→真实 ArtifactStore | 真实文件及 store；受控 harness | 成功基准逐一漏文件；长输出；容器 inspect 可得/不可得实际 image ID | 原 bytes/hash、metadata、有限摘要、null image ID 的原因 | 大日志不允许静默截断；缺 config/summary 时仍需原目录诊断，不保证完整失败证据 |
| TA-15；R§7–8 / A§9–11：证据与清理失败优先级 | 通过报告掩盖 put/cleanup 失败；附错误覆盖首次主错误 | 通过后必要登记/评测清理失败成为主失败；原失败保留、追加诊断；取消仍取消；可得证据/原目录保留 | 多故障决策表、故障注入 | Evaluator→Runtime | 真实 store/进程；外部受控 Docker CLI；存储故障用独立目录破坏/限制 | resolved+磁盘失败/清理失败；unresolved+相同故障；取消+故障；分别启动失败+登记失败、非零退出+清理失败、无效报告+登记失败、进程超时+清理失败 | Run reason、主/附错误、已登记 manifests、残留身份 | 补充不同主错误路径，不以 unresolved 代表全部异常；未穷举主错误×清理×登记；不模拟全系统 OOM 后可靠诊断；权限故障需验证宿主可复现性 |
| TA-16；R§7 / A§10：SQLite 原子终态 | EvaluationCompleted refs 丢失、与 snapshot/RunFinished 部分提交、取消伪造 completed | 正常结果 refs 原样进入事件已有字段，与终态及 snapshot 同事务；冲突/事务失败无部分提交；取消无 EvaluationCompleted | 真实事务边界、冲突注入、重开读取 | Runtime/RunExecution + SQLiteRunStore | 真实 SQLite/FileArtifactStore；受控外部响应 | passed/failed 携 refs；真实序列/版本冲突；失败事务；取消重开 DB | 完整事件顺序、refs、snapshot/终态、前后数据库记录 | artifact store 与 journal 非跨存储事务；不承诺取消事件完整引用（D3） |
| TA-17；A§10–11：唯一交付来源及收集失败 | Application 从私有状态取 refs；list 失败还交付未验证引用；二次全量扫描 | 正常只交付验证 manifests并去重；一份坏内容使全量失败；仅剩余预算内验证已保存 patch ref 后可交付 patch，否则空；终态不改、Workspace保留 | 真实损坏边界、预算路径、结果契约 | Application + 真实 FileArtifactStore + SQLite + rendering | 真实 store/文件；受控 Provider/harness | 评测内容损坏；patch亦坏；manifest损坏；deadline耗尽；打乱 artifact 顺序 | ApplicationResult refs、artifact_collection诊断、SQLite终态、CLI patch URI/非零结果 | 仅落盘未登记/无内存ref patch不保证恢复；磁盘追溯≠Application已交付 |
| TA-18；R§8 / A§5.2、11：测试与进程超时 | 拉取/启动挂住不计整体超时；超时误成功；阶段预算重置 | process timeout>test timeout；整体从启动前覆盖启动/拉取/报告；故障结束清理；阶段共享deadline而非逐步重置 | 配置边界、故障注入 | Settings + Evaluator→Runtime | 真实异步 subprocess；受控 executable/CLI | 等于/小于超时配置；启动/拉取/报告卡住；明确测试超时标记 | 时序、EVALUATION_PROCESS_TIMEOUT/已确认测试码、Run失败、清理身份 | 单 Evaluator 还可增加独立清理/收集预算；不是整 Run 时限 |
| TA-19；R§8 / A§8.3、11：进程组与大输出 | stdout PIPE 堵塞；只杀主进程；shield 让后台任务无界继续 | 输出直接落文件；TERM后不退则KILL并回收主进程及组；首次取消保护清理但受deadline；取消及时返回 | 真实外部进程故障注入 | POSIX Evaluator + Runtime cancellation | 可生成子孙进程/忽略TERM的受控 harness | 大 stdout/stderr；测试/报告阶段取消；清理期间再次取消 | 原日志bytes、进程组PID及退出/残留记录、CANCELLED | macOS/Linux语义需分别取得证据；强退出/崩溃不承诺完整回收 |
| TA-20；R§8 / A§11：容器精准清理 | 宽泛前缀删邻居；忽略冲突后缀；正常通过未查残留 | 仅完整本次名或一个合法数字时间戳后缀；保存身份、按ID删、复查不存在；不删其他attempt/Agent/镜像；失败阻止成功 | 名称分区、状态转换、外部契约 | Evaluator清理 + Docker边界 | 受控 Docker CLI；另有真实daemon回收后盾 | 正确名/后缀/近似前缀/非数字/多后缀；删除失败、不可达、daemon变化 | 查询/删除argv、容器身份、保留邻居、cleanup错误 | 上游容器命名漂移需5.0.2契约控制；连接不可达留下人工义务 |
| TA-21；R§7–8 / A§11–12：Agent 生命周期 | 失败/取消销毁Agent；成功清理错改历史Run；独立容器复用Agent | 成功且无交付错才destroy；失败/取消/收集错保留；destroy失败Run仍成功但CLI非零；评测新容器分别回收 | 生命周期状态表、组合旅程 | Bootstrap/Application/Runtime + Workspace | 真实Local Git与受控Docker边界；真实SQLite/store | 成功/失败/提前取消/评测取消/收集错/destroy错 | Workspace存在性、容器ID、finalization、Run/CLI结果 | 保留attempt目录一律不自动GC；跨进程恢复排除 |
| TA-22；A§10、14：汇总资源成本 | 并行读完整日志导致峰值随总大小增长；超时还启动后续校验 | 同一次登记/汇总仅持有当前完整文件bytes，不返回内容；损坏仍拒绝；超时停止后续工作；每Run一次最终全量汇总 | 隔离进程资源实验、完整性反例 | 真实Evaluator登记/FileArtifactStore汇总/Application | 真实大文件、独立进程、RSS/分配与文件I/O观测 | 固定最大文件改变文件数；固定文件数改变最大文件；收集预算不足 | 峰值/输入规模/宿主记录、refs/hash、截止后I/O记录、原目录 | 数值oracle DEFER；最大单文件仍占内存，线程/多Run可叠加，不能宣称固定内存上限 |
| TA-23；R§3–5 / A§6、8、10：镜像策略 | 覆盖修改共享数据；不同Agent基线被拒；默认preflight冒充覆盖证明 | 本次task.image可替换、共享摘要不变；Agent镜像/基线独立；记录image_preflight_verified和实际ID来源；执行缺缓存行为按D1，覆盖门槛按D2 | 配置决策表、真实文件/镜像边界 | Bootstrap + Evaluator | 真实快照/store；受控image inspect；真实缓存镜像补强 | 默认/覆盖；缓存有/无；允许拉取开/关；不同Agent基线 | task/config/源摘要、image ID来源、准备/执行结论 | D1/D2 DEFER；检查后删除缓存竞态不提供强离线保证 |
| TA-24；R§4–5 / A§1、10、14：验证隔离 | gold/隐藏规则/缓存/socket挂入Agent或日志送回模型 | Agent挂载与输入无gold/隐藏数据/宿主socket；评测在新容器；正常数据无gold字段；完整日志只作宿主artifact，CLI不打印隐藏详情 | 配置/输入审阅、外部边界反例 | Bootstrap挂载/Provider请求/CLI输出及真实容器inspect | 真实配置/文件；外部边界记录；真实Docker补强 | 数据目录与Agent路径不同；日志含合成隐藏标记；恶意字段 | mount/请求/输出清单、正常数据字段、容器ID区别 | 非完整威胁模型或harness沙箱认证；权限/网络沿用外部harness，专项交安全负责人 |
| TA-25；R§9 / A§13：真实5.0.2链路与完整准备 | doubles兼容但真实参数/路径/报告/清理失败；7项证据冒充25项 | 一个已预检开发任务有效patch成功、非空无效候选失败，report→artifact→SQLite→终态一致且容器回收；完整冻结前25/25 gold均通过 | 真实边界集成、关键旅程、逐任务preflight | 内部结构化Bootstrap入口→Application→外部harness/Docker | 真实5.0.2、真实缓存镜像/store/SQLite；Provider只负责可控提交 | 执行前核验所选开发任务；gold只在宿主测试驱动；不使用正式任务调优 | 版本/manifest/镜像/patch hash、原报告/日志/事件/终态、回收记录；另存25项准备证据 | 本轮未执行；无需真实模型证明此确定性链路，但不证明Agent修复能力；成本与镜像可用性未知 |

## 5. 技术与保真度取舍

候选组合以“失败能否被真实观察”为选择依据，不按金字塔比例或覆盖率分配测试数量。

| 候选范围 | 速度 / 资源 | 维护性 / 可靠性 | 能提供的保真度 | 取舍 |
| --- | --- | --- | --- | --- |
| 数据/报告/配置规则的进程内测试 | 快，数据小 | 独立临时文件、独立预期，确定性强 | 严格类型、集合、摘要、错误分类 | 首选；不需要每个分支再跑CLI/Docker |
| 真实Git、ArtifactStore、SQLite与受控外部可执行文件 | 中等，有限进程与磁盘 | 通过公开协议和文件结果断言，避免私有函数绑定 | bytes、事务、交付、取消、argv/env、进程组 | 跨对象语义的主证据；不称为真实SWE-bench验证 |
| 隔离进程资源/发布竞争实验 | 较慢，受宿主条件影响 | 需可复现尺度、同步与测量规则 | 完整bytes内存、线程发布竞争、操作系统边界 | 仅为对应资源风险保留；暂不制定绝对RSS阈值 |
| 真实5.0.2与Docker关键旅程 | 镜像/测试成本高 | 依赖固定版本、已就绪数据和缓存；外部失败可诊断 | 上游参数、真实报告、容器与SDK兼容 | 保留一项开发任务的成功/非空失败后盾；不复制本地报告全分支 |
| 真实模型修复任务 | 成本高、随机性高 | 弱于确定性评测触发的故障定位 | Agent能力而非Evaluator确定性契约 | 本功能分析不要求，用受控Provider提交已知候选；正式benchmark另行定义 |

选择决策表而非单纯pairwise，是因为连接优先级、报告AND条件与多重故障都有已知高风险组合。选择往返属性而非固定文本snapshot，是因为patch正确性要求逐字节相等。测试状态路径而非每个函数的调用次数，是因为一次提交、取消与终态才是契约。只有“禁止调用模型/未启动harness/禁止删除邻居”等交互本身是要求时，外部调用记录才进入oracle。

不要求通用fuzz、全系统chaos、负载/soak、恢复或迁移测试：当前没有相应需求阈值/流程，确定性反例足以攻击已知风险。若严格解析器的反例仍出现未知崩溃，再对其做定向生成/模糊输入；不能用随机数据替代规则样本。SQLite故障用真实事务冲突/约束反例，避免mock项目内部commit。选择性mutation可用于检查“去掉任一成功条件仍通过”“删除refs持久化”“无损转换被strip”是否被检测，但不设置万能mutation分数。

## 6. 测试依赖、数据与环境

### 6.1 外部替身边界

| 依赖 | Double kind与表示语义 | 不表示的语义 | 漂移控制与真实后盾 |
| --- | --- | --- | --- |
| ModelProvider | stub，返回最终候选/错误，记录请求；未调用是要求时记录进入oracle | 实际模型修复质量、token与计费 | 使用真实ModelProvider请求/响应契约；Evaluator不调用模型，无需为此功能采购模型证据 |
| SWE-bench executable | 受控进程；读取真实inputs，写固定版本文件树/报告，产生exit/大输出/子进程/超时 | 真实测试执行、patch应用、harness缓存与Docker SDK | 固定5.0.2参数/报告样本；TA-25真实有效/无效候选后盾 |
| Docker CLI | 受控可执行文件；返回context/info/inspect/list/delete，记录argv与环境；删除范围为交互oracle | daemon连接、TLS握手、真实容器隔离、镜像拉取 | 固定CLI响应契约；真实Unix或当前支持endpoint验证执行/清理；TLS若列为已支持也须真实验证TLS证据：test design 的 TC-U15-04 在真实验证 TLS daemon A 上观察预检、harness 容器与清理，并保留 B 的邻居；环境缺失记未执行，不能用 TC-U3-04 或 Unix 链路替代 |

不mock CandidateSubmission、Workspace.export_patch、报告判断、Application finalization、ArtifactStore或SQLite内部方法。需要真实存储失败时用隔离目录中缺失/损坏内容、不可写路径或容量受限环境；不可稳定复现的故障保留缺口，不能用fake冒充真实磁盘证据。

### 6.2 数据与隔离规则

- 确定性数据为合成完整25项元数据包、最小真实Git仓库、逐字节patch corpus、严格报告及单条件反例。它们不携带gold solution或真实隐藏测试。包成员集合取正式清单，规则用独立合成值；不能把小样本放宽成允许任意任务集合。
- 每个测试独立Run/attempt、临时store/SQLite/Workspace/工作目录；不同测试不共享可变manifest、容器名或PID。按公开状态/文件/进程握手同步，不靠固定sleep假设阶段已经完成。
- 版本化输入schema、报告样本与patch corpus，保存fixture摘要、失败输入、宿主/解释器、版本/argv、时间线。真实log含敏感验证信息，仅保存在授权宿主证据目录，不提交隐藏测试或gold到仓库。
- 真实集成仅用5个开发任务中的已预检项；2个已预检正式任务不用于实现或Prompt调优。25项全量实验由准备/benchmark责任人管理，不能为减少成本临时换题或用7项样本称全量通过。
- 时间oracle使用monotonic deadline与已批准预算。报告实测elapsed、阶段开始/结束与测试监督时限；监督时限与调度容差是测试环境参数，不是放宽产品预算。不可中断I/O与线程限制单独留证。
- 资源实验先固定最大单文件大小及数量，改变数量观察总bytes与峰值关系，再改变最大文件验证单文件成本。保留原测量结果；尚未批准的数值准则不转为自动验收阈值。
- 若外部结果不稳定，区分产品失败、harness失败、环境缺失和fixture设置错误；保留首次失败。重跑不能替换失败证据，跳过/隔离不计已验证。隔离需要责任人、原因、期限与保留信号，具体执行策略移交CI负责人。

## 7. 当前基础证据与差距

这里只读抽查相关测试及代码，不是完整suite audit，不宣称测试通过。

| 已有材料 | 可复用的基础能力 | 不证明的新义务 |
| --- | --- | --- |
| `tests/runtime/test_runtime.py` 的成功前评测、失败终态及span测试 | 通用Evaluator注入、TASK_VERIFIED/EVALUATION_FAILED、事件作用域 | 严格SWE-bench判定、必要artifact条件、refs终态事务、取消证据 |
| `tests/storage/test_sqlite.py` 的原子commit与冲突测试 | 真实SQLite事务/重开验证基础 | 带评测refs的EvaluationCompleted与终态/snapshot一致性 |
| `tests/artifacts/test_file_store.py` 的重开/损坏/缺内容测试 | manifest持久化与完整性契约 | 顺序校验内存、一次汇总、限时patch回退、发布中取消 |
| `tests/environments/test_local.py` / `test_docker.py` 的patch导出与进程测试 | 真实基线/patch、已有进程边界 | 新公开导出总预算、内部finally有界、锁等待和取消优先级；部分Docker测试用内部backend替身，不能作为本次真实Docker证据 |
| `tests/cli/test_application.py` / `test_bootstrap.py` | finalization保留Run结果、Workspace策略 | 新独立Evaluator装配、候选提前提交、manifest唯一交付、artifact_collection与按type展示 |

当前 `src/miniagent/evaluation/` 只有通用契约与passthrough，未找到 `tests/evaluation/`；Application当前未注入Evaluator、结束后才导出patch，FileArtifactStore当前使用gather校验所有内容。不能把基础测试映射直接标成新义务已有覆盖，也不因基础测试使用替身而要求重写无关旧测试。

质量扫描结论：功能/兼容性、事务完整性、取消/超时、隔离、资源与可观测性均有必要义务。并发仅要求不同Run身份与清理互不干扰，workers=1不是宿主并发配额。性能只分析已有预算与日志内存形态，没有吞吐/SLA来源，不新增压测阈值。CLI涉及错误、artifact类型与隐藏信息呈现，没有新增GUI或可访问性标准依据。安全扫描仅覆盖已写明的数据/挂载/路径/连接要求，harness权限和网络专项需安全负责人判断。

## 8. 充分性判断与移交

```text
Subject:              Independent Evaluator首版Docker独立校验与Local兼容性
Mode:                 Design
Strategy pass:        Obligation
Decision:             PARTIAL；D1–D3、报告局部分类、超时标记、资源测量oracle为DEFER
Test basis:           R§1–9；已确认逐任务门槛；A§5–15的待批准细化
Architecture basis:   2026-10-10架构完整评审稿，未获整体批准；review PROCEED不等于批准
Quality risks:        误判成功 > 校验/交付关联与证据完整 > 取消/清理 > 规则冻结/连接 > 资源成本
Manual evaluation:    证据链、挂载/敏感信息审阅；未知分类样本与残留资源人工核对
Adequacy signals:     TA义务映射、单条件反例、真实边界记录、事务前后差异、真实集成与preflight缺口
Handoffs:             规格负责人确认开放oracle；批准后Portfolio→shift-left；CI/traceability/security/data分别承接
Residual risk:        未批准/未执行；外部harness语义与真实TLS缺证据；跨存储、线程/强终止、镜像缓存竞态为已描述限制
Next action:          集中确认架构D1–D3及报告/超时样本，再只对受影响TA行完成Portfolio pass
Verification:         前轮需求/架构与选定代码/测试的只读核对；本轮仅文档修订与结构/追踪检查；未运行运行时测试
```

Portfolio完成后再向缺陷前移移交最早能观察各故障的位置；CI的触发、阻断、报告保留、新鲜度和失败行动另行决定，本文不制定pipeline。需求追踪另记测试实现与实际执行状态，不将“拟取得”改写为“通过”。数据准备负责人提交25项全量冻结证据；真实边界执行者提交开发任务有效/非空无效候选及容器回收证据。未确定的责任人和环境继续显式保留，不由本文替任何人接受剩余风险。

文档阶段按仓库要求不运行pytest、Docker、gold preflight、Ruff或类型检查；本轮仅修订 test analysis 与 test design，没有修改Python、原需求/架构稿、支持清单或宿主准备数据。


## 9. Review 修订追踪

以下为设计义务的修订映射，不是执行证据。原 TA/TC ID 不变，新增 TC 接续各单元编号。

| Review 结论 | 分析义务与 oracle | Test design 对应 | 剩余限制 |
| --- | --- | --- | --- |
| 报告字段名错误 | TA-12 按 A§9 消费 patch_successfully_applied，不以 patch_applied 别名替代 | U7 E4、TC-U7-07 | 完整固定版本列表样本仍需确认 |
| 必需字段删除未覆盖 | TA-12 区分类型错误、必需 bool 缺失、必需通过结构缺失与可选 infra_failure 缺省 | TC-U7-11～13 | 失败键等 Q-4 局部合法性及部分精确分类仍 DEFER |
| 重复正式提交未覆盖 | TA-07/09 分别从 P/E/F 再次触发正式提交，验证无新导出/登记、状态及 submission_id 不变 | TC-U4-11～13；I1～I3 | Q-7 返回形式未定；有效转换与禁止动作分开统计 |
| 原失败只由 unresolved 代表 | TA-15 分别跨启动失败、非零退出、报告校验、整体超时路径追加一个后续故障，保留原主错误 | TC-U8-09～12；关联 TA-12/13/18 | 是四个补充实例，不是全部异常故障笛卡尔积 |
| 真实 TLS 义务未落到用例 | TA-05 取得真实 TLS 预检/执行/清理连接证据，复用 TA-25 的开发任务链路 | TC-U15-04、P4；TC-U3-04 只作受控契约后盾 | 环境不可用为阻塞/未执行；context 之外的真实 TLS 来源与握手拒绝路径未穷举 |

此次补齐明确义务的设计映射，不替代 D1–D3、规格整体批准、固定报告/超时样本、资源测量准则及 Portfolio pass。所有新增证据仍为拟取得，真实 TLS 义务在执行证据取得前保持开放。
