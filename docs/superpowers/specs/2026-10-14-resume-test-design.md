# Resume Test Design

日期：2026-10-19；文件日期沿用所属需求增量。状态：测试设计评审稿，未实现、未执行。

Review 修订：按 [Test Strategy Audit](2026-10-14-resume-test-strategy-audit.md) F1–F5 修正事务故障位置、存活调用者停机判据、确定性旅程结果、retry 精确计数及锁区间同步。保留原 TC/R/TA ID；新增 TC 续号，审计原结论保留为历史，本文修订不代表重新审计通过。

## 1. 测试设计计划

| Unit | Source | Logic | Technique | Why this technique |
| --- | --- | --- | --- | --- |
| U1 | D§5；A§15；R23；TA-26 | CLI 标识、路由与调用兼容 | 等价分区 | 不同输入类别有不同解析/拒绝结果 |
| U2 | D§7；A§4.1、14；R01、33、39；TA-01、29 | 格式与初始化证据 | 等价分区 | 当前格式、未知格式、缺恢复证据需区分 |
| U3 | D§6；A§4.3–4.4；R12、13、33；TA-12～14 | 暂停/恢复生命周期 | 状态转换 | 同一恢复事件是否允许依赖当前持久状态 |
| U4 | D§7、10；A§4、7、9–10；R01、12、34；TA-01/04/18/24 | 恢复编排、提交不明与共享存储故障 | 用例测试 | 跨组件异常出口与公开store的写后回滚路径分别观察 |
| U5 | D§7.1、9；A§4.4；R07、33；TA-06、12 | State 与历史事实一致性 | 等价分区 | 每种独立不一致必须拒绝 |
| U6 | D§9；A§8.2；R05、06、14、19、24；TA-03、05、07 | 未完成调用能否重试 | 决策表 | 开始/结果/工具分类/收尾证据共同决定行为 |
| U7 | D§9、11；A§5；R02～04、07～09、20；TA-05、06、11 | 持久化 phase 重入 | 状态转换 | 持久 phase/处置决定下一业务事件 |
| U8 | D§11；A§4、13；R20、26；TA-06、20、27 | 游标、Step 与 retry 界限 | 边界值分析 | 有序计数的邻界决定完成、继续或停止 |
| U9 | D§11；A§10；R10；TA-18 | 旧授权失效与当前 Policy | 状态转换＋决策表 | 授权历史需保持；失效后另有 Policy 规则 |
| U10 | D§8；A§8.3；R19；TA-08 | 宿主执行身份/整组收尾 | 决策表 | 身份、可读性与组活动联合决定安全性 |
| U11 | D§8；A§8.4；R36、37；TA-09、10 | helper/exec 门控许可 | 状态转换 | 必须按持久许可顺序发 RPC，且覆盖重入与死亡 |
| U12 | D§8；A§8.1；R15～18；TA-15、16 | 原 Workspace 身份接回 | 等价分区 | Local 路径和 Docker 身份的独立有效/无效类别 |
| U13 | D§7.2–3、11；A§4、13；R25、27；TA-17、19 | 冻结装配/连接 | 等价分区 | 冻结数据保留、兼容变化与明确不兼容类别 |
| U14 | D§9、11；A§5、13、15；R02、21、26；TA-20、30 | 请求、压缩与 Usage | 状态转换 | 请求确认和 summary 发布前后行为不同 |
| U15 | D§12.1；A§12.2；R28、29、35；TA-21 | candidate 提交 | 状态转换 | registered 后不能重新 export，未确认 export 另有守卫 |
| U16 | D§12.2；A§12.3；R27、30～32；TA-22、23 | 评估 attempt 与结果复用 | 状态转换 | 结果确认前后分别重评/只终止，旧资源须收尾 |
| U17 | D§12.1、13；A§12.4；R34、35、38；TA-14、24 | diagnostic finalization | 用例测试 | 事件追加流程及失败保证，业务状态全程保持 |
| U18 | D§5；A§7.3；R22；TA-25、28 | dry-run 只读计划 | 决策表 | 预检拒绝、live checks、凭据共同决定输出 |
| U19 | D§6；A§11、18 C10；R13、14、34；TA-13 | 信号路由与共享预算 | 决策表＋边界值分析 | 信号序次/持久化阶段决定动作，deadline 另有邻界 |
| U20 | D9；A§6、9；R11；TA-02 | 文件锁、CAS 与执行隔离 | 用例测试 | 多进程竞争与异常退出路径，不以 fake 锁证明 |
| U21 | D§14；A§16；R01、04、12、18、25～32；TA-17、22、27、30 | 真实边界组合旅程 | 用例测试 | Local、Docker、双连接、SWE-bench 各有不可缩小的组合风险 |

生命周期、许可协议、数据分类、边界与跨进程路径分别建模。单用决策表无法验证已提交操作的重入顺序；单用状态路径又会遗漏错误格式、游标边界和独立身份反例。不采用 pairwise 代替已知多条件规则。

依据：[需求设计 D](2026-10-14-resume-design.md)、[架构 A](2026-10-14-resume-architecture.md)、[Test Analysis](2026-10-14-resume-test-analysis.md)。D1–D10、C01–C12 已确认；本文不重新批准或修改产品契约。保留原 R01–R39 和 TA-01～TA-30；U、TC、EP、规则、转换和路径编号是本文局部 ID。

## 2. 假设、待落实项与公共测试约定

| ID | Assumption or question | Why it matters | Affects |
| --- | --- | --- | --- |
| ASM-1 | 除明确标记 robustness 的优先级测试外，负向用例每次仅一个无效条件；其余均合法 | 避免故障遮蔽 | 全部单故障反例 |
| ASM-2 | 数据为合成/隔离夹具；真实 SWE-bench 仅选已准备 development task | 防止污染现场或使用正式任务调优 | 全部；U21 |
| ASM-3 | 表中“变体”必须各自独立运行并保留 variant ID，不把多种失效塞进一次执行 | 保留可定位的证据与计数 | 带变体的用例 |
| ASM-4 | 边界可控时使用测试时钟/公开 deadline 参数，步长 δ=1ms；30 秒仍为产品默认/冻结总预算 | δ 是设计测量精度，不是产品新阈值 | U19-BVA |
| ASM-5 | 冻结定价夹具无 cache；input=1、output=2 单位价格/百万 token；累计基数 input=40/output=10、cost=0.00006 | 独立计算 oracle，避免调用被测计费函数造预期 | U13、U14 |
| ASM-6 | 存储故障夹具只在独立测试库安装带前缀检查的 SQLite 条件触发器；不改变产品 schema，不 mock store/Journal/transition | 明确失败发生在实际写入后，未命中不得计事务证据 | TC-U2-13、TC-U4-11～13；F1 |
| ASM-7 | U8 的 retry 边界使用已持久化 retryable failure、无活动未确认 attempt、强制压缩分支；summary reserve/source/容量均合法；K=2 | 排除 unknown 重发和可回退旧 context 的软压缩分支，精确计算剩余 retry | TC-U8-14～16；F4 |
| Q-1 | 真实 macOS、Linux、Docker 双 daemon/TLS、development task 的可用环境/执行负责人尚未登记 | 决定哪些真实后盾可执行，不改变预期 | U10～12、U20、U21；执行前落实 |
| Q-2 | 墙钟测量误差 ε 与监督上限需按执行宿主记录；当前不选任意秒数放宽预算 | 默认预算验证需要可信计时；超出误差不能判通过 | TC-U19-13、TC-U21-01；计时结论暂 DEFER |
| Q-3 | 新事件最终枚举名、公共屏障的具体实现尚未确定 | 本文以业务事实命名屏障，不依赖私有方法；未命中窗口即未验证 | 全部故障注入用例 |
| Q-4 | 真实 development task 的固定 `P_unresolved` bytes/hash、版本化 manifest 与独立已确认 unresolved 证据尚未装入执行夹具 | 预期已固定为 TASK_UNRESOLVED，缺夹具只记 DEFER，不能换成任意结果或现场给预期赋值 | TC-U21-04～06；F3 |

没有改变产品 expected result 的待选需求。Q 项是执行/测量落实项；未解决时对应结果记未执行/DEFER，不能用跳过、任意 sleep 或反复重跑替代。

公共夹具与步骤：

1. `F` 是独立 Run UUID `12345678-1234-4234-8234-123456789abc` 的当前格式记录，完整 retained attachment、原 Agent/config/pricing/tools/context，Local 或专属 Docker；`F2` 为另一 Run。首笔 UUID 由 application 分配，非让测试改写原 Run。
2. `B3` 是同 Step 三个不同 Call，分别向独立业务日志追加 `c0/c1/c2`；完整结果、消息与预期 Usage 在输入中固定。`P` 是真实临时 Git 仓库导出的候选 patch，`PD` 为内容不同的 diagnostic patch。U21 的真实评估专用 `P_unresolved` 是已独立确认能正常应用但不能修复所选 development task 的非空 patch；不得将其他 P 的未知结果直接当作它。
3. 表中“恢复”统一为：确认屏障位置 → 若需要发送真实信号/kill 原父进程 → 新进程调用 resume → 新连接读取 State/Event/Approval/Snapshot/Artifact → 读取外部日志/进程/容器事实。断言每一步的状态/禁用动作，不能只检查最后 status。
4. 跨进程使用真实 SQLite、Git、ArtifactStore、Workspace、OS 进程；provider 为符合公开协议的确定性 stub。harness/Engine 受控端点仅负责可控协议分支；真实 daemon 和真实 harness 的后盾在 U21，不把 stub 通过称真实兼容。
5. `NO-WORK`：无新增 provider/业务工具/export/harness 调用、Engine create/start、容器创建/启动/清理；允许本场景规定的只读 inspection。预检拒绝还需业务 DB 不变；ResumeStarted 后允许已确认的恢复事实及明确授权的 attach/旧评估清理，不能机械套用预检零写入。
6. 每测试保留 run/attempt/source sequence、外部调用时间线、PID 出生/组身份、container/exec ID、完整错误/输出及最后确认记录。无证据时不从文件/stdout/report 猜成功；不清理邻居。秘密哨兵 `secret-resume-fixture` 不得进入 DB/Event/Snapshot/artifact/CLI 诊断。

公共屏障（语义位置，不要求产品暴露同名函数）：

| ID | 可定位事实 | 观察/注入方式 | 必查禁令 |
| --- | --- | --- | --- |
| G0 | 首笔完整 RunCommit 已完成，provider 未获请求 | 委托真实 RuntimeStore 的公开测试适配器在 commit 返回边界握手 | 不提前输出可恢复命令/请求 |
| G1 | request/ToolExecutionStarted 已提交，外部动作未放行 | Store 边界与受控 provider/工具进程握手 | 开始事实未确认不得动作 |
| G2 | 子进程已产生可观察副作用，完成事务尚未提交 | 测试命令向独立管道握手，Store 公开提交入口握手 | 无可信完成不得跳过未知 shell |
| G3-K | 事务已提交，调用者未收到确认即硬中断 | 真实 commit 后阻断返回并 kill；仅验证新进程重读。旧用例未写后缀的“G3 kill”均指此模式 | 已确认结果不重放；不据此宣称旧进程会处理提交不明错误 |
| G3-E | 真实提交已应用，仍存活调用者收到不确定持久化错误 | 委托边界保存实际 commit 结果供监督者核对，再向调用者抛该控制错误；测试不先 kill/cancel 调用者 | 命令自行退出且禁止后继发令；重开确认实际新事实；应用只报告最后收到确认的 sequence |
| G3-U | 未调用底层提交，调用者仍仅收到同一不确定错误 | 公开委托边界一次性不下发所选提交并抛错误；只建模调用者不知道结果，不算 SQLite 回滚实验 | 与 G3-E 相同停机，重开显示旧事实；不把“不确定”翻译为未发生后继续 |
| G4 | helper 等待许可；身份/许可意图尚未确认 | helper 控制协议、委托 Store；独立 Engine 记录器 | 不发 RPC；EOF/timeout 不自放行 |
| G5 | helper 已获 start 许可，Engine 响应延迟 | 受控 Engine 接受 start 并握手，真实 helper 仍活动 | 当前 Running=false 不能授权新链 |
| G6 | candidate put 成功，引用尚未进 RunCommit | 公开 ArtifactStore/Store 边界委托真实实现握手 | 孤立文件不被认领 |
| G7 | 完整 EvaluationResult 已提交，终态事务尚未发起 | 真 Store 确认后握手 | 不再启动 evaluator |
| G8 | diagnostic 开始/身份/许可/结果事实追加 | 公开 observer/Store 边界与真实 export/helper | 不修改业务 State/Snapshot |

屏障不 mock 内部 transition、Journal 或 SQLite 成功/回滚；事务机制用真实数据库后段故障与重开验证。G3-E/U 只注入公开持久化边界的确认不确定性，不伪造底层事务结果。错误必须到达仍存活的 application，监督者等待其正常错误出口并读取外部日志；若监督者先超时 kill，则该变体失败/未验证，不能拿零副作用计通过。G3-K 的 kill 后重入独立保留。暂不能落实某屏障时保留 Q-3。

## 3. 分单元测试设计

### U1：CLI 输入与兼容调用

技术：等价分区。输入是首参数、完整 ID、允许选项、调用入口；输出为路由/参数拒绝/业务结果。以下有效分区有不同解析契约，不合并成一个“合法字符串”。

| Input | Partition | Validity | Values / rule | Why equivalent | Representative |
| --- | --- | --- | --- | --- | --- |
| UUID | U1-E1 | 有效 | 完整连字符 UUID | 规范化为同内部 UUID | `12345678-1234-4234-8234-123456789abc` |
| UUID | U1-E2 | 有效 | 完整 32 hex | 相同 UUID 的允许表示 | `12345678123442348234123456789abc` |
| UUID | U1-E3 | 有效 | `run_`+完整 UUID | 仅显示前缀，不改主键 | `run_12345678-1234-4234-8234-123456789abc` |
| UUID | U1-E4 | 有效 | `run_`+32 hex | 允许前缀组合 | `run_12345678123442348234123456789abc` |
| UUID | U1-E5 | 无效 | 模糊前缀 | 不支持缩写 | `12345678` |
| UUID | U1-E6 | 无效 | 任意显示名 | 非完整 UUID | `run_xxx` |
| UUID | U1-E7 | 无效 | 缺参数 | 必需参数缺失 | `resume` |
| 路由 | U1-E8 | 有效 | 旧 task 调用 | 首词非 run/resume 进入旧 parser | `miniagent "fix issue"` |
| 路由 | U1-E9 | 有效 | 显式 run | 子命令 task 保留 | `miniagent run "fix issue"` |
| 路由 | U1-E10 | 有效 | 保留词作 task | 通过 run 规避路由歧义 | `miniagent run "resume"` |
| 选项 | U1-E11 | 有效 | 只指定数据库位置 | 不覆盖 Run 定义 | `resume --run-id <F> --database <abs-db>` |
| 选项 | U1-E12 | 无效 | 覆盖冻结定义 | task/model/workspace/预算/定价均不接受 | `resume --run-id <F> --model other` |
| 入口 | U1-E13 | 有效 | help | 解析但不执行 | `miniagent resume --help` |
| 入口 | U1-E14 | 有效 | Python main/trigger | 保留既有导入调用 | 当前 trigger 的 main(...) 调用参数 |

不组合独立无效分区；E12 的其他字段为同一拒绝规则的补充变体。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U1-01 | 规范 UUID | U1-E1 | F 安全可恢复；用连字符 ID dry-run | 定位 F；输出 run_ 前缀 ID，内部 UUID 不变 | R23；D§5 |
| TC-U1-02 | 32 hex UUID | U1-E2 | 同 F 用 32 hex | 与 01 同一记录，无新 Run | R23；D§5 |
| TC-U1-03 | 显示前缀 | U1-E3 | 同 F 用 run_+连字符 | 前缀只做输入/展示，不进主键 | R23；D§5 |
| TC-U1-04 | 前缀与 hex | U1-E4 | 同 F 用 run_+32 hex | 定位 F | R23；D§5 |
| TC-U1-05 | 拒绝缩写 | U1-E5 | 仅 ID 改短 | exit 2；NO-WORK | R23；D§5 |
| TC-U1-06 | 拒绝任意名称 | U1-E6 | 仅 ID 改 run_xxx | exit 2；NO-WORK | R23；D§5 |
| TC-U1-07 | 缺 ID | U1-E7 | 无 --run-id | exit 2；NO-WORK | R23；D§5 |
| TC-U1-08 | 旧 task 入口 | U1-E8 | 旧选项选 Local，provider 固定候选 | task/选项到达原装配；正常结果，最终显示 ID | R23；D5 |
| TC-U1-09 | 显式 run | U1-E9 | 与 08 同 task/选项 | 等价任务/配置，成功 exit 0 | R23；D5 |
| TC-U1-10 | 保留词 task | U1-E10 | 独立变体 task=`resume`、`run` | 作为新任务原文，不进入恢复路由 | R23；D§5 |
| TC-U1-11 | 外部数据库定位 | U1-E11 | 默认库有另一记录，--database 指向 F 库 | 只定位指定库 F，冻结路径不取 cwd | R23、25；D§5 |
| TC-U1-12 | 不接受定义覆盖 | U1-E12 | model 为主例；task/workspace/预算/定价逐项独立变体 | 参数错误 exit 2；不装配、不改原定义 | R23；D6 |
| TC-U1-13 | help 全入口 | U1-E13 | 顶层/run/resume --help 各独立调用 | exit 0；对应帮助可读；NO-WORK | R23；A§15 |
| TC-U1-14 | 导入兼容 | U1-E14 | 用当前 main(...) 签名及 trigger 导入调用各一例 | 无新增必需参数；进入 application 执行作用域，原行为可用 | R23；D5 |

