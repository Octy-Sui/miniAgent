# Resume 测试策略审计

审计日期：2026-10-19。

范围：[Test Design](2026-10-14-resume-test-design.md) 与 [Test Analysis](2026-10-14-resume-test-analysis.md) 的证据组合，不是运行代码 review 或已执行 suite audit。依据为[需求 D](2026-10-14-resume-design.md)、[已确认架构 A](2026-10-14-resume-architecture.md)。

**Mode：Audit；Strategy pass：Combined；Decision：NOT-ADEQUATE。** 发现 2 项 P1、3 项 P2。主要问题是部分刺激不能观察它声称验证的失败，以及真实旅程没有固定结果 oracle；不是因文档阶段没有运行测试而判定不足。原分析的 PARTIAL 尚未反映这些证据路径问题。

## 1. Findings

### F1 · P1：重复 event ID 在过早位置失败，无法证明完整事务回滚

位置：[TC-U4-11](2026-10-14-resume-test-design.md#u4恢复编排的异常出口)（第 240 行）、§4.2（第 858 行）；关联 Analysis 第 60 行、TA-04/18/24。

TC-U4-11 对 AC01～AC18 都用“与既有 event ID 冲突”的注入，要求 State/Event/Approval/Snapshot 整体保持旧值。但当前可复用 SQLite 实现先插 events，再处理 approvals、snapshot，最后更新 Run（`src/miniagent/storage/sqlite.py:337`）；event_id 的 UNIQUE 冲突发生在 event 插入阶段。单事件提交可能完全未产生新写入；多事件提交最多验证先前事件插入的回滚。两者都没有触及后续审批、Snapshot、Run 更新。

因此，即使后续这些写入脱离事务、错误地分别提交，此刺激仍可能通过。“SQLite 约束失败而非提前输入校验”不能消除这个盲点。矩阵列出了必须共同观察的对象，但注入并未保证它们真的发生过待回滚修改。

建议：保留少量共享存储层真实事务故障测试，为发生了部分写入后的不同阶段提供可定位故障/进程终止，重开检查全旧或全新；审批/快照版本冲突可承担相应后段失败。再用各业务边界的成功/重入测试独立验证该边界确实把所需字段装入同一提交。不要将同一过早 UNIQUE 错误重复 18 次当作 18 种业务原子性证据。首次创建与已有 Run commit 的故障入口也应分别落实。

### F2 · P1：kill 路径不能证明应用收到提交不明错误后会停止

位置：Test Design 第 66 行 G3、第 233/237 行 TC-U4-04/08；关联 Analysis 第 77 行 TA-04；依据 A§7.1、§9。

G3 明确采用“阻塞/丢弃确认并 kill 原进程”。TC-U4-04 阻断返回，TC-U4-08 也在确认丢失后 kill，再验证新进程恢复。这能证明持久化重读及硬中断恢复，但旧进程没有机会进入持久化错误处理。“旧进程没有继续业务/诊断”因 kill 自然成立。

如果实现错误地捕获提交不明异常，然后 attach/start、dispatch 或 diagnostic export，上述 kill 用例仍可能通过。TC-U10-05/TC-U16-12 的 observer 错误传播针对其他业务作用域，不能代替 ResumeStarted/Completed 两个特定安全边界。

建议：将确认丢失拆为两个明确变体。一个真实 commit 后 kill，验证下次重读；另一个真实 commit 成功后由公开委托边界向仍存活的调用者返回明确的持久化不确定错误，不先 kill，让命令自行退出。分别在 Started 与 Completed 后断言禁止动作、资源保留、最后确认 sequence。对“不写成但调用方也不能确定”的变体沿用同一停机规则。

### F3 · P2：真实旅程接受任意终态，缺少预先确定的业务结果

位置：Test Design 第 817 行 TC-U21-01、第 820 行 TC-U21-04；关联 Analysis 第 95～96 行 TA-22/23、第 135 行真实后盾。

Local 旅程允许“成功交付或明确失败链”；真实 SWE-bench 旅程只要求实际 report/refs/result 进入终态，未给 P 的预期通过/未解决结果、对应终止原因或 CLI 结果。一个保留资源身份、生成可信失败报告但永远失败的恢复装配，可以满足这些文字条件，无法证明固定候选恢复后的业务结果未改变。

建议：为这两条正向组合旅程固定确定性 provider 序列与预期结果。Local 使用能够正常完成的固定输入，断言具体成功结果/退出码；SWE-bench 至少选择一个预先确认结果的非空候选，固定 expected pass 或 expected unresolved、终态/原因与证据关联。不要求增加另一整套真实成功/失败矩阵；非目标基础设施故障应记失败或环境阻塞，不纳入用例通过分支。

### F4 · P2：retry 仅断言上界，提前放弃也会通过

位置：Test Design 第 378～380 行 TC-U8-14～16、第 599 行 TC-U14-07；关联 Analysis 第 93 行 TA-20；依据 A§5、§13 的原分支/已耗预算保留契约。

“最多余下 2 次/1 次 retry”和“不刷新预算”可以检测超额请求，却不能检测恢复时错误地把剩余额度清零。零次 retry 后提前终止也满足这两个上界。TC-U14-07 的“按原余额”没有补充具体输入序列和必需的成功结果；未确认请求重发用例也不是已确认 retryable failure 后继续的同一条件。

建议：为尚有额度的两个边界给定 retryable failure 后的固定成功 summary，断言实际必须发生的请求数、原 Step/分支及最终发布/继续结果；用尽额度的用例断言零额外 retry 和原失败。这样同时检验不多用、也不少用。冻结 retry 规则沿用既有行为，不新增产品预算。

### F5 · P2：同时释放竞争进程不能保证锁持有区间重叠

位置：Test Design 第 794 行 TC-U20-02；关联 Analysis 第 75 行 TA-02、第 162 行同步约定。

“两真进程同屏障同时 resume”只同步发起时刻。调度可能使 A 完成并释放锁后，B 才实际取锁；正确实现此时也不会返回 RUN_IN_USE，可能按更新后的状态拒绝或继续合法恢复。用例却无条件要求一个立即冲突，容易形成假失败。

建议：竞争开始后，让获得锁的一方在持锁区间内向监督进程确认并阻塞；收到另一方的明确冲突结果后才释放赢家。保留双方取锁/释放时间线，证明失败进程的实际尝试位于持有区间。TC-U20-01/03 已有“持锁期间尝试”的语义，可复用相同公开握手，避免另造竞速夹具。

## 2. 风险到证据的审计记录

以下按本轮审计的实质证据义务逐行记录；Evidence 是静态发现与修正后需要取得的证据，不是已执行结果。

| Risk / criterion | Failure mode | Oracle | Technique | Scope / fidelity | Dependencies | Data / environment | Evidence | Residual blind spot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F1；TA-01/04/18/24；A§9 | 只检验早期事件插入失败，后续对象非原子写入漏检 | 确认已有待回滚修改；重开后全旧或全新；业务提交字段配套 | 真实事务故障＋边界重入 | 共享 SQLite 机制与具体 Journal/业务提交分开验证 | 真 SQLite/RunCommit/审批/Snapshot | 隔离库；事件后、审批后、快照后的可定位故障；创建单独处理 | 当前插入顺序证实早期失败盲点；需后段失败及业务组合证据 | 不承诺文件/远端原子性；注入实现需落实 |
| F2；TA-04；A§7.1/9 | 应用收到不确定持久化错误后仍发令 | 存活调用者自行停止，零 attach 变更/dispatch/exec 诊断；下次读确认事实 | 确认丢失与 kill 两类故障 | Resume 编排到公开存储边界，实际副作用记录 | 真 SQLite、公开委托适配器、真实进程 | Started/Completed 已写无确认；另列未写但无法确定变体 | G3 kill 缺少错误处理观察；需存活进程错误出口记录 | kill 仍用于硬中断恢复，不能删除其独立后盾 |
| F3；TA-22/23；D§14 | 真实装配恢复总是失败却当验收通过 | 固定输入的已知结果/终态/原因/退出码及原 patch 关联 | 确定性关键旅程 | Local 真链路；实际 development task+harness | Git/SQLite/Artifact/daemon/harness，provider 受控 | 一个已确认结果的非空 P；固定 provider 序列 | 当前结果分支宽松；需具体 expected 与实测对照 | 不证明模型生成修复能力；真实环境仍 Q-1 |
| F4；TA-20；A§5/13 | 恢复时清零剩余 retry，过早失败 | 给定失败→成功序列，精确请求数及发布结果；额度尽后零额外 retry | 边界＋状态路径 | Runtime/compaction+真实存储，最小确定性范围 | 协议 provider、真实 context/Journal | K=2，used=0/1/2，各独立固定响应序列 | 当前只设上界；需拒绝“无 retry”突变的正向 oracle | 不新增预算或真实账单断言 |
| F5；TA-02；D9 | 合法非重叠调度被报告为锁故障 | 失败进程取锁时赢家已确认持锁且尚未释放 | 有序并发握手 | 真双进程/flock/原应用作用域 | POSIX/SQLite/管道 | 屏障位于取锁之后，赢家等待输家结果 | 当前同步发起不足；需持锁区间时间线 | 无 NFS/多主机支持；OS 环境仍 Q-1 |

## 3. 保留结论与充分性修正

R01～R39 都有映射条目，238 个 TC 定义的 ID/引用可解析；这些结构检查成立。它们不能修复上述 oracle/刺激缺陷，有限模型的 16/16、7/7 等设计分数不能单独支撑义务已有可靠证据路径。

保留的策略选择：未知 shell fail closed、已确认调用不重放、原 Workspace 身份、helper 与 remote group 分别确认、candidate/diagnostic 分离、已确认评估结果不重跑、只支持当前存储格式、真实 SQLite/Git/daemon 的最低保真度，以及不拿随机模型能力测试代替恢复测试。Q-1～Q-3 的环境/测量/实现缺口原文已披露，本轮不将它们重复列为新缺陷；未执行也不算文档设计的缺陷。

组合经济性：F1 的 18 个同类早期约束失败变体，以及 U11 中多条相同未授权窗口的零 RPC 断言，需按故障检出能力复核。建议先证明共享机制，再保留能捕捉不同业务装配错误的边界后盾；不能仅凭相同预期就删除真实 OS/daemon/评估组合测试。其余同 scope 重复未作全量等价证明，不建议本轮直接删用例。

```text
Subject:              Resume Test Analysis / Test Design 的证据有效性与可靠性
Mode:                 Audit
Strategy pass:        Combined
Decision:             NOT-ADEQUATE
Test basis:           D1–D10、R01–R39；TA-01～30；审计对象当前工作区版本
Architecture basis:   已确认 A§4–15、18；现有 SQLite 顺序仅用于检查 proposed injection 的保真度
Quality risks:        事务/提交不明假通过 > 真实旅程与retry弱oracle > 锁竞争假失败
Manual evaluation:    静态反例/突变推演；未执行人工场景，保留原CLI文案审读义务
Adequacy signals:     ID/R映射结构检查通过；5项刺激/oracle/同步问题；无执行或mutation通过证据
Handoffs:             测试设计修订 F1～F5；traceability 更新对应证据映射；执行环境落实既有Q项
Residual risk:        F1/F2关键路径证据不足；环境/测量/屏障仍部分DEFER；未接受剩余风险
Next action:          先修正TC-U4-11与G3/TC-U4-04/08，再更新受影响TA行和覆盖声明
Verification:         只读对照两份审计对象、需求/架构及SQLite/compaction选定代码；ID/映射检查
                      未运行pytest/Docker/harness，未修改被审计文档或运行代码
```

这是证据组合的审计结论，不是宣称 Resume 产品实现存在这五个运行缺陷。修订应优先改变刺激和可证伪判据，不靠增加用例数量或扩大覆盖百分比解决。
