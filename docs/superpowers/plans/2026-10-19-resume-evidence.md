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

Final suite and final review results are appended after actual execution. No prefilled pass.
