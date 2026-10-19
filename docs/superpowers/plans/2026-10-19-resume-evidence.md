# Resume execution evidence — 2026-10-19

**Local + Docker完整支持尚未验收。** 实现位于 `feat/resume`；本记录不将环境缺失、跳过或测试设计当成验收通过。原项目未合并。

## Executed evidence

- macOS host / Python 3.13.14；Local 跨进程组：18 passed（80.23s），包括固定 B3、四次恢复、RECOVERING SIGKILL、真实 flock 区间、G0/G1/G2/G3-K、F2 E/U、未知 shell 拒绝、确认 ERROR/TIMEOUT、真实 Console SIGINT、30s 默认共享预算及预校准 ε。日志 `/private/tmp/resume-28-green.log`（扩展回归）和 task-28 completion 日志；最终全量 node 结果见下述 XML。
- 独立文件 oracle：c0/c1/c2 各一次；原 Git index hash、Run UUID/created_at 不变；2 confirmed requests、0 unknown、140/30 tokens、$0.00020；duration 排除恢复间暂停。
- Docker Engine 29.8.2，daemon `9fda18c4-1d24-4886-991b-04ad37640350`，预置镜像 `sha256:18fa05d1fc555eb673f798a17cfc1a32b86b6cd9f9a56c64b7840cd5e35e7c10`；6 个真实 daemon variants passed（14.16s）。原完整容器 ID running/exited 接回、workdir 外依赖层、CLI 无 create/pull、helper/exec/guest 出生身份、已死 supervisor 的活 guest 组、删除/错误 ID 拒绝。
- 精确 start/permission 许可窗口与父进程死亡另用外部 Engine 记录验证；其结果不是实际网络延迟或多 daemon 的替代。
- 独立 unresolved manifest：11 个解析用例 passed；真实 SWE-bench journey、三 daemon、verified TLS 共 3 项 DEFER。用户明确答复没有预先确认的 unresolved manifest。
- 原 compaction acceptance runner 在公共 phase driver 中补传既有审批依赖；真实 pipe 输入回归通过。

## Remaining acceptance gaps

| Scope | Status | Reason / entry |
| --- | --- | --- |
| Linux host Local/locks/signals | DEFER | 本次为 macOS host；Linux guest 不等价 |
| Full Docker Application + SWE-bench resume | DEFER | `MINIAGENT_RESUME_UNRESOLVED_MANIFEST`；独立 prior fixture 未提供。入口已有，但该可选入口未在真实 fixture 上执行，不能宣称已验证 |
| Dual daemon A/B + ambient C | DEFER | `MINIAGENT_RESUME_CONNECTIONS_MANIFEST`；实际三 daemon 不可用 |
| Verified TLS CLI/SDK | DEFER | `MINIAGENT_RESUME_TLS_MANIFEST` + `MINIAGENT_RESUME_TLS_IMAGE`；实际 TLS 环境不可用 |
| Real daemon delayed network start | DEFER | 组件 Engine 窗口通过，未在实际 daemon 制造相同网络延迟 |
| Docker restarting/removing/dead | COMPONENT ONLY | 真实稳定复现不可用；组件状态分支覆盖 |
| OS/kernel-stuck storage exit bound | DEFER | SQLite worker 必须 drain 后释放锁；30s 测量只覆盖合作式操作/清理/诊断受控等待 |

## Reproduction and artifacts

```sh
MINIAGENT_RESUME_DOCKER_IMAGE=sha256:18fa05d1fc555eb673f798a17cfc1a32b86b6cd9f9a56c64b7840cd5e35e7c10 uv --cache-dir /private/tmp/miniagent-uv-cache run pytest -q --junitxml=/private/tmp/miniagent-resume-final.xml
```

测试自己的临时目录内保留 environment.json、timing-calibration.json、deadline-measurement.json、duration-oracle.json、barriers-*.json、commits.jsonl、wire.jsonl、result-*.json、lock-*.json、origin.json、真实 SQLite、docker-environment.json、docker-attachment.json、docker-cli.jsonl、permissions.jsonl、live-guest.json、guest-inspection.json。失败时仅清理有所有权证明的测试资源。JUnit 的 classname/name 标识具体 node/variant；`/private/tmp/miniagent-resume-final.log` 为最终控制台结果。临时证据不随 git 提交；重跑会生成新目录。

## F1–F5 / atomic commits

| Obligation | Observable evidence |
| --- | --- |
| F1 prefix crash | StoreBoundary after actual committed sequence; independent SQLite read; new OS process reopens |
| F2 live caller stop | applied_then_error / not_dispatched_then_error exits itself; no provider wire/diagnostic/next effect; Started/Completed failures in CLI suite |
| F3 fixed result | Local fixed successful B3 PASS; SWE-bench fixed valid unresolved DEFER |
| F4 exact balances | compaction resume tests K=2/1/0, business retry not refreshed; fixed Local usage/cost |
| F5 lock interval | winner approval/finalization ready < loser attempt < full RUN_IN_USE result < release permission; no supervisor kill used as PASS |

AC 同笔断言应检查公开 store 提交后的 State/Event/Snapshot/Approval，而非只看最后状态。下列对应沿用绑定 TD，不用最终状态相等替代。

| Atomic commit | TD cases |
| --- | --- |
| AC01 | TC-U2-01、TC-U2-13 |
| AC02 | TC-U14-01 |
| AC03 | TC-U14-02、TC-U14-07 |
| AC04 | TC-U14-03、TC-U7-05、TC-U7-06 |
| AC05 | TC-U6-01、TC-U6-05 |
| AC06 | TC-U11-01、TC-U11-04 |
| AC07 | TC-U11-05、TC-U11-15 |
| AC08 | TC-U6-02～04、TC-U7-02 |
| AC09 | TC-U7-03、TC-U7-04、TC-U7-08 |
| AC10 | TC-U15-01、TC-U15-04 |
| AC11 | TC-U16-01、TC-U16-02 |
| AC12 | TC-U16-07～09 |
| AC13 | TC-U7-07、TC-U16-07～09 |
| AC14 | TC-U3-04、TC-U19-02 |
| AC15 | TC-U4-03～04、TC-U4-14、TC-U4-16 |
| AC16 | TC-U4-07～08、TC-U4-15、TC-U4-17 |
| AC17 | TC-U17-01、TC-U17-03～05 |
| AC18 | TC-U14-05、TC-U14-06、TC-U14-11 |

## Source mapping

下面保留绑定 TD 的 R/TA→TC/U 映射；执行层沿下文 TC→组内 node→JUnit variant 追踪。该映射本身不计作通过。

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


## Traceability index

R01–R39、TA-01–30 按 TD §4.3 映射到 U1–U21。以下给出每个 TC 的实际测试组入口，再列组内 node；组内测试执行通过不自动证明 TC 的全部独立 variants。未逐 variant 核对的项保留 `variant audit pending`，因此本文件不是完整 AC/TC 审计通过声明。