### U2：存储与首笔证据

技术：等价分区；镜像首次冻结是数据准备契约，允许拉取的两个值均需独立验证。每项损坏只改一个字段。

| Input | Partition | Validity | Rule / representative | Why equivalent |
| --- | --- | --- | --- | --- |
| 存储 | U2-E1 | 有效 | 空库新建 schema v3/Run/Snapshot v3/recovery v2 | 直接创建唯一当前格式 |
| 存储 | U2-E2 | 有效 | 当前格式重开 | 无迁移/重建 |
| 存储 | U2-E3 | 无效 | SQLite user_version=2 | 非当前库格式都拒绝 |
| envelope | U2-E4 | 无效 | Run envelope=2 | 旧 Run 不解码兼容 |
| envelope | U2-E5 | 无效 | Snapshot envelope=2 | 旧 Snapshot 不兼容 |
| recovery | U2-E6 | 无效 | recovery version=1 | 不使用 checkpoint 旧描述 |
| 版本 | U2-E7 | 无效 | 当前版本+1 | 未知未来版本不默认读取 |
| 恢复证据 | U2-E8 | 无效 | 当前格式 attachment 缺失 | 缺事实，不等同旧格式 |
| 应用 envelope | U2-E9 | 无效 | kind=`other_application` | 未支持 kind 拒绝，不动态 import |
| 应用 envelope | U2-E10 | 无效 | payload digest 改 1 字节 | 定义绑定不可验证 |
| 评估镜像 | U2-E11 | 有效 | 内容 ID 可 inspect | 首笔前可冻结 |
| 评估镜像 | U2-E12 | 无效 | inspect 明确不存在 | allow pull 不可绕过冻结 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U2-01 | 首次完整记录 | U2-E1、E11 | 真新库/Workspace；G0 确认后 kill；新进程 resume | 首笔含完整身份/定义/镜像内容 ID；ID/恢复命令只在提交后打印；create/RunStarted 各一次；同现场首次请求 | R01；A§4.1、9 |
| TC-U2-02 | 当前库重开 | U2-E2 | 当前库已有 F 和 F2；重开读取 F | 全部记录保持；无旧表转换或自动删除；F 可恢复 | R33；A§14 |
| TC-U2-03 | 拒绝旧 DB | U2-E3 | 唯一变化 user_version=2 | RESUME_SCHEMA_UNSUPPORTED，exit 1；原 bytes/表/Workspace 保留；NO-WORK | R33；A§14 |
| TC-U2-04 | 拒绝旧 Run | U2-E4 | 当前库只改 F Run envelope 版本 | 格式不支持，exit 1；不按默认补字段或转换 | R33；A§14 |
| TC-U2-05 | 拒绝旧 Snapshot | U2-E5 | 当前 F 所需 Snapshot 只改版本 | 格式不支持；不覆盖/跳过必要校验证据后继续 | R33；A§14 |
| TC-U2-06 | 拒绝旧 recovery | U2-E6 | recovery v1，旁有 checkpoint | 格式不支持；不读 checkpoint 恢复现场 | R33；D4、D10 |
| TC-U2-07 | 拒绝未来格式 | U2-E7 | DB/Run/Snapshot/recovery/应用 payload 逐项 current+1，各独立运行 | 格式不支持；不转换、不写库 | R33；A§14 |
| TC-U2-08 | 当前缺证据 | U2-E8 | 当前格式缺 attachment；执行/装配证据缺失各补充变体 | RESUME_METADATA_MISSING；不扫描目录/容器认领 | R33；D10 |
| TC-U2-09 | 未知 kind | U2-E9 | 仅应用 envelope kind 错 | 明确拒绝未知 kind，NO-WORK；不加载所指定代码；精确子码不作未规定断言 | R33；A§4.1 |
| TC-U2-10 | 绑定摘要坏 | U2-E10 | payload/RunDefinition 绑定摘要分别一项损坏 | 拒绝不完整/不一致证据，不静默采纳当前定义；NO-WORK | R33；A§4.1 |
| TC-U2-11 | 镜像缺失且允许拉取 | U2-E12 | Docker 初始化 allow_image_pull=True；评估镜像不存在 | EVALUATION_IMAGE_UNAVAILABLE/1；无 pull、Workspace 创建、RunRecord、模型请求、恢复命令 | R39；A§4.1 |
| TC-U2-12 | 镜像缺失且禁止拉取 | U2-E12 | 同 11，仅 allow=False | 同 11；不创建可恢复 Run | R39；A§4.1 |
| TC-U2-13 | 首次创建写后失败 | 新Run首次写入失败路径；F1 | 独立已初始化测试库，F2为已有对照、目标新Run尚不存在；安装仅匹配目标的AFTER INSERT条件触发器：新行完整State/recovery/装配字段可见后发专用RAISE(FAIL)；经真实公开创建入口触发，不套用已有Run commit | 失败信号/原因链证明已到新行写后，随后重开无目标Run/关联事件/不完整描述；无模型/恢复命令；仅本次新资源有界清理，F2不变。未命中或创建拆成多笔导致残留则失败；成功控制见TC-U2-01 | R01、39；A§9、14；F1 |

| Test ID | Valid partitions combined | Reduction rationale |
| --- | --- | --- |
| TC-U2-01 | U2-E1、U2-E11 | 空库当前格式和可用镜像同时为合法前提；失败分区各独立验证，不声称覆盖所有初始化配置组合 |

### U3：Run 生命周期

技术：状态转换。起点 CREATED；合法持久化业务前缀建立其他起始状态。终点 SUCCEEDED/FAILED/CANCELLED。此模型只针对 pause、resume、recovery_complete 三种事件；原业务正常转换由 U7/16 与既有测试承接。

`pause` 表示首信号且暂停提交成功；`resume` 表示持锁、预检通过且 Started 已确认；`complete` 表示 attach/装配完成且 Completed 已确认。不能用这张表推断任何不满足 guard 的事件也获准。

| State / Event | pause | resume | complete |
| --- | --- | --- | --- |
| CREATED | SUSPENDED / T1 | RECOVERING / T7 | ?（无恢复 session） |
| PREPARING | SUSPENDED / T2 | RECOVERING / T8 | ? |
| RUNNING | SUSPENDED / T3 | RECOVERING / T9 | ? |
| WAITING_APPROVAL | SUSPENDED+invalidate / T4 | RECOVERING+invalidate / T10 | ? |
| EVALUATING | SUSPENDED / T5 | RECOVERING / T11 | ? |
| RECOVERING | SUSPENDED+原 session / T6 | RECOVERING+原业务来源 / T13 | PREPARING/RUNNING/EVALUATING / T14–16 |
| SUSPENDED | ?（重复暂停非本文契约） | RECOVERING / T12 | ? |
| SUCCEEDED | 无出边，信号只结束 finalization | rejected / I1 | 无出边 |
| FAILED | 无出边，信号只结束 finalization | rejected / I2 | 无出边 |
| CANCELLED | 无出边，信号只结束 finalization | rejected / I3 | 无出边 |

`?` 是不在所规定调用前提内的事件，不补造异常文本/错误码，不计入覆盖分母。

| Transition | From | Event [guard] | To / action |
| --- | --- | --- | --- |
| T1、T2、T3、T4、T5、T6 | 按上表六个活动状态逐一 | pause [提交预算足够] | SUSPENDED；T4 同事务失效 pending；T6 保留最初业务来源 |
| T7、T8、T9、T10、T11、T12、T13 | 上表七个可恢复状态逐一 | resume [完整预检] | RECOVERING；Started/attempt/Snapshot 原子提交；T10 同事务失效 pending；T13 不把 RECOVERING 当业务来源 |
| T14 | RECOVERING | complete [原 CREATED/PREPARING] | PREPARING，原 phase |
| T15 | RECOVERING | complete [原 RUNNING/WAITING_APPROVAL] | RUNNING，原 phase、无旧 pending |
| T16 | RECOVERING | complete [原 EVALUATING] | EVALUATING，原 phase |
| I1、I2、I3 | 三终态逐一 | resume | RUN_NOT_RESUMABLE，State 不变 |

| Path | Start / event sequence | Transitions | End / checkpoints |
| --- | --- | --- | --- |
| U3-P1 | CREATED → pause → resume → complete | T1、T12、T14 | 每步读库；PREPARING 同现场 |
| U3-P2 | PREPARING → pause → resume → complete | T2、T12、T14 | 同准备 phase |
| U3-P3 | RUNNING → pause → resume → complete | T3、T12、T15 | 同 Step/phase |
| U3-P4 | WAITING_APPROVAL → pause → resume → complete | T4、T12、T15 | pending 失效，待当前 Policy |
| U3-P5 | EVALUATING → pause → resume → complete | T5、T12、T16 | 同 patch/评估恢复点 |
| U3-P6 | 原 RUNNING → resume → pause → resume → complete | T9、T6、T12、T15 | RECOVERING 被暂停也保留最初 RUNNING 来源 |
| U3-P7 | CREATED / PREPARING / RUNNING / WAITING_APPROVAL / EVALUATING 各合法前缀 → resume → complete | T7～11、T14～16 | 五条独立路径，不合并一次运行 |
| U3-P8 | RECOVERING 遗留 → resume → complete | T13、T15 | 原业务来源不漂移 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U3-01 | 创建后暂停 | U3-P1 | G0 后首信号；按 P1 恢复 | 状态逐步符合路径；无 finished_at/termination_reason；同 UUID/Workspace | R01、13；A§4 |
| TC-U3-02 | 准备中暂停 | U3-P2 | 完整描述的 PREPARING | 同准备恢复点；不重复 create | R13；A§4 |
| TC-U3-03 | 运行中暂停 | U3-P3 | 安全工具边界 | 同 Step/cursor，未开始新工作 | R13；A§4 |
| TC-U3-04 | 审批中暂停 | U3-P4 | 唯一 pending | SUSPENDED 同事务 pending 清空、resolution INVALIDATED，历史 Call 保留 | R10、13；A§10 |
| TC-U3-05 | 评估中暂停 | U3-P5 | 可确认收尾的评估 attempt | 同 patch/phase；后续行为由 U16；不换 evaluator | R13、30；A§12 |
| TC-U3-06 | attach 前再暂停 | U3-P6 | Started 确认、attach 未完成时信号 | SUSPENDED 保留未完成 session；下次完整预检/attach，目标 RUNNING | R12；A§4.3 |
| TC-U3-07 | 五种业务来源直接恢复 | U3-P7 | 五个独立 variant，均有合法原 phase | CREATED 对应 PREPARING；其余按 T14～16；每次 Started/Completed 一对，无 RunStarted | R12；A§4、7 |
| TC-U3-08 | 恢复中硬杀再恢复 | U3-P8 | RECOVERING 原来源 RUNNING，原进程 kill | 新 session source 更新，业务来源不变，无重复工具 | R12；A§4.3 |
| TC-U3-09 | 不重开成功 | I1 | SUCCEEDED；调用 resume | RUN_NOT_RESUMABLE/1；State/Event 不变；NO-WORK | R33；D10 |
| TC-U3-10 | 不重开失败 | I2 | FAILED；调用 resume | 同 09，失败原因不改 | R33；D10 |
| TC-U3-11 | 不重开取消 | I3 | CANCELLED；调用 resume | 同 09，取消原因不改 | R33；D10 |

### U4：恢复编排的异常出口

技术：用例测试。Actor 为 CLI 用户；trigger 为 resume。主路径：①取锁/读记录 → ②只读预检/纯 plan → ③Started 提交 → ④attach/live checks → ⑤冻结装配/授权失效 → ⑥Completed 提交 → ⑦dispatch/finalization。成功后条件是原身份继续且只执行未完成业务；失败保证按分支时点区分。

P15～17是该用例依赖的共享store子用例，actor为公开RuntimeStore调用者，流程为准备合法原记录→提交完整组合→观察后段故障或成功→重开核对；不要求用CLI重复覆盖相同事务机制。

| Path | Branch | Condition | Ends | Postcondition |
| --- | --- | --- | --- | --- |
| U4-P1 | 主路径 | 全部通过 | ⑦ | Started/Completed/Snapshot 各自一致，RunStarted 不重复 |
| U4-P2 | ② | 明确预检拒绝 | 拒绝 | 业务 DB 不变、现场保留、NO-WORK |
| U4-P3 | ③前 | Started 未提交即失败 | 停止 | 原状态；无 attach 变更 |
| U4-P4 | ③后 | Started 已写无确认后硬杀 | 硬中断 | 新进程读到 RECOVERING；只证明重入，不证明旧进程错误处理 |
| U4-P5 | ④ | 原 exited 容器启动后 Git live check 失败 | ResumeFailed | RECOVERING/原来源保留，不业务 FAILED，不替换容器 |
| U4-P6 | ⑤ | 缺 provider 凭据 | ResumeFailed | 无模型请求，原现场和恢复点保留 |
| U4-P7 | ⑥前 | Completed 提交失败 | 停止 | 最后确认 RECOVERING，不 dispatch |
| U4-P8 | ⑥后 | Completed 已写无确认后硬杀 | 硬中断 | 新进程按实际最新记录恢复；与存活错误出口分开 |
| U4-P9 | P5 记录错误时 | ResumeFailed 本身写不成 | 停止 | 只报告最后确认 sequence，保留现场，不尝试业务补救 |
| U4-P10 | ⑦入口 | 传入 record 陈旧/未完成恢复 | 拒绝继续 | latest sequence/恢复状态不符不得业务执行 |
| U4-P11 | ③后 | Started已应用，G3-E不确定错误返回存活调用者 | 自行错误退出 | 无attach/start/清理/诊断；实际库为RECOVERING |
| U4-P12 | ⑥后 | Completed已应用，G3-E不确定错误返回存活调用者 | 自行错误退出 | 无dispatch/新业务/诊断/销毁；实际库为Completed目标状态 |
| U4-P13 | ③前 | Started未下发，G3-U返回不确定错误 | 自行错误退出 | 原记录不变，不能以未发生为由继续attach |
| U4-P14 | ⑥前 | Completed未下发，G3-U返回不确定错误 | 自行错误退出 | 保留最后RECOVERING/原恢复点；不dispatch或补发 |
| U4-P15 | 共享store机制 | 审批/event写入后，Snapshot插入前触发真实DB故障 | 整体回滚 | 后段失败前缀命中；重开所有对象/版本为旧值 |
| U4-P16 | 共享store机制 | event/审批/Snapshot/Run State均可见后，提交前真实DB故障 | 整体回滚 | 全部新对象均已尝试写入；重开全部旧值 |
| U4-P17 | 共享store机制 | 与P15/P16同形合法提交，无故障触发器 | 成功 | 重开全部新值且完整绑定，作为注入夹具正控制 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U4-01 | 主路径使用最新记录 | U4-P1 | F 安全；真实 commit 后进入 Runtime.resume | 原 ID/task/context，Started→Completed→业务顺序；无 create/RunStarted | R01、12；A§7、10 |
| TC-U4-02 | 预检不写入 | U4-P2 | Local marker 明确错；其余合法 | WORKSPACE_IDENTITY_MISMATCH；全业务存储差分不变 | R15、34；A§7 |
| TC-U4-03 | Started 回滚 | U4-P3 | 真实 CAS 冲突阻止③ | 无 Started/attempt 增量/Snapshot 部分提交；无 start/export | R12；A§9 |
| TC-U4-04 | Started硬中断重入 | U4-P4 | G3-K：真Started commit后阻断返回并kill，再新进程resume | 新连接显示完整RECOVERING/attempt/Snapshot；新进程继承原业务来源，不重复工具；旧进程停机处理由TC-U4-14单独验证 | R12；A§7；F2 |
| TC-U4-05 | 启动后 live check 拒绝 | U4-P5 | 真 exited 原容器的 workdir/Git 不满足约束 | ResumeFailed，不 FAILED；保留原 container ID/source/phase | R16、34；A§7、8 |
| TC-U4-06 | 装配缺引用 | U4-P6 | 完整只读预检后实际装配缺 API key | ENVIRONMENT_REFERENCE_MISSING 原因可定位；无模型；RECOVERING 保留 | R25、34；A§7 |
| TC-U4-07 | Completed 回滚 | U4-P7 | 真 CAS 冲突于⑥ | 无 Completed/Snapshot 部分发布；无 dispatch/诊断/销毁 | R12；A§9 |
| TC-U4-08 | Completed硬中断重入 | U4-P8 | G3-K：真Completed commit成功后阻断返回，kill后新进程resume | 读到已确认恢复状态，新进程不重放已完成工具；不以原进程被kill证明其错误处理，另见TC-U4-15 | R12；A§7；F2 |
| TC-U4-09 | 失败记录再失败 | U4-P9 | 先 live check 失败，后真实存储不可写 | 输出持久化错误/最后 sequence；不启动诊断、不销毁原资源；不伪造 FAILED | R34；A§9 |
| TC-U4-10 | 陈旧 Runtime 入口 | U4-P10 | latest sequence 已前进；另独立变体 record 仍 RECOVERING | 两变体均不推进业务，保留 DB；不把传入对象当执行授权 | R12；A§10 |
| TC-U4-11 | 审批写后事务回滚 | U4-P15；DB-A | 使用§4.2的合法event/审批/Snapshot/State组合，条件触发器在Snapshot写入前验证新event及审批版本已可见，再发专用DB错误；真实公开store.commit | 必须收到前缀命中故障；重开event/审批/State/Snapshot/所有版本均旧值，无半写。相同早期重复event ID不算替代；同形正控制TC-U4-13 | A§9；TA-04/18；F1 |
| TC-U4-12 | Run State写后整体回滚 | U4-P16；DB-B | 同形合法提交；AFTER UPDATE Run条件触发器确认event/审批/Snapshot/State及sequence/version新值均可见，再RAISE(FAIL) | 故障必须在Run更新后命中，事务整体回滚，重开全部旧值；只回滚最后SQL而遗留前段任何新值则失败；无内部mock | A§9；TA-04/18/24；F1 |
| TC-U4-13 | 同形事务成功控制 | U4-P17；DB-C | 全新隔离库，使用与11/12同形变更、不装故障触发器；真实commit成功再重开 | 精确新State/event/审批version/Snapshot/source/sequence配套可读；不能因夹具本来非法而把失败当回滚证据 | A§9；F1 |
| TC-U4-14 | Started已写但存活调用者停机 | U4-P11；G3-E | Started真commit已应用后，向存活CLI抛一次持久化不确定控制错误；不先kill/cancel | CLI自行exit1；无attach/start/清理/dispatch/诊断；资源保留。监督者重开见完整Started/RECOVERING；CLI报告其提交前最后已确认sequence，不把外部已写结果当继续授权 | R12、34；A§7、9；F2 |
| TC-U4-15 | Completed已写但存活调用者停机 | U4-P12；G3-E | Completed真commit已应用后，同样向存活调用者抛错误 | 自行exit1；无新provider/业务工具/export/harness/销毁；重开见完整Completed目标status/phase/Snapshot；资源保留，不自动retry | R12、34；A§7、9；F2 |
| TC-U4-16 | Started未写仍保守停机 | U4-P13；G3-U | 一次性不下发Started提交，却向CLI返回相同不确定错误；CLI不知注入分支 | 自行exit1；库/恢复次数保持旧值；无attach/start/诊断/补发；现场保留；此用例不计SQLite回滚证据 | R12、34；A§7、9；F2 |
| TC-U4-17 | Completed未写仍保守停机 | U4-P14；G3-U | Started/attach/装配已确认，仅不下发Completed提交并返回不确定错误 | 自行exit1；最后RECOVERING/原来源/phase保留；自错误返回起零dispatch/诊断/销毁/补发；不撤销已合法发生的attach | R12、34；A§7、9；F2 |

