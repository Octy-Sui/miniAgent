# Resume Test Analysis

分析日期：2026-10-19。文件日期沿用所属需求增量。

Review 修订：依据 [Test Strategy Audit](2026-10-14-resume-test-strategy-audit.md) F1–F5，同步 [Test Design](2026-10-14-resume-test-design.md) 的后段事务故障、存活调用者停机、固定旅程结果、retry精确oracle和持锁区间同步。原审计NOT-ADEQUATE作为修订前结论保留；本稿PARTIAL是设计修订后仍有缺口的自评，待复审，不表示执行通过或自动关闭审计。

## 1. 范围与结论

本次分析同主机 POSIX 环境下 Local / Docker Run 的安全跨进程续跑，涵盖新 Run 初始化、暂停、恢复判定、原 Workspace 接回、phase 推进、审批、Usage、候选提交、独立评估与 finalization。

**Mode：Design。Strategy pass：Combined。Decision：PARTIAL。** 两份输入已明确记载 D1–D10、C01–C12 及后续评审选择获确认，因此合并进行风险义务与证据组合分析，无须重新等待架构批准。明确契约均有下述证据路径；真实 macOS / Linux、Docker 连接和 development task 环境本轮未核验，可执行环境、固定候选/独立结果夹具、后段故障屏障与时间测量参数仍须落实。PARTIAL 表示证据组合的剩余缺口，不表示产品已部分验收；未替负责人接受风险。

本文完成 Test Analysis，确定测试条件、oracle、技术、范围及保真度；不展开逐条测试脚本，不创建实现计划，不修改运行代码。所有“拟取得证据”均不是已实现或已执行结果。

### 1.1 测试依据

| 来源 | 权威范围 | 本次使用 |
| --- | --- | --- |
| [需求设计 D](2026-10-14-resume-design.md) §1–15 | D1–D10、安全前提、CLI、R01–R39 | 行为与验收 oracle |
| [架构 A](2026-10-14-resume-architecture.md) §4–15、18 | 已确认的数据、提交、执行监督、装配边界 | 固定 scope / fidelity；C08 不兼容旧格式、C10 冻结 30 秒预算 |
| 当前相关代码和测试 | 已有机制及测试边界 | 只读抽查可复用基础；不能覆盖或改写新契约 |

下文 TA-01～TA-30 为本文局部义务 ID；R01～R39 为 D§14 的原验收 ID。D§n / A§n 指上述两份文件。输入文档仍在工作区修改中；本分析以本次读取的内容为依据，不把其最初的代码基线当作当前提交状态。

不纳入：checkpoint / rollback / recreate、跨主机 / NFS / Windows、数据库硬链接别名、任意 shell exactly-once、外部服务恢复、脱离受管理组的 daemon 检测、完整文件历史一致性证明、终态 repair、通用 RecoveryController、GC。保留已有 checkpoint API 的其他调用者契约，但 Resume 不得调用这些 API。Run State Snapshot 仍是恢复校验与 compaction 的必要机制。

## 2. 风险台账

按影响排序；暴露是触发条件，不是发生概率。未提供生产事故、发生率或流量数据；需求和架构对失败后果的证据明确，实际暴露频率未知。

| 风险 / 义务 | 可观察失败模式 | 影响 | 暴露条件 | 风险证据与信心 |
| --- | --- | --- | --- | --- |
| 最高：副作用安全；TA-03、07～10、21、22、24 | 未确认 shell 重放；旧 helper 延迟发令；子进程未结束就执行后继 | 重复修改、并发写入、错误地宣称安全续跑 | kill、提交不明、主进程先退出、Docker start 延迟、诊断残留 | D§8–10、12；A§8–9、12；明确契约，OS / Engine 行为须真实证据 |
| 最高：互斥；TA-02、04 | 同 Run 双推进、锁过早释放、CAS 失败后仍发令 | 相同现场并发业务与清理 | run/resume/dry-run 竞争、finalization、FD 继承、陈旧 record | D9；A§6–7、9；文件锁与事务都不可用单一进程 fake 证明 |
| 高：恢复源完整；TA-01、05、06、11、12 | 丢失已提交结果、重复消息/Step、状态组合非法、失败变成功候选 | 进度与判定损坏 | 每一事务前后中断、较旧 Snapshot、结果错配 | D§7、9；A§4–5、9；判据明确 |
| 高：现场身份；TA-15～17 | 接回替代目录/容器、reset 丢修改、在另一 daemon 执行 | 数据损失、错误资源被执行或删除 | symlink、marker/label 改变、PID 复用、context 漂移 | D§4、8；A§8、13；身份检查不等于完整现场未被人修改的证明 |
| 高：暂停与生命周期；TA-13、14、24 | CLI 变 CANCELLED、终态重开、30 秒分阶段重置、暂停失败改 FAILED | 不可续跑、无法停止、错误终态 | bootstrap/runtime/resume/finalization 信号、第二信号、提交超时 | D2、D10；A§11、12.4、18 C07/C10；时间阈值已确认 |
| 高：授权与冻结定义；TA-18～20、27 | 消费旧批准、配置/定价被新默认替换、无限刷新 retry | 越权执行、错误请求/计费、无界自动工作 | 当前 Policy 变化、cwd/配置变化、多次 Resume | D§7、11；A§10、13；明确契约，非秘密 endpoint 变化需审计 |
| 高：评估独立性；TA-21～23、29 | 孤立 patch/report 被认领、重复评估、误删邻居、镜像缺失仍创建 Run | 验收来源错误、误判成功、资源损坏 | artifact 与 SQLite 之间中断、harness 中断、tag 移动、双 daemon | D§12；A§4.1、12；至少一个真实 development task 是明确义务 |
| 中：可操作性/审计；TA-25、26、28、30 | dry-run 有副作用、错误优先级错、泄密、旧入口失效、span 伪闭合 | 误操作、信息暴露、无法定位拒绝 | 缺凭据、旧格式、stopped Docker、非法 ID、导入调用 | D§5、13；A§7、14–15；稳定码/退出码明确 |

## 3. Oracle 与中断模型

### 3.1 共同可观察量