| TC | Design behavior | Executable group | Variant evidence |
| --- | --- | --- | --- |
| TC-U1-01 | 规范 UUID | [U1](#u1) | variant audit pending |
| TC-U1-02 | 32 hex UUID | [U1](#u1) | variant audit pending |
| TC-U1-03 | 显示前缀 | [U1](#u1) | variant audit pending |
| TC-U1-04 | 前缀与 hex | [U1](#u1) | variant audit pending |
| TC-U1-05 | 拒绝缩写 | [U1](#u1) | variant audit pending |
| TC-U1-06 | 拒绝任意名称 | [U1](#u1) | variant audit pending |
| TC-U1-07 | 缺 ID | [U1](#u1) | variant audit pending |
| TC-U1-08 | 旧 task 入口 | [U1](#u1) | variant audit pending |
| TC-U1-09 | 显式 run | [U1](#u1) | variant audit pending |
| TC-U1-10 | 保留词 task | [U1](#u1) | variant audit pending |
| TC-U1-11 | 外部数据库定位 | [U1](#u1) | variant audit pending |
| TC-U1-12 | 不接受定义覆盖 | [U1](#u1) | variant audit pending |
| TC-U1-13 | help 全入口 | [U1](#u1) | variant audit pending |
| TC-U1-14 | 导入兼容 | [U1](#u1) | variant audit pending |
| TC-U2-01 | 首次完整记录 | [U2](#u2) | variant audit pending |
| TC-U2-02 | 当前库重开 | [U2](#u2) | variant audit pending |
| TC-U2-03 | 拒绝旧 DB | [U2](#u2) | variant audit pending |
| TC-U2-04 | 拒绝旧 Run | [U2](#u2) | variant audit pending |
| TC-U2-05 | 拒绝旧 Snapshot | [U2](#u2) | variant audit pending |
| TC-U2-06 | 拒绝旧 recovery | [U2](#u2) | variant audit pending |
| TC-U2-07 | 拒绝未来格式 | [U2](#u2) | variant audit pending |
| TC-U2-08 | 当前缺证据 | [U2](#u2) | variant audit pending |
| TC-U2-09 | 未知 kind | [U2](#u2) | variant audit pending |
| TC-U2-10 | 绑定摘要坏 | [U2](#u2) | variant audit pending |
| TC-U2-11 | 镜像缺失且允许拉取 | [U2](#u2) | variant audit pending |
| TC-U2-12 | 镜像缺失且禁止拉取 | [U2](#u2) | variant audit pending |
| TC-U2-13 | 首次创建写后失败 | [U2](#u2) | variant audit pending |
| TC-U3-01 | 创建后暂停 | [U3](#u3) | variant audit pending |
| TC-U3-02 | 准备中暂停 | [U3](#u3) | variant audit pending |
| TC-U3-03 | 运行中暂停 | [U3](#u3) | variant audit pending |
| TC-U3-04 | 审批中暂停 | [U3](#u3) | variant audit pending |
| TC-U3-05 | 评估中暂停 | [U3](#u3) | variant audit pending |
| TC-U3-06 | attach 前再暂停 | [U3](#u3) | variant audit pending |
| TC-U3-07 | 五种业务来源直接恢复 | [U3](#u3) | variant audit pending |
| TC-U3-08 | 恢复中硬杀再恢复 | [U3](#u3) | variant audit pending |
| TC-U3-09 | 不重开成功 | [U3](#u3) | variant audit pending |
| TC-U3-10 | 不重开失败 | [U3](#u3) | variant audit pending |
| TC-U3-11 | 不重开取消 | [U3](#u3) | variant audit pending |
| TC-U4-01 | 主路径使用最新记录 | [U4](#u4) | variant audit pending |
| TC-U4-02 | 预检不写入 | [U4](#u4) | variant audit pending |
| TC-U4-03 | Started 回滚 | [U4](#u4) | variant audit pending |
| TC-U4-04 | Started硬中断重入 | [U4](#u4) | variant audit pending |
| TC-U4-05 | 启动后 live check 拒绝 | [U4](#u4) | variant audit pending |
| TC-U4-06 | 装配缺引用 | [U4](#u4) | variant audit pending |
| TC-U4-07 | Completed 回滚 | [U4](#u4) | variant audit pending |
| TC-U4-08 | Completed硬中断重入 | [U4](#u4) | variant audit pending |
| TC-U4-09 | 失败记录再失败 | [U4](#u4) | variant audit pending |
| TC-U4-10 | 陈旧 Runtime 入口 | [U4](#u4) | variant audit pending |
| TC-U4-11 | 审批写后事务回滚 | [U4](#u4) | variant audit pending |
| TC-U4-12 | Run State写后整体回滚 | [U4](#u4) | variant audit pending |
| TC-U4-13 | 同形事务成功控制 | [U4](#u4) | variant audit pending |
| TC-U4-14 | Started已写但存活调用者停机 | [U4](#u4) | variant audit pending |
| TC-U4-15 | Completed已写但存活调用者停机 | [U4](#u4) | variant audit pending |
| TC-U4-16 | Started未写仍保守停机 | [U4](#u4) | variant audit pending |
| TC-U4-17 | Completed未写仍保守停机 | [U4](#u4) | variant audit pending |
| TC-U5-01 | 合法状态与较旧 Snapshot | [U5](#u5) | variant audit pending |
| TC-U5-02 | 游标投影冲突 | [U5](#u5) | variant audit pending |
| TC-U5-03 | 非运行游标 | [U5](#u5) | variant audit pending |
| TC-U5-04 | 缺 batch | [U5](#u5) | variant audit pending |
| TC-U5-05 | 批未完却收 Step | [U5](#u5) | variant audit pending |
| TC-U5-06 | 审批状态无 pending | [U5](#u5) | variant audit pending |
| TC-U5-07 | 暂停残留 pending | [U5](#u5) | variant audit pending |
| TC-U5-08 | 活动 Call 错位 | [U5](#u5) | variant audit pending |
| TC-U5-09 | 候选无原文 | [U5](#u5) | variant audit pending |
| TC-U5-10 | 终止决定缺失 | [U5](#u5) | variant audit pending |
| TC-U5-11 | current_step 不清理 | [U5](#u5) | variant audit pending |
| TC-U5-12 | 终态有执行恢复点 | [U5](#u5) | variant audit pending |
| TC-U5-13 | 来源丢失 | [U5](#u5) | variant audit pending |
| TC-U5-14 | 批结果关联不完整 | [U5](#u5) | variant audit pending |
| TC-U6-01 | 尚未执行 Call | [U6](#u6) | variant audit pending |
| TC-U6-02 | 已提交 SUCCESS | [U6](#u6) | variant audit pending |
| TC-U6-03 | 已提交 ERROR | [U6](#u6) | variant audit pending |
| TC-U6-04 | 已提交 TIMEOUT | [U6](#u6) | variant audit pending |
| TC-U6-05 | 开始后执行前 kill | [U6](#u6) | variant audit pending |
| TC-U6-06 | 副作用后结果前 kill | [U6](#u6) | variant audit pending |
| TC-U6-07 | 看似只读 shell/未知插件 | [U6](#u6) | variant audit pending |
| TC-U6-08 | 只读旧执行已结束 | [U6](#u6) | variant audit pending |
| TC-U6-09 | 只读旧执行活动 | [U6](#u6) | variant audit pending |
| TC-U6-10 | 只读身份未落库 | [U6](#u6) | variant audit pending |
| TC-U6-11 | 未知 shell 优先于新批准 | [U6](#u6) | variant audit pending |
| TC-U7-01 | 已接受 batch 直接执行 | [U7](#u7) | variant audit pending |
| TC-U7-02 | 批中间重入 | [U7](#u7) | variant audit pending |
| TC-U7-03 | 最后结果后收 Step | [U7](#u7) | variant audit pending |
| TC-U7-04 | 全批成功 | [U7](#u7) | variant audit pending |
| TC-U7-05 | 已提交失败响应 | [U7](#u7) | variant audit pending |
| TC-U7-06 | 候选接受后重入 | [U7](#u7) | variant audit pending |
| TC-U7-07 | 结果后只终止 | [U7](#u7) | variant audit pending |
| TC-U7-08 | 确定失败决定持久化 | [U7](#u7) | variant audit pending |
| TC-U8-01 | 游标 -2 | [U8](#u8) | variant audit pending |
| TC-U8-02 | 游标 -1 | [U8](#u8) | variant audit pending |
| TC-U8-03 | 游标 0 | [U8](#u8) | variant audit pending |
| TC-U8-04 | 游标 1 | [U8](#u8) | variant audit pending |
| TC-U8-05 | 游标 2 | [U8](#u8) | variant audit pending |
| TC-U8-06 | 游标 3 | [U8](#u8) | variant audit pending |
| TC-U8-07 | 游标 4 | [U8](#u8) | variant audit pending |
| TC-U8-08 | 游标 5 | [U8](#u8) | variant audit pending |
| TC-U8-09 | 尚无 Step | [U8](#u8) | variant audit pending |
| TC-U8-10 | 已用一 Step | [U8](#u8) | variant audit pending |
| TC-U8-11 | 额度前一位 | [U8](#u8) | variant audit pending |
| TC-U8-12 | 达到额度 | [U8](#u8) | variant audit pending |
| TC-U8-13 | guard 超额度健壮性 | [U8](#u8) | variant audit pending |
| TC-U8-14 | 两次余额均须使用 | [U8](#u8) | variant audit pending |
| TC-U8-15 | 最后一次余额必须重试 | [U8](#u8) | variant audit pending |
| TC-U8-16 | 用尽额度零额外请求 | [U8](#u8) | variant audit pending |
| TC-U9-01 | pending 失效再申请 | [U9](#u9) | variant audit pending |
| TC-U9-02 | 批准未执行重新授权 | [U9](#u9) | variant audit pending |
| TC-U9-03 | 已执行历史保持 | [U9](#u9) | variant audit pending |
| TC-U9-04 | 部分失效可重入 | [U9](#u9) | variant audit pending |
| TC-U9-05 | DENY 当前调用 | [U9](#u9) | variant audit pending |
| TC-U9-06 | APPROVAL 当前调用 | [U9](#u9) | variant audit pending |
| TC-U9-07 | 非交互不默认同意 | [U9](#u9) | variant audit pending |
| TC-U9-08 | 未知 shell 优先 | [U9](#u9) | variant audit pending |
| TC-U10-01 | 真组完整退出 | [U10](#u10) | variant audit pending |
| TC-U10-02 | 读取身份失败 | [U10](#u10) | variant audit pending |
| TC-U10-03 | 出生身份不匹配 | [U10](#u10) | variant audit pending |
| TC-U10-04 | 主进程退出子组仍活 | [U10](#u10) | variant audit pending |
| TC-U10-05 | observer 落库失败不包装 | [U10](#u10) | variant audit pending |
| TC-U11-01 | 门控主路径 | [U11](#u11) | variant audit pending |
| TC-U11-02 | 身份前 kill | [U11](#u11) | variant audit pending |
| TC-U11-03 | 等许可超时 | [U11](#u11) | variant audit pending |
| TC-U11-04 | 身份提交不明 | [U11](#u11) | variant audit pending |
| TC-U11-05 | start 意图未确认 | [U11](#u11) | variant audit pending |
| TC-U11-06 | 延迟 start 尚可能发生 | [U11](#u11) | variant audit pending |
| TC-U11-07 | helper 退出但 start 不明 | [U11](#u11) | variant audit pending |
| TC-U11-08 | supervisor 死子组仍活 | [U11](#u11) | variant audit pending |
| TC-U11-09 | exec 身份证据丢失 | [U11](#u11) | variant audit pending |
| TC-U11-10 | 暂停中回收 helper | [U11](#u11) | variant audit pending |
| TC-U11-11 | H0不发创建许可 | [U11](#u11) | variant audit pending |
| TC-U11-12 | H0不发start许可 | [U11](#u11) | variant audit pending |
| TC-U11-13 | H1不发创建许可 | [U11](#u11) | variant audit pending |
| TC-U11-14 | H1不发start许可 | [U11](#u11) | variant audit pending |
| TC-U11-15 | H2不提前start | [U11](#u11) | variant audit pending |
| TC-U11-16 | H4不重复start | [U11](#u11) | variant audit pending |
| TC-U12-01 | Local 原现场 | [U12](#u12) | variant audit pending |
| TC-U12-02 | HEAD 合法改变 | [U12](#u12) | variant audit pending |
| TC-U12-03 | 原目录消失 | [U12](#u12) | variant audit pending |
| TC-U12-04 | marker 消失 | [U12](#u12) | variant audit pending |
| TC-U12-05 | marker 错主 | [U12](#u12) | variant audit pending |
| TC-U12-06 | 规范路径越界 | [U12](#u12) | variant audit pending |
| TC-U12-07 | 链接替换 | [U12](#u12) | variant audit pending |
| TC-U12-08 | 基线丢失 | [U12](#u12) | variant audit pending |
| TC-U12-09 | running 容器原层 | [U12](#u12) | variant audit pending |
| TC-U12-10 | exited 原容器 | [U12](#u12) | variant audit pending |
| TC-U12-11 | 容器被删 | [U12](#u12) | variant audit pending |
| TC-U12-12 | 同名替换 | [U12](#u12) | variant audit pending |
| TC-U12-13 | labels 不符 | [U12](#u12) | variant audit pending |
| TC-U12-14 | 镜像身份不符 | [U12](#u12) | variant audit pending |
| TC-U12-15 | 执行配置不符 | [U12](#u12) | variant audit pending |
| TC-U12-16 | 不支持容器状态 | [U12](#u12) | variant audit pending |
| TC-U12-17 | daemon 身份变更 | [U12](#u12) | variant audit pending |
| TC-U13-01 | 默认漂移不改定义 | [U13](#u13) | variant audit pending |
| TC-U13-02 | 兼容 endpoint 变更审计 | [U13](#u13) | variant audit pending |
| TC-U13-03 | 模型协议不兼容 | [U13](#u13) | variant audit pending |
| TC-U13-04 | 容量资料缺失/不兼容 | [U13](#u13) | variant audit pending |
| TC-U13-05 | 工具指纹漂移 | [U13](#u13) | variant audit pending |
| TC-U13-06 | 缺执行环境引用 | [U13](#u13) | variant audit pending |
| TC-U13-07 | 评估定义不符 | [U13](#u13) | variant audit pending |
| TC-U13-08 | tag 移动但原内容在 | [U13](#u13) | variant audit pending |
| TC-U13-09 | 原镜像内容不可用 | [U13](#u13) | variant audit pending |
| TC-U13-10 | 不使用 ambient daemon | [U13](#u13) | variant audit pending |
| TC-U13-11 | 秘密只经当前引用 | [U13](#u13) | variant audit pending |
| TC-U14-01 | 请求开始前先落计数 | [U14](#u14) | variant audit pending |
| TC-U14-02 | 请求中断重发 | [U14](#u14) | variant audit pending |
| TC-U14-03 | 响应确认只加一次 | [U14](#u14) | variant audit pending |
| TC-U14-04 | summary 未确认 | [U14](#u14) | variant audit pending |
| TC-U14-05 | summary 响应有 Usage 未发布 | [U14](#u14) | variant audit pending |
| TC-U14-06 | summary 已发布 | [U14](#u14) | variant audit pending |
| TC-U14-07 | 强制分支完成原业务retry | [U14](#u14) | variant audit pending |
| TC-U14-08 | 无 Usage 不显示零 | [U14](#u14) | variant audit pending |
| TC-U14-09 | 关闭 unknown 后再中断 | [U14](#u14) | variant audit pending |
| TC-U14-10 | 新 Trace/活动时长 | [U14](#u14) | variant audit pending |
| TC-U14-11 | summary原source失效 | [U14](#u14) | variant audit pending |
| TC-U15-01 | 首次候选提交 | [U15](#u15) | variant audit pending |
| TC-U15-02 | 已注册只复用 | [U15](#u15) | variant audit pending |
| TC-U15-03 | export 未确认可重做 | [U15](#u15) | variant audit pending |
| TC-U15-04 | put 成功引用未提交 | [U15](#u15) | variant audit pending |
| TC-U15-05 | 旧 export 残留 | [U15](#u15) | variant audit pending |
| TC-U15-06 | 明确 empty | [U15](#u15) | variant audit pending |
| TC-U15-07 | 明确 failed | [U15](#u15) | variant audit pending |
| TC-U15-08 | 已注册 patch 丢失 | [U15](#u15) | variant audit pending |
| TC-U15-09 | 已注册 hash 损坏 | [U15](#u15) | variant audit pending |
| TC-U15-10 | 已注册错 ownership | [U15](#u15) | variant audit pending |
| TC-U15-11 | 候选/诊断不混用 | [U15](#u15) | variant audit pending |
| TC-U16-01 | 评估首次开始 | [U16](#u16) | variant audit pending |
| TC-U16-02 | 旧评估确认退出再重评 | [U16](#u16) | variant audit pending |
| TC-U16-03 | host 组未结束 | [U16](#u16) | variant audit pending |
| TC-U16-04 | host 身份未确认 | [U16](#u16) | variant audit pending |
| TC-U16-05 | 旧评估清理不确定 | [U16](#u16) | variant audit pending |
| TC-U16-06 | 严格归属保护邻居 | [U16](#u16) | variant audit pending |
| TC-U16-07 | 通过结果后只终态 | [U16](#u16) | variant audit pending |
| TC-U16-08 | 失败结果后只终态 | [U16](#u16) | variant audit pending |
| TC-U16-09 | 错误结果后只终态 | [U16](#u16) | variant audit pending |
| TC-U16-10 | 孤立报告非结果 | [U16](#u16) | variant audit pending |
| TC-U16-11 | 终态不重评 | [U16](#u16) | variant audit pending |
| TC-U16-12 | 控制异常穿过 evaluator | [U16](#u16) | variant audit pending |
| TC-U17-01 | 诊断只追加事件 | [U17](#u17) | variant audit pending |
| TC-U17-02 | 未收尾跳过诊断 | [U17](#u17) | variant audit pending |
| TC-U17-03 | 开始提交失败 | [U17](#u17) | variant audit pending |
| TC-U17-04 | 身份许可提交不明 | [U17](#u17) | variant audit pending |
| TC-U17-05 | 终态诊断窗口硬杀 | [U17](#u17) | variant audit pending |
| TC-U17-06 | 暂停诊断残留阻塞恢复 | [U17](#u17) | variant audit pending |
| TC-U17-07 | 暂停诊断失败不终止任务 | [U17](#u17) | variant audit pending |
| TC-U17-08 | 成功交付失败保留现场 | [U17](#u17) | variant audit pending |
| TC-U17-09 | 成功交付后销毁 | [U17](#u17) | variant audit pending |
| TC-U18-01 | 安全 Local 只读 | [U18](#u18) | variant audit pending |
| TC-U18-02 | stopped Docker 待检 | [U18](#u18) | variant audit pending |
| TC-U18-03 | 旧评估资源仅列清理 | [U18](#u18) | variant audit pending |
| TC-U18-04 | 确定拒绝 | [U18](#u18) | variant audit pending |
| TC-U18-05 | 缺库不初始化 | [U18](#u18) | variant audit pending |
| TC-U18-06 | 旧库不切换 journal | [U18](#u18) | variant audit pending |
| TC-U18-07 | 缺 Docker 引用事实阻断 | [U18](#u18) | variant audit pending |
| TC-U18-08 | 锁冲突优先 | [U18](#u18) | variant audit pending |
| TC-U18-09 | 终态优先现场错 | [U18](#u18) | variant audit pending |
| TC-U18-10 | 未知 shell 优先依赖错 | [U18](#u18) | variant audit pending |
| TC-U18-11 | Workspace 阻断优先候选坏 | [U18](#u18) | variant audit pending |
| TC-U18-12 | 拒绝输出可定位不泄密 | [U18](#u18) | variant audit pending |
| TC-U18-13 | 当前库无此Run | [U18](#u18) | variant audit pending |
| TC-U19-01 | 首笔前信号 | [U19](#u19) | variant audit pending |
| TC-U19-02 | 非终态首信号 | [U19](#u19) | variant audit pending |
| TC-U19-03 | shell 无可信返回 | [U19](#u19) | variant audit pending |
| TC-U19-04 | 收尾已可信可先提交 | [U19](#u19) | variant audit pending |
| TC-U19-05 | 终态后信号 | [U19](#u19) | variant audit pending |
| TC-U19-06 | 第二信号强停 | [U19](#u19) | variant audit pending |
| TC-U19-07 | 库取消保持终态 | [U19](#u19) | variant audit pending |
| TC-U19-08 | 结果类型与暂停展示 | [U19](#u19) | variant audit pending |
| TC-U19-09 | 已超过 deadline | [U19](#u19) | variant audit pending |
| TC-U19-10 | 恰好 deadline | [U19](#u19) | variant audit pending |
| TC-U19-11 | 仅余1ms | [U19](#u19) | variant audit pending |
| TC-U19-12 | 仅余2ms | [U19](#u19) | variant audit pending |
| TC-U19-13 | 真默认共享30秒 | [U19](#u19) | variant audit pending |
| TC-U20-01 | run 对 resume | [U20](#u20) | variant audit pending |
| TC-U20-02 | 持锁区间内双resume竞争 | [U20](#u20) | variant audit pending |
| TC-U20-03 | 锁覆盖 finalization | [U20](#u20) | variant audit pending |
| TC-U20-04 | 锁 FD 不传给子进程 | [U20](#u20) | variant audit pending |
| TC-U20-05 | 规范键一致 | [U20](#u20) | variant audit pending |
| TC-U20-06 | 不同 Run 并行 | [U20](#u20) | variant audit pending |
| TC-U20-07 | 不 unlink 锁文件 | [U20](#u20) | variant audit pending |
| TC-U20-08 | 真 CAS 双提交 | [U20](#u20) | variant audit pending |
| TC-U21-01 | 两种POSIX宿主Local固定成功 | [U21](#u21) | variant audit pending |
| TC-U21-02 | Docker原层续跑 | [U21](#u21) | variant audit pending |
| TC-U21-03 | 连续四次恢复 | [U21](#u21) | variant audit pending |
| TC-U21-04 | 真SWE-bench固定unresolved恢复 | [U21](#u21) | DEFER: external fixture/environment |
| TC-U21-05 | 真双daemon绑定 | [U21](#u21) | DEFER: external fixture/environment |
| TC-U21-06 | 真实TLS链路 | [U21](#u21) | DEFER: external fixture/environment |

### U1

- [`tests/cli/test_resume_main.py`](../../../tests/cli/test_resume_main.py)
  - `tests/cli/test_resume_main.py::test_complete_uuid_forms_identify_same_run` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_short_or_prefixed_ids_are_parameter_errors` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_resume_refuses_frozen_overrides` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_real_entrypoint_help_has_independent_route` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_explicit_run_keeps_resume_as_task` (variants: JUnit XML)
- [`tests/scripts/test_trigger.py`](../../../tests/scripts/test_trigger.py)
  - `tests/scripts/test_trigger.py::test_trigger_passes_task_context_to_real_bootstrap` (variants: JUnit XML)
  - `tests/scripts/test_trigger.py::test_trigger_prepares_selected_development_task` (variants: JUnit XML)
  - `tests/scripts/test_trigger.py::test_trigger_overrides_default_model` (variants: JUnit XML)
  - `tests/scripts/test_trigger.py::test_trigger_rejects_invalid_selection_before_launching` (variants: JUnit XML)

### U2

- [`tests/cli/test_resume_bootstrap.py`](../../../tests/cli/test_resume_bootstrap.py)
  - `tests/cli/test_resume_bootstrap.py::test_missing_image_never_creates_resumable_run` (variants: JUnit XML)
  - `tests/cli/test_resume_bootstrap.py::test_first_record_contains_complete_definition_before_publication` (variants: JUnit XML)
  - `tests/cli/test_resume_bootstrap.py::test_create_after_insert_failure_cleans_only_new_resources` (variants: JUnit XML)
  - `tests/cli/test_resume_bootstrap.py::test_new_run_lock_is_acquired_before_workspace_creation` (variants: JUnit XML)
  - `tests/cli/test_resume_bootstrap.py::test_uncertain_create_preserves_new_workspace_without_publication` (variants: JUnit XML)
- [`tests/storage/test_resume_format.py`](../../../tests/storage/test_resume_format.py)
  - `tests/storage/test_resume_format.py::test_current_schema_round_trip` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_unsupported_format_is_untouched` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_read_only_missing_database_creates_nothing` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_read_only_open_preserves_database_and_journal_mode` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_read_only_rejects_all_mutations` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_unknown_nonempty_version_zero_database_is_not_initialized` (variants: JUnit XML)
  - `tests/storage/test_resume_format.py::test_unsupported_nested_snapshot_format_is_rejected` (variants: JUnit XML)

### U3

- [`tests/runtime/test_resume_state.py`](../../../tests/runtime/test_resume_state.py)
  - `tests/runtime/test_resume_state.py::test_resume_keeps_original_business_source` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_waiting_approval_pause_clears_pending` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_all_business_origins_survive_recovery` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_invalid_phase_projection` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_batch_cursor_boundaries` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_terminal_states_have_no_recovery_edges` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_summary_retry_uses_frozen_limit` (variants: JUnit XML)
- [`tests/runtime/test_interruption.py`](../../../tests/runtime/test_interruption.py)
  - `tests/runtime/test_interruption.py::test_shared_deadline_does_not_refresh` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_interruption_suspends_without_fabricated_tool_result` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_explicit_cancellation_remains_terminal` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_expired_interruption_never_starts_new_work` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_bare_cancelled_error_is_not_suspension` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_trusted_result_commits_before_pause_only_with_budget` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_pending_approval_is_invalidated_atomically_with_suspension` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_interruption_before_initial_record_creates_no_run` (variants: JUnit XML)

### U4

- [`tests/cli/test_resume.py`](../../../tests/cli/test_resume.py)
  - `tests/cli/test_resume.py::test_resume_uses_completed_commit_record` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_dry_run_needs_no_provider_key_or_new_directories` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_changed_endpoint_is_audited_without_secrets` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_dry_run_rejects_valid_frozen_older_tool_contract` (variants: JUnit XML)
- [`tests/cli/test_resume_failures.py`](../../../tests/cli/test_resume_failures.py)
  - `tests/cli/test_resume_failures.py::test_started_or_completed_uncertain_error_exits_without_more_work` (variants: JUnit XML)
  - `tests/cli/test_resume_failures.py::test_missing_key_after_started_keeps_recovering_and_records_failure` (variants: JUnit XML)
  - `tests/cli/test_resume_failures.py::test_killed_after_started_reenters_original_business_source` (variants: JUnit XML)
  - `tests/cli/test_resume_failures.py::test_resume_failed_commit_uncertainty_stops_without_diagnostics` (variants: JUnit XML)
  - `tests/cli/test_resume_failures.py::test_attachment_changes_after_started_retains_workspace_and_recovers_failure` (variants: JUnit XML)
- [`tests/storage/test_resume_transactions.py`](../../../tests/storage/test_resume_transactions.py)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_approval_write` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_run_update` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_success_control` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_tool_results_require_exact_business_scope` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_unconsumed_approval_uses_execution_history` (variants: JUnit XML)

### U5

- [`tests/runtime/test_resume_state.py`](../../../tests/runtime/test_resume_state.py)
  - `tests/runtime/test_resume_state.py::test_resume_keeps_original_business_source` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_waiting_approval_pause_clears_pending` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_all_business_origins_survive_recovery` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_invalid_phase_projection` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_batch_cursor_boundaries` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_terminal_states_have_no_recovery_edges` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_summary_retry_uses_frozen_limit` (variants: JUnit XML)
- [`tests/storage/test_resume_transactions.py`](../../../tests/storage/test_resume_transactions.py)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_approval_write` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_run_update` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_success_control` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_tool_results_require_exact_business_scope` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_unconsumed_approval_uses_execution_history` (variants: JUnit XML)

### U6

- [`tests/runtime/test_tool_batch_resume.py`](../../../tests/runtime/test_tool_batch_resume.py)
  - `tests/runtime/test_tool_batch_resume.py::test_batch_failure_uses_earlier_persisted_results` (variants: JUnit XML)
  - `tests/runtime/test_tool_batch_resume.py::test_finished_batch_at_step_limit_completes_without_new_request` (variants: JUnit XML)
  - `tests/runtime/test_tool_batch_resume.py::test_step_guard_limits_only_new_steps` (variants: JUnit XML)
- [`tests/runtime/test_resume_planner.py`](../../../tests/runtime/test_resume_planner.py)
  - `tests/runtime/test_resume_planner.py::test_unknown_tool_rejects_even_when_execution_ended` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_ended_retries_new_attempt` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_missing_identity_is_busy` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_resume_count_is_audit_only` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_rejection_priority_is_stable` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_pending_start_stays_busy_after_helper_exit` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_adjacent_priority_groups` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_active_or_unknown_group_rejects` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_state_cursor_requires_exact_committed_tool_prefix` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_guest_group_check_can_defer_only_after_known_helper_end` (variants: JUnit XML)

### U7

- [`tests/runtime/test_runtime_resume.py`](../../../tests/runtime/test_runtime_resume.py)
  - `tests/runtime/test_runtime_resume.py::test_resume_attaches_without_create_or_run_started` (variants: JUnit XML)
  - `tests/runtime/test_runtime_resume.py::test_resume_rejects_stale_or_recovering_record` (variants: JUnit XML)
  - `tests/runtime/test_runtime_resume.py::test_resume_dispatches_original_tool_and_complete_step_phases` (variants: JUnit XML)
  - `tests/runtime/test_runtime_resume.py::test_resume_restarts_uncommitted_evaluation_without_completing_step_twice` (variants: JUnit XML)
  - `tests/runtime/test_runtime_resume.py::test_resume_prepares_existing_created_record_without_run_started` (variants: JUnit XML)
  - `tests/runtime/test_runtime_resume.py::test_resume_terminates_from_complete_evaluation_result` (variants: JUnit XML)
- [`tests/runtime/test_request_resume.py`](../../../tests/runtime/test_request_resume.py)
  - `tests/runtime/test_request_resume.py::test_started_request_retries_same_step_and_counts_unknown_once` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_committed_candidate_dispatches_without_model_request` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_failure_disposition_with_nonempty_text_never_evaluates` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_confirmed_usage_preserves_prior_unknown_attempts` (variants: JUnit XML)
- [`tests/runtime/test_evaluation_resume.py`](../../../tests/runtime/test_evaluation_resume.py)
  - `tests/runtime/test_evaluation_resume.py::test_committed_evaluation_result_finishes_without_evaluator` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_evaluator_control_error_never_becomes_business_failure` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_evaluator_cannot_commit_result_with_unfinished_identified_execution` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_real_observer_commit_uncertainty_stops_evaluation` (variants: JUnit XML)

### U8

- [`tests/runtime/test_resume_state.py`](../../../tests/runtime/test_resume_state.py)
  - `tests/runtime/test_resume_state.py::test_resume_keeps_original_business_source` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_waiting_approval_pause_clears_pending` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_all_business_origins_survive_recovery` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_invalid_phase_projection` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_batch_cursor_boundaries` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_terminal_states_have_no_recovery_edges` (variants: JUnit XML)
  - `tests/runtime/test_resume_state.py::test_summary_retry_uses_frozen_limit` (variants: JUnit XML)
- [`tests/runtime/test_tool_batch_resume.py`](../../../tests/runtime/test_tool_batch_resume.py)
  - `tests/runtime/test_tool_batch_resume.py::test_batch_failure_uses_earlier_persisted_results` (variants: JUnit XML)
  - `tests/runtime/test_tool_batch_resume.py::test_finished_batch_at_step_limit_completes_without_new_request` (variants: JUnit XML)
  - `tests/runtime/test_tool_batch_resume.py::test_step_guard_limits_only_new_steps` (variants: JUnit XML)
- [`tests/runtime/test_compaction_resume.py`](../../../tests/runtime/test_compaction_resume.py)
  - `tests/runtime/test_compaction_resume.py::test_summary_retry_balance_is_exact` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_business_context_retry_is_not_refreshed` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_published_summary_and_business_retry_survive_reopen` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_summary_resume_keeps_original_source_and_prefix_with_new_estimator` (variants: JUnit XML)

### U9

- [`tests/runtime/test_approval_resume.py`](../../../tests/runtime/test_approval_resume.py)
  - `tests/runtime/test_approval_resume.py::test_old_approval_is_not_reused_for_current_policy` (variants: JUnit XML)
- [`tests/storage/test_resume_transactions.py`](../../../tests/storage/test_resume_transactions.py)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_approval_write` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_run_update` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_success_control` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_tool_results_require_exact_business_scope` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_unconsumed_approval_uses_execution_history` (variants: JUnit XML)

### U10

- [`tests/environments/test_managed_process.py`](../../../tests/environments/test_managed_process.py)
  - `tests/environments/test_managed_process.py::test_command_completion_requires_entire_group_end` (variants: JUnit XML)
  - `tests/environments/test_managed_process.py::test_reused_pid_identity_does_not_kill_neighbor` (variants: JUnit XML)
  - `tests/environments/test_managed_process.py::test_identity_unreadable_is_unknown` (variants: JUnit XML)
  - `tests/environments/test_managed_process.py::test_identity_commit_failure_reaps_owned_group_and_propagates` (variants: JUnit XML)
  - `tests/environments/test_managed_process.py::test_live_child_with_closed_pipes_prevents_trusted_tool_completion` (variants: JUnit XML)

### U11

- [`tests/environments/test_docker_exec_helper.py`](../../../tests/environments/test_docker_exec_helper.py)
  - `tests/environments/test_docker_exec_helper.py::test_exec_rpcs_require_confirmed_identity_permissions` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_uncertain_identity_commit_never_releases_permission` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_start_permission_without_started_proof_is_unknown` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_helper_without_valid_create_permission_sends_no_rpc` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_inspection_never_claims_group_end_from_exec_state` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_identity_commit_applied_and_unapplied_both_stop_live_helper` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_permitted_delayed_start_stays_unknown_after_helper_exit` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_helper_parent_death_before_permission_sends_no_rpc` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_expired_command_budget_issues_no_permission` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_helper_output_limit_is_preserved` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_docker_exec_completes_only_after_helper_exec_and_group_end` (variants: JUnit XML)
  - `tests/environments/test_docker_exec_helper.py::test_interruption_between_start_commit_and_permission_sends_no_start_rpc` (variants: JUnit XML)
- [`tests/environments/test_docker_supervisor.py`](../../../tests/environments/test_docker_supervisor.py)
  - `tests/environments/test_docker_supervisor.py::test_supervisor_separates_control_frames_from_command_output` (variants: JUnit XML)
  - `tests/environments/test_docker_supervisor.py::test_supervisor_exit_does_not_hide_live_group` (variants: JUnit XML)
  - `tests/environments/test_docker_supervisor.py::test_supervisor_streams_large_stdin_and_binary_output` (variants: JUnit XML)
  - `tests/environments/test_docker_supervisor.py::test_killed_supervisor_leaves_no_completed_evidence` (variants: JUnit XML)
  - `tests/environments/test_docker_supervisor.py::test_real_sdk_chain_preserves_binary_io_and_end_evidence` (variants: JUnit XML)
  - `tests/environments/test_docker_supervisor.py::test_guest_identity_commit_failure_never_releases_command` (variants: JUnit XML)
- [`tests/acceptance/resume/test_docker_faults.py`](../../../tests/acceptance/resume/test_docker_faults.py)
  - `tests/acceptance/resume/test_docker_faults.py::test_real_helper_and_guest_birth_identity_is_recorded` (variants: JUnit XML)
  - `tests/acceptance/resume/test_docker_faults.py::test_dead_supervisor_with_live_group_is_busy` (variants: JUnit XML)

### U12

- [`tests/environments/test_local_attach.py`](../../../tests/environments/test_local_attach.py)
  - `tests/environments/test_local_attach.py::test_attach_keeps_workspace_bytes_index_and_baseline` (variants: JUnit XML)
  - `tests/environments/test_local_attach.py::test_attach_rejects_changed_local_identity` (variants: JUnit XML)
  - `tests/environments/test_local_attach.py::test_attach_rechecks_after_inspection` (variants: JUnit XML)
- [`tests/environments/test_docker_attach.py`](../../../tests/environments/test_docker_attach.py)
  - `tests/environments/test_docker_attach.py::test_stopped_inspection_defers_git_without_start` (variants: JUnit XML)
  - `tests/environments/test_docker_attach.py::test_container_status_is_checked_without_repairs` (variants: JUnit XML)
  - `tests/environments/test_docker_attach.py::test_single_identity_or_security_mismatch_is_rejected` (variants: JUnit XML)
  - `tests/environments/test_docker_attach.py::test_attach_uses_full_original_id_and_frozen_connection` (variants: JUnit XML)
  - `tests/environments/test_docker_attach.py::test_real_running_and_exited_container_retains_original_layer` (variants: JUnit XML)
- [`tests/acceptance/resume/test_docker.py`](../../../tests/acceptance/resume/test_docker.py)
  - `tests/acceptance/resume/test_docker.py::test_same_container_layer_survives_resume` (variants: JUnit XML)
  - `tests/acceptance/resume/test_docker.py::test_deleted_or_replaced_container_is_rejected` (variants: JUnit XML)

### U13

- [`tests/cli/test_run_definition.py`](../../../tests/cli/test_run_definition.py)
  - `tests/cli/test_run_definition.py::test_frozen_definition_round_trip_and_digest` (variants: JUnit XML)
  - `tests/cli/test_run_definition.py::test_frozen_definition_rejects_scope_or_digest_mismatch` (variants: JUnit XML)
  - `tests/cli/test_run_definition.py::test_record_location_does_not_require_provider_credentials` (variants: JUnit XML)
  - `tests/cli/test_run_definition.py::test_attachment_requires_absolute_original_paths` (variants: JUnit XML)
  - `tests/cli/test_run_definition.py::test_provider_identity_cannot_persist_credentials` (variants: JUnit XML)
- [`tests/cli/test_resume.py`](../../../tests/cli/test_resume.py)
  - `tests/cli/test_resume.py::test_resume_uses_completed_commit_record` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_dry_run_needs_no_provider_key_or_new_directories` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_changed_endpoint_is_audited_without_secrets` (variants: JUnit XML)
  - `tests/cli/test_resume.py::test_dry_run_rejects_valid_frozen_older_tool_contract` (variants: JUnit XML)
- [`tests/environments/test_docker_connection.py`](../../../tests/environments/test_docker_connection.py)
  - `tests/environments/test_docker_connection.py::test_bound_connection_ignores_ambient_context` (variants: JUnit XML)
  - `tests/environments/test_docker_connection.py::test_bound_tls_reference_must_still_exist` (variants: JUnit XML)
  - `tests/environments/test_docker_connection.py::test_invalid_frozen_connection_is_rejected` (variants: JUnit XML)
- [`tests/acceptance/resume/test_docker_connections.py`](../../../tests/acceptance/resume/test_docker_connections.py)
  - `tests/acceptance/resume/test_docker_connections.py::test_workspace_and_evaluator_use_their_own_daemons` (variants: JUnit XML)
  - `tests/acceptance/resume/test_docker_connections.py::test_frozen_tls_connection_survives_resume` (variants: JUnit XML)

### U14

- [`tests/runtime/test_request_resume.py`](../../../tests/runtime/test_request_resume.py)
  - `tests/runtime/test_request_resume.py::test_started_request_retries_same_step_and_counts_unknown_once` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_committed_candidate_dispatches_without_model_request` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_failure_disposition_with_nonempty_text_never_evaluates` (variants: JUnit XML)
  - `tests/runtime/test_request_resume.py::test_confirmed_usage_preserves_prior_unknown_attempts` (variants: JUnit XML)
- [`tests/runtime/test_compaction_resume.py`](../../../tests/runtime/test_compaction_resume.py)
  - `tests/runtime/test_compaction_resume.py::test_summary_retry_balance_is_exact` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_business_context_retry_is_not_refreshed` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_published_summary_and_business_retry_survive_reopen` (variants: JUnit XML)
  - `tests/runtime/test_compaction_resume.py::test_summary_resume_keeps_original_source_and_prefix_with_new_estimator` (variants: JUnit XML)
- [`tests/acceptance/resume/test_local.py`](../../../tests/acceptance/resume/test_local.py)
  - `tests/acceptance/resume/test_local.py::test_local_fixed_success_after_sigint` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_four_resumes_preserve_origin_and_usage` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_unmatched_barrier_cannot_pass_as_interruption` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_default_signal_deadline_uses_one_precalibrated_budget` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_signal_while_real_console_stdin_is_open` (variants: JUnit XML)

### U15

- [`tests/cli/test_candidate_resume.py`](../../../tests/cli/test_candidate_resume.py)
  - `tests/cli/test_candidate_resume.py::test_registered_candidate_reuses_exact_artifact` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_registered_candidate_rejects_invalid_artifact` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_uncommitted_put_is_not_adopted` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_committed_empty_or_failed_submission_never_exports_again` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_candidate_and_diagnostic_results_have_independent_identities` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_controlled_index_rejects_business_index_and_escape` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_export_retry_requires_old_chain_end_and_new_index` (variants: JUnit XML)
  - `tests/cli/test_candidate_resume.py::test_trusted_timeout_keeps_file_change_and_verified_group_end` (variants: JUnit XML)

### U16

- [`tests/runtime/test_evaluation_resume.py`](../../../tests/runtime/test_evaluation_resume.py)
  - `tests/runtime/test_evaluation_resume.py::test_committed_evaluation_result_finishes_without_evaluator` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_evaluator_control_error_never_becomes_business_failure` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_evaluator_cannot_commit_result_with_unfinished_identified_execution` (variants: JUnit XML)
  - `tests/runtime/test_evaluation_resume.py::test_real_observer_commit_uncertainty_stops_evaluation` (variants: JUnit XML)
- [`tests/evaluation/test_swebench_resume.py`](../../../tests/evaluation/test_swebench_resume.py)
  - `tests/evaluation/test_swebench_resume.py::test_attempt_is_committed_before_directory_and_harness` (variants: JUnit XML)
  - `tests/evaluation/test_swebench_resume.py::test_attempt_commit_uncertainty_stops_before_directory` (variants: JUnit XML)
  - `tests/evaluation/test_swebench_resume.py::test_inspection_never_deletes_and_cleanup_preserves_agent_and_neighbor` (variants: JUnit XML)
  - `tests/evaluation/test_swebench_resume.py::test_interrupted_evaluation_restarts_same_patch_after_owned_cleanup` (variants: JUnit XML)
  - `tests/evaluation/test_swebench_resume.py::test_live_or_unverifiable_harness_blocks_new_attempt` (variants: JUnit XML)
  - `tests/evaluation/test_swebench_resume.py::test_artifact_control_error_is_not_translated_to_evaluation_failure` (variants: JUnit XML)
- [`tests/acceptance/resume/test_swebench.py`](../../../tests/acceptance/resume/test_swebench.py)
  - `tests/acceptance/resume/test_swebench.py::test_unresolved_manifest_requires_independent_prior_evidence` (variants: JUnit XML)
  - `tests/acceptance/resume/test_swebench.py::test_valid_prior_manifest_is_frozen_without_generating_an_oracle` (variants: JUnit XML)
  - `tests/acceptance/resume/test_swebench.py::test_swebench_resume_preserves_patch_and_expected_unresolved` (variants: JUnit XML)

### U17

- [`tests/cli/test_diagnostic_resume.py`](../../../tests/cli/test_diagnostic_resume.py)
  - `tests/cli/test_diagnostic_resume.py::test_diagnostic_only_appends_events` (variants: JUnit XML)
  - `tests/cli/test_diagnostic_resume.py::test_terminal_diagnostic_crash_never_opens_business_cursor` (variants: JUnit XML)
  - `tests/cli/test_diagnostic_resume.py::test_uncertain_diagnostic_blocks_suspended_resume` (variants: JUnit XML)
  - `tests/cli/test_diagnostic_resume.py::test_direct_diagnostic_cannot_use_business_observer` (variants: JUnit XML)
  - `tests/cli/test_diagnostic_resume.py::test_resume_closes_verified_old_diagnostic_before_business_completion` (variants: JUnit XML)
- [`tests/cli/test_evaluation_delivery.py`](../../../tests/cli/test_evaluation_delivery.py)
  - `tests/cli/test_evaluation_delivery.py::test_delivery_reuses_validated_patch_after_workspace_changes` (variants: JUnit XML)
  - `tests/cli/test_evaluation_delivery.py::test_collection_failure_only_returns_verified_patch` (variants: JUnit XML)
  - `tests/cli/test_evaluation_delivery.py::test_missing_submitted_patch_manifest_preserves_workspace` (variants: JUnit XML)
  - `tests/cli/test_evaluation_delivery.py::test_empty_local_success_destroys_workspace_without_finalization_error` (variants: JUnit XML)
  - `tests/cli/test_evaluation_delivery.py::test_renderer_finds_patch_by_type` (variants: JUnit XML)

### U18

- [`tests/cli/test_resume_dry_run.py`](../../../tests/cli/test_resume_dry_run.py)
  - `tests/cli/test_resume_dry_run.py::test_real_dry_run_is_read_only_without_provider_key` (variants: JUnit XML)
  - `tests/cli/test_resume_dry_run.py::test_missing_database_dry_run_does_not_create_it` (variants: JUnit XML)
  - `tests/cli/test_resume_dry_run.py::test_lock_conflict_precedes_missing_run` (variants: JUnit XML)
  - `tests/cli/test_resume_dry_run.py::test_unknown_shell_precedes_missing_key_and_retains_scene` (variants: JUnit XML)
  - `tests/cli/test_resume_dry_run.py::test_old_database_is_not_initialized_or_migrated` (variants: JUnit XML)
- [`tests/cli/test_resume_main.py`](../../../tests/cli/test_resume_main.py)
  - `tests/cli/test_resume_main.py::test_complete_uuid_forms_identify_same_run` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_short_or_prefixed_ids_are_parameter_errors` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_resume_refuses_frozen_overrides` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_real_entrypoint_help_has_independent_route` (variants: JUnit XML)
  - `tests/cli/test_resume_main.py::test_explicit_run_keeps_resume_as_task` (variants: JUnit XML)
- [`tests/runtime/test_resume_planner.py`](../../../tests/runtime/test_resume_planner.py)
  - `tests/runtime/test_resume_planner.py::test_unknown_tool_rejects_even_when_execution_ended` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_ended_retries_new_attempt` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_missing_identity_is_busy` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_resume_count_is_audit_only` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_rejection_priority_is_stable` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_pending_start_stays_busy_after_helper_exit` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_adjacent_priority_groups` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_read_only_active_or_unknown_group_rejects` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_state_cursor_requires_exact_committed_tool_prefix` (variants: JUnit XML)
  - `tests/runtime/test_resume_planner.py::test_guest_group_check_can_defer_only_after_known_helper_end` (variants: JUnit XML)

### U19

- [`tests/cli/test_resume_signals.py`](../../../tests/cli/test_resume_signals.py)
  - `tests/cli/test_resume_signals.py::test_first_signal_requests_interruption_not_terminal_cancellation` (variants: JUnit XML)
  - `tests/cli/test_resume_signals.py::test_bare_cancelled_error_is_not_a_signal_or_suspension` (variants: JUnit XML)
  - `tests/cli/test_resume_signals.py::test_real_process_signal_covers_application_scope` (variants: JUnit XML)
  - `tests/cli/test_resume_signals.py::test_repeated_cancellation_drains_sqlite_before_releasing_run_lock` (variants: JUnit XML)
  - `tests/cli/test_resume_signals.py::test_expired_cleanup_deadline_cannot_start_a_sqlite_commit` (variants: JUnit XML)
- [`tests/runtime/test_interruption.py`](../../../tests/runtime/test_interruption.py)
  - `tests/runtime/test_interruption.py::test_shared_deadline_does_not_refresh` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_interruption_suspends_without_fabricated_tool_result` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_explicit_cancellation_remains_terminal` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_expired_interruption_never_starts_new_work` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_bare_cancelled_error_is_not_suspension` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_trusted_result_commits_before_pause_only_with_budget` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_pending_approval_is_invalidated_atomically_with_suspension` (variants: JUnit XML)
  - `tests/runtime/test_interruption.py::test_interruption_before_initial_record_creates_no_run` (variants: JUnit XML)
- [`tests/acceptance/resume/test_local.py`](../../../tests/acceptance/resume/test_local.py)
  - `tests/acceptance/resume/test_local.py::test_local_fixed_success_after_sigint` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_four_resumes_preserve_origin_and_usage` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_unmatched_barrier_cannot_pass_as_interruption` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_default_signal_deadline_uses_one_precalibrated_budget` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_signal_while_real_console_stdin_is_open` (variants: JUnit XML)

### U20

- [`tests/storage/test_run_lock.py`](../../../tests/storage/test_run_lock.py)
  - `tests/storage/test_run_lock.py::test_same_run_lock_conflicts_until_release` (variants: JUnit XML)
  - `tests/storage/test_run_lock.py::test_lock_survives_file_reuse_and_is_not_inherited` (variants: JUnit XML)
  - `tests/storage/test_run_lock.py::test_distinct_runs_can_hold_locks` (variants: JUnit XML)
- [`tests/storage/test_resume_transactions.py`](../../../tests/storage/test_resume_transactions.py)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_approval_write` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_rolls_back_after_run_update` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_resume_transaction_success_control` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_tool_results_require_exact_business_scope` (variants: JUnit XML)
  - `tests/storage/test_resume_transactions.py::test_unconsumed_approval_uses_execution_history` (variants: JUnit XML)
- [`tests/acceptance/resume/test_locking.py`](../../../tests/acceptance/resume/test_locking.py)
  - `tests/acceptance/resume/test_locking.py::test_loser_attempt_is_inside_winner_lock_interval` (variants: JUnit XML)
  - `tests/acceptance/resume/test_locking.py::test_lock_covers_finalization` (variants: JUnit XML)

### U21

- [`tests/acceptance/resume/test_local.py`](../../../tests/acceptance/resume/test_local.py)
  - `tests/acceptance/resume/test_local.py::test_local_fixed_success_after_sigint` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_four_resumes_preserve_origin_and_usage` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_unmatched_barrier_cannot_pass_as_interruption` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_default_signal_deadline_uses_one_precalibrated_budget` (variants: JUnit XML)
  - `tests/acceptance/resume/test_local.py::test_signal_while_real_console_stdin_is_open` (variants: JUnit XML)
- [`tests/acceptance/resume/test_docker.py`](../../../tests/acceptance/resume/test_docker.py)
  - `tests/acceptance/resume/test_docker.py::test_same_container_layer_survives_resume` (variants: JUnit XML)
  - `tests/acceptance/resume/test_docker.py::test_deleted_or_replaced_container_is_rejected` (variants: JUnit XML)
- [`tests/acceptance/resume/test_swebench.py`](../../../tests/acceptance/resume/test_swebench.py)
  - `tests/acceptance/resume/test_swebench.py::test_unresolved_manifest_requires_independent_prior_evidence` (variants: JUnit XML)
  - `tests/acceptance/resume/test_swebench.py::test_valid_prior_manifest_is_frozen_without_generating_an_oracle` (variants: JUnit XML)
  - `tests/acceptance/resume/test_swebench.py::test_swebench_resume_preserves_patch_and_expected_unresolved` (variants: JUnit XML)
- [`tests/acceptance/resume/test_docker_connections.py`](../../../tests/acceptance/resume/test_docker_connections.py)
  - `tests/acceptance/resume/test_docker_connections.py::test_workspace_and_evaluator_use_their_own_daemons` (variants: JUnit XML)
  - `tests/acceptance/resume/test_docker_connections.py::test_frozen_tls_connection_survives_resume` (variants: JUnit XML)

## Verification result

首次全量回归：791 passed / 6 skipped（235.33s），随后旧 Docker workspace 真镜像入口单独 1 passed（2.72s）。

一次 fresh-context `gpt-6-astra` 审查（e228f70..aa66be2）发现 1 Critical / 5 Important / 2 Minor；已进行一次修复 pass，不进行二次审查。修复前每类新回归均观察到失败：

| Finding | RED → GREEN regression |
| --- | --- |
| Later unrecorded host command / missing harness proof | `tests/cli/test_host_execution_gates.py` 四个真实 caller SIGKILL variants；`tests/evaluation/test_swebench_resume.py::test_ended_preflight_helpers_do_not_prove_harness_end` |
| Exhausted failed-Step window | `tests/runtime/test_terminal_resume_boundaries.py::test_failed_step_stop_survives_lost_completion_ack` |
| Terminal model failure window | 同文件 `test_fatal_model_failure_survives_lost_closure_ack`（provider/usage）与 `test_exhausted_context_retry_survives_confirmed_failure_reopen` |
| Approval termination/signal window | `tests/cli/test_approval_termination_signals.py` 拒绝/过期/UI异常 × SIGINT/SIGTERM 六 variants，真实审批协调器、SQLite、OS 信号 |
| Current capacity validation | `tests/cli/test_resume_model_capacity.py` 缺文件/缺 entry/两类不兼容/兼容变化保留冻结值 |
| Prefixed full UUID contract | `tests/cli/test_resume_main.py` 的两个 `run_` 完整 UUID variants；原 Task 27 拒绝 ruling 已撤回 |

全量回归曾暴露 `HARNESS_START_FAILED` 被误分为 `HARNESS_PROCESS_FAILED`：已增加 close-on-exec 错误确认管道，保留原 exec 错误契约；相关 44 项定向回归通过，随后重新运行全量。

宿主命令采用独立 permission pipe，目标 exec 前确认身份；只有父调用者持写端，目标 stdin 独立，exec 保留 PID/出生身份。Local、Docker CLI、harness 共用此门。缺 harness 身份仍保守 busy。

Deferred minors：同目录不同数据库/同 UUID 的锁键假冲突（fail closed）；bootstrap 函数内导入。外部环境和逐 variant 审计缺口不因修复或全量测试通过而消失。

修复后最终全量回归：**815 passed, 5 skipped in 267.91s**。Ruff（本次相关 Python 文件）、`ty check`、`git diff --check` 通过。

`/private/tmp/miniagent-resume-reviewed.xml` 和 `/private/tmp/miniagent-resume-reviewed.log` 为本次最终逐 node/variant 结果。5 个 skipped 的明确原因如下；没有将其计为 PASS。

- `tests.acceptance.resume.test_docker_connections::test_workspace_and_evaluator_use_their_own_daemons`: DEFER: three independently prepared daemon connections unavailable

- `tests.acceptance.resume.test_docker_connections::test_frozen_tls_connection_survives_resume`: DEFER: independently prepared verified TLS daemon/image unavailable

- `tests.acceptance.resume.test_swebench::test_swebench_resume_preserves_patch_and_expected_unresolved`: DEFER: no independent prior unresolved manifest; user confirmed unavailable

- `tests.integration.test_swebench_evaluation::test_real_ready_development_patch_is_verified`: Real SWE-bench requires explicit cached-image opt-in

- `tests.integration.test_swebench_evaluation::test_real_invalid_patch_fails`: Real SWE-bench requires explicit cached-image opt-in

## Rulings I made

按时间顺序保留执行裁决和错误代价；其中 Task 27 的 run_ 拒绝规则已被 Final 裁决明确撤回。

- Ruling: create isolated worktree without another conversational consent gate — development authorization covers reversible isolation under developer instructions — cost if wrong: discard isolated branch.
- Task 4: Ruling: application envelope lives in runtime.recovery and CLI imports it — runtime must not import CLI; metadata is runtime-owned — cost if wrong: relocate one data type without a format change.
- Task 4: Ruling: advance recovery-context decoder to v2 here, rather than Task 5 — round-trip tests require writer and reader to agree within this commit — cost if wrong: version work lands one task early.
- Task 5: Ruling: explicitly close SQLite connections at every store I/O boundary — sqlite3 context managers only end transactions and leaked handles blocked journal-mode test setup — cost if wrong: formerly implicit connection lifetimes end sooner, with no caller-visible connection API.
- Task 6: Ruling: add tests/__init__.py so public cross-domain resume_support imports are stable — tests was not an importable package — cost if wrong: pytest import names may change; targeted and final regression must verify collection.
- Task 8: Ruling: expose read-only attempt_id on ExecutionObserver — Workspace identity producers otherwise cannot associate executions with the current operation using the frozen API — cost if wrong: observer adapters must provide this one identifier.
- Task 9: Ruling: retain owned process identities in a WeakKeyDictionary through register_host_process — reap_process keeps its existing signature but must use the original birth identity after leader exit — cost if wrong: process handles remain weakly tracked for their lifetime; no persisted format change.
- Task 10: Ruling: add shared Workspace/WorkspaceBackend protocol requirements with Task 13 after Docker attach exists — requiring them now would force fake Docker stubs or break currently valid callers — cost if wrong: protocol enforcement is delayed across three intermediate commits; Local concrete methods are available now.
- Task 11: Ruling: add optional max_output_bytes to run_docker_exec — required backend-configured truncation cannot be preserved through the frozen signature otherwise — cost if wrong: one optional keyword beyond the planned signature; existing callers remain valid.
- Task 11: Ruling: StoreBoundary can additionally match identity permission — exact create/start E/U barriers must use real SQLite at distinct public event boundaries — cost if wrong: test-only fixture matcher gains one optional filter.
- Ruling: Task 12 adds run_docker_exec_output with raw byte output and optional stdin_bytes while retaining run_docker_exec's public result contract — existing checkpoint tar streams and binary subprocess I/O cannot round-trip through UTF-8 strings — extra optional internal API must remain consistent with the single execution core.
- Ruling: Task 12 dispatches container exec from DockerWorkspaceBackend._run to the managed SDK core — every production exec, including lifecycle/export/checkpoint, is gated while existing scripted backend tests continue to exercise command assembly — tests overriding _run do not verify the SDK boundary; real Docker SDK tests supply that evidence.
- Ruling: Task 12 moves shared export fixtures from a fake Docker CLI execution path to a fake external Engine HTTP boundary with real fixture subprocesses — production container exec no longer uses CLI and cannot support the old fake boundary — tests now allow bounded remote EOF cleanup propagation before probing the fixture process; real guest cleanup has separate Docker evidence.
- Ruling: Task 12 export budget fixture uses eight seconds instead of three — four fresh SDK helper interpreter startups made the old budget expire before reaching the cleanup fault under load (observed task-done failure) — elapsed budget assertion remains bounded at eleven seconds; test runtime increases.
- Ruling: Task 13 adds an optional AttachmentT generic parameter to WorkspaceBackend and describe_attachment to Workspace, updating the two typed test doubles — each concrete backend consumes its own attachment subtype while existing one-parameter Backend annotations remain valid — generic consumers needing attach must select the attachment type.
- Ruling: Task 13 leaves historical guest_execution_groups as an ordered pending live check — the attachment has no historical execution identities; those are persisted in State and consumed by Task 23's resume transaction — attach verifies each of its own managed command groups, while Task 23 must inspect historical guest identities before business continuation.
- Ruling: Task 14 adds TerminationDecided as a persisted event and optional attempt_id fields on model events, with every production request writer supplying the UUID — response disposition/state remains authoritative, and existing synthetic model events remain constructible — consumers can require attempt IDs when validating a current OperationRef but cannot assume every synthetic historical model event has one.
- Ruling: Task 14 replaces the context-compaction acceptance harness's private manual loop with the common _drive entrypoint and updates private request-handler tests — keeping a second old loop would bypass persisted phase dispatch — the harness now validates the same phase driver used by normal and resumed execution.
- Ruling: Task 15 persists hard_trigger on CompactionRequested and reuses the original request's covered_messages/source_version during continuation — a fresh estimator can otherwise choose a different prefix even with the same source Snapshot — readers tolerate the synthetic event default, while production writers always record the trigger; frozen prefix selection adds one optional context planning parameter.
- Ruling: Task 16 preserves the original tool_step_failed all-non-success rule from binding design §9 (“按原 Step 失败规则继续”), overriding test-design TC-U7-03's contradictory mixed-result expected failed — changing to any-non-success would alter runtime behavior beyond resume requirements — the mixed ERROR/SUCCESS oracle is successful and an earlier SUCCESS followed by ERROR/ERROR proves whole-batch recovery; if the intended product change was any failure, that needs an explicit requirement change.
- Ruling: Task 16 extends waiting_case with an optional fully formed State — old storage-focused fixture intentionally omits runtime prefix/allowed-tools; current-policy tests need genuine runtime input — a small optional test factory parameter, existing callers unchanged.
- Ruling: Task 17 verifies every identified execution's matching committed ended evidence before accepting tool/evaluation results or replacing component operations — an adapter returning a result after identification without ended evidence otherwise silently cleared the operation (new RED test demonstrated this) — persisted operations require an extra history read at completion; memory-only runs retain evidence in a local collector.
- Ruling: Task 17 removes the private stage_evaluation_result/pending_evaluation_result path and uses the full durable EvaluationResult for terminal output/artifacts — no callers outside Runtime existed and keeping the fallback would preserve a second result authority — private integrations using that helper must move to await record_evaluation_result.
- Ruling: Task 18 introduces the planned OperationInterrupted payload and generalizes interrupt_operation to non-model operations only after verified chain end — Runtime resume phase tests need a genuine completed-recovery prefix, and Task 24 must close safe export/evaluation/read-only operations too — adds explicit interruption events instead of pretending interrupted requests produced ModelResponded; only model/summary closures affect unknown usage.
- Ruling: Task 19 adds a callable cleanup-deadline scope in environments and bounds Journal.record_activity with the first interruption's remaining budget — a task already running must observe a later signal without an environments→Runtime import, and identity/result writes must not get a fresh pause timeout — two small shared-scope APIs and one extra journal deadline check; regular cancellation behavior is unchanged.
- Ruling: Task 19 adds minimal RunSuspension rendering before Task 27 — widening ApplicationResult immediately makes the existing renderer's RunResult-only contract invalid — detailed resume/status output remains Task 27; this early branch only shows the Run ID and signal exit code.
- Ruling: Task 20 recognizes a duplicate Run only by SQLite PRIMARYKEY constraint code — the real AFTER INSERT RAISE(FAIL) test showed run_exists sees the newly inserted uncommitted row and wrongly translated a trigger failure to RunAlreadyExists — adds sqlite.py to this task; genuine duplicate-ID behavior remains unchanged.
- Ruling: Task 20 cleans newly owned resources after a create error only when the concrete SQLite call returned sqlite3.Error and a fresh read proves the target row absent — this store's awaited transaction has finished rollback, unlike generic confirmation uncertainty — unknown/no-dispatch adapters still retain resources; cleanup is bounded to ten seconds.
- Ruling: Task 20 extends the existing external Engine fixture with an optional CLI command prefix and moves configured_host to the shared CLI conftest — bound Docker bootstrap now uses the SDK and cannot be tested with CLI-only exec responses — legacy fake CLI lifecycle checks remain, while guest execution crosses the external Engine API boundary.
- Ruling: Task 21 captures diagnostic submit's returned value in Application finalization and updates typed Workspace doubles for the optional temporary_index_path — result now intentionally exposes only candidate state, so the old finalizer would lose diagnostic output — diagnostic event persistence remains Task 25; candidate and diagnostic cache identities are independent now.
- Ruling: Task 21 records verified Local cleanup end evidence for timeout/cancellation only, while persistence/managed failures remain immediate stop controls — Task 17's trusted-result guard requires genuine ended evidence for a confirmed TIMEOUT, but writing after an uncertain identity commit would be unsafe — adds one awaited cleanup evidence commit within the same operation/deadline.
- Ruling: Task 21 keeps the current evaluator's generic attempt ID as an in-process correlation after a candidate component closes its narrower export operation — record_evaluation_result still binds to the already committed EvaluationStart without inventing a new attempt — one non-authoritative correlation field, recreated by begin_evaluation after resume.
- Task 22: Ruling: SWEbenchAttemptState lives in evaluation/swebench_docker rather than CLI — keeps evaluation independent of CLI and lets the planner consume a single typed resource contract — cost if wrong: relocate/re-export the schema.
- Task 22: Ruling: shared Docker CLI subprocess runner also uses managed host identity and observer — all evaluator CLI commands require the same stop proof as harness commands — cost if wrong: stricter process identity requirements on standalone preflight.
- Task 22: Ruling: resource inspection accepts frozen executable and host executions as optional keyword arguments — connection and attempt alone cannot prove original harness end — cost if wrong: callers must supply persisted execution facts.
- Task 23: Ruling: use RESUME_METADATA_MISSING and RESUME_STATE_INCONSISTENT rather than abbreviated plan step names — binding design §13 defines the stable public codes — cost if wrong: consumers using abbreviated draft codes must update.
- Task 24: Ruling: rehydrate_application requires explicit run_store keyword — RunRecord deliberately contains no database locator, so deriving a database or consulting ambient Settings would break the held-lock source — cost if wrong: factory callers must supply the already-open writer.
- Task 24: Ruling: dry-run constructs only inert retained Workspace descriptors to read current tool contracts, without attach/prepare/execute — tool metadata currently belongs to concrete tool instances — cost if wrong: future tool constructors must remain free of side effects or expose separate metadata.
- Task 24: Ruling: deferred guest inspection is implemented at the application live-check boundary using a read-only /proc probe after attach — the backend attachment alone has no historical execution identities — cost if wrong: unavailable guest Python or ambiguous PID identity conservatively blocks resume.
- Task 25: Ruling: finalize_diagnostic accepts optional cleanup_timeout_seconds and CandidateSubmission accepts a separate diagnostic observer getter — preserve frozen cleanup reserves and prevent a diagnostic call inheriting a business evaluation scope — cost if wrong: callers supplying custom observers must choose the correct channel.
- Task 25: Ruling: external FinalizationWorkspace tests now describe a retained attachment — diagnostic export must allocate an owned temporary index before effects — cost if wrong: third-party Workspace doubles need the already-public attachment contract.
- Task 25: Ruling: Resume orchestration also closes verified old diagnostic chains before ResumeCompleted — read-only proof authorizes continuation, but the historical chain must be explicitly interrupted with its fresh end facts before new business work — cost if wrong: extra audit-only commits may conservatively reject uncertain persistence.
- Task 26: Ruling: startup RunExecution.start is also awaited through interruption handling — signal arrival during the first create otherwise waits for the hard deadline — cost if wrong: interrupted create is conservatively reported as persistence uncertainty and cleaned only after fresh no-row proof.
- Task 26: Ruling: cancellation while awaiting a store acknowledgement is RunPersistenceError after the real SQLite worker drains; repeated cancellation cannot release the Run lock early — cancellation does not prove rollback — cost if wrong: OS-level stuck storage can delay hard exit; wall-clock acceptance remains separately measured.
- Task 26: Ruling: ExecutionObserver adds synchronous check_permission and Docker attach accepts optional before_start — checking only before a commit leaves a signal window between its acknowledgement and actual permission — cost if wrong: custom observers must implement the added method; all repository callers and doubles were updated.
- Task 27: Ruling: reject run_ prefixed IDs despite the contradictory plan assertion — binding design and the same task acceptance text require full UUID forms only — cost if wrong: users must copy the full published UUID rather than a prefixed display token.
- Task 27: Ruling: ModelLimitsError preserves the existing safe model-limit diagnostics while generic configuration failures stay redacted — broad ValueError redaction broke trigger CLI compatibility — cost if wrong: callers matching ValueError still work, but additional safe configuration errors may need typed presentation later.
- Task 28: Ruling: rehydrate_application accepts a fresh approval_handler and CLI resume installs ConsoleApprovalHandler — the real B3 journey was RED with only c0 written because restored calls had no handler — cost if wrong: custom rehydration callers must explicitly provide their current approval UI.
- Task 28: Ruling: Console approval uses cancellable POSIX stdin readiness instead of a default executor thread — a real open-pipe SIGINT test hung after suspension until supervisory cleanup (RED), then exited 130 after the fix — cost if wrong: non-POSIX CLI input needs a separate adapter.
- Task 28: Ruling: the existing compaction acceptance runner forwards its configured approval_handler to the common Runtime driver — integration exposed APPROVAL_UNAVAILABLE caused by an omitted dependency after Task 14 — cost if wrong: runner approval must track the public runtime constructor.
- Task 29: Ruling: precise delayed-start/parent-death permission windows reuse the existing external Engine tests; six new acceptance variants use the actual prepared daemon — fake RPC observations and real SDK/guest behavior are complementary evidence, not substitutes — cost if wrong: real delayed network-start and full application Docker journey remain DEFER rather than an unsupported PASS.
- Task 29: Ruling: same-container acceptance proves a fresh-process backend attach and outside-workdir layer retention; a complete Docker Application/evaluator journey remains DEFER pending the independent Task 30 development fixture — no evaluator downgrade or invented task context — cost if wrong: complete Local + Docker support is not yet accepted.
- Task 30: Ruling: keep independent unresolved, three-daemon and verified TLS journeys explicitly DEFER when their external inputs are unavailable — user confirmed no independent unresolved manifest; synthetic reports cannot establish the expected real result — cost if wrong: complete Local + Docker support remains unaccepted.
- Task 30: Ruling: the evidence index lists every one of 244 TD cases with actual executable group/node entry points, but labels per-variant audit pending instead of inferring coverage from a green group — current tests do not carry a trustworthy one-to-one TC annotation — cost if wrong: requirement-by-requirement acceptance still needs an independent variant audit.
- Final: Ruling: withdraw the Task 27 rejection of run_ full UUID inputs — the actual binding design section 5 explicitly permits prefixed hyphenated and 32-hex UUIDs; prior reading was incorrect — cost if wrong: two accepted full-UUID spellings must be reconsidered, never abbreviations.
- Final: Ruling: share a stdlib-only permission-pipe host launcher across Local commands, Docker CLI and SWE-bench harness — the reviewer reproduced later unrecorded commands running while earlier ended identities authorized retry — cost if wrong: one host Python startup per command and dependence on a private exec gate; target argv/stdin/PID/birth identity remain preserved.
- Final: Ruling: require an explicit harness identity plus whole-chain end for evaluation cleanup — ended preflight docker_helper identities cannot prove a harness never started or ended — cost if wrong: prelaunch crashes with no harness identity conservatively remain busy.
- Final: Ruling: check the persisted consecutive-failure limit before the max-step guard on new-Step entry — a committed failed Step can precede its termination commit; original consecutive-failure termination precedence must survive that gap — cost if wrong: persisted exhausted counters stop a Run earlier than a max-step reason.
- Final: Ruling: persist fatal model TerminationDecided/disposition atomically with operation interruption or final context-failure response, and make matching record_termination idempotent — a confirmed terminal failure must not become an unknown retry after a lost acknowledgement — cost if wrong: private interrupt_operation/record_model_failure gain optional termination/message keywords and audit events may share the closure commit.
- Final: Ruling: commit staged approval rejection/expiry/invalidation together with TerminationDecided and clear pending before dropping its batch — the previously durable WAITING_APPROVAL/terminate prefix could not suspend — cost if wrong: approval audit resolution appears before RunFinished and the valid intermediate business status is RUNNING.
- Final: Ruling: load current capacity from the frozen model_limits_file and require support for the frozen context window and selected output limit, without replacing frozen values — binding section 7.3 requires missing/incompatible capacity rejection — cost if wrong: unavailable capacity files now block Resume; compatible larger values do not expand requests.

## Deferred minors

- Final: minor (deferred): RunLock key omits database filename; different databases in one directory with the same UUID falsely conflict (fail closed).
- Final: minor (deferred): bootstrap rehydration has function-local imports contrary to the repository import-placement rule.

Task 30 保持部分验收状态；本记录不宣称 Local + Docker 完整支持已经验收。