Test level(s): F1在共享SQLite组件的真实事务边界观察最便宜，AC业务字段组合另用实际Journal/Runtime路径；F2在Resume application用例到真实store的边界观察，必须让调用者自行处理错误，不能只测store抛异常。F1/F2修正不增加内部实现mock。

### U5：State 和历史一致性

技术：等价分区。合法 F 为 U5-E0；U5-E1～E12 分别是下表单一错误，相同错误的其他数值受同 invariant 约束。每条通过构造、decode、公开 transition 可达入口各验证适用情况；不要求构造本来无法到达的 transition 输入。

| Partition | Validity | Representative / equivalence rule |
| --- | --- | --- |
| U5-E0 | 有效 | 合法 phase/cursor/batch/current_step/operation/history 配套 |
| U5-E1 | 无效 | RUNNING/request_model 的 cursor=EXECUTE_TOOL_CALLS；投影不同步 |
| U5-E2 | 无效 | 非 RUNNING 却保留非空 cursor；投影必须空 |
| U5-E3 | 无效 | execute_tools 无 batch；phase 缺必要数据 |
| U5-E4 | 无效 | complete_step 的 next_index<len；批未完成 |
| U5-E5 | 无效 | WAITING_APPROVAL 无 pending；该状态必须恰一个 |
| U5-E6 | 无效 | SUSPENDED 有 pending；其他状态必须没有 |
| U5-E7 | 无效 | tool operation 指向 next_index 之外调用 |
| U5-E8 | 无效 | candidate disposition 无原文；不得从 assistant 文本补造 |
| U5-E9 | 无效 | terminate disposition 无终止决定 |
| U5-E10 | 无效 | Step 已完成却有 current_step |
| U5-E11 | 无效 | 终态仍有 phase 或 active operation；两者独立变体 |
| U5-E12 | 无效 | SUSPENDED/RECOVERING 无合法原业务来源 |
| U5-E13 | 无效 | 已提交 [0,next_index) 结果缺失/重复/错 scope；这些是独立输入变体 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U5-01 | 合法状态与较旧 Snapshot | U5-E0 | F 最新 State 已提交 c0，Snapshot 尚在 c0 前 | 接受最新 State，只执行 c1/c2；较旧 Snapshot 不回退进度 | R04；A§4.1 |
| TC-U5-02 | 游标投影冲突 | U5-E1 | 唯一改 cursor | 拒绝验证；Resume 报 RESUME_STATE_INCONSISTENT，NO-WORK | R33；A§4.4 |
| TC-U5-03 | 非运行游标 | U5-E2 | 唯一改 cursor 非空 | 同 02 | R33；A§4.4 |
| TC-U5-04 | 缺 batch | U5-E3 | 唯一删 batch | 同 02，不重请求模型补批 | R33；A§4.4 |
| TC-U5-05 | 批未完却收 Step | U5-E4 | len=3、next_index=2、phase=complete_step | 同 02，不补造 StepCompleted | R07、33；A§4.4 |
| TC-U5-06 | 审批状态无 pending | U5-E5 | 删除唯一 pending | 同 02 | R10、33；A§4.4 |
| TC-U5-07 | 暂停残留 pending | U5-E6 | SUSPENDED 携 pending | 同 02，不把非法状态当正常旧批准 | R10、33；A§4.4 |
| TC-U5-08 | 活动 Call 错位 | U5-E7 | next_index=1，operation 指 c2 | 同 02，无真实工具执行 | R33；A§4.4 |
| TC-U5-09 | 候选无原文 | U5-E8 | assistant 非空但 candidate 原文缺失 | 拒绝，不凭文本进入评估 | R08、33；A§4.4 |
| TC-U5-10 | 终止决定缺失 | U5-E9 | terminate phase 无决定 | 拒绝，不猜成功/失败 | R09、33；A§4.4 |
| TC-U5-11 | current_step 不清理 | U5-E10 | 原 StepCompleted 已提交却残留 current_step | 拒绝不一致，无第二次 StepCompleted | R07、33；A§4.4 |
| TC-U5-12 | 终态有执行恢复点 | U5-E11 | phase 非空、operation 非空两个独立变体 | 模型/codec 拒绝非法终态；不使用其恢复点 | R33；A§4.4 |
| TC-U5-13 | 来源丢失 | U5-E12 | 分别缺失或来源=RECOVERING 无原业务来源 | 拒绝不一致，不重新 create | R12、33；A§4.3 |
| TC-U5-14 | 批结果关联不完整 | U5-E13 | 结果缺 c0、重复 c0、错 Run、错 Step、错 call_index、错 tool_call_id 各独立变体 | 严格查询/校验拒绝；不解析渲染 ToolMessage 猜结果，不收 Step | R07、33；A§4.2 |

### U6：已开始调用的结果判定

技术：决策表。条件：S=业务开始事实已提交，R=可信最终结果及收尾事实已提交，K=工具 unknown_outcome，E=旧执行已证明结束。规则空间 2×2×2×2=16；R=Y/S=N 不可达，R=Y/E=N 与“可信完成”矛盾。可达规则折叠如下，`-` 只折叠相同行为的无关条件。

| Type | Condition / action | C1 | C2 | C3 | C4 | C5 |
| --- | --- | --- | --- | --- | --- | --- |
| 条件 | S | N | Y | Y | Y | Y |
| 条件 | R | N | Y | N | N | N |
| 条件 | K | - | - | reject | retry_read_only | retry_read_only |
| 条件 | E | - | Y | - | Y | N/unknown |
| 动作 | 行为 | 当前 Policy 后执行原 Call | 跳过已完成调用 | UNCERTAIN_TOOL_EXECUTION | 同 Call 新 attempt | WORKSPACE_BUSY |

Findings：无未指定可达列；未知第三方默认 reject；shell 不按文本/exit code 改分类。C3 折叠 E 因为即使进程已停仍不能证明 shell 结果；保留 C4/C5 因并发安全不同。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U6-01 | 尚未执行 Call | C1 | batch 已提交，c0 无开始；恢复后 Policy ALLOW | 不请求模型，按原 Call 执行；先提交开始再进工具 | R03；D§9 |
| TC-U6-02 | 已提交 SUCCESS | C2 | c0 SUCCESS 完成且改文件；G3 kill | 修改/ToolResult/消息保留，c0 不重跑，继续 c1 | R04；D§9 |
| TC-U6-03 | 已提交 ERROR | C2 | c0 ERROR 可信结果与组结束；文件已修改 | 修改保留且不重跑；失败纳入整批计数 | R24；D§9 |
| TC-U6-04 | 已提交 TIMEOUT | C2 | c0 TIMEOUT 已确认组收尾/结果 | 同 03；TIMEOUT 不意味着回滚 | R24；D§9 |
| TC-U6-05 | 开始后执行前 kill | C3 | G1 ToolExecutionStarted 后、shell 未放行；原容器 exited | UNCERTAIN_TOOL_EXECUTION，NO-WORK，尤其不 start 容器 | R05；D§9 |
| TC-U6-06 | 副作用后结果前 kill | C3 | G2 c0 已追加一次，未完成提交 | 原修改保留；拒绝，不再追加/跳过/请求模型 | R05；D§9 |
| TC-U6-07 | 看似只读 shell/未知插件 | C3 | shell=`cat file`；默认第三方工具各独立未确认输入 | 都拒绝；不凭命令文本或再次批准放行 | R05；A§8.2 |
| TC-U6-08 | 只读旧执行已结束 | C4 | read_file 未提交结果；结束证据确定；恢复 | 同 Call 新 attempt，唯一最终结果；tool_calls 随真实新开始+1 | R06；D§9 |
| TC-U6-09 | 只读旧执行活动 | C5 | read_file 旧进程仍在 | WORKSPACE_BUSY，无新读取/模型 | R19；D§9 |
| TC-U6-10 | 只读身份未落库 | C5 | read_file 开始后、身份前 kill，无法证明收尾 | WORKSPACE_BUSY；缺 PID 不当未执行 | R06、19；A§8.2 |
| TC-U6-11 | 未知 shell 优先于新批准 | C3 robustness | 同时有未确认 shell、当前 Policy ALLOW/新人工批准、exited 容器 | 仍 UNCERTAIN_TOOL_EXECUTION；批准/启动不绕过安全判定 | R05、10；A§7.1 |

### U7：共用 phase dispatch

技术：状态转换。起点 prepare；终态 phase=None。持久 phase 才是状态，模型请求/工具代码执行是事件，不将进程内函数位置当另一游标。current_step、batch、disposition 为 guard。

| State / Event | prepare 完成 | 接受模型处置 | 完成工具 | 收 Step | 评估结果已确认 / 终止 |
| --- | --- | --- | --- | --- | --- |
| prepare | request_model / T1 | ? | ? | ? | ? |
| request_model | ? | execute_tools/tools T2；evaluate_candidate/candidate T3；terminate/失败 T4 | ? | ? | ? |
| execute_tools | ? | ? | execute_tools/非末 T5；complete_step/末 T6 | ? | ? |
| complete_step | ? | ? | ? | request_model/可继续 T7；terminate/失败上限 T8 | ? |
| evaluate_candidate | ? | ? | ? | 候选 Step 只完成一次 | terminate/完整结果 T9 |
| terminate | ? | ? | ? | ? | None/终态 T10 |
| None/终态 | 无出边 | 无出边 | 无出边 | 无出边 | 无出边 |

其余 `?` 事件不是该 phase 的调度动作；非法组合由 U5 拒绝，不补造这些事件的调用错误。有效转换清单就是 T1～T10（起点/事件/终点见上表）。

| Path | Start / events | Transitions | Checkpoints |
| --- | --- | --- | --- |
| U7-P1 | prepare → tools(B3) → 各 Call 完成 → 收 Step → 新请求 | T1、T2、T5、T6、T7 | G0、接受 batch 后、每个结果后、Step 完成前均可重入 |
| U7-P2 | request_model → 明确失败处置 → terminate | T4、T10 | 失败处置已提交、RunFinished 前 |
| U7-P3 | request_model → candidate → 完整评估结果 → terminate | T3、T9、T10 | final response 后/EVALUATING 前、G7 |
| U7-P4 | complete_step → failure limit 决定 → terminate | T8、T10 | 已决定终止后 kill，不丢失败 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U7-01 | 已接受 batch 直接执行 | U7-P1/T1、T2 | G0 开始；batch 原子提交后 kill | 原 batch 从 c0 执行，接受响应只累计一次，不新请求模型取工具 | R01、03；A§5 |
| TC-U7-02 | 批中间重入 | U7-P1/T5 | c0 完成后 kill，next_index=1 | c0 日志一次，c1/c2 顺序各一次；响应消息不重复 | R04；A§5 |
| TC-U7-03 | 最后结果后收 Step | U7-P1/T6、T7 | c0 ERROR，c1/c2 SUCCESS；next_index=3、Step 未收 | StepCompleted 一次且 failed；原失败计数+1，不只看后半成功；清 batch/current_step | R07；A§5 |
| TC-U7-04 | 全批成功 | U7-P1/T7 | 三项 SUCCESS、原 consecutive_failed_steps>0 | Step 成功一次、连续失败归零，再按原 max_steps 决定下一 Step | R07；D§11 |
| TC-U7-05 | 已提交失败响应 | U7-P2 | 非空 assistant 但明确无效/失败处置已提交 | 原 FAILED/终止原因；无成功候选/新模型/评估 | R09；A§5 |
| TC-U7-06 | 候选接受后重入 | U7-P3 | candidate 原文已提交，begin_evaluation 未提交 | 原 candidate 进入评估，候选 Step 完成一次；不请求模型 | R08；A§5 |
| TC-U7-07 | 结果后只终止 | U7-P3/T9、T10 | G7 完整通过结果，终态前 kill | 复用完整结果/refs，EvaluationCompleted/RunFinished 一次，无 evaluator 重跑 | R32；A§5 |
| TC-U7-08 | 确定失败决定持久化 | U7-P4/T8 | Step failure limit 决定已提交；模型异常/Policy 错误为独立补充 variant | 恢复原终止决定，不重新模型或把错误转候选 | R09；A§5 |

### U8：计数边界

技术：3-value BVA。整数步长=1。`next_index` 域是可提交整数，n=3；steps_started 域为非负整数、M=3；summary retry 使用配置 `compaction_max_retries=2` 的合法测试 Run，已耗 retry 域为 0..2。大于 M 的 Step 计数只在 eligibility guard 的合成健壮性输入中使用，不宣称能从冻结 M=3 的正常历史到达。恢复次数 3 不是产品边界，另由 U21 连续旅程证明。

| Condition | Partition | Validity / outcome | Min | Max | Step | Source |
| --- | --- | --- | --- | --- | --- | --- |
| next_index | P1 | 无效 | -∞ | -1 | 1 | A§4.4 的 [0,n] 约束 |
| next_index | P2 | 执行剩余 Call | 0 | 2 | 1 | D§9 |
| next_index | P3 | 收原 Step | 3 | 3 | 1 | D§9 |
| next_index | P4 | 无效 | 4 | +∞ | 1 | A§4.4 |
| steps_started | P5 | 允许开始下一 Step | 0 | 2 | 1 | D§11 |
| steps_started | P6 | 不允许开始新 Step | 3 | +∞ | 1 | D§11；当前 Step 可继续 |
| retry_used | P7 | 尚有 retry 余额 | 0 | 1 | 1 | A§5、13；测试配置 K=2 |
| retry_used | P8 | 已用尽，不刷新 | 2 | 2 | 1 | A§5、13 |