每条恢复路径同时观察两种事实：独立进程实际做了什么，以及 SQLite 最后确认了什么。不能用事件条数代替副作用次数，也不能用文件存在、stdout、孤立报告反推出可信完成。

- 业务身份固定为 Run / Step / call_index / tool_call_id；执行 attempt 独立。用包含唯一业务标记的追加文件、外部请求记录和独立进程/容器检查观察重复与并发。append 只用于测试命令，不代表任意 shell 的原子语义。
- 用新进程/新连接读取最新 RunRecord.state、原事件中的完整 ToolEnded.result、审批版本、Snapshot、artifact bytes/hash。预期进度、失败数与 Usage 从预先定义的输入计算，不能调用被测 planner/transition 生成预期。
- 断言已确认响应/Tool Message 的内容和顺序、每个 Call 一个最终结果、StepStarted/Completed 的业务唯一性、sequence 与 snapshot version 单调；diagnostic 只增长事件 sequence，不产生业务 Snapshot。
- 失败路径必须观察禁止动作：模型、业务工具、Engine RPC/start、导出、评估、清理。仅断言错误码不够。外部边界记录是这些禁止契约的 oracle，不对项目内部 incidental calls 作 mock 断言。
- 记录 interruption / unconfirmed 请求的上界含义；未知 tokens/cost 不能显示成确认的零。真实 provider 收费不在可证明范围。

### 3.2 每个安全边界的三类窗口

| Stimulus / 窗口 | 预期恢复依据 | 必须观察的反例 |
| --- | --- | --- |
| 开始事实提交之前中断 | 仅最后确认状态；未授权副作用不得发生 | 工具先执行后补记、provider 先请求后计数 |
| 开始事实确认，完成事实尚未确认 | 按 operation 类型及结束证据判定；shell 一律拒绝，可信只读可在旧执行已结束后新 attempt | 从“没有 PID”“文件已改”“Running=false”推断安全或成功 |
| 完成事务已提交，调用者尚未收到确认/后继尚未开始 | 新进程读取确认事实，跳过完成操作 | 重复 Tool Result/Usage/评估；本进程在提交不明后继续发令 |

A§9的每个业务边界需在同次公开create/commit上验证组合字段；共享事务机制另以少量后段故障验证，不能重复早期event ID冲突就称State/Approval/Snapshot已被检验回滚。测试数据库条件触发器必须证明新event/审批已写，或State/Snapshot/审批/event全部新值在事务内已可见，之后触发真实DB错误；重开后全旧，同形成功控制重开全新。首次创建的写后失败独立验证。公开RuntimeStore委托适配器只负责边界握手/确认不确定性：G3-K真实commit后kill验证重入；G3-E真实commit后向存活调用者抛不确定控制错误；G3-U不下发提交却返回同一错误。E/U必须等待应用自行错误退出并记录零后继发令，监督者先kill不能证明停机协议。DB故障和确认错误不能互当证据；不patch私有实现。屏障未命中或前缀不成立即未验证，不用固定sleep推断。

### 3.3 恢复决策与状态约束

测试判定顺序固定为 A§7.1：锁冲突 → 不存在/格式不支持 → 终态/缺证据/State 不一致 → 未确认 shell → Workspace/旧执行 → 冻结依赖/candidate/evaluation。用同时存在两个可独立判断故障的输入验证优先级，尤其未知 shell + stopped Docker：不得先启动容器或重新审批。

对所有合法业务来源（CREATED、PREPARING、RUNNING、WAITING_APPROVAL、EVALUATING）覆盖暂停/恢复；RECOVERING 再中断须保留最初业务来源。终态无出边。phase、cursor 投影、batch、current_step、pending、active_operation、disposition 的合法与非法组合以 A§4.4 为 oracle；构造、decode、transition 三种入口均验证。只覆盖可达组合，不用任意状态排列制造无意义样本。

## 4. 测试义务与最小证据组合

Evidence 均为**拟取得**。表中 Data / environment 同时给出 stimulus。真实依赖为默认；“受控”边界的语义与后盾见 §6。同一行可以有较小范围和真实边界后盾，二者证明不同风险。