| Boundary / coverage items | 3-value set（去重后） | Expected |
| --- | --- | --- |
| U8-C：-1/0/2/3/4 的有效与无效 partition 边界 | -2、-1、0、1、2、3、4、5 | 负值/大于3拒绝；0/1/2执行原下标；3收 Step |
| U8-S：P5 min=0/max=2、P6 min=3 | 0、1、2、3、4 | 0/1/2允许新 Step；3/4禁止新 Step；负计数非合法计数域，已从邻居集移除 |
| U8-R：P7 min=0/max=1、P8=2 | 0、1、2 | 固定后续响应检验必需请求数与结果；不只设上界；-1/3 不在已耗额度合法域，移除 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U8-01 | 游标 -2 | U8-C=-2 | B3 唯一改 next_index=-2，decode/校验 | 拒绝无效 State，无业务 | R33；A§4.4 |
| TC-U8-02 | 游标 -1 | U8-C=-1 | 同上改 -1 | 同 01 | R33；A§4.4 |
| TC-U8-03 | 游标 0 | U8-C=0 | 合法 batch，恢复 | c0/c1/c2 各一次，不请求模型重取 batch | R03；D§9 |
| TC-U8-04 | 游标 1 | U8-C=1 | c0 已确认，恢复 | 只 c1/c2，c0 消息不重复 | R04；D§9 |
| TC-U8-05 | 游标 2 | U8-C=2 | c0/c1 已确认，恢复 | 只 c2，然后 complete_step | R04、07；D§9 |
| TC-U8-06 | 游标 3 | U8-C=3 | 三结果已确认，Step 未完成 | 零新工具，StepCompleted 一次 | R07；D§9 |
| TC-U8-07 | 游标 4 | U8-C=4 | 唯一改 next_index=4 | 拒绝无效 State，无业务 | R33；A§4.4 |
| TC-U8-08 | 游标 5 | U8-C=5 | 唯一改 next_index=5 | 同 07 | R33；A§4.4 |
| TC-U8-09 | 尚无 Step | U8-S=0 | M=3，request_model 无 current_step | 新 Step 编号1，计数1 | R20；A§5 |
| TC-U8-10 | 已用一 Step | U8-S=1 | M=3，一 Step 已完成 | 新 Step 编号2，计数2 | R20；A§5 |
| TC-U8-11 | 额度前一位 | U8-S=2 | M=3，两 Step 已完成 | 新 Step 编号3，计数3 | R20；A§5 |
| TC-U8-12 | 达到额度 | U8-S=3 | 两独立分支：无 current_step；已有 Step3 的剩余 batch | 前者不新请求/Step；后者完成原 batch/Step，随后仍不新 Step | R20；D§11 |
| TC-U8-13 | guard 超额度健壮性 | U8-S=4 | 仅向新 Step 资格判定给 s=4/M=3，不建伪造 Run 历史 | 不允许新 Step；不以此证明该历史合法 | R20；D§11 |
| TC-U8-14 | 两次余额均须使用 | U8-R=0；F4 | ASM-7：K=2、used=0，初次summary的retryable失败已确认。恢复后的provider固定顺序：retryable summary错误→合法summary S→业务candidate；S通过source/容量校验 | 恰2笔新summary请求和1笔业务请求；retry_used最终2；S发布一次、context/version前进一次；原Step不增加；零次或仅一次summary后提前失败、第三次summary均失败 | R21、26；A§5、13；F4 |
| TC-U8-15 | 最后一次余额必须重试 | U8-R=1；F4 | ASM-7：K=2、used=1，最近summary retryable失败已确认；恢复后的固定顺序：合法summary S→业务candidate | 恰1笔新summary和1笔业务请求；used最终2；S发布一次并继续原Step；零summary/提前终止或多一笔summary均失败；不刷新K | R21、26；A§5、13；F4 |
| TC-U8-16 | 用尽额度零额外请求 | U8-R=2；F4 | ASM-7：K=2、used=2，最后retryable错误已确认但失败决定尚待落库；provider设置收到任意请求即记录失败 | 恰0笔新summary、0笔业务请求，无新context版本；原Step不重开；FAILED/MODEL_FAILED/COMPACTION_FAILED，沿强制压缩原失败终止；不刷新额度 | R21、26；A§5、13；F4 |

这里的请求增量从本次resume的第一笔provider调用起计算，原已确认请求/Usage保留，不将summary retry误当未确认请求重发；provider还要校验请求角色和消息来源，不能只靠总数掩盖分支错误。`S`是预先固定、source引用/容量均合法的summary，原业务调用仍在同一current_step。若夹具无法进入ASM-7的合法分支，记录设置失败，不用另一可回退分支替代。

Test level(s): F4在Runtime/compaction与协议provider的确定性组件范围观察即可；真实SQLite负责跨进程保留进度，真实模型不能增加这些精确请求断言的信心。

### U9：审批状态与当前 Policy

技术：状态转换；对失效后的 Policy 增加独立决策表。状态 P=pending，A=APPROVED 未执行，C=已有执行开始的消费历史，I=旧授权已失效，N=新 pending。起点是原业务 Call 经 Policy 进入 P；A/C 通过真实审批/执行前缀获得。

| State / Event | pause/恢复失效事实确认 | 当前 Policy APPROVAL | 当前 Policy ALLOW/DENY |
| --- | --- | --- | --- |
| P | I / T1 | ?（先处理旧请求） | ? |
| A | I / T2 | ? | ? |
| C | C / T3，历史不改 | 不重评已完成调用 | 不重执行已完成调用 |
| I | I / T4，幂等继续 | N / T5 | 不建 pending / T6(ALLOW)、T7(DENY) |
| N | I / T8，下一次中断再失效 | ? | ? |

T1 必须跟 pause/Started 同事务，T2 在 Completed 前按审批版本继续提交、保留人工 Resolution。N 是当前 Policy 新请求，不复用旧 request ID。`?` 是不满足恢复流程前置事件，非补造 API 契约。

| Path | Sequence | Transitions | Checkpoints |
| --- | --- | --- | --- |
| U9-P1 | P→I→N→再次暂停 I | T1、T5、T8 | 每次 pending 与 status invariant；新旧 request ID |
| U9-P2 | A→I→ALLOW | T2、T6 | 原人工 Resolution 不变；只当前授权可执行 |
| U9-P3 | C→C | T3 | 已完成结果/人类审批历史保留 |
| U9-P4 | 多个未消费授权部分失效→kill→I→DENY | T4、T7 | 持久化版本续接，禁止失效遗漏或覆盖历史 |

Policy 表条件：调用未知副作用 U、Policy 三值 P；空间2×3=6，U=Y 三列结果相同折叠，当前工具未执行且可安全重试为 U=N。

| Type | Condition / action | C1 | C2 | C3 | C4 |
| --- | --- | --- | --- | --- | --- |
| 条件 | U | Y | N | N | N |
| 条件 | P | - | DENY | APPROVAL | ALLOW |
| 动作 | 结果 | 先 UNCERTAIN_TOOL_EXECUTION | 正常 DENIED Result | 新请求/等待，不能自动同意 | 执行原 Call |

Findings：无可达列冲突。无交互 handler 的精确既有终止分类沿用现有契约，只断言“不自动同意/不执行”，不编造新错误码。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U9-01 | pending 失效再申请 | U9-P1 | WAITING_APPROVAL 暂停/恢复；当前仍 APPROVAL；新请求时再暂停 | 旧 INVALIDATED 与 pending 清空原子；新 request 关联同 Call/Workspace/current revision；再次暂停失效新 pending | R10；A§10 |
| TC-U9-02 | 批准未执行重新授权 | U9-P2、C4 | APPROVED 无 ToolExecutionStarted；当前 ALLOW | authorization invalidated，人工 Resolution 不改；当前 Policy 后才执行 | R10；A§10 |
| TC-U9-03 | 已执行历史保持 | U9-P3 | c0 执行完成，active_operation 已清 | c0 旧批准不被当未消费；无新批准/c0 执行 | R10；A§10 |
| TC-U9-04 | 部分失效可重入 | U9-P4 | 多个未消费 APPROVED，提交首个失效后 kill；当前 DENY | 其余按持久化版本继续失效；只一个当前 DENIED Result；无业务 | R10、12；A§10 |
| TC-U9-05 | DENY 当前调用 | C2 | 无未知工具，当前 DENY | 正常 DENIED Result，推进原 Call；不继承旧 ALLOW/批准 | R10；D§11 |
| TC-U9-06 | APPROVAL 当前调用 | C3 | 已失效旧批准，当前 APPROVAL，handler 有 | 新 request，未新批准前零执行 | R10；D§11 |
| TC-U9-07 | 非交互不默认同意 | C3 | 同 06，唯一去掉 handler | 保持既有非交互拒绝/失败契约；零执行 | R10；A§10 |
| TC-U9-08 | 未知 shell 优先 | C1 robustness | 未确认 shell+当前 DENY/APPROVAL/ALLOW 三个独立 variant | 均先 UNCERTAIN_TOOL_EXECUTION；无新审批/工具 | R05、10；A§7 |

### U10：Local 受管理执行收尾

技术：决策表。I=完整出生/boot/PGID/SID/attempt 身份匹配，O=身份探测可读且无歧义，G=组仍活动。空间2³=8；O=N 时其他观察无可信意义，折叠为 C2；O=Y/I=N 时不以活动性证明旧组结束，折叠为 C3。

| Type | Condition / action | C1 | C2 | C3 | C4 |
| --- | --- | --- | --- | --- | --- |
| 条件 | O | Y | N | Y | Y |
| 条件 | I | Y | - | N | Y |
| 条件 | G | N | - | - | Y |
| 动作 | 收尾事实 | 可确认 | unknown/busy | unknown/busy；不误杀 PID | busy；不提交可信工具完成 |

Findings：这里“可确认”不改变 U6 的 shell 未确认结果拒绝；新进程不凭“组没了”补 ToolResult。仅本进程创建且身份验证的执行可按预算回收。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U10-01 | 真组完整退出 | C1 | 真 Local 命令/子组结束；观察后正常完成 read_file/工具 | 收尾与真实结果一起提交，无活动组；后继可执行 | R19、24；A§8.3 |
| TC-U10-02 | 读取身份失败 | C2 | 探测外部边界返回 AccessDenied；其余合法 | WORKSPACE_BUSY，不声称结束，不 kill 不可验证 PID | R19；A§8.3 |
| TC-U10-03 | 出生身份不匹配 | C3 | 一个真实邻居 PID，持久描述 create_time 为另一出生身份 | WORKSPACE_BUSY；邻居仍存活，无新工作；不按 PID 粗杀 | R19；A§8.3 |
| TC-U10-04 | 主进程退出子组仍活 | C4 | 真进程父退出、子仍同组持续写标记 | 不提交 ToolEnded；不并发下一命令；有界回收仅当前验证组，或恢复 busy | R19；A§8.3 |
| TC-U10-05 | observer 落库失败不包装 | C2/安全异常传播 | 分配真句柄后，身份提交触发真实 CAS/存储错误 | 有界回收本次已知组；不转普通 Tool ERROR 继续；不导出诊断；保留最后事实 | R19、34；A§8.2、9 |

### U11：Docker helper 与 exec 许可协议

技术：状态转换。模型状态是可观察的许可/执行阶段，不增加产品持久化枚举：H0=operation 开始已提交；H1=helper 等许可、身份尚未确认；H2=helper 身份/创建许可意图已提交；H3=exec ID/start 意图已提交；H4=start 已获许可、结果可能未确认；H5=已验证启动后结束且 helper/组均收尾；HX=未获许可而 EOF/超时退出。业务结果未提交时，shell 仍交 U6 拒绝。

本单元恢复 busy 的测试默认使用 candidate export 的受管理 observer scope，测试 Workspace 执行协议，而非未确认 shell；否则 shell 优先拒绝会遮蔽 helper 收尾规则。真实 candidate/Git 的组合后盾见 U15/U21。协议记录器的返回不当作真实 Engine 收尾证据。

| State / Event | 分配/持久化下一身份 | 发 create 许可 | 发 start 许可 | 父死亡/EOF 或门控超时 |
| --- | --- | --- | --- | --- |
| H0 | H1 / T1（只启动门控 helper） | 禁止 / I1 | 禁止 / I2 | 原 operation 遗留，不补发令 |
| H1 | H2 / T2（身份/意图确认） | 禁止 / I3 | 禁止 / I4 | HX / T7，不发 RPC |
| H2 | H3 / T4（已获创建许可后保存 exec/start 意图） | T3，才可 exec_create | 禁止 / I5 | 无新许可；确认 helper 是否收尾 |
| H3 | ? | 不再次 create | H4 / T5，才可 exec_start | 无新许可；确认是否曾获 start 许可 |
| H4 | H5 / T6（实际启动结束证据+组/helper 收尾） | 不重发 | 禁止重发 / I6 | 旧发令可能未排除则 busy |
| H5 | 允许按 operation 类型正常完成 | 不重发 | 不重发 | 已确认事实保留 |
| HX | 不补造 exec | 不自重试 | 不自重试 | 已确认无许可，仍按业务类型判定 |

有效转换清单 T1=H0→H1、T2=H1→H2、T3=H2 发创建许可（留 H2）、T4=H2→H3、T5=H3→H4、T6=H4→H5、T7=H1→HX。非法许可 I1～I6 如表；测试协议客户端不会真的从产品正常调用这些非法事件，oracle 是在对应窗口零 RPC/无重发，不凭空要求新错误码。

| Path | Sequence | Transitions | Checkpoints |
| --- | --- | --- | --- |
| U11-P1 | H0→H1→H2→create→H3→start→H4→H5 | T1～T6 | 每许可时间严格晚于对应 commit；exec/group 身份持久关联 |
| U11-P2 | H0→H1→父 kill→HX | T1、T7 | G4；EOF/超时不自行 RPC |
| U11-P3 | H3→start→H4→父 kill→恢复 | T5；未排除旧 start 不前进 | G5；Running=false/无组不代表安全 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U11-01 | 门控主路径 | U11-P1 | 真实 helper/管道，受控 Engine；按各提交握手放行 | create/start 各一次，许可 attempt 匹配；无 RPC 早于身份/意图确认；H5 三重收尾 | R36、37；A§8.4 |
| TC-U11-02 | 身份前 kill | U11-P2、I1～I4 | G4 真 helper 已起，身份/意图尚未确认即 kill 父 | EOF 退出，零 Engine RPC；无自许可/重试；shell 不因此变成功 | R37；A§8.4 |
| TC-U11-03 | 等许可超时 | T7、I3、I4 | helper H1，无父许可保持到门控 timeout | helper 有界退出，零 RPC，不重试 | R37；A§8.4 |
| TC-U11-04 | 身份提交不明 | T2 守卫、I3 | 身份 commit 已写但返回丢失，父停止/关闭门控 | 不发送 create 许可；不把“可能写成”当放行；已知 helper 有界回收 | R37；A§8.4 |
| TC-U11-05 | start 意图未确认 | T4 守卫、I5 | exec_create 已返回 ID，start 意图提交失败/不明各独立 variant | 不发 start；不产生业务副作用；按已知句柄有界收尾 | R37；A§8.4 |
| TC-U11-06 | 延迟 start 尚可能发生 | U11-P3 | G5，helper 活动、Engine 当前 Running=false；父 kill 后 resume | WORKSPACE_BUSY，零新执行链/容器启动；不重发 start | R36；A§8.4 |
| TC-U11-07 | helper 退出但 start 不明 | I6 | helper 已退出，已获 start 许可，Running=false、无已启动后结束证据 | 仍 WORKSPACE_BUSY，不能声称从未启动或已结束 | R36；A§8.4 |
| TC-U11-08 | supervisor 死子组仍活 | T6 守卫 | 真实 Docker supervisor kill，后台子仍同组；exec Running=false | 不确认收尾、不提交可信结果；实际恢复查原组，不能并发 | R19、36；A§8.4 |
| TC-U11-09 | exec 身份证据丢失 | T6 守卫 | 原 exec inspect 不存在，daemon 身份仍匹配 | 拒绝未知收尾，不用新 exec 替代；若 shell 未确认仍先 UNCERTAIN_TOOL_EXECUTION | R19、36；A§8.4 |
| TC-U11-10 | 暂停中回收 helper | T5→T6 或不确认 | 真已放行 RPC 受控阻塞，首信号暂停 | 停止新许可、关闭通道；有限 RPC 预算/有界回收，无 executor 无限等待；未三重确认不造结果 | R13、36；A§8.4 |

I1～I6 表示系统不得主动发出的许可动作；不是要求实现一个可接受乱序许可的用户 API。各自独立的短路径是：

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U11-11 | H0不发创建许可 | I1 | operation 开始确认后屏障阻止 helper 身份登记 | 零 create 许可、零 Engine RPC | R37；A§8.4 |
| TC-U11-12 | H0不发start许可 | I2 | 与11独立运行，在H0结束许可观察窗口 | 零 start 许可、零 exec_start | R37；A§8.4 |
| TC-U11-13 | H1不发创建许可 | I3 | 真helper已起但身份提交未确认，暂停/关闭通道 | 不发创建许可，helper退出，零RPC | R37；A§8.4 |
| TC-U11-14 | H1不发start许可 | I4 | 真helper身份尚未确认，独立run阻断该提交 | 不发start许可，零exec_start | R37；A§8.4 |
| TC-U11-15 | H2不提前start | I5 | 创建许可已获，exec ID/start意图提交前保持屏障 | 可有一次create；零start许可/exec_start | R37；A§8.4 |
| TC-U11-16 | H4不重复start | I6 | exec_start响应延迟，helper仍有未决RPC，原进程等待/暂停 | 单attempt最多一笔start；不重发/不切新执行链；不无限等待 | R36；A§8.4 |

### U12：retained Workspace 接回

技术：等价分区。每个 partition 是 attachment 一个独立事实的有效/拒绝类别；不把 Local 与 Docker 称为可互换数据。全部变体都须保留原现场，不调用 checkpoint/create/reset。

| Input | Partition | Validity | Rule / representative | Why equivalent |
| --- | --- | --- | --- | --- |
| Local | U12-L1 | 有效 | 原路径/marker/基线匹配，HEAD 未变 | 原目录接回 |
| Local | U12-L2 | 有效 | 工具合法 commit 改 HEAD，原 baseline 可用 | HEAD 不必等 baseline |
| Local | U12-L3 | 无效 | 原目录不存在 | 无现场可接回 |
| Local | U12-L4 | 无效 | marker 不存在 | 所有权证据缺失 |
| Local | U12-L5 | 无效 | marker Run/Workspace 不符 | 错身份，各独立变体 |
| Local | U12-L6 | 无效 | 规范路径超 root containment | 越界不可接回 |
| Local | U12-L7 | 无效 | 原目录或祖先被 symlink 替换 | 禁止重定向，不是路径字符串相等就通过 |
| Local | U12-L8 | 无效 | baseline commit 不可用于导出 | 原 patch 基线丢失 |
| Docker | U12-D1 | 有效 | running 原完整 ID | 原层接回 |
| Docker | U12-D2 | 有效 | exited 原完整 ID | Started 后才启动原容器 |
| Docker | U12-D3 | 无效 | 原 ID 被删、无同名替代 | 无现场 |
| Docker | U12-D4 | 无效 | 原 ID 被删但同名新 ID | 名称不证明身份 |
| Docker | U12-D5 | 无效 | managed/run/workspace label 之一错 | 归属不符，各独立变体 |
| Docker | U12-D6 | 无效 | 创建镜像内容 ID 不匹配 | 错现场身份 |
| Docker | U12-D7 | 无效 | workdir/安全/资源配置之一不匹配 | 冻结执行约束不符，各独立变体 |
| Docker | U12-D8 | 无效 | paused/restarting/removing/dead | 首版不自动修复，各状态独立 |
| Docker | U12-D9 | 无效 | daemon ID 不匹配 | 不能操作另一 daemon |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U12-01 | Local 原现场 | U12-L1 | staged/unstaged/未跟踪文件均有修改，安全边界恢复 | 相同 Run/Workspace/目录；原 index/bytes 保留；无复制/reset/checkpoint | R15；D7 |
| TC-U12-02 | HEAD 合法改变 | U12-L2 | 原工具已确认 commit，baseline 仍有效 | 允许 attach；patch 仍以原 baseline 导出 | R15；A§8.1 |
| TC-U12-03 | 原目录消失 | U12-L3 | 只删隔离测试原目录 | WORKSPACE_MISSING，NO-WORK；不按 source 重建 | R15；D§8.1 |
| TC-U12-04 | marker 消失 | U12-L4 | 唯一移走 marker | 拒绝缺所有权证据，NO-WORK；不补建 marker | R15；D§8.1 |
| TC-U12-05 | marker 错主 | U12-L5 | Run 错、Workspace 错各独立 | WORKSPACE_IDENTITY_MISMATCH；保留目录 | R15；D§8.1 |
| TC-U12-06 | 规范路径越界 | U12-L6 | 唯一路径改为 root 外目录 | WORKSPACE_IDENTITY_MISMATCH；不读取/写入目标业务内容 | R15；D§8.1 |
| TC-U12-07 | 链接替换 | U12-L7 | 原目录、任一祖先各独立替换为 symlink | 拒绝重定向；不因 resolve 后存在就接回 | R15；A§8.1 |
| TC-U12-08 | 基线丢失 | U12-L8 | 原 baseline 在真实仓库中不可用 | 拒绝，NO-WORK；不选当前 HEAD 代替基线 | R15；D§8.1 |
| TC-U12-09 | running 容器原层 | U12-D1 | 真原容器安全中断；workdir 外有安装标记 | 同完整 ID/层；后续工具可读取外部依赖；无 create/pull | R16、18；D7 |
| TC-U12-10 | exited 原容器 | U12-D2 | 真 stop 后安全恢复 | Started 确认后仅启动该 ID，再做 workdir/Git live checks；不重建 | R16；A§7–8 |
| TC-U12-11 | 容器被删 | U12-D3 | 原测试 ID 不存在 | WORKSPACE_MISSING；不自动 create/pull | R17；D§8.2 |
| TC-U12-12 | 同名替换 | U12-D4 | 另建同名新 ID，原 ID 不存在 | 拒绝；不 start/exec 替代容器，不降级 Local/evaluator | R17；D§8.2 |
| TC-U12-13 | labels 不符 | U12-D5 | managed/run/workspace 各改一项独立 variant | WORKSPACE_IDENTITY_MISMATCH，NO-WORK | R17；D§8.2 |
| TC-U12-14 | 镜像身份不符 | U12-D6 | 描述预期内容 ID 与真实 inspect 不同 | WORKSPACE_IDENTITY_MISMATCH，不拉 tag 修复 | R17；A§8.1 |
| TC-U12-15 | 执行配置不符 | U12-D7 | workdir/安全/资源限制逐项一变体 | 拒绝身份/冻结约束不符，NO-WORK | R17；D§8.2 |
| TC-U12-16 | 不支持容器状态 | U12-D8 | 四状态分别；瞬态用受控 inspect 分支，paused/dead 可用时真验证 | DOCKER_CONTAINER_STATE_UNSUPPORTED；不 unpause/restart/修复 | R17；A§8.1 |
| TC-U12-17 | daemon 身份变更 | U12-D9 | 固定连接 inspect 得另一 daemon ID | DOCKER_DAEMON_MISMATCH；不操作容器；不改 ambient context 重试 | R17；D§7.2 |

### U13：冻结装配定义与秘密引用

技术：等价分区。输入为当前装配事实相对于冻结定义的差异，输出为“原定义继续/兼容变化审计/拒绝”。每个指纹变体独立运行。

| Input | Partition | Validity | Representative / equivalence rule |
| --- | --- | --- | --- |
| 默认值/路径 | U13-E1 | 有效 | cwd、默认 model/config、定价文件变化；冻结绝对路径/原内容仍可用 |
| provider 身份 | U13-E2 | 条件有效 | endpoint 非秘密身份变，但同 provider/model/协议兼容，需审计 |
| 模型/协议 | U13-E3 | 无效 | 同名模型但请求协议不兼容 |
| 容量资料 | U13-E4 | 无效 | 当前资料缺失或不兼容，不能覆盖冻结值 |
| 工具契约 | U13-E5 | 无效 | definition/schema/execution options/unknown_outcome/supervision 版本之一指纹变化 |
| 环境引用 | U13-E6 | 无效 | 执行时 provider key 或工具 executable 引用不可解析 |
| evaluator 身份 | U13-E7 | 无效 | task/prepared record/manifest/tasks digest/harness/image 内容之一变化 |
| 评估镜像 tag | U13-E8 | 有效 | tag 已移动但原内容 ID 仍可用 |
| 评估镜像内容 | U13-E9 | 无效 | 原内容不可用，即使同 tag 新内容可拉 |
| Docker 连接 | U13-E10 | 有效 | ambient context 改变但冻结连接可用 |
| 秘密来源 | U13-E11 | 有效 | 当前凭据值变化，但引用及协议兼容；只持久化引用 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U13-01 | 默认漂移不改定义 | U13-E1 | 更换 cwd/默认定价/model/config，冻结路径保留；恢复业务请求 | 原 task/Agent/generation/容量/配置/绝对 artifact/data/work root；旧 cost 不重算，后续原 pricing | R25；D6、A§13 |
| TC-U13-02 | 兼容 endpoint 变更审计 | U13-E2 | 当前引用解析到兼容 endpoint，provider/model/协议不变 | 原请求语义保留，记录非敏感身份变化；不保存 key | R25；A§13 |
| TC-U13-03 | 模型协议不兼容 | U13-E3 | 唯一变更协议支持事实 | MODEL_CONFIG_MISMATCH；无模型请求 | R25；A§13 |
| TC-U13-04 | 容量资料缺失/不兼容 | U13-E4 | 缺失、不兼容各独立变体 | MODEL_CONFIG_MISMATCH；不取当前新默认补救 | R25；D§7.3 |
| TC-U13-05 | 工具指纹漂移 | U13-E5 | 五个字段族逐项独立改动 | TOOL_CONTRACT_MISMATCH；不执行未确认调用；Policy revision 变化不混进此指纹拒绝 | R25；A§8.2、13 |
| TC-U13-06 | 缺执行环境引用 | U13-E6 | provider key/工具 executable 各独立缺失 | ENVIRONMENT_REFERENCE_MISSING 的可定位原因；模型/业务工具零调用；不销毁现场 | R25；D§7.3 |
| TC-U13-07 | 评估定义不符 | U13-E7 | task/prepared record/manifest/tasks/harness/image digest 各一独立变体 | EVALUATION_CONTEXT_MISMATCH；不换 task/evaluator，不启动评估 | R27；D§12.2 |
| TC-U13-08 | tag 移动但原内容在 | U13-E8 | 原 image ID 可 inspect，tag 指新 ID | 仍使用原 ID；无 pull/替代 | R27；A§12.3 |
| TC-U13-09 | 原镜像内容不可用 | U13-E9 | 原 ID 不存在、同名 tag 新内容存在或可拉 | 明确拒绝，NO-WORK；不拉 tag 替代 | R27；A§4.1、12.3 |
| TC-U13-10 | 不使用 ambient daemon | U13-E10 | 冻结 Workspace=A、evaluator=B；ambient=C；受控记录完整 argv/env/RPC | 每个 inspect/start/exec/harness/cleanup 各走 A/B；不触及 C；真后盾 U21 | R25、27；D§7.2 |
| TC-U13-11 | 秘密只经当前引用 | U13-E11 | 首次与恢复 key 不同，恢复 key=秘密哨兵；TLS 私钥哨兵同理 | 当前外部边界获正确凭据；DB/Event/Snapshot/Artifact/CLI 无秘密值/完整敏感 argv/env | R25；D§7.2–3 |

### U14：请求、压缩发布与计量

技术：状态转换。起点 Q0=已提交 context、下一请求未开始；Q1=业务 attempt 已开始无响应；Q2=业务响应已接受；S1=summary attempt 无确认响应；S2=summary 响应/usage 已确认但未发布 context；S3=CompactionCompleted+Snapshot 已发布。Q2/S2 代表持久事实，不要求新增同名状态字段。

| State / Event | 请求开始 | 接受响应 | 发布 summary | 中断后恢复 |
| --- | --- | --- | --- | --- |
| Q0 | Q1 / T1，先计数 | ? | ? | 仍按原边界检查 |
| Q1 | 不重复原 attempt | Q2 / T2，usage/处置原子 | ? | Q1 新 attempt / T3，旧 unconfirmed 一次 |
| Q2 | 下一 phase，非重请求 | 不重复接受 | ? | T4，原响应/usage 保留 |
| S1 | 不重复原 attempt | S2 / T5 | 禁止未接受发布 | S1 新 attempt / T6，原 summary unconfirmed 一次 |
| S2 | 依据 request_progress | 不重复累计 | S3 / T7，原 source validation | T8：不使用未发布 summary，保留已知 usage，按原余额重算 |
| S3 | 回原请求分支 / T9 | 不重复 summary | 不重发布同版本 | T10：保留新 context/version/usage |

有效转换 T1～T10 如表。普通 Step 边界压缩与强制 context-limit 分支是不同 guard，不因重建 estimator 又做一次普通压缩。retry_guard 的数值邻界由 U8-R 单独覆盖，不在此表重列 BVA。

| Path | Sequence / transitions | Checkpoints |
| --- | --- | --- |
| U14-P1 | Q0→Q1→kill→T3→Q2→T4 | 原 StepStarted 一次、请求两 attempt、unknown 一次 |
| U14-P2 | S1→kill→T6→S2→T7→S3→T9 | summary 和业务 attempt 分开计数 |
| U14-P3 | S2→kill→T8→按原余额处理 | 未发布 summary 不进 Context；已有 usage 保留 |
| U14-P4 | S3→kill→T10→业务请求 | version/source chain 不回退、不重复压缩 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U14-01 | 请求开始前先落计数 | T1 | G1 请求开始已提交、provider 许可未发即 kill | 恢复保守记旧 unconfirmed=1；新 attempt 请求数再+1，同 Step，不宣称确实收费 | R02；A§13 |
| TC-U14-02 | 请求中断重发 | U14-P1/T1～4 | provider 已收到请求但无返回，kill 后恢复 | 原请求+新请求计数2；旧 unknown 只+1；同 StepStarted；新请求用原 context/generation | R02；A§5、13 |
| TC-U14-03 | 响应确认只加一次 | T2、T4 | 基数 ASM-5、原unknown=2；响应 input100/output20、cost0.00014，在 G3 kill | confirmed input140/output30、cost0.00020、unknown仍2；恢复不再累计该响应；unknown 不被 add_request_usage 重置 | R02、25；A§13 |
| TC-U14-04 | summary 未确认 | U14-P2/T5～7、T9 | summary 发出无返回 kill，再按原预算成功 | 旧 summary unknown 一次，新 attempt 独立；只原子发布的新 summary 进 Context，不新建业务 Step | R21；A§5、13 |
| TC-U14-05 | summary 响应有 Usage 未发布 | U14-P3/T8 | 可信 summary usage 已记录，CompactionCompleted 未提交；kill | 已知 usage 保留；旧 summary 文本不直接充当 published context；原 source/retry 余额重算 | R21；A§5 |
| TC-U14-06 | summary 已发布 | U14-P4/T10 | CompactionCompleted+Snapshot 原子确认后 kill | 精确 context/version/source chain/usage 保留；不重复压缩原区间 | R21；A§5 |
| TC-U14-07 | 强制分支完成原业务retry | T3、T6、T9；F4 | 原业务首次请求context-limit已确认；request_progress=强制压缩，原业务context-limit retry预算1、已耗0，重试请求尚未发出；summary额度尚余1。恢复后固定合法summary S→同Step业务retry成功candidate | 恰1笔summary、1笔原Step业务retry，业务retry_used最终1；source链/容量合法且context只发布一次；无一般Step边界压缩/新Step。若该唯一业务retry返回context-limit（独立变体），按CONTEXT_CAPACITY_EXCEEDED/MODEL_FAILED终止，不再summary/第三笔业务请求；不刷新该Step的原预算 | R02、21；A§5、13；F4 |
| TC-U14-08 | 无 Usage 不显示零 | T4、T8 | 业务失败响应/summary 失败响应无 Usage 各独立变体 | 输出/累计保留 unknown 语义与已确认部分；不伪造0 consumption | R02、21；A§13 |
| TC-U14-09 | 关闭 unknown 后再中断 | T3、T6 的重入 | 原 attempt 已关闭 interrupted_unconfirmed，发新请求前再 kill | 同旧 attempt unknown 不二次增加；新实际开始才加 requests | R02、26；A§13 |
| TC-U14-10 | 新 Trace/活动时长 | T3、T10 | 两进程活动段 a/b，中间暂停间隙 h；各段独立记录 clock | 新 span 关联原 Run/Step/Call/source/attempt；旧 span 仅 interrupted/unknown，无伪完成；active_duration≈a+b，不含h，容差见 Q-2 | R12、21、26；A§13、15 |
| TC-U14-11 | summary原source失效 | T7 守卫 | 已计划summary后，原source事件/版本校验不匹配；仅改变一处source事实 | 拒绝发布；context/version不部分前进，已知summary usage不丢；不把旧/伪source静默换新后成功 | R21、33；A§5 |