| Risk / criterion | Failure mode | Oracle | Technique | Scope / fidelity | Dependencies | Data / environment | Evidence | Residual blind spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TA-01；R01、33；D§7 / A§4、14 | 缺定义也发布可恢复 ID；旧格式自动转换；旧 Snapshot 覆盖新 State | 首笔 Run 含完整 attachment/冻结定义，create/RunStarted 一次；仅支持 schema v3、Run/Snapshot v3、recovery v2；旧格式原样拒绝，当前缺证据给 METADATA_MISSING | 等价分区、序列、真实存储契约 | bootstrap + 真实 SQLite/codec，独立重开 | Git、文件系统、Pydantic；provider 受控 | 空库/当前库/旧或未来版本/缺字段/摘要坏；首笔前后 kill；较旧 Snapshot | 数据库前后内容、ID 输出顺序、无请求/无自动重建 | 首笔前硬杀孤立现场不认领、不 GC |
| TA-02；R11；D9 / A§6 | 锁不覆盖 finalization、FD 继承、unlink 导致双持有 | 同规范数据库路径+UUID同时仅一持有；赢家确认持锁后保持到输家实际冲突结果返回，时间线证明重叠；冲突RUN_IN_USE；父死后锁释放且子不持FD；不同Run不互阻 | 并发竞争、进程故障 | 真实 flock + CLI/application 跨进程 | POSIX、SQLite、真实 subprocess | run/resume/dry-run 相互竞争；路径规范别名；finalization 屏障；kill 锁主进程 | 锁时间线、第二进程无副作用、不同 Run 可推进 | NFS/硬链接/运行中移动 DB 不支持 |
| TA-03；R05、14；D§9 / A§8 | shell 开始无结果却重放/跳过/重新请求 | 开始后执行前、修改后结果前均 UNCERTAIN_TOOL_EXECUTION；保留现场和原 attempt；无后续模型/工具/启动 | 决策表、kill、负面交互 | planner + 实际 Local/Docker 工具跨进程 | 真实 Workspace/SQLite；受控 provider | shell 与默认未知第三方工具；命令文本看似只读；叠加 ALLOW/新批准/stopped 容器 | 文件修改次数、原开始 sequence、外部调用零增量、拒绝定位信息 | 无法证明 shell 没副作用时保守拒绝是预期 |
| TA-04；R12、34；D§10 / A§7、9 | ResumeStarted/Completed 提交不明后继续；attach 失败把任务 FAILED | Started原子进入RECOVERING；Completed确认后才dispatch；ResumeFailed保留原现场/来源；提交已应用/未下发但调用方均不确定时，存活命令自行exit1且无后继attach变更/dispatch/exec诊断；硬杀只证明重入 | 状态路径、故障注入、真实事务 | resume 编排 + Journal/SQLite | 真实 commit/重开/attach；受控外部故障 | Started/Completed分别G3-K硬杀、G3-E已写不确认且调用者存活、G3-U未下发仍报不确定；attach live check失败；ResumeFailed再写失败 | 重开后的唯一事实、无后继 start/清理/业务、最后确认 sequence | 无法落库错误时不能保证 ResumeFailed 存在 |
| TA-05；R03、04、24；D§9 / A§5 | 重跑已提交调用，或 ERROR/TIMEOUT 修改被撤销 | 从 next_index 继续同 batch；已提交完整结果/消息及 SUCCESS/ERROR/TIMEOUT 修改保留，不重请求模型 | 分区、跨进程路径 | 共用 dispatch + 实际 shell + 存储 | 真实 Git/Workspace；受控 provider | 三调用 batch，在中间完成事务后 kill；首调用不同结果 | 每调用副作用次数、请求数、消息/结果身份、原 Workspace ID | 不将未落库 TIMEOUT 当完成 |
| TA-06；R07、20；D§11 / A§4–5、13 | 只按剩余结果算失败；Step/上限重置 | 整批严格关联结果计算成败；StepCompleted 一次；max_steps 已满仍收完原 batch，不开始下一 Step | 边界、状态转换、关联反例 | Runtime + 真实按 Step 结果查询 | SQLite、真实 policy/tools | next_index=0/中间/len；limit 前/等于；前半失败后半成功；缺失/重复/错 Run/Step/Call 结果 | failure count、Step 与请求计数、坏历史拒绝 | 更长任意序列用不变量补充，不穷举 |
| TA-07；R06、19；D§9 / A§8 | 未确认只读与旧读取并发；重试变新 Call | 仅显式 retry_read_only 且旧执行已结束可重试；同 Call 新 attempt、tool_calls 按真实开始计数、一个最终结果；未知身份拒绝 | 等价分区、状态转换 | Tool runner + read_file + 实际进程检查 | 真实 Workspace/SQLite | 旧读取结束/活动/身份缺失；工具指纹变化；重试中再次 kill | attempt/Call 对应、完整结果、无并发读进程 | 不把 fake 的“结束”当 OS 证据 |
| TA-08；R19；D§8 / A§8.3 | 主进程退出或 PID 相同被当整组结束；误杀复用 PID | 校验 boot/PID/create_time/PGID/SID/attempt；活组、AccessDenied、歧义均 busy；有残留不提交可信结果，不粗略 kill | 进程故障、身份分区 | 实际 Local process group + 身份判定 | 真实 psutil/POSIX；受控探测事实仅测分支 | 父退出子进程仍写；组回收；出生身份不匹配/不可读 | 组成员/副作用日志、可信结果缺失、邻居存活 | PID 复用难稳定制造；分支证据须与真实出生身份检查并列 |
| TA-09；R36、37；D§8 / A§8.4 | helper 未落库就 RPC；EOF 自放行；旧延迟 start 与恢复并发 | 开始→helper 身份/许可意图提交→create 许可；exec ID/start 意图提交→start 许可；提交不明不放行；旧发令可能性不明 busy | 协议状态转换、门控屏障、真实 kill | 宿主 helper 真进程 + 协议后盾，Docker 实际链路 | SQLite、管道、SDK；受控 Engine 记录器 + 真实 daemon | 门控各前后 kill；EOF/超时/错 attempt 许可；helper 退出但已放行 start 结果不明 | Engine RPC/start 次数与时间线、无许可零 RPC、拒绝新链/容器启动 | Running=false + helper 退出不是未决 start 的安全证明 |
| TA-10；R19、36；A§8.4 | exec 主进程结束或 supervisor 被杀就提交结果 | 需 helper、exec、容器内组均可验证收尾；supervisor 死而子组活拒绝；exited 不把未知 shell 升为成功 | 真边界故障注入 | 实际 Docker exec + Python supervisor | 专属 daemon/container/SDK | supervisor kill、后台子进程、远端暂未运行、daemon 无响应 | exec/group 身份、子进程活动、结果未确认、无后继 | 逃离受管理组后台写入不在保证内 |
| TA-11；R08、09；D§7 / A§5 | 非空 assistant 被当候选、失败处置丢失、候选重复请求 | 原 tools/candidate/terminate 处置决定 dispatch；候选不再请求，失败直接原失败路径；含模型异常/Policy 错误/failure limit 已提交决定 | 决策表、事务前后 kill | Runtime dispatch + SQLite | 真实 response/termination 模型；provider/evaluator 受控 | 响应接受后、begin_evaluation 前；无效响应非空文本；已提交不同失败原因 | 同候选/终止原因、零额外请求、StepCompleted 唯一 | 未提交的异常不推断恢复 |
| TA-12；R12、26、33；A§4、5、7 | 再恢复把 RECOVERING 当业务来源；游标或 active operation 漂移 | 合法来源/phase 恢复，cursor 是强校验投影；重复恢复保留最初来源；超过 3 次仍可继续、次数仅随真实 Started 增长 | 状态模型、边界、生成不变量 | 纯 State/planner + 多次跨进程后盾 | Pydantic、SQLite/Journal | 合法状态遍历；不合法 phase/batch/pending/disposition；4 次以上安全暂停、恢复中信号 | decode/transition 拒绝、来源/审计/进度保留 | 不用工具次数或 status 猜另一套 cursor |
| TA-13；R13、14、34；D2 / A§11 | 信号写 CANCELLED/虚假结果；库 cancel 变暂停；超时重置 | CLI 首 SIGINT/TERM 保存 SUSPENDED/恢复点，无 finished_at/reason/RunFinished/虚假 StepCompleted；130/143；库显式 cancel 保持终态 CANCELLED；共享冻结 30 秒总预算 | 状态路径、真实信号、deadline 边界 | CLI 最外层作用域 + 实际子进程；库 API | 真实 OS signal、SQLite、进程回收；受控等待 | bootstrap 首笔前后、模型/summary/tool/resume/评估/finalization；第二信号/预算耗尽 | 退出码、elapsed/阶段预算、最后确认事实、资源保留、结果联合类型 | 超预算/硬杀不保证 SUSPENDED 落库；墙钟容差另定 |
| TA-14；R33、34；D§13 / A§11–12 | 终态重开，finalization 失败销毁现场 | 终态 Resume 无状态写入且无业务；仅 SUCCEEDED 且 artifact finalization 成功才销毁；FAILED/CANCELLED/SUSPENDED/恢复失败保留 | 生命周期与多重故障 | Application + 真实存储/资源生命周期 | Local/Docker、ArtifactStore | 三终态、成功后收集/cleanup 失败、终态后信号 | 终态 State/详情不变、资源存在、错误/退出码 | 本版无终态修复命令 |
| TA-15；R15；D§8.1 / A§8.1 | Local 复制重建/reset；symlink 换目录；合法 HEAD 变化误拒 | 原 Run/Workspace/规范路径/marker/可用 baseline 匹配；逐层链接替换拒绝；HEAD 可合法变化，index/工作树修改保留 | 路径分区、真实 Git/filesystem | backend attach 真目录 | Git、Path、ownership marker | 缺目录/仓库/marker、错身份、root 越界、祖先 symlink、合法 commit、基线不可用 | 目录/文件/index 对比、稳定错误、零 create/reset | 不证明无人手改每个文件；恶意并发路径替换非完整安全认证 |
| TA-16；R16～18；D§8.2 / A§8.1 | 同名新容器接回、exited 启动过早、writable layer 丢失 | 同完整 ID/labels/image/workdir/资源配置；running 接回，exited 仅 Started 后启动；paused/restarting/removing/dead 拒绝；保留 workdir 外依赖 | 容器状态分区、真实 attach | 实际 Docker backend 跨进程 | 专属真实容器/SQLite | 原容器 running/exited、删除后同名替换、label/配置错、外部目录安装标记 | 容器 ID/层内容、start 时间、无 create/pull/替代 | 部分瞬态状态需受控 inspect 分支加实际代表后盾 |
| TA-17；R17、25、27；D§7.2–3 / A§4、8、13 | 校验 A daemon 却用 ambient B 执行/清理 | Workspace 与 evaluator 各自冻结连接/daemon；inspect/start/exec/harness/cleanup 均绑定所属连接；缺引用/身份变化拒绝，不回退 | 配置决策表、连接真实边界 | 装配至 backend/evaluator；真实连接后盾 | Docker CLI+SDK、连接引用/TLS；双 daemon | ambient context 改 B；原 Workspace/evaluator 不同 daemon；endpoint/TLS 路径缺失 | argv/env、真实 daemon 容器归属、邻居保留 | 真实双 daemon/TLS 环境未核验；受控 CLI 不能证明握手 |
| TA-18；R10；D§11 / A§10 | 未消费旧批准继续有效、改写历史人工 Resolution | pending INVALIDATED+清空与 Started/暂停原子；未执行 APPROVED 授权失效，已执行历史不改；同 Call/Workspace 重新当前 Policy，DENY/APPROVAL/ALLOW 各沿契约 | 审批决策表、事务中断 | 真实 Policy/approval/Journal/SQLite | 当前 revision 与受控交互 handler | pending/approved 未执行/已执行；部分失效后 kill；无 handler；未知 shell+新批准 | approval 版本/Resolution、无旧授权执行、新 request 身份 | Policy 本身全量规则非本增量范围 |
| TA-19；R25；D6 / A§4、13 | 当前默认覆盖冻结配置、秘密落库 | 同任务/Agent/model/generation/runtime/工具指纹/容量；后续原 pricing，不重算旧 cost；不兼容拒绝；秘密仅当前环境引用，endpoint 变化审计 | 配置分区、差异/保留不变量 | rehydration + 实际 request/codec | 真实配置/定价解析；provider 受控 | cwd/默认文件/模型容量变化或缺失；execution/supervision 版本变化；秘密哨兵 | 请求字段、精确 cost、脱敏 DB/artifact/output、拒绝前无请求 | 不证明真实 provider 的兼容性/收费；API 契约需 provider 回归 |
| TA-20；R02、21、26；D§11 / A§5、13 | 同 Step 增编号、Usage 重加、未知计费清零、retry 重置 | 请求前 attempt/计数提交；遗留 attempt unconfirmed 只加一次；同 Step 新请求；已知 response/summary/失败 usage 一次；原retry分支/余额；K=2、used=0/1时固定后续错误/成功序列，恰2/1笔summary并发布继续，used=2时0额外请求且原失败；未发布summary不进context，已发布保留 | 计数守恒、边界、状态路径 | Runtime + 真实 compaction/SQLite | 确定性 provider、真实 context source validation | 请求前后 kill、summary 发布前后、context-limit 强制压缩、预算耗尽后多次 resume | 请求内容/次数、context/version、已确认 cost、unknown 提示、无重复 Step | 真 token 计数与 provider 账单不在 oracle；暂停时长见 TA-30 |
| TA-21；R28、29、35；D§12.1 / A§12.2 | 重导出已确认 patch、认领最新孤立 artifact、诊断覆盖候选 | registered 验证原 ref/bytes/hash/Run/baseline；缺损拒绝；未确认 export 仅旧链结束后同 candidate 新 attempt/独立临时 index；empty/failed 走原失败，不无限 retry | 发布窗口故障、内容不变量 | CandidateSubmission + 实际 Git/ArtifactStore/SQLite | 真实 patch 文件与 index；受控 evaluator | export/put/引用提交之间 kill；孤立更新 patch、诊断 patch、ref 缺失/篡改/错 Run | evaluator 实际输入 bytes/ref、原 index 不变、export 次数、临时路径归属 | 文件系统与 DB 非原子，孤立 artifact 允许保留 |
| TA-22；R30、31；D§12.2 / A§12.3 | 旧 harness/容器未结束就重评、模糊名称误删 | 原 task/patch/config，旧 host 组及严格 instance/harness_run_id 所属资源确认收尾/清理后才新 attempt；未知 EVALUATION_ATTEMPT_BUSY，邻居/Agent 不删 | 故障注入、归属反例、恢复路径 | evaluator + 真宿主组/真实 Docker 清理后盾 | 实际 SQLite/Artifact；受控 harness 和真实 development task | harness 身份前后 kill；残留组/容器；相似名称其他 Run；清理失败 | 新旧attempt/patch hash、启动/清理顺序及范围、无并发；真实development task用独立已确认P_unresolved，严格TASK_UNRESOLVED而非任意失败 | fake不能证明真资源兼容；独立manifest/expected未落实则DEFER |
| TA-23；R27、32；D§12.2 / A§9、12.3 | 已提交结果重评、孤立 report 升级成功、定义变更静默接受 | 完整 EvaluationResult/output/refs 先持久化再终态；已提交通过/失败/错误复用，EvaluationCompleted 只一次；孤立 report 无效；task/data/harness/image digest 不兼容拒绝，不降级 | 结果分区、事务窗口、契约 | Runtime + SWE adapter + 真实持久化 | ArtifactStore/SQLite；确定性 evaluator；真实 harness 后盾 | ResultRecorded 后终态前 kill；pass/fail/error；孤立 report；镜像 tag 移动/原内容失效 | 完整恢复结果、终态/证据对应、零新harness、evaluator kind；Local真链路固定SUCCEEDED/TASK_VERIFIED/0；真实P_unresolved固定FAILED/EVALUATION_FAILED/TASK_UNRESOLVED/1 | expected来自独立冻结夹具，不能从本轮实际结果生成；既有报告解析不重复 |
| TA-24；R34、35、38；D§12.1 / A§12.4 | diagnostic 创建业务 operation/修改终态；暂停后与遗留诊断并发 | 独立 finalization 事件/attempt/临时 index/许可/refs；原业务 State、phase、candidate、终止详情及 Snapshot 不变；开始提交失败不导出，身份提交不明不放行；暂停残留导致 busy | 状态不变量、门控故障、真实 kill | Application finalization + Recorder + 真 export/helper | Git、SQLite、ArtifactStore、Local/Docker | 终态/暂停后开始、身份、许可、完成各窗口 kill；写失败；旧业务未收尾 | State/Snapshot 前后相等、仅事件 sequence 增长、候选 ref 不变、无并发 | 终态 diagnostic 未完成不提供自动 repair |
| TA-25；R22；D§5 / A§7.3、14 | dry-run 写库/mkdir/exec/启动/清理，待检报告通过 | 同锁/同 planner，只读数据库；ready/requires_live_checks=0、rejected=1；stopped/组检查/清理需求明确延期；仅允许锁文件初始化 | 副作用差分、配置分区 | CLI 全链真实只读文件；Docker 只读 inspect | SQLite ro、真实目录、受控命令记录+真实 daemon | 缺 provider key、缺 DB、旧库、缺 artifact root、stopped 容器、旧评估资源、缺 Docker 凭据 | 逻辑 DB 与 artifact tree 前后摘要、无 root/目录生成、外部零 exec/start/delete、待检输出 | 在隔离环境比较；锁文件排除；0 不保证 live checks 成功 |
| TA-26；R23；D5 / A§15 | Typer group 破坏旧入口/trigger；短 ID 接受；允许覆盖原任务 | 旧 task/run/resume/help 和 main(...) 签名行为兼容；完整 UUID（连字符/32 hex、可带 run_）；非法 ID/覆盖选项=2；词语任务显式 run；错误/信号码按契约 | CLI 输入分区、调用契约 | 真实入口解析/subprocess + Python 导入调用 | Typer、实际 settings 定位；provider 受控 | run/resume 词语、选项顺序、缺/短/任意 ID；--database；trigger 调用 | exit/stdout/stderr、传入任务与执行位置、无误启动 | 不把 --help 成功当完整参数兼容 |
| TA-27；R12、26；D8 / A§7、10 | resume_attempts 超过阈值拒绝、一次命令自动无界 retry | 只有真实 ResumeStarted 增审计；dry-run/预检拒绝不增；重复 RECOVERING 原来源不变；每命令一次恢复流程，业务既有 retry 额度不刷新 | 边界、序列 | planner/编排 + 跨进程序列 | 实际 Journal/SQLite | 0/3/4+ attempts，安全暂停/恢复失败交替、重复 dry-run | 审计单调与实际 Started 对应、无 attempt-limit 错误、无自动循环 | 人工多次 Resume 总成本不设上限 |
| TA-28；R05、15、17、27、33；D§13 / A§7、15 | 错误码/顺序不稳、预检失败写 FAILED、输出秘密 | 按固定拒绝优先级输出 stable code/Run/phase/source/Step/Call/Workspace；预检拒绝无业务写；依赖错误保留非敏感原因 | 多故障决策表、泄露哨兵 | planner + CLI 展示 | 真实错误结构/存储；受控环境事实 | 每错误类代表反例；未知 shell+缺 key+exited、终态+坏现场、锁冲突+缺记录 | 主错误与定位字段、DB 不变、敏感哨兵不出现 | 不认证完整安全政策；特殊异常翻译须专项审查 |
| TA-29；R39；D§7.3 / A§4.1 | allow pull 绕过首次冻结，产生不完整可恢复 Run | 允许拉取开/关下缺镜像均 EVALUATION_IMAGE_UNAVAILABLE/1；Workspace/Run/模型/恢复命令/自动 pull 全无；冻结内容 ID 不被新 tag 替代 | 初始化决策表、顺序契约 | 实际 bootstrap + 真实镜像 inspect 后盾 | 冻结 evaluator 连接；受控 provider | 首次镜像有/无、连接失败不同于缺镜像、tag 变更；已有临时资源 | 首笔前失败、无业务/资源创建、临时资源有界清理 | 用户自行准备镜像再新建 Run，不测试自动拉取成功路径 |
| TA-30；R02、12、21、26；D§13 / A§13、15 | 旧 monotonic 导致时长错、旧 span 伪成功、身份串 Run | 新 Trace scope 关联原 Run/Step/Call/attempt/source；旧未闭合仅 interrupted/unknown；active_duration 累计各进程活动区间，暂停不计 | 时间关系、不变量、跨进程关联 | Recorder/Runtime + 真实重开 | 单调时钟分段测量；确定性等待 | 暂停间隙明显大于活动段；恢复同 Step/不同 Run；未闭合 span | duration 区间关系、span 身份/事件、无伪完成 | 不实现 Trace 聚合；计时容差/注入接口待设计落实 |