### U15：candidate 提交状态

技术：状态转换。起点 not_started；终止提交状态 registered/empty/failed（不等于 Run 终态）。registered 的 patch 还须完整性验证才能作为评估输入。

| State / Event | 开始 export | export/注册可信完成 | Resume |
| --- | --- | --- | --- |
| not_started | exporting / T1，先记 attempt/index | ? | T1，用原 candidate |
| exporting | 不并发新链 | registered/非空 T2；empty T3；failed T4 | 旧链已结束→新 exporting/T5；旧链未确认→busy/I1 |
| registered | 禁止重复 export / I2 | 不重复登记 | T6，验证原 ref/bytes/hash/ownership/baseline；损坏拒绝/I5 |
| empty | 禁止借恢复再 export / I3 | 不升级成功 | T7，原失败终止 |
| failed | 禁止借恢复再 export / I4 | 不升级成功 | T8，原失败终止 |

转换清单：T1～T8 与 I1～I5 的起点/事件/结果如表。T5 重试的是未确认提交，新的 attempt/临时 index，不认领孤立 artifact；不是对明确 failed 的业务重试。

| Path | Sequence | Transitions | Postcondition |
| --- | --- | --- | --- |
| U15-P1 | not_started→exporting→registered→Resume验证 | T1、T2、T6 | 同 candidate/ref/bytes，evaluator 只读注册 patch |
| U15-P2 | exporting→kill→旧链结束→新 exporting→registered | T5、T2 | 同 candidate，新 attempt/index，原 index 不变 |
| U15-P3 | exporting→empty→terminate | T3、T7 | 无 harness，原失败 |
| U15-P4 | exporting→failed→terminate | T4、T8 | 不因 Resume 二次提交 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U15-01 | 首次候选提交 | U15-P1/T1、T2 | 真 Git/P，已接受 candidate；export/put/引用提交顺序观察 | 开始事实先于 export；原 candidate/ref/hash/baseline 与完成一起确认后才 evaluator | R08、28；A§12.2 |
| TC-U15-02 | 已注册只复用 | T6、I2 | registered 后 kill；工作树又有变化，旁有更新 artifact/PD | 仅原 P/ref 评估；零 export/再登记，非最新 artifact | R28、35；A§12.2 |
| TC-U15-03 | export 未确认可重做 | U15-P2 | exporting G2 中断，已验证旧链/组结束 | 新 attempt/index 重导出同 candidate；原 index/工作树不变，不清理未证明归属路径 | R29；A§12.2 |
| TC-U15-04 | put 成功引用未提交 | U15-P2/G6 | 真 put 成功后 kill，孤立 patch 存在；旧链结束 | 不直接认领孤立 patch；新 attempt 导出，引用只取新可信提交 | R29；A§12.2 |
| TC-U15-05 | 旧 export 残留 | I1 | exporting，旧组/可延迟发令 helper 未结束，各独立 variant | WORKSPACE_BUSY；无新 export/evaluator，不扫临时目录乱删 | R29、36；A§12.2 |
| TC-U15-06 | 明确 empty | U15-P3、I3 | empty 已持久化，终态前 kill | 原评估失败路径，无 harness、无新 export，不重新请求模型 | R09、29；A§12.2 |
| TC-U15-07 | 明确 failed | U15-P4、I4 | failed 提交结果已确认，终态前 kill | 复用原失败，不重导出、不刷新 retry | R09、29；A§12.2 |
| TC-U15-08 | 已注册 patch 丢失 | I5 | 仅移走隔离存储 P bytes | CANDIDATE_ARTIFACT_INVALID；不从 Workspace 重导出，NO-WORK | R28；D§12.1 |
| TC-U15-09 | 已注册 hash 损坏 | I5 | 仅改 P 一个字节 | 同 08；不评估更改后的 patch | R28；D§12.1 |
| TC-U15-10 | 已注册错 ownership | I5 | 仅引用指向 F2 合法 artifact | 同 08；不使用他 Run patch | R28；D§12.1 |
| TC-U15-11 | 候选/诊断不混用 | T6 | P registered、PD diagnostic 分别已登记；暂停后 resume | 候选原文/submission/ref 未被 PD 覆盖；评估输入精确 P | R35；D§12.1 |

### U16：评估 attempt 恢复

技术：状态转换。起点 E0=patch registered/评估未开始；E1=attempt 开始且结果未确认；E2=旧 attempt interrupted 且资源确认已结束/受控清理；E3=完整结果已持久化；ET=对应 Run 终态。

| State / Event | 开始评估 | Resume/中断判定 | 接受结果 | 终态提交 |
| --- | --- | --- | --- | --- |
| E0 | E1 / T1，先保存 attempt/身份条件再目录/进程 | 同 T1，原 patch/config | ? | ? |
| E1 | 禁止并发 / I1 | E2 / T2，旧 host/容器确认清理；未知/busy I2 | E3 / T4，完整结果/refs | 未有完整结果不成功 |
| E2 | E1 / T3，新 attempt，同 patch | 保留 interrupted 历史 | ? | ? |
| E3 | 禁止重复评估 / I3 | T5，跳过 evaluator | 不重复累计/发布 | ET / T6，对应 pass/fail/error |
| ET | 禁止重开 / I4 | RUN_NOT_RESUMABLE | 不升级孤立报告 | 无出边 |

转换清单 T1～T6、I1～I4 如表；无结果的孤立 report 不是 E3。清理只针对已保存且严格 instance/harness_run_id 匹配的旧 attempt，不作用于 Agent 或邻居。

| Path | Sequence | Transitions | Postcondition |
| --- | --- | --- | --- |
| U16-P1 | E0→E1→E3→ET | T1、T4、T6 | 首次评估身份、完整结果、终态关联 |
| U16-P2 | E1→kill→E2→新E1→E3→ET | T2、T3、T4、T6 | 同 patch 新 attempt，旧组/资源先结束 |
| U16-P3 | E3→kill→恢复→ET | T5、T6 | 零新 harness，完整结果复用 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U16-01 | 评估首次开始 | U16-P1/T1、T4 | E0；记录目录/进程创建与 attempt commit 顺序 | attempt UUID/harness_run_id/task/patch/目录/资源条件先落库，完整结果/refs 确认后才终态 | R30、32；A§9、12.3 |
| TC-U16-02 | 旧评估确认退出再重评 | U16-P2 | harness 开始后 kill 原父；旧 host 组结束，可识别遗留容器 | 旧 attempt interrupted；严格清理后同 P 新 attempt，新资源身份独立；原审计保留 | R30；A§12.3 |
| TC-U16-03 | host 组未结束 | I1、I2 | E1，旧组确实活动 | EVALUATION_ATTEMPT_BUSY；无新 harness，不并发旧组 | R31；A§12.3 |
| TC-U16-04 | host 身份未确认 | I2 | 开始事实有，但宿主出生/组身份缺失且无法证明结束 | 同 03；不凭 PID/name 或容器已停猜收尾 | R31；A§12.3 |
| TC-U16-05 | 旧评估清理不确定 | I2 | 容器归属明确，但 stop/remove 结果不明 | EVALUATION_ATTEMPT_BUSY；保留恢复点，零新评估 | R31；D§12.2 |
| TC-U16-06 | 严格归属保护邻居 | T2 | Agent 容器+旧 attempt 容器+F2相似名容器；只有旧 attempt 可清理 | 只旧 attempt 被清理；Agent/F2 保留；后续新 attempt 用原 task/P | R30、31；A§12.3 |
| TC-U16-07 | 通过结果后只终态 | U16-P3、I3 | G7 passed/output/refs 完整，终态前 kill | 原 passed/output/refs→SUCCEEDED；EvaluationCompleted 一次，零新 harness | R32；A§9 |
| TC-U16-08 | 失败结果后只终态 | U16-P3、I3 | G7 valid unresolved 失败结果 | 原 FAILED/评估失败，原证据；不再评估碰运气 | R32；D§12.2 |
| TC-U16-09 | 错误结果后只终态 | U16-P3、I3 | G7 可信 infra/error 结果 | 原失败与 error/output/refs 保留；不改成功/不新评估 | R32；D§12.2 |
| TC-U16-10 | 孤立报告非结果 | T2、T3 | report 声称成功但无 ResultRecorded；旧资源确认结束 | 不升级 E3；同 P 新 attempt 后才判结果 | R30、32；D§12.2 |
| TC-U16-11 | 终态不重评 | I4 | 可信失败已提交 RunFinished，再 resume | RUN_NOT_RESUMABLE，业务 State/证据不变，无新 harness | R33；D10 |
| TC-U16-12 | 控制异常穿过 evaluator | T4 守卫 | observer 提交失败、执行归属未知、收尾未知各独立变体 | 停止推进/保留最后事实；不包装成 passed=False 后写 FAILED/RunFinished，不导出 exec 诊断 | R31、34；A§12.1 |

### U17：diagnostic finalization

技术：用例测试。Actor 为完成/暂停命令的 Application；trigger 是 Runtime 返回；成功步骤：①持锁且业务执行已收尾/预算够 → ②追加 DiagnosticStarted/index → ③身份/许可逐次提交后执行 → ④追加结果/artifact ref → ⑤按原 finalization 成败决定资源策略。失败保证始终是原业务 State/phase/active_operation/candidate/finished_at/reason 及业务 Snapshot 不变。

| Path | Branch | Condition | Ends | Postcondition |
| --- | --- | --- | --- | --- |
| U17-P1 | 主路径 | 安全且预算够 | 完成事件 | 只增长事件 sequence，引用 diagnostic，非 candidate |
| U17-P2 | ① | 业务/旧执行不确定 | 跳过导出 | 记录跳过原因，无 export |
| U17-P3 | ② | 开始事实写不成 | 停止 | 不启动进程/RPC |
| U17-P4 | ③ | 身份/许可提交不明 | 停止/回收已知执行 | 不放行、不用旧 sequence 补写状态 |
| U17-P5 | ②/③/④ | 终态后任一屏障 kill | 中断 | 终态不重开、业务 operation 空 |
| U17-P6 | ③ | SUSPENDED 的 diagnostic 留旧 helper/export 未确认 | 后续 Resume 拒绝 | WORKSPACE_BUSY，无并发 |
| U17-P7 | ④/⑤ | 暂停诊断失败 | 附加错误 | SUSPENDED/原恢复点/Workspace 保留 |
| U17-P8 | ⑤ | SUCCEEDED 但 artifact collection 失败 | finalization error | 成功终态不改，Workspace 保留，CLI 非零，无修复 Resume |
| U17-P9 | ⑤ | SUCCEEDED 且 artifact finalization 成功 | destroy | 原成功保持，仅此路径允许销毁 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U17-01 | 诊断只追加事件 | U17-P1 | SUSPENDED / FAILED / CANCELLED 三独立 variant；安全真 export P→PD | 最新 CAS 事件顺序含独立 attempt/index/身份/许可/结果/ref；原业务 State/Snapshot 逐字段一致；不变 candidate | R34、35、38；A§12.4 |
| TC-U17-02 | 未收尾跳过诊断 | U17-P2 | 旧业务组/helper 未确认 | 无 export/Engine RPC；跳过原因可见；暂停状态不改 | R14、34；D§13 |
| TC-U17-03 | 开始提交失败 | U17-P3 | 真 CAS/存储失败阻止 DiagnosticStarted | 零 export/进程/RPC；不虚构开始/完成，不改终态 | R38；A§12.4 |
| TC-U17-04 | 身份许可提交不明 | U17-P4 | G8 身份/许可两个独立 variant commit 确认丢失 | 不放行；有界回收本次已知 helper/组；State/Snapshot/candidate 不变 | R38；A§12.4 |
| TC-U17-05 | 终态诊断窗口硬杀 | U17-P5 | SUCCEEDED 后 diagnostic 开始、身份、许可、完成后确认丢失四独立 variant | 每窗口只留原业务 State，phase=None、active_operation=None；新 resume 均终态拒绝，不重试诊断 | R38；A§12.4 |
| TC-U17-06 | 暂停诊断残留阻塞恢复 | U17-P6 | SUSPENDED diagnostic helper 尚可能 start、远端暂未运行 | WORKSPACE_BUSY；无业务/容器启动/新诊断链 | R36、38；A§12.4 |
| TC-U17-07 | 暂停诊断失败不终止任务 | U17-P7 | 真 export/登记失败各独立 variant | 仅附加 finalization error；SUSPENDED/原 phase/Workspace 保留，不写 FAILED/RunFinished | R34；D§13 |
| TC-U17-08 | 成功交付失败保留现场 | U17-P8 | 已 SUCCEEDED，artifact collection 失败 | 原成功结果不改，CLI 非零；现场保留，resume RUN_NOT_RESUMABLE | R33、34；D§13 |
| TC-U17-09 | 成功交付后销毁 | U17-P9 | SUCCEEDED 且原 artifact finalization 成功 | 持锁到收尾，才允许销毁当前 Workspace；不删除其他资源 | D§13；A§11.2 |

### U18：dry-run 与拒绝优先级

技术：决策表。已取同 Run 锁；B=明确阻断，L=有待 live checks/清理需求，M=provider key 存在。空间2³=8；B=Y 时 L/M 不影响“拒绝”，B=N 时 M 不影响只读计划，折叠为3列。Docker 连接所需凭据不是 M，缺失导致 B=Y。

| Type | Condition / action | C1 | C2 | C3 |
| --- | --- | --- | --- | --- |
| 条件 | B | Y | N | N |
| 条件 | L | - | N | Y |
| 条件 | M | - | - | - |
| 动作 | 输出/退出码 | rejected / 1 | ready / 0 | requires_live_checks / 0 |
| 不变量 | 只读 | 三列都必须 DB/Artifact/tree 不变；允许锁文件初始化，无 exec/start/模型/清理 | 同左 | 同左 |

Findings：无可达冲突；不同阻断的 code 不相互折叠，C1 内使用独立原因样本。requires_live_checks 不是实际可恢复承诺。拒绝优先级的 robustness 测试只验证 A§7 定义的不同组之间顺序，不编造同组内优先级。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U18-01 | 安全 Local 只读 | C2 | F Local ready，缺 provider key；重复 dry-run | ready/0；无需模型 key；记录可读；Run/Event/Approval/Artifact tree 摘要不变，attempt 不增 | R22；A§7.3 |
| TC-U18-02 | stopped Docker 待检 | C3 | 原 exited 容器身份合法，需启动后 Git/组检查 | requires_live_checks/0，明确 workdir/Git/组待检；不 start/exec | R22；A§7.3 |
| TC-U18-03 | 旧评估资源仅列清理 | C3 | 宿主可确认结束，尚有严格归属旧评估资源需清理 | 计划列清理需求，零 delete/stop/harness/新目录 | R22；D§5 |
| TC-U18-04 | 确定拒绝 | C1 | marker 错身份，其余合法 | rejected/1，稳定身份码和定位；DB/现场不变 | R22；D§5 |
| TC-U18-05 | 缺库不初始化 | C1 | --database 指向不存在文件，artifact root 同样不存在 | exit 1、明确不能定位记录；不创建数据库/ArtifactStore/评估目录；仅锁路径可初始化 | R22、33；A§14 |
| TC-U18-06 | 旧库不切换 journal | C1 | 隔离旧格式库，运行前取 bytes/journal mode/目录清单 | 格式拒绝；无 initialize/migration/WAL 切换，不改 bytes | R22、33；A§14 |
| TC-U18-07 | 缺 Docker 引用事实阻断 | C1 | 唯一缺 Docker TLS/endpoint 所需引用，provider key 任意 | rejected/1，非敏感缺引用/访问原因；不把它当 provider key 可省略 | R22、25；A§7.3 |
| TC-U18-08 | 锁冲突优先 | C1 robustness | 原进程持锁，同时 ID 在可读取库中不存在 | RUN_IN_USE；不读变动现场、不输出后层错误；无业务写 | R11、22；A§7.1 |
| TC-U18-09 | 终态优先现场错 | C1 robustness | 合法终态 F，同时 marker 错 | RUN_NOT_RESUMABLE；无 attach、无修改 | R33；A§7.1 |
| TC-U18-10 | 未知 shell 优先依赖错 | C1 robustness | shell 结果未知，同时 exited 容器、模型凭据缺失 | UNCERTAIN_TOOL_EXECUTION；不 start、不重新审批、不以缺 key 盖过安全原因 | R05、22；A§7.1 |
| TC-U18-11 | Workspace 阻断优先候选坏 | C1 robustness | 明确 Workspace ID mismatch，同时 candidate artifact 缺失 | WORKSPACE_IDENTITY_MISMATCH；不清理/重导出 candidate | R15、28；A§7.1 |
| TC-U18-12 | 拒绝输出可定位不泄密 | C1 | 未确认 shell 输入中含秘密哨兵；命令含敏感参数 | 输出 Run/Workspace/Step/Call/tool/开始 sequence/phase/source；说明保留现场且不自动续跑，无秘密完整参数/force入口 | R05；D§13 |
| TC-U18-13 | 当前库无此Run | C1 | 当前格式库正常存在，有F2但没有F | RUN_NOT_FOUND/1；无模型/工具/容器操作，库/F2不变，不凭目录认领 | R33；D§13 |

### U19：信号路由与暂停总预算

主技术：决策表。N=第一/后续信号，P=首笔记录前/非终态记录后/终态后，S=SIGINT/SIGTERM。空间2×3×2=12；非首信号按顶层取消折叠3种 P；首信号保留3种 P，S 不改变动作但改变退出码，保留独立信号变体。

| Type | Condition / action | C1 | C2 | C3 | C4 |
| --- | --- | --- | --- | --- | --- |
| 条件 | N | 首 | 首 | 首 | 后续 |
| 条件 | P | 首笔前 | 非终态后 | 终态后 | - |
| 动作 | 停止意图 | 停新工作，有界 bootstrap 清理 | 停新工作，有界收尾/暂停提交/剩余诊断 | 只终止 finalization，不改终态 | 取消顶层 task，不保证暂停落库 |
| 动作 | 信号码 | SIGINT→130、SIGTERM→143 | 同左 | 同左 | 按已有信号 scope 退出；混合信号序列未另定优先码，不作断言 |

Findings：库 CancellationToken 是独立契约，不能折叠进 CLI signal。裸 asyncio.CancelledError 未携带 interruption 意图不能自动变 SUSPENDED；不额外发明其上层结果码。

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U19-01 | 首笔前信号 | C1 | Workspace/临时资源已准备但完整记录未确认；SIGINT/TERM 独立 variant | 130/143；有界清理本次新资源，无不完整可恢复记录/恢复命令，不请求模型 | R13；A§11 |
| TC-U19-02 | 非终态首信号 | C2 | 模型等待/summary等待/安全工具边界/resume attach 阶段四 variant × INT/TERM | 停新工作；若暂停提交成功 SUSPENDED+原 phase/元数据，无 finished_at/reason/RunFinished，130/143；资源保留 | R13；A§11 |
| TC-U19-03 | shell 无可信返回 | C2 | shell 已开始、修改未结果；INT/TERM 独立 variant | 不造 ToolEnded/StepCompleted；保留 active attempt/现场；下次 UNCERTAIN_TOOL_EXECUTION | R14；D§6 |
| TC-U19-04 | 收尾已可信可先提交 | C2 | 结果真实返回且组结束已确认，预算仍够，信号到达 | 可先完整提交工具完成再 SUSPENDED；不存在“结果消息有、next_index未进”等半提交 | R13、24；A§11 |
| TC-U19-05 | 终态后信号 | C3 | SUCCEEDED/FAILED/CANCELLED 的 finalization 中各独立 variant，INT/TERM | 130/143；原终态/详情不改，尤其不转 SUSPENDED；无新业务 | R33、38；A§11 |
| TC-U19-06 | 第二信号强停 | C4 | 第一个信号处理尚在有界等待，再发同种信号；INT/TERM 独立 variant | 顶层取消，不要求 SUSPENDED；新进程读取最后确认事实；不伪造完成 | R13、14；A§11 |
| TC-U19-07 | 库取消保持终态 | 独立 cancellation 契约 | 不传 interruption token，显式 CancellationToken cancel | RunResult=CANCELLED，原 cancellation reason，finished_at 存在；不是 RunSuspension | R13；D2 |
| TC-U19-08 | 结果类型与暂停展示 | C2 / C3 消费者 | 启用 interruption 返回 RunSuspension；未启用正常终态；裸 CancelledError 独立变体 | Application/rendering 按联合正确显示；暂停无 finished_at/reason；未授权取消不自动当暂停 | R13；A§11.2 |

副技术：3-value BVA。总预算 B=30s；`r=B-elapsed` 单调剩余，单位ms/步长δ=1ms（ASM-4）。可用域 r>0，耗尽域 r≤0。负剩余是真实 deadline 超时输入，不是负配置。

| Condition | Partition | Validity / outcome | Min | Max | Step | Source |
| --- | --- | --- | --- | --- | --- | --- |
| 剩余预算 r | U19-P1 | 可使用剩余额度，不承诺一定完成 | δ | +∞ | δ=1ms | A§11 C10 |
| 剩余预算 r | U19-P2 | 不可重新开预算；停止/保留最后事实 | -∞ | 0 | δ=1ms | A§11 C10 |

| Boundary | 3-value set（两分区邻界去重） | Expected |
| --- | --- | --- |
| P1 min=1ms；P2 max=0ms | -1ms、0ms、1ms、2ms | ≤0 不重启收尾/诊断预算；>0 至多使用剩余量 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U19-09 | 已超过 deadline | r=-1ms | 可控 deadline，收尾已耗30s+1ms | 不新开诊断/30秒，按信号码退出/保留最后事实，非 FAILED | R13、34；A§11 |
| TC-U19-10 | 恰好 deadline | r=0ms | 可控 clock 指向30s | 同 09；不因 <=/< 错误多开阶段 | R13、34；A§11 |
| TC-U19-11 | 仅余1ms | r=1ms | 可控 clock，准备进入诊断 | 至多1ms剩余；允许跳过，不能获得新30s；不强求1ms足够提交 | R13、34；A§11 |
| TC-U19-12 | 仅余2ms | r=2ms | 同 11，剩余2ms | 至多2ms，非只能 r==1 才处理；无预算刷新 | R13、34；A§11 |
| TC-U19-13 | 真默认共享30秒 | 默认值集成后盾 | 真实信号，操作收尾/持久化/诊断逐阶段受控阻塞且记录 elapsed | 共享冻结30s；不是各阶段30s；总退出时间≤30s+实测ε；超时允许无 SUSPENDED，最后事实不改 FAILED；Q-2未定不得判时间通过 | R13、34；A§18 C10 |

### U20：锁与 CAS 多进程路径

技术：用例测试。Actor 是两条并发命令；主流程 ①规范 DB/UUID → ②非阻塞取 flock → ③bootstrap/运行/恢复 → ④finalization → ⑤释放。主成功后条件是同 Run 只一个推进者；副本 CAS 防陈旧提交，不能替代②。

| Path | Branch | Condition | Ends / postcondition |
| --- | --- | --- | --- |
| U20-P1 | 主路径 | A持锁，B同 Run执行 | B立即RUN_IN_USE，无副作用；A正常到④再释放 |
| U20-P2 | ② | 两个resume竞争，赢家确认持锁后保持至输家冲突已观察 | 仅一推进者；输家尝试严格处于持锁区间，无双Started/工具 |
| U20-P3 | ④ | A finalization 尚未完成 | B仍冲突，不提前释放 |
| U20-P4 | ③ | A被 SIGKILL、子进程仍活 | 锁释放可获得，但旧执行事实仍阻止不安全续跑 |
| U20-P5 | ② | DB路径规范别名/UUID允许表示 | 指向相同锁 |
| U20-P6 | ② | A/B不同 Run | 两个均可推进，彼此目录/资源独立 |
| U20-P7 | ⑤后 | 锁文件仍留 | 再进入用同 inode，防 unlink 双持有 |
| U20-P8 | ③ | stale event/snapshot/approval CAS | 仅一事务赢家，无半写、陈旧者停止 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U20-01 | run 对 resume | U20-P1 | 新 Run 已持锁发布 F，运行中另一进程 resume/dry-run 两独立 variant | 第二个立即 RUN_IN_USE/1；无 attach/模型/工具；锁不杀持有者 | R11；A§6 |
| TC-U20-02 | 持锁区间内双resume竞争 | U20-P2；F5 | 两真进程竞争；赢家在已取真实flock、Started已实际提交但业务未放行的公开store返回边界向监督者握手并阻塞；输家照常非阻塞取锁。监督者收到输家完整结果后才向赢家交付正常提交返回值 | 输家RUN_IN_USE/1且零业务；时间线满足lock_acquired < loser_attempt < loser_result < winner_release。赢家随后完成原恢复，同业务副作用一次；若持锁握手/冲突未到而需监督kill则失败/设置错误，不计通过 | R11；D9、A§6；F5 |
| TC-U20-03 | 锁覆盖 finalization | U20-P3 | A在诊断/收集/销毁屏障各独立 variant，B resume | B仍 RUN_IN_USE；A finalization 退出后才可取锁 | R11；A§6 |
| TC-U20-04 | 锁 FD 不传给子进程 | U20-P4 | A起真长寿子进程，kill A，B取锁 | B可获取 OS 锁，证明子不持锁；旧组仍活则 busy/未知工具拒绝，不与其并发 | R11、19；A§6、8 |
| TC-U20-05 | 规范键一致 | U20-P5 | 同 DB 规范路径与含 `..` 的等价路径、UUID带/不带run_两独立 variant | 同一锁冲突，不能绕过规范化 | R11；A§6 |
| TC-U20-06 | 不同 Run 并行 | U20-P6 | 同 DB 中 F/F2 各合法安全恢复 | 二者可同时推进、无串身份/observer；文件/审批/容器清理互不干扰 | R11；D9、A§8.2 |
| TC-U20-07 | 不 unlink 锁文件 | U20-P7 | A释放后B持锁，C再竞争；记录文件inode | 文件保留、同inode；C冲突，不出现新inode双持有 | R11；A§6 |
| TC-U20-08 | 真 CAS 双提交 | U20-P8 | 两真连接相同 expected sequence；snapshot version、approval version各独立冲突变体 | 一成功一冲突；State/Event/Approval/Snapshot同事务，不部分写；陈旧提交方不发新工作 | R12；A§9–10 |

Test level(s): F5必须使用真POSIX进程/flock及应用持锁作用域；公开提交委托只保持已获锁进程，不替代锁或用sleep模拟重叠。TC-U20-01/03复用“赢家确认持锁→输家完成尝试→赢家释放”的监督握手。

### U21：真实边界组合旅程

技术：用例测试。Actor 为用户在新进程 Resume；主流程：①准备冻结定义与真实资源 → ②运行到持久化边界 → ③信号/kill → ④另一进程同 ID resume → ⑤用原 evaluator 完成 → ⑥核对结果/交付/资源。不是随机模型能力测试，provider 提交预先固定响应/候选。

固定oracle：Local provider顺序为 `B3` tool batch（input40/output10）→final candidate原文`resume-local-ok`（input100/output20），冻结容量与阈值使该序列不触发compaction，工具/交付夹具均成功；只在c0完成已确认、c1开始事实未提交的安全屏障暂停一次。真实SWE-bench使用版本化夹具manifest里的 `P_unresolved`：记录development instance ID、patch bytes/hash、冻结task/data/harness/image身份，以及本轮恢复测试之前独立确认的“patch应用成功、valid unresolved”报告/必要失败测试集合。expected固定为FAILED/EVALUATION_FAILED、错误TASK_UNRESOLVED/CLI1；不在本轮读取结果后反向生成expected。缺manifest/先验证据按Q-4记DEFER，不把缺环境或infra error当该预期失败通过。

| Path | Branch / condition | End / postcondition |
| --- | --- | --- |
| U21-P1 | 主路径，Local/macOS与Linux，固定B3→resume-local-ok | 同目录/消息/Usage/进度；SUCCEEDED/TASK_VERIFIED、原文输出、恢复命令exit0 |
| U21-P2 | Docker 原容器 running/exited；有workdir外依赖 | 同完整ID/原writable layer，真实helper/exec/supervisor，不重新create |
| U21-P3 | 在安全边界重复正常暂停恢复4次 | 无次数上限，原Step/usage不重置，无无界自动retry |
| U21-P4 | SWE-bench development task，固定P_unresolved，原评估被中断 | 同P新attempt；正常应用、valid unresolved→FAILED/EVALUATION_FAILED/TASK_UNRESOLVED，CLI1 |
| U21-P5 | 真实冻结Workspace daemon A、evaluator daemon B，ambient C漂移 | 所有阶段各用A/B；邻居/Agent不误删；不操作C |
| U21-P6 | P5连接中含已验证TLS，环境提供隔离daemon | CLI/SDK/harness/清理均走匹配TLS参数，真实握手与身份有效 |

| ID | Title | Covers | Test data / action | Expected result | Trace |
| --- | --- | --- | --- | --- | --- |
| TC-U21-01 | 两种POSIX宿主Local固定成功 | U21-P1；F3 | macOS/Linux独立variant，真SQLite/Git/Artifact；用本单元固定provider顺序，c0安全边界首SIGINT后恢复 | 原命令SUSPENDED/130，恢复SUCCEEDED/TASK_VERIFIED/exit0，output精确resume-local-ok；c0/c1/c2各一次，模型请求总数2、已确认input140/output30/cost0.00020（ASM-5价格）、unknown0；同Run/Workspace/定义，无重复消息；成功artifact/收尾后才销毁。任意FAILED/infra error不是通过分支；计时另按Q-2 | R04、13、25；D§14；F3 |
| TC-U21-02 | Docker原层续跑 | U21-P2 | 真daemon/SDK；c0已装workdir外测试依赖；running/exited各独立variant | 同container ID；依赖可用，c0不重跑，余批完成；exec/helper/group收尾证据可查 | R04、16、18；D§14 |
| TC-U21-03 | 连续四次恢复 | U21-P3 | 四个安全边界，每次先等待真实暂停完成再人工调用resume；其中一次Started后再kill | 审计与真实Started次数一致且至少4；原Step/失败数/已知Usage保留；RECOVERING原来源不变；每命令只一次恢复流程 | R12、26；D8 |
| TC-U21-04 | 真SWE-bench固定unresolved恢复 | U21-P4；F3 | 至少一项manifest中已确认结果的development task及P_unresolved（见Q-4）；harness开始后kill父，确认旧资源收尾后恢复 | 同patch bytes/hash/ref新attempt、原审计保留；真实报告patch_successfully_applied=true、valid unresolved/required失败集合与独立夹具相符；FAILED/EVALUATION_FAILED、error.code=TASK_UNRESOLVED、CLI1，结果/refs原子用于终态，Workspace保留；无PassThrough。infra/error/patch不应用/错误task/意外成功均不算通过 | R27～32；D§14；F3 |
| TC-U21-05 | 真双daemon绑定 | U21-P5 | Workspace=A、evaluator=B、ambient=C；各有邻居标记；复用真实dev task链路 | inspect/start/exec走A，镜像/评估/清理走B，不触及C；Agent与邻居完整保留 | R17、25、27、30；D§7.2 |
| TC-U21-06 | 真实TLS链路 | U21-P6 | 验证TLS的daemon作为A或B，冻结证书引用；重复一次真实恢复/评估/清理 | 实际CLI和SDK连同一预期daemon；无ambient回退/跳过验证；资源归属可证，秘密不落库 | R25、27；A§8.4、13 |

TC-U21-05/06复用同一P_unresolved/manifest及固定终态oracle，附加双daemon/TLS断言；不另接受“任意可信失败”。Test level(s): F3的固定输入先在确定性装配路径可核验；Local真OS旅程与至少一个真实development task仅保留其各自组合风险，不以更多真实模型请求提高信心。

## 4. 覆盖、追踪与剩余风险

### 4.1 按技术衡量的设计覆盖

以下“已覆盖”表示测试设计中有可定位用例；**实际执行覆盖为未取得**。百分比只对本文明确列出的有限模型成立，不代表产品全部输入/序列覆盖，也不把 Q/`?`/环境缺失计为通过。用例变体不并入独立 TC 行数，执行时必须逐个展开。