## 5. 验收追踪与技术取舍

### 5.1 R01–R39 映射

此表只说明每条验收条件有测试义务承接，不表示测试已实现或通过。

| 原验收 | 测试义务 | 原验收 | 测试义务 | 原验收 | 测试义务 |
| --- | --- | --- | --- | --- | --- |
| R01 | TA-01 | R14 | TA-03、13 | R27 | TA-17、23、28 |
| R02 | TA-20、30 | R15 | TA-15、28 | R28 | TA-21 |
| R03 | TA-05 | R16 | TA-16 | R29 | TA-21 |
| R04 | TA-05 | R17 | TA-16、17、28 | R30 | TA-22 |
| R05 | TA-03、28 | R18 | TA-16 | R31 | TA-22 |
| R06 | TA-07 | R19 | TA-07～10 | R32 | TA-23 |
| R07 | TA-06 | R20 | TA-06 | R33 | TA-01、12、14、28 |
| R08 | TA-11 | R21 | TA-20、30 | R34 | TA-04、13、14、24 |
| R09 | TA-11 | R22 | TA-25 | R35 | TA-21、24 |
| R10 | TA-18 | R23 | TA-26 | R36 | TA-09、10 |
| R11 | TA-02 | R24 | TA-05 | R37 | TA-09 |
| R12 | TA-04、12、27、30 | R25 | TA-17、19 | R38 | TA-24 |
| R13 | TA-13 | R26 | TA-12、20、27、30 | R39 | TA-29 |

### 5.2 最小范围选择

| 候选组合 | 速度 / 利用率 | 维护性 / 可靠性 | 能证明的边界 | 决策 |
| --- | --- | --- | --- | --- |
| 纯 planner、模型校验、独立状态模型 | 快、资源小 | 确定性强；按输入输出而非私有布局 | 拒绝规则、合法状态、优先级、预算不变量 | 覆盖组合分支；不能证明进程/DB 语义 |
| Runtime/Journal + 真 SQLite/Git/Artifact | 中等、无需网络 | 每测试临时数据、公开查询，故障定位明确 | 原子事实、完整结果/引用、索引保留、重开 | 事务和内容语义的主证据 |
| 真 Local 跨进程、signal、flock、psutil | 较慢、少量子进程 | 有界握手，不依赖 sleep/特定 PID | 锁释放、组收尾、真实信号、跨进程身份 | 必须保留，进程内取消测试不可替代 |
| 真 Docker helper/exec/supervisor 与冻结连接 | 较贵、需专属容器 | 受 daemon/OS 影响，记录详细身份 | Engine start、组残留、原层接回、连接一致性 | 必须保留；本地 fake 只承担协议分支 |
| 实际 development task SWE-bench 恢复 | 最贵、需要冻结输入/镜像 | 受 harness 版本与环境影响 | 同 patch、严格资源归属、真实评估恢复到结果/终态 | 至少一项已准备 development task；不复制报告解析全分支 |
| 真实模型生成修复方案 | 慢、花费且随机 | 重现和 oracle 均弱 | 模型任务能力 | 本增量不要求；以确定性候选隔离恢复风险 |