| Unit | Technique / target | Coverage items | Covered by design | Design coverage | Not covered / residual risk |
| --- | --- | --- | --- | --- | --- |
| U1 | EP，每输入 each-choice | 14个分区E1～E14 | 14 | 14/14 | 不枚举任意字符串；原旧选项需执行回归 |
| U2 | EP | 12个分区E1～E12 | 12 | 12/12 | 未来版本按拒绝规则，不维护兼容分支；首笔失败为额外路径 |
| U3 | ST，0-switch＋指定拒绝 | 有效T1～16；终态resume I1～3 | 16；3 | 16/16；3/3 | 不宣称all-transitions/全1-switch；`?`调用无规定前提，不推断错误契约 |
| U4 | Use case | P1～P17：原10路径、存活错误出口4路径、共享store3路径 | 17 | 17/17设计路径 | G3-K与G3-E/U分开；AC不是18次重复回滚，DB-A/B/C前缀触发仍待Q-3落实 |
| U5 | EP | E0～E13 | 14 | 14/14 | 输入需独立通过适用构造/decode/transition入口；不是只测一个validator |
| U6 | Collapsed DT | C1～C5 | 5 | 5/5折叠列 | 不等于16原始组合全覆盖；不可能列明确排除 |
| U7 | ST，0-switch | T1～T10；P1～P4路径 | 10；4 | 10/10；4/4 | 非dispatch事件`?`、任意工具序列不穷举 |
| U8 | 3-value BVA | cursor8值＋Step5值＋retry3值 | 8＋5＋3 | 16/16设计值 | retry的0/1/2边界均有固定后续序列、精确请求数/结果，不只上界；Step超限是guard合成输入 |
| U9 | ST；secondary DT | T1～T8；C1～C4 | 8；4 | 8/8；4/4折叠列 | 其他Policy规则不在本增量；无handler精确既有分类不新造 |
| U10 | Collapsed DT | C1～C4 | 4 | 4/4 | 实际PID复用概率不测；真实出生检查与错身份分支并列 |
| U11 | ST，0-switch＋指定许可禁令 | T1～T7；I1～I6 | 7；6 | 7/7；6/6 | Engine受控协议不证明真收尾；真实后盾U21，Q-1/Q-3 |
| U12 | EP | Local8类＋Docker9类 | 17 | 17/17 | 每个label/资源/状态变体独立；瞬态inspect分支不冒充真实状态制造 |
| U13 | EP | E1～E11 | 11 | 11/11 | 双daemon/TLS真语义另由U21；不证明provider实际计费 |
| U14 | ST，0-switch | T1～T10；发布source守卫 | 10；守卫另有TC-U14-11 | 10/10 | 时间容差Q-2；独立模型中的S2不得绕过source validation |
| U15 | ST，0-switch＋指定拒绝 | T1～T8；I1～I5 | 8；5 | 8/8；5/5 | I5含独立完整性变体，不宣称所有ArtifactStore损坏模式 |
| U16 | ST，0-switch＋指定拒绝 | T1～T6；I1～I4 | 6；4 | 6/6；4/4 | 实际harness命名/资源兼容U21；既有report解析单元不重复展开 |
| U17 | Use case | P1～P9 | 9 | 9/9 | 无终态repair；业务State不变与事件追加均须实测 |
| U18 | Collapsed DT | C1～C3；4个明确优先组间robustness样本 | 3；4 | 3/3；4/4选定样本 | 不测同组内部未规定优先级；不是所有多故障组合 |
| U19 | Collapsed DT；3-value BVA | C1～C4；r=-1/0/1/2ms | 4；4 | 4/4；4/4 | 两种信号/阶段variant须分别执行；默认30s计时结论Q-2 |
| U20 | Use case | P1～P8 | 8 | 8/8设计路径 | P2用持锁后握手证明区间重叠；POSIX本地FS，非NFS/多主机 |
| U21 | Use case | P1～P6 | 6 | 6/6设计路径 | Local固定成功、SWE-bench固定valid unresolved；Q-1/Q-4未落实，真实证据未取得 |

模型归并说明：U3/P7 的多个业务来源是独立变体，只共享步骤；U6/9/10/18/19 用 `-` 折叠相同动作而非隐藏未知规则；U12 的不同字段/状态变体始终一项无效。U21 只保留能增加真实组合信心的路径，不把 U5/6 的每个逻辑反例再跑全套真实 SWE-bench。

### 4.2 A§9 原子提交矩阵

AC01～AC18保留为业务提交组合义务ID，**不再是TC-U4-11的18个同类约束失败变体**。每行通过实际业务路径到公开create/commit入口，观察本次真实提交是否同时包含列出的字段/事件/版本绑定，再以新连接读取结果。字段拆成两笔提交，即使最终值一致也不满足该行的原子组合契约；AC17明确只追加事件、保持业务State/Snapshot不变。现有成功/重入用例承担各自业务装配，统一补充这些组合断言，而非逐行重复制造相同的UNIQUE错误。

共享事务机制只保留以下三个有不同检测能力的路径；首次创建另由TC-U2-13/01覆盖：

| ID / path | Independent fixture and fault point | Observable pass / fail oracle |
| --- | --- | --- |
| DB-A / U4-P15 | 合法WAITING_APPROVAL记录已有pending/审批版本v、Snapshot版本V与sequence q；一次公开commit目标为RECOVERING，包含Started、旧pending INVALIDATED/清空、Snapshot和更新State。临时测试库的Snapshot写前条件触发器，只有查询到本次新事件与审批版本v+1/resolution才发专用数据库故障 | 错误/原因链必须表明前缀命中；若未到该点或前缀缺失则失败/夹具不成立。重开q/v/V及原pending/State均未改变；事件/审批单独提交会被检出 |
| DB-B / U4-P16 | 同形合法commit；Run行AFTER UPDATE触发器读取本次event、审批、新Snapshot、State、last_sequence/latest_snapshot_version全新值，只有全部吻合才RAISE(FAIL)。该SQLite动作保留当前语句改动供外层事务回滚，不用会自动整体回滚的RAISE(ROLLBACK)代替 | 收到全前缀命中故障；重开后全部旧值。任何审批/Snapshot/Run新值残留即失败，不能只断言最后一条SQL回滚；不mock连接或transaction实现 |
| DB-C / U4-P17 | 全新隔离库，同样合法原记录/变更，不装故障触发器，正常提交 | 重开后所有新值/版本绑定均在，且只有该Run受影响；排除夹具本来非法导致的假回滚通过 |

这些是测试数据库环境故障，通过公开store操作触发，不 patch `_commit_sync` 或内部回调。触发器只在每测试独立库生命周期内存在；用例结束销毁该库，不写产品schema。触发条件和专用错误是夹具观测，非产品新增错误契约。若SQL写入顺序合法重排，按同样前缀义务选择对应的后段数据库触发点，记录实际已修改对象，不要求产品保留当前SQL顺序；无法定位就保留Q-3。DB-A/B失败后不向应用伪造成功，G3-E/U则另测调用者的不确定错误处理，二者证据不能互换。

| Obligation | A§9边界 | 同次公开提交必须共同观察的事实 | 成功/重入组合用例 |
| --- | --- | --- | --- |
| AC01 | 首次创建 | State+完整恢复/装配描述 | TC-U2-01、TC-U2-13 |
| AC02 | 新Step请求 | StepStarted+ModelRequested+Step/attempt+请求计数 | TC-U14-01 |
| AC03 | 同Step新请求 | ModelRequested+新attempt+计数+原retry分支，Step不增 | TC-U14-02、TC-U14-07 |
| AC04 | 接受响应 | 消息+usage+关闭attempt+处置/phase | TC-U14-03、TC-U7-05、TC-U7-06 |
| AC05 | 工具开始 | 开始event+attempt/Call+tool_calls | TC-U6-01、TC-U6-05 |
| AC06 | helper放行 | helper身份+attempt+放行意图 | TC-U11-01、TC-U11-04 |
| AC07 | 业务句柄/start | exec/group身份+operation+start意图 | TC-U11-05、TC-U11-15 |
| AC08 | 工具完成 | 完整result+消息+next_index+收尾+清operation | TC-U6-02～04、TC-U7-02 |
| AC09 | Step完成 | StepCompleted+整批判定+失败数+清batch/current_step+下phase | TC-U7-03、TC-U7-04、TC-U7-08 |
| AC10 | 候选patch提交 | submission/candidate+ref/hash/baseline+关闭export attempt | TC-U15-01、TC-U15-04 |
| AC11 | 评估attempt开始 | attempt/harness/目录/归属+patch+operation | TC-U16-01、TC-U16-02 |
| AC12 | 评估结果 | 完整result/output/refs+关闭attempt+终止处置 | TC-U16-07～09 |
| AC13 | 终态 | EvaluationCompleted（适用）+RunFinished+终态State/Snapshot | TC-U7-07、TC-U16-07～09 |
| AC14 | 暂停 | RunSuspended+原phase/元数据+pending失效/清空+Snapshot | TC-U3-04、TC-U19-02 |
| AC15 | 恢复开始 | Started+RECOVERING+来源/attempt+pending失效+Snapshot | TC-U4-03～04、TC-U4-14、TC-U4-16 |
| AC16 | 恢复完成 | Completed+目标status/原phase+Snapshot | TC-U4-07～08、TC-U4-15、TC-U4-17 |
| AC17 | diagnostic | 事件身份/许可/结果/refs只追加，业务State/Snapshot不变 | TC-U17-01、TC-U17-03～05 |
| AC18 | compaction发布（A§5补充） | CompactionCompleted+context/version+source+Snapshot | TC-U14-05、TC-U14-06、TC-U14-11 |

文件/远端副作用不随这些事务回滚。AC10 的 artifact bytes 可以已写但引用未确认，AC05/07 的 shell 可以已修改现场，均由对应不确定性规则处置，不声称跨存储exactly-once。

### 4.3 原验收到具体用例

每条原 R ID 原样保留；多个用例分别承担规则/路径与真实后盾。

| Source | Test cases | Source | Test cases |
| --- | --- | --- | --- |
| R01 | TC-U2-01、TC-U2-13、TC-U4-01 | R21 | TC-U14-04～07、TC-U14-11、TC-U8-14～16 |
| R02 | TC-U14-01～03、TC-U14-07～09 | R22 | TC-U18-01～07、TC-U18-10 |
| R03 | TC-U6-01、TC-U7-01、TC-U8-03 | R23 | TC-U1-01～14 |
| R04 | TC-U6-02、TC-U7-02、TC-U8-04～05、TC-U21-01～02 | R24 | TC-U6-03～04、TC-U19-04 |
| R05 | TC-U6-05～07、TC-U6-11、TC-U9-08、TC-U18-10 | R25 | TC-U1-11、TC-U13-01～06、TC-U13-10～11、TC-U21-05～06 |
| R06 | TC-U6-08、TC-U6-10 | R26 | TC-U8-14～16、TC-U14-09～10、TC-U21-03 |
| R07 | TC-U5-05、TC-U5-11、TC-U5-14、TC-U7-03～04、TC-U8-06 | R27 | TC-U13-07～10、TC-U21-04～06 |
| R08 | TC-U5-09、TC-U7-06、TC-U15-01 | R28 | TC-U15-02、TC-U15-08～10、TC-U21-04 |
| R09 | TC-U7-05、TC-U7-08、TC-U15-06～07 | R29 | TC-U15-03～07 |
| R10 | TC-U3-04、TC-U9-01～08 | R30 | TC-U16-01～02、TC-U16-06、TC-U16-10、TC-U21-04 |
| R11 | TC-U18-08、TC-U20-01～07 | R31 | TC-U16-03～06、TC-U16-12 |
| R12 | TC-U3-06～08、TC-U4-01～17、TC-U14-10、TC-U21-03 | R32 | TC-U7-07、TC-U16-07～10 |
| R13 | TC-U3-01～05、TC-U19-01～02、TC-U19-04～08、TC-U19-13 | R33 | TC-U2-02～10、TC-U3-09～11、TC-U5-02～14、TC-U16-11、TC-U18-13 |
| R14 | TC-U6-05～06、TC-U17-02、TC-U19-03、TC-U19-06 | R34 | TC-U4-05～09、TC-U4-14～17、TC-U17-02～04、TC-U17-07～08、TC-U19-09～13 |
| R15 | TC-U12-01～08 | R35 | TC-U15-02、TC-U15-11、TC-U17-01 |
| R16 | TC-U4-05、TC-U12-09～10、TC-U21-02 | R36 | TC-U11-06～10、TC-U11-16、TC-U17-06 |
| R17 | TC-U12-11～17、TC-U21-05 | R37 | TC-U11-01～05、TC-U11-11～15 |
| R18 | TC-U12-09、TC-U21-02 | R38 | TC-U17-01、TC-U17-03～06、TC-U19-05 |
| R19 | TC-U6-09～10、TC-U10-01～05、TC-U11-08～09、TC-U20-04 | R39 | TC-U2-11～12 |
| R20 | TC-U8-09～13、TC-U7-04 | — | — |

TA追踪（未改变原分析义务）：TA-01→U2/U4/U5；TA-02→U20；TA-03→U6；TA-04→U4；TA-05→U6/U7；TA-06→U5/U7/U8；TA-07→U6；TA-08→U10；TA-09/10→U11；TA-11→U7；TA-12→U3/U5；TA-13→U19；TA-14→U3/U17；TA-15/16→U12；TA-17→U13/U21；TA-18→U4/U9；TA-19→U13；TA-20→U8/U14；TA-21→U15；TA-22/23→U16/U21；TA-24→U4/U17；TA-25→U18；TA-26→U1；TA-27→U21；TA-28→U18；TA-29→U2；TA-30→U14/U21。

### 4.4 执行时的公共观察与残余边界

- 每个跨进程路径逐步核对同 Run/Workspace/task/定义、原消息内容/顺序（system/user/assistant/tool 均不重复）、已确认结果/Step唯一性、phase/cursor、Usage/unknown、审批版本、sequence/Snapshot单调。必须同时读外部副作用/RPC/harness日志，不能仅数事件。
- 进程/容器事实区分 host helper、host harness、remote exec 与 guest process group。先排除旧 helper/未决start，再判断remote组；无证据时busy，不把filelock释放或exec Running=false当收尾。
- U10～12/U20/U21的真边界必须留下环境/版本/镜像/daemon/数据身份。受控inspect/Engine/harness仅为已标注规则分支提供证据，不能替代真实daemon接回和至少一项真实development task恢复。
- 时间边界用受控clock判断是否刷新budget；墙钟后盾需先落实Q-2。存储/observer屏障须按Q-3落实。真实task/P_unresolved/expected证据需按Q-4冻结。没有可靠命中窗口/前缀或独立结果oracle即未验证，不根据最终结果反推命中或生成expected。
- 不保证脱组daemon、手动并发修改、任意shell exactly-once、网络/内存恢复、跨主机灾备；身份校验不等于历史内容一致性证明。无checkpoint/回滚/终态repair/attempt-limit，不新增这些测试的成功路径。
- 人工评审 charter：使用 TC-U18-02/10/12 与 TC-U19-05 的输出，确认“待live checks”“可能计费的上界”“原现场保留但拒绝自动续跑”“终态finalization失败不可resume”不会误导用户；保留原输出与发现，不凭主观文案评分认证恢复安全。

本次交付为 review-ready Markdown，不是自动化代码、测试管理工具导入文件、实现计划或 CI gate。测试执行/门禁/证据保留与新鲜度另行安排；需求追踪需另记用例实现和执行结果，不能把上述设计分母当作通过数量。

Verification：核对审计F1–F5、两份规格及SQLite/compaction的相关现有边界；检查原TC/R/TA ID保留、新增ID续号、表格/链接/追踪与提交组合矩阵。仅修订测试文档，未运行pytest、Docker、SWE-bench、Ruff或类型检查；设计修正不等于执行验证。

### 4.5 Review修订追踪

此表记录审计发现的设计修正及能否排除原假通过/假失败，不替代审计重跑或测试执行。原审计NOT-ADEQUATE结论作为历史保留；本文没有自行升级为ADEQUATE。

| Audit | Confirmation design（优先修正原反例） | Technique / neighbouring checks | Trace / remaining obligation |
| --- | --- | --- | --- |
| F1 | TC-U4-11的故障必须命中新审批写后前缀；TC-U4-12必须命中全新State/Snapshot/审批/event前缀；重开均全旧。只会早期event回滚、后续autocommit的缺陷不能通过 | 用例路径P15～17；同形成功控制TC-U4-13；创建入口TC-U2-13/01独立；AC01～18只验证业务提交组合 | TA-01/04/18/24；DB触发点/schema版本与Q-3待执行落实，不再重复18个早期错误 |
| F2 | TC-U4-14/15让存活调用者收到已应用但确认不明的控制错误，自行退出且零后继发令；捕获后继续attach/dispatch/诊断的缺陷会失败 | 用例路径P11～14；未下发提交TC-U4-16/17；硬杀重入TC-U4-04/08保留，与停机错误出口分开 | TA-04；G3-E/U须等待调用者实际退出；监督kill不能计通过 |
| F3 | TC-U21-01固定SUCCEEDED/TASK_VERIFIED/exit0/output；TC-U21-04固定valid unresolved/FAILED/EVALUATION_FAILED/TASK_UNRESOLVED/exit1。总是失败或返回任意infra error不再通过 | 关键旅程P1/P4，双daemon/TLS P5/P6复用同一固定候选结果；不新增真实模型随机测试 | TA-22/23；Q-4缺失则DEFER，不允许现场反向决定expected |
| F4 | TC-U8-14/15必须分别2/1笔新summary后发布成功并继续；TC-U8-16必须0笔且原失败。清零或刷新额度都不能通过 | 原3-value retry边界不变；TC-U14-07精确唯一业务retry及其context-limit失败变体 | TA-20；固定source/容量/强制分支，失败设置不能换软压缩路径 |
| F5 | TC-U20-02必须有持锁握手和loser_attempt/result均在winner_release前的时间线；不以同时发起推断重叠 | 竞争路径P2；复用P1/P3的持锁监督；赢家可为任一进程，取锁本身仍真实竞争 | TA-02；无重叠证明/超时kill不算锁冲突验证；跨OS环境仍Q-1 |