决策表优于 pairwise：未知 shell 的优先拒绝、helper 许可、评估归属均有已知多条件规则，不能以两两组合声称覆盖。状态转换优于逐函数断言：失败发生在多次中断与合法重入之间。边界测试用于 next_index、max_steps、retry 余额和 30 秒共享 deadline；不虚构 token/cost budget 引擎。

生成式不变量可扩展合法状态路径、批结果关联、审计/Usage 保留；不强制新增属性测试依赖。模型必须独立写出许可/状态规则，不能调用产品 planner 作 oracle。通用 fuzz/chaos、负载/soak、RPO/RTO、全量 benchmark 不增加已知协议风险的必要信心，本轮不要求。若确定性解析反例仍发现未知崩溃，再做定向生成。

选择性 mutation 仅在 oracle 强度存疑时使用，例如删掉 shell 拒绝、提前发送 start、跳过整批早期失败、重置 unknown 计数、用最新 patch 替代原引用。测试应能检测这些具体错误；不设置覆盖率、mutation 百分比或统一测试层级比例。

## 6. 数据、替身与环境

### 6.1 依赖策略

| 依赖 | 替身及表示语义 | 未表示的语义 | 漂移控制 / 真实后盾 |
| --- | --- | --- | --- |
| ModelProvider | 协议 stub 返回确定性响应/错误/Usage；外部记录请求；请求禁止/次数是契约时进入 oracle | 网络收费、模型修复质量、真实 tokenization | 使用实际 ModelRequest/response 类型；现有 provider 契约回归；不把账单列为验收 |
| Engine/进程 inspection facts | planner 使用显式事实值；协议测试用受控 RPC 端点/记录器；错误分支可提供 AccessDenied/PID 出生错等事实 | 实际 kernel 枚举、TLS、Engine start 语义 | helper 门控用真实进程/通道；TA-08～10、16～17 真实 OS/daemon 后盾；API 样本按项目锁定 SDK 契约 |
| harness executable | 真实受控进程，产生子进程/报告/exit，并按冻结身份模拟资源记录 | 上游真实命名、patch 应用、测试执行 | 现有 harness 参数/报告契约 + TA-22/23 至少一项真实 development task |
| RuntimeStore 测试适配器 | 委托真实 SQLite，仅在公开调用边界握手或丢失确认 | 不模拟事务成功、回滚或 crash durability | 每次重开真库检查；约束冲突/进程 kill 保留实际事务证据 |

不 mock 项目内部 State、transition、Journal、CandidateSubmission、hash、Git、ArtifactStore 或 flock。数据和依赖拥有真实但确定性行为时直接使用；需要控制异步故障的位置，用显式公开观察协议和外部握手。无法稳定制造的 OS 故障应标注证据限制，不悄悄改成 fake 后宣称真实验证。

### 6.2 数据与隔离

- 每测试独立 Run/attempt、临时 SQLite/Artifact/Local root、Git 仓库、marker、临时 index、容器和 harness 目录。共享 builder/不可变样本，不共享可变 Workspace。测试工具不继承锁 FD。
- 合成数据含三调用 batch、不同位置失败结果、合法候选/失败响应、定价和 Usage 独立算例、版本坏/缺字段/错 scope 的存储样本、孤立 patch/report、相似标签邻居。凭据用可检索哨兵，断言 DB/Event/Snapshot/artifact/CLI 不出现秘密值，不复制真实密钥。
- 故障夹具保留初始状态、屏障名称、发送信号及提交时间线、每个外部调用、sequence/attempt/PID 出生身份、container/exec ID、fixture 版本和最小失败序列。失败时先收只读证据，再仅回收本测试已证明归属的资源。
- 信号和竞争用管道/socket/外部进程握手或提交确认触发；固定sleep不能证明窗口。竞争时赢家在已持锁的公开提交边界确认并等待，收到输家冲突后才放行，记录acquired < loser_attempt < loser_result < release；同时发起不算持锁重叠。监督进程须有超时，但超时kill不算存活调用者自行停机或锁冲突通过。
- 支持面为 macOS 与 Linux。两者均需真实 flock、进程出生/组、信号和 Local 恢复证据；Docker 通常经 daemon/VM 运行，其 guest 组身份与 host helper 身份分别记录，不能混为一个 PID 空间。
- 时间规则：产品暂停预算固定 30 秒且冻结，所有阶段共享；较小受控 deadline 检查分支，真实默认值链路检查集成。记录 monotonic elapsed、阶段剩余预算、监督上限与调度误差。容差需在测试设计固定且独立记录，不能用测试监督时限代替产品预算，不能按阶段各放宽 30 秒。
- SWE-bench仅用已准备development task和独立确认的非空P_unresolved。manifest预先冻结patch bytes/hash、task/data/harness/image/daemon身份及正常应用但valid unresolved的报告/必要失败测试集合；expected固定FAILED/EVALUATION_FAILED/TASK_UNRESOLVED/CLI1，缺夹具记DEFER，infra error不算预期失败通过。Local固定provider B3→resume-local-ok，恢复SUCCEEDED/TASK_VERIFIED/CLI0。实际敏感数据留授权环境，不复制隐藏测试/gold/密钥。双daemon/TLS可用性未知，合成CLI不证明真实握手。
- 不用“重跑到通过”覆盖首次失败。环境缺失、产品失败、受控夹具故障分别记录；跳过/隔离不算验证。若需隔离，必须指定负责人、原因、期限与保留信号，本次不创建隔离政策或 CI gate。

## 7. 当前基础证据与缺口

只读抽查，不是全量测试审计；本轮没有执行下列测试。

| 现有材料 | 可复用基础 | 仍缺的 Resume 信心 |
| --- | --- | --- |
| `tests/storage/test_sqlite.py` 的完整 ToolEnded 重开、CAS、原子 State/context/Event/Snapshot、Approval、脱敏与不支持版本测试 | 真实 SQLite 与原格式契约；完整结果可持久化 | 新 schema/envelope、attach、恢复/暂停事务、未消费授权、提交不明后的停机协议；旧格式测试需按拒绝旧版本重定向，不能增加迁移 |
| `tests/runtime/test_compaction.py` 的即时发布/回滚、summary Usage、同 Step retry、source validation | compaction 原子协议与当前有界 retry | request_progress 跨进程保留、未知请求一次计数、强制压缩分支恢复与不重复边界压缩 |
| `tests/environments/test_local.py` 的 timeout 子进程回收、Git patch 与 read_file | Local 进程和内容基础 | retained attach、出生/组身份与残留、真实锁/kill/信号重入；checkpoint recover 测试不证明 attach |
| `tests/environments/test_docker.py` 的资源限制、timeout、patch 与可选 Docker 测试 | 既有容器工具基础 | exec ID/helper 门控/supervisor/原层接回。部分用 ScriptedDockerBackend 覆盖内部 `_run`，不能算真实 daemon 验收 |
| `tests/cli/test_candidate_submission.py`、`tests/evaluation/test_swebench_cleanup.py` / `test_swebench_docker.py` | 候选只提交一次、当前 attempt 清理、冻结连接参数等 | 提交事实跨进程、旧 harness 与邻居、双连接实际执行、TLS 握手；synthetic TLS material 仅参数测试 |
| `tests/cli/test_main.py` 的信号/退出码、Application finalization 测试 | 现 CLI 取消和交付行为基线 | 首信号从取消改暂停、独立结果类型、旧 main/trigger、显式路由和 dry-run；保留库取消回归 |

当前 `RunStatus` 无 SUSPENDED，`AgentRuntime.run()` 仍创建新 State，recovery context 仍为 v1/checkpoint 结构。新 Resume 义务不能因这些基础测试存在而标为覆盖；本分析也不据此要求重写与本增量无关的既有测试。

### 7.1 质量扫描与局部 DEFER

| 方面 | 结论 / 明确限制 | 后续材料 |
| --- | --- | --- |
| 功能、并发、恢复、完整性 | 主风险；TA-01～24、27/29/30 分别覆盖受治理路径，不为每个组件机械复制同一套测试 | 按A§9记录业务同次提交组合；共享DB-A/B/C后段故障/成功控制单独验证，禁止用18个同类早期错误替代 |
| 兼容与版本 | 当前格式唯一支持，旧格式拒绝；main(...)、Workspace.execute、Evaluator 基本调用和终态 RunResult 保留 | 新公开类型/调用方清单由实现设计落实；不能用迁移测试替代拒绝测试 |
| 性能/容量 | 仅已确认 30 秒共享预算与现有 retry 限额；无新吞吐、内存、RPO/RTO 目标 | 计时容差/测试监督参数局部 DEFER，交测试设计与运行环境负责人；不重新询问预算是否批准 |
| 安全/隐私 | 文件边界、身份、审批、秘密引用、冻结连接、邻居保护有功能 oracle | 不据功能全绿认证 hostile host、symlink TOCTOU、容器逃逸/完整安全策略；如扩大保证交安全负责人 |
| 可操作性/可用性 | CLI 拒绝原因、pending live checks、unknown 计费和退出码可客观断言 | 人工审读拒绝和 dry-run 文案；无新增 GUI/可访问性标准来源 |
| 环境保真 | 真实macOS/Linux、Docker与至少一个独立已知候选结果的development task是所需证据 | 环境/执行负责人未指定，当前可用性及P_unresolved manifest/独立expected夹具局部DEFER；不能用受控harness/CLI、任意失败或跳过替代 |
| 可测试性 | 事务故障需写后前缀证明；G3-E/U需存活错误出口；锁需持有区间握手；禁止私有monkeypatch或竞速sleep | test design 确定公开 observer/store 边界的屏障与独立日志；尚无实现，不宣称屏障已可用 |

未发现需要重新确认的 D/C 产品选择。上述 DEFER 是测量、环境与观测落实项，不改变已确认架构；若实现无法提供可信 oracle 或真实边界，应将对应义务改为 DEFER/NOT-ADEQUATE，而非默认降低要求。

## 8. 充分性判断与移交

```text
Subject:              同主机 Local/Docker Resume 增量，从首次初始化至终态 finalization
Mode:                 Design
Strategy pass:        Combined
Decision:             PARTIAL（F1–F5设计修订待复审）；真实环境、独立候选oracle与测量/屏障为局部DEFER
Test basis:           D§1–15、D1–D10、R01–R39；A§4–15、18 C01–C12 与评审后边界
Architecture basis:   2026-10-19 已确认架构工作区版本；只采用当前格式，无迁移
Quality risks:        未知/并发副作用 > 持久化/身份/评估完整性 > 暂停/冻结/授权 > CLI/审计
Manual evaluation:    未知 shell、requires_live_checks、unknown 计费和 finalization 拒绝文案审读；残留证据核对
Adequacy signals:     R01–R39 映射、A§9 提交矩阵、不变量、实际副作用/请求记录、真实边界与环境缺口
Handoffs:             测试设计落实刺激/屏障/测量；shift-left 选择最早可观察位置；CI/CD 管触发/门禁/证据保留
                      traceability 管需求到实现及执行结果；运行环境负责人提供 OS/daemon/task；安全专项按边界承接
Residual risk:        未执行、环境和负责人未落实；任意 shell exactly-once/人为并发修改/脱组 daemon/灾备不保证
Next action:          复审Test Design的F1–F5修订，落实后段DB故障/存活错误出口/锁握手及固定P_unresolved夹具
Verification:         对照审计与选定SQLite/compaction边界修订文档；TC/R/TA/提交组合与diff检查；未运行runtime tests
```

本组合在已确认架构下为每项明确验收提供可证伪路径；未以测试数量、覆盖率或已有绿色 suite 判为 ADEQUATE。后续只对架构/环境变化影响的行重做 portfolio，不重新展开全部义务。CI 触发、门禁、新鲜度、隔离和失败行动另行决定；本文件不将测试设计、测试实现和测试执行混为已取得证据。

## 9. F1–F5修订映射

下列只记录设计证据路径已修改，未声明审计关闭或用例通过；完整刺激/预期在Test Design对应TC中。原风险与R/TA ID保持不变。

| Audit | Affected obligation / corrected evidence path | Test Design | Remaining gap |
| --- | --- | --- | --- |
| F1 | TA-01/04/18/24：业务AC01～18检验同次提交组合；共享DB-A审批写后、DB-B全State/Snapshot/审批/event写后故障须前缀命中，重开全旧；DB-C正常控制全新；创建入口独立 | TC-U2-13/01、TC-U4-11～13；§4.2 | 条件触发器/schema/错误原因链尚待夹具实现，未命中不计证据；不再18次重复早期event冲突 |
| F2 | TA-04：G3-E/U向存活调用者返回不确定错误，必须自行exit1且零后继attach/dispatch/exec诊断；G3-K只证明重读 | TC-U4-14～17；硬杀TC-U4-04/08独立保留 | 确认错误注入不能伪造DB结果；监督kill不算停机协议通过 |
| F3 | TA-22/23：Local固定成功TASK_VERIFIED/CLI0；真实非空P_unresolved固定valid unresolved/EVALUATION_FAILED/TASK_UNRESOLVED/CLI1，排除任意infra失败 | TC-U21-01、TC-U21-04～06 | 新Q-4的manifest/独立结果证据未装入夹具；环境未核验；保持DEFER |
| F4 | TA-20：K=2/used=0、1分别按固定错误/成功响应恰2、1笔summary后发布继续；used=2零额外请求且原失败；唯一业务retry亦有精确请求/终止判据 | TC-U8-14～16、TC-U14-07 | 必须进入固定强制压缩/source合法分支，不以软压缩回退或unknown重发替代 |
| F5 | TA-02：赢家已取真实锁并握手，等待输家实际冲突后再放行，验证整个尝试在持锁区间内 | TC-U20-02，复用TC-U20-01/03同步 | POSIX/真实双进程环境仍未执行；超时清理不算竞争验证 |
