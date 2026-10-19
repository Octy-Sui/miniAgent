# Resume：进程中断后的安全续跑

日期：2026-10-14

状态：D1–D10 需求决策已确认；2026-10-19 架构 review 后同步取消旧存储格式兼容与迁移；尚未进入实现计划或实现

后续 [architecture-critique.md](../../../architecture-critique.md) 的三项执行 / 初始化边界已补齐；镜像首次冻结按用户决定采用“缺失即报错、不可 Resume”。

依据：当前代码 `e228f70`，以及用户确认的 Local + Docker、接回原 Workspace、不使用 checkpoint 的恢复方式

## 1. 目标与成功标准

一次 Run 在进程中断后，通过 `miniagent resume --run-id run_<uuid>` 从另一个进程接回原 Workspace 并安全续跑。沿用原 Run ID、任务、Agent Specification、Working Context、已确认 Tool Results、Step 进度、配置和累计 Usage。

成功标准：

1. 同主机 Local 与 Docker 均支持续跑；Local 接回原目录，Docker 接回原容器。
2. 已持久化的工具调用不重放；剩余调用按照原 Tool Batch 继续，仍受当前 Policy 检查。
3. 已开始而未提交可信结果的 shell/未知副作用工具拒绝自动续跑，不回滚、不跳过、不交给模型绕过。
4. CLI 首枚 SIGINT/SIGTERM 将 Run 暂停为非终态 SUSPENDED；下一进程可按安全判定续跑。
5. 同一 Run 只由一个进程推进；恢复过程中再次中断仍遵循同一协议，恢复次数不设上限。
6. Workspace 缺失、身份不匹配、旧执行未结束或关键证据缺失时，在模型请求和业务工具调用前拒绝。
7. Docker 续跑保持 SWE-bench 独立评估，不降级为 Local 或 PassThroughEvaluator。

安全保证是“不自动重放未知副作用、避免受管理执行并发”，不是任意 shell 的 exactly-once，也不是磁盘回滚或灾备。SQLite 事务无法与文件、进程或外部服务副作用组成原子事务。

## 2. 已确认的需求决策

| 编号 | 用户确认的选择 | 设计约束 |
| --- | --- | --- |
| D1 | Local + Docker；复用保留的 Workspace | 两种 backend 都进入首版验收 |
| D2 | CLI 首枚信号暂停为 SUSPENDED | 与库 API 的显式取消终止区分 |
| D3 | 拒绝未知 shell 自动续跑 | 无 force、自动重放或人工认领入口 |
| D4 | 不实现 checkpoint | 不创建、读取或恢复 Workspace checkpoint |
| D5 | 保留旧调用，增加 run、resume | 保留 `miniagent "task"` 与 trigger 的 Python 调用 |
| D6 | 冻结原模型、配置和定价依据 | Resume 不接受覆盖原任务或运行定义的选项 |
| D7 | 接回原 Workspace | 保持 Workspace ID、Local 路径或 Docker container ID |
| D8 | 提供 dry-run；恢复次数不限制 | 次数仅审计，不构成恢复拒绝条件 |
| D9 | 同主机文件锁 + SQLite CAS | 锁覆盖整个执行和 finalization |
| D10 | 缺恢复证据拒绝；终态不重开 | 不推断现场；旧存储格式不兼容，不恢复 FAILED/CANCELLED/SUCCEEDED |

本文件不再包含待选择的替代方案；以下是落实这些决定所需的具体契约。

## 3. 当前实现与缺口

| 已有能力 | 代码依据 | 必须补齐 |
| --- | --- | --- |
| State、Events、Approval、可选 Snapshot 的原子提交 | `storage/sqlite.py::_commit_sync` | 跨进程执行互斥及 Resume 事件 |
| 每笔活动提交更新最新 RunRecord.state | `runtime/recording.py`、`runtime/journal.py` | 从已有记录 attach，而非重新 create |
| Tool Result/Tool Message 与 next_index 一起提交 | `runtime/transitions.py::complete_tool` | runner 从 next_index 执行，Step 成败使用整批结果 |
| 业务工具调用前提交 ToolExecutionStarted | `runtime/tool_runner.py::_execute_prepared` | 活动 attempt 与未知执行判定 |
| Local 原址、Docker 原容器保留 | `cli/application.py`、`environments/local.py`、`environments/docker.py` | 持久化接回身份，backend 新增 attach |
| 现有 backend recover 依赖 checkpoint 并创建新 Workspace | `environments/local.py`、`environments/docker.py` | Resume 不调用这些接口 |
| RunRecoveryContext 保存脱敏 Workspace Spec | `runtime/recovery.py` | 无 checkpoint 的 attachment 描述与装配上下文 |
| 第一枚信号触发 cancellation | `cli/main.py::run_with_signals` | 区分 interruption 与 terminal cancellation |
| 内存 Trace Scope、审批版本和评估结果暂存 | `trace_context.py`、`journal.py`、`execution.py` | 持久化恢复阶段，重建执行对象 |
| SWE-bench task preparation、候选提交与评估 attempt | `cli/bootstrap.py`、`candidate_submission.py`、`evaluation/swebench.py` | 冻结评估定义、提交状态及受管理进程/容器身份 |

现有失败和取消 Run 通常保留资源，但它们是终态，仍不能 Resume。可续跑对象是新增 SUSPENDED，以及硬中断遗留的非终态 Run；“资源保留”和“生命周期可恢复”是两个独立条件。

现有 Workspace 超时/取消会进入内存 BROKEN；Docker 还可能停止容器。新进程不得复制内存 BROKEN 状态，也不得仅靠重新实例化就宣称环境可用，必须验证持久化现场和执行收尾证据。

## 4. 范围与安全前提

支持同一主机、本地文件系统上的同一 SQLite 数据库、Artifact Store，以及 Run 原有 Local 目录或同一 Docker daemon 的原容器。Workspace 在中断期间继续归该 Run 独占；Runtime 不自动丢弃修改，也不从源仓库或镜像重新创建。

Local 原目录及其 Git 基线保留；Docker 原容器 writable layer 保留，因此 workdir 外的依赖安装也继续存在。停止的容器可以在验证身份和恢复判定后启动；被删除的容器不能恢复。进程内存、已经退出的后台服务、临时网络连接不承诺恢复。

接回原现场不提供历史内容一致性证明。首版检查身份、路径边界、Git 基线、容器身份及受管理活动执行，不能证明无人手动修改过每个文件。任意人工修改、其他进程写入和命令自行脱离受管理进程组的后台写入不在恢复保证内；用户应保持 Workspace 独占。检测到明确冲突、残留执行或无法验证受管理执行结束时 fail closed，不假设文件锁能够终止旧子进程。

不包含：Workspace checkpoint/rollback/recreate、跨主机恢复、容器丢失重建、通用 L0–L5 RecoveryController、自动模型切换、任务修改、终态重启、人工补录未知 Tool Result、完整 Trace 聚合/export、资源垃圾回收、终态 finalization 重试命令。

## 5. CLI 契约

```text
miniagent "修复这个问题" [现有选项]
miniagent run "修复这个问题" [现有选项]
miniagent resume --run-id run_<完整 UUID>
miniagent resume --run-id <完整 UUID>
miniagent resume --run-id <id> --dry-run
miniagent resume --run-id <id> --database /absolute/path/runs.sqlite3
```

`run_` 是显示/输入前缀，内部 UUID、数据库主键和 Artifact run_id 不变。接受带连字符或 32 位十六进制的完整 UUID；不接受模糊前缀或任意 `run_xxx`。第一笔完整恢复描述持久化后、首次模型请求前打印 Run ID 和恢复命令，最终输出也显示 ID。

显式路由保留旧 task 调用；首参数为 run/resume 时进入对应子命令，以这两个词为完整任务时使用 `miniagent run "resume"`。现有选项解析、help 和参数错误必须回归验证，不能依赖 Typer 增加第二个 command 后的隐式行为。`trigger` 导入调用的 `main(...)` 签名与现有行为保留。

Resume 不接受新 task/model/workspace/预算/定价覆盖。默认数据库来自设置，`--database` 仅指定查找位置。Workspace 路径、Artifact root、评估数据/work root 从 Run 的绝对路径描述定位，不随 cwd 漂移。凭据通过当前进程的环境引用解析；不持久化秘密值。

`--dry-run` 非阻塞获取同一个 Run 的文件锁，进行同样的只读预检，并打印可执行计划或拒绝原因。允许读取数据库、Artifact、Local 元数据、Docker daemon/container inspect、已知进程的活动状态及依赖版本。不得写 Run/Event/Approval，不创建或启动容器，不执行 Workspace 命令、模型、业务工具、评估或清理操作。锁文件初始化可发生，但不保留未来执行权。

若原容器已停止，dry-run 只验证可从 host inspect 得到的事实，并明确列出“容器启动后的 workdir/Git 校验待实际 Resume 执行”，不能为了验证而启动容器，也不能把待检项报告为已通过。评估残留资源需要清理时，计划列出清理需求，dry-run 不执行清理。

输出包含 Run ID、状态、Step 进度、已完成调用数、Workspace ID/路径或 container ID、恢复 phase、审计次数、未确认计费请求。错误包含稳定码和定位信息，不打印凭据或完整敏感参数。成功退出 0；参数错误 2；拒绝/冲突/任务失败 1；SIGINT/SIGTERM 130/143。

## 6. 生命周期与暂停

新增非终态 SUSPENDED；SUCCEEDED、FAILED、CANCELLED 保持终态且不可重开。

| 原状态/事实 | Resume 行为 |
| --- | --- |
| CREATED/PREPARING，有完整 attachment 描述 | 接回现场，完成准备后开始首次请求 |
| RUNNING，锁可获得 | 按持久化 phase 恢复，并检查残留执行 |
| WAITING_APPROVAL | 使未消费旧授权失效，重新走当前 Policy |
| EVALUATING | 使用原 candidate/patch 和 evaluator 定义恢复评估 |
| SUSPENDED | 按暂停前恢复点恢复；未知 shell 仍拒绝 |
| RECOVERING | 保留原 phase/来源，重新验证并接回同一 Workspace |
| SUCCEEDED/FAILED/CANCELLED | RUN_NOT_RESUMABLE，无业务状态写入 |

允许活动非终态转入 SUSPENDED；WAITING_APPROVAL 在清空 pending 后也可转入 RECOVERING。RECOVERING 可根据保存恢复点转入 PREPARING、RUNNING 或 EVALUATING；终止待提交 phase 先回到其合法原状态，再走原终止路径。Snapshot/State validator 与所有公开 RunStatus 消费者同步更新。

CLI 首枚 SIGINT/SIGTERM 表示暂停意图：停止发起新工作，取消当前可等待操作，进行有超时上限的收尾，提交 RunSuspended 与恢复点并保留资源。不能调用 finish(CANCELLED)，不能写 RunFinished 或虚假的 StepCompleted。退出码保持信号码。

只有真实返回的可信结果且受管理执行已收尾，才提交 Tool Result。被取消的 shell 没有可信结果时保留“已开始、结果未知”，不把取消当成未发生副作用。第二枚信号和 SIGKILL 无需完成暂停提交，下次读取最后确认记录判断。

库调用者的显式 CancellationToken 仍产生终态 CANCELLED。CLI 单独传递 interruption 意图；避免改变已有 Python 调用者的取消契约。Application/RunResult/rendering 支持非终态暂停结果，暂停无 finished_at/termination_reason，原因放在 interruption 元数据。

从 WAITING_APPROVAL 暂停或恢复时，在同一事务把当前未决 request resolve 为系统 INVALIDATED，并清空 approval.pending；原 Call 恢复点保留。保持“只有 WAITING_APPROVAL 有 pending”的 invariant。已批准但未执行的授权按第 11 节处理。

## 7. 持久化恢复描述

新增数据均使用版本化 Pydantic 结构，复用 RunState/RunRecoveryContext/RunCommit，不序列化进程对象、凭据或缓存。

### 7.1 执行恢复点

- phase：prepare、request_model、execute_tools、complete_step、evaluate_candidate、terminate。
- 复用 steps_started、Tool Batch、next_index；已完成 batch 的业务结果可从原事件/消息读取。
- 活动操作：类型、attempt ID、关联 Step/Call、开始事件 sequence、已确认的执行身份；无活动操作为 None。
- 已接受模型响应的处置：tool batch、candidate 或结构化失败决策，和响应一起原子提交。
- 暂停前状态/phase 与 interruption 原因；RECOVERING 保存原恢复点和 source sequence。
- resume_attempts 仅累计真实 ResumeStarted，用于审计，任何数值都不触发上限拒绝。

phase 是 dispatch 依据；既有 RunCursor 在 RUNNING 时与其一致，其他状态仍无 cursor。恢复元数据不保留另一份可独立漂移的 execution cursor。新增验证覆盖 phase/batch/pending operation/candidate/失败决策的合法组合。

必须覆盖 final response 已持久化但 begin_evaluation 尚未提交的窗口，直接进入评估；失败或无效 response 则进入原失败路径，不凭 assistant 文本非空推断成功候选。

### 7.2 Workspace attachment

通用字段：Run ID、Workspace ID、脱敏 Persisted Workspace Spec、backend、原 Git base_revision、Artifact root、工具契约指纹和 Resume 协议版本。明确 attachment 模式为 retained_workspace，不要求 checkpoint。

Local 描述保存 Local root、workspace_dir、repository root 的绝对规范路径，以及 `.miniagent-workspace` ownership marker 的预期 Run ID/Workspace ID。marker 复用当前文件名/格式；新的持久化描述不能只从 source_dir 推导路径。

Docker 描述保存完整 container ID、Workspace ID、managed/run/workspace labels、创建镜像的内容 ID、workdir、daemon ID 和非敏感连接描述。连接引用覆盖 endpoint/context/TLS 配置路径，秘密材料从当前环境解析；不以同名容器替代原 ID。

原 Workspace backend 和 evaluator 的 Docker 连接都需要明确持久化、校验和注入；如果原来使用不同 daemon，分别保存并验证。所有 inspect/start/exec/评估/清理命令都使用各自冻结连接，不能验证一台 daemon 后用当前 ambient context 操作另一台。

现有 checkpoint/recover/restore API 保留给其他调用者，本功能不调用、不补建或读取 checkpoint。Run State Snapshot 与 Workspace checkpoint 不同：前者是数据库状态副本，Resume 和 compaction 仍使用前者。

### 7.3 冻结装配定义

保存原 Agent/model/generation、RuntimeConfig、工具 definition/contract metadata 指纹、原模型 pricing entry 的内容和摘要、模型容量、candidate submission/finalization 超时设置。定价固定使用原内容，累计 usage 不重新计算。

保存 evaluator kind。Local 为 pass_through；Docker 为 swebench，附原 SWEbenchTaskContext、prepared task 身份/record digest、manifest/tasks digest、harness version、实际评估镜像内容 ID、评估数据/work root 与所有评估 timeout/allow-image-pull 设置。固定评估身份，不靠当前 CLI 默认值重建任务。

评估镜像内容 ID 必须在 bootstrap 预检时、创建 Workspace 和首笔 RunRecord 之前取得。即使 `swebench_allow_image_pull=True`，原镜像尚不存在也直接报 `EVALUATION_IMAGE_UNAVAILABLE`，退出 1；不自动拉取，不延迟到评估阶段冻结，不写不完整 Run 或输出恢复命令，因此不可 Resume。需先自行准备镜像，再新建 Run。这是对当前允许缺失镜像继续初始化行为的明确调整；已冻结 Run 的镜像内容 ID 仍不得被拉取后的同名 tag 替换。

Resume 从当前环境加载凭据和工具可执行文件，校验它们支持冻结定义。原 Docker daemon 必须匹配；评估镜像内容 ID、冻结数据及 harness version 不符则拒绝，不静默使用新版本。记录 provider endpoint 的非敏感身份变化；同一 provider/model 与请求协议仍必须兼容。

当前模型容量资料不能静默覆盖原配置：资料缺失/不兼容时拒绝，原冻结值仍是请求依据。Policy 使用当前版本，只重新评估未执行调用，并审计 revision 变化。

### 7.4 存储格式

产品尚未上线，直接更新初始 SQLite schema、Run/Snapshot/recovery context 及变化的 Event payload。保留明确版本校验，但不实现旧数据库迁移、旧 envelope 解码或历史版本兼容；现有开发数据库需另行重建，程序不自动删除、覆盖或转换旧数据。

不支持的数据库 / envelope 版本在读取或写入前明确拒绝。当前格式的 Run 没有完整 attachment/执行/冻结装配证据时返回 RESUME_METADATA_MISSING，不能扫描残留目录或容器猜测身份。缺凭据不应阻止无须凭据的当前格式记录读取/dry-run；拆分现有 Settings 初始化依赖时只改 Resume 所需范围。

### 7.5 新 Run 初始化

新 Run 生成 UUID、取得对应文件锁，完成 Workspace 和依赖准备，收集 attachment 与冻结装配描述。沿用现有 Runtime create/start 路径只创建一次 Run；首笔记录必须包含可接回身份与恢复协议，成功后才公开 Run ID 并请求模型。

首笔记录之前准备失败，按现有 bootstrap 规则清理本次新资源；已经持久化的 Run 发生暂停/恢复失败则保留原资源。进程在 Workspace 创建后、首笔记录前被硬杀可能留下孤立现场，本轮不支持猜测式认领或垃圾回收。

## 8. 接回原 Workspace

backend 新增 attach(spec, attachment) -> Workspace，与现有 create/recover 分开。成功后 Run ID、Workspace ID、路径/container ID 都保持不变，重新创建的只是 Python 对象与进程内 operation lock。

### 8.1 Local

1. 校验 attachment 所有权、规范路径和 root containment，检查原目录/仓库/ownership marker 存在且匹配；不得允许符号链接重定向到另一目录。
2. 校验保存的 Git base_revision 在仓库中仍存在且可用于原 patch baseline；HEAD 可因已确认工具的合法 commit 改变，不能误要求 HEAD 永远等于 base_revision。
3. 根据受管理 command 身份检查是否仍有旧执行；发现残留或身份无法验证则 WORKSPACE_BUSY，不执行新工具，不按 PID 粗略强杀。
4. 在原 repository root 装配 LocalWorkspace，不复制 source、不 git reset、不删除变更。若依赖失效，通过正常工具失败或预检不兼容错误报告，不伪称原环境已重建。

### 8.2 Docker

1. 先验证当前连接的 daemon ID 与保存值一致，inspect 保存的完整 container ID。
2. managed/run/workspace labels、创建镜像 ID、workdir、安全/资源配置必须匹配；同名新容器或 label mismatch 拒绝。
3. running 容器在确认无旧受管理工具执行后接回；exited 容器在实际 Resume 的 RECOVERING 阶段启动同一个容器。paused、restarting、removing、dead 状态拒绝并保留现场，首版不自动修复。
4. 已停止容器只有通过只读身份预检和未知工具判定后才允许启动。启动后检查 workdir、Git 基线及可执行环境；失败不执行模型/业务工具，不创建替代容器。
5. 原容器 writable layer 不清理，原 container ID 不变；禁止调用 checkpoint recover、重新拉起同镜像的新容器或降级 evaluator。

文件锁不是旧命令的收尾证明。工具完成契约需要包含受管理执行已结束的证据：Local 的进程组/启动身份，Docker 的 exec/受管理命令身份。开始副作用调用前先持久化业务开始记录；分配到执行句柄后补充身份，完成结果与收尾事实一起提交。执行句柄尚未来得及落库即中断的 shell 仍按未知执行拒绝，不能因未发现 PID 就视为未执行。

Docker SDK helper 也属于受管理执行：持久化其 PID / 出生身份、进程组、attempt / exec 关联及放行 / 收尾事实。helper 启动后等待门控，身份和放行意图提交后才能收到许可并发出 Engine RPC；exec ID 和 start 意图提交后才能收到 start 许可。父进程死亡、通道 EOF 或超时不得自行放行 / 重试。Resume 先确认旧 helper 结束并排除延迟 start，再校验远端 exec / 进程组；身份或发令可能性不明返回 WORKSPACE_BUSY，不因远端暂未运行而继续。start 结果未知时，helper 已退出和 Running=false 本身也不够，仍需已启动后结束或不可能发起 start 的证据。未知 shell 始终按 UNCERTAIN_TOOL_EXECUTION 拒绝。

正常返回的业务命令不得留下仍写 Workspace 的受管理子进程；发现残留则停止，不推进为可续跑完成态。任意脱离进程组的外部守护进程不保证自动发现，这一边界需在工具运行契约中明确。

## 9. State 提交与恢复判定

最新 RunRecord.state 是恢复源；Snapshot 用于状态校验/审计以及 compaction source lookup，不因 Snapshot 较旧而丢弃之后已提交的 Tool Result。

每次业务工具执行：prepare/Policy/approval → 原子提交 ToolExecutionStarted 与活动 attempt → 执行 → 确认可信结果与受管理执行收尾 → 原子提交 Tool Result、Tool Message、next_index、清空活动操作及完成事件。需要 Snapshot 的安全边界沿用 Journal 规则，不额外保存文件副本。

没有 checkpoint 或代码事务：工具修改原现场后，结果尚未落库就中断，现场可能已有修改，仍判未知 shell 并拒绝。不能从文件变化、stdout 日志、孤立 artifact 或容器已停止反推出成功结果。提交结果不明时本进程停止，下一进程读取数据库确认，不继续发出新工作。

ERROR/TIMEOUT 同样可能留下文件修改。已有可信 Tool Result 和执行结束证据则保留该结果及原文件，按原 Step 失败规则继续；只有开始/取消事件而无可信结果则按未知执行处理。

新工具默认不可重试未知结果；仅显式受信任只读契约允许重试，分类加入现有 contract metadata。shell 不按命令文本猜测安全；未完成 read_file 重试也必须先确认旧受管理读取进程结束。

| 持久化事实 | 自动行为 | 必须禁止 |
| --- | --- | --- |
| 模型请求未开始 | 开始下一 Step | 重置进度/usage |
| 模型请求开始，无已提交响应 | 关闭旧 attempt，重发同 Step 请求 | 猜测旧响应，再增加 Step 编号 |
| response + Tool Batch 已提交 | 从 next_index 继续 | 重新请求模型或重跑完成调用 |
| 调用未进入实际执行 | 当前 Policy 与必要的新审批 | 使用旧进程授权缓存 |
| 只读调用开始，无结果，旧读取已结束 | 同一 Call ID 新 attempt | 虚构旧结果或与旧进程并发 |
| shell/未知工具开始，无可信完成记录 | UNCERTAIN_TOOL_EXECUTION，退出 | 回滚/重放/跳过后请求模型 |
| Tool Result 已原子提交 | 保留消息和原现场，执行剩余调用 | 重执行来验证旧结果 |
| batch 全完成，StepCompleted 未提交 | 读取整批结果计算成败，完成 Step 一次 | 仅按本次剩余结果计算失败数 |
| candidate 已提交，评估未完成 | 按原 evaluator/patch 恢复 | 重新生成 candidate |
| 失败响应和处置已提交，RunFinished 未提交 | 原失败终态 | 当作成功候选或绕过失败 |
| RECOVERING 遗留 | 原恢复点、同一 Workspace 再次验证/接回 | create/recover 新 Workspace |

业务 Call 身份为 (run_id, step_number, call_index, tool_call_id)，真实执行 attempt 有独立 ID。只读重试仍只有一个最终业务 Tool Result，tool_calls 统计真实已开始 attempt，不由 cursor 反推。

CompactionCompleted + Snapshot 保持现有原子协议。已完成压缩保留最新 context/version，不重复；未提交 summary 响应作废，必要时重新压缩。已知 summary usage 保留，未确认请求计入计费不确定性。

## 10. 互斥与恢复事务

同主机 OS advisory file lock，键为数据库规范绝对路径 + Run UUID，锁文件放在数据库邻近固定目录。不在结束时删除锁文件，避免不同 inode 双持有；FD 不传给 subprocess。PID/status/mtime 不替代锁。

新 Run、Resume、dry-run 都遵守相同协议。实际运行从 Workspace 发布前到 finalization 完成持有锁，冲突立即 RUN_IN_USE，无 force。多个 Run 可以并行。仅保证本地文件系统、同主机且遵守协议的客户端；不支持 NFS、多主机或旧客户端同时写新格式。expected sequence CAS 继续拦截陈旧提交，但不能独自拦截副作用。

恢复流程：

1. 解析 ID、获取锁、读取 State/恢复描述/必要事件与审批事实。
2. 只读预检：终态、缺证据、未知工具、Workspace 身份、冻结配置、Docker task/patch/评估活动执行，以及业务 / diagnostic 遗留 helper。先排除旧 helper / 未决 start，再判断远端收尾。输出纯 ResumePlan 或结构化拒绝。
3. 原子提交 ResumeStarted + RECOVERING + 原恢复点/source sequence + resume_attempts 增量 + Snapshot；同事务 INVALIDATE 当前 pending approval 并清空 pending。
4. attach 原 Workspace；必要时启动已验证的原 Docker 容器，完成只读预检无法完成的校验。按第 12 节只清理已证明属于旧评估 attempt 的资源。
5. 装配冻结 provider/tools/pricing/evaluator，失效尚未消费的旧批准；原子提交 ResumeCompleted、恢复原 phase/status 和 Snapshot。
6. 提交成功后才进入共用 Runtime dispatch；正常结束沿用 finalization。

Workspace 缺失等预检拒绝不写业务状态。步骤 3 后失败写 ResumeFailed，保持 RECOVERING 与原恢复点；不把任务改成 FAILED，不销毁/删除原 Workspace。失败事件也无法提交时保留最后确认记录并输出持久化错误。

ResumeStarted 提交结果不明则停止，不能继续 attach 中的启动/清理操作；下次读取数据库判断。ResumeCompleted 提交结果不明同样停止，不启动业务执行、不销毁原资源。重复恢复的次数不限；不自动循环无限 retry，每条 resume 命令仅发起一次恢复流程，后续重试由用户再次调用。

## 11. Policy、审批、配置与 Usage

Workspace ID 接回后不变，但跨进程中断后仍使所有当前未消费旧授权失效。未决 request 系统 INVALIDATED；APPROVED 但工具尚未执行则 authorization invalidated，保留历史人类 Resolution。已经完成调用的历史授权不改写。

当前 Call 重新 prepare 并评估当前 Policy：DENY 产生正常 DENIED Result，APPROVAL 创建新 request，ALLOW 才执行。新 request 关联原 Call/同 Workspace/当前 revision。未知 shell 不能通过再次审批变得可重放。无交互 handler 时沿用现有非交互契约，不自动同意。

原 max_steps、max_consecutive_failures、compaction/generation/容量及所有冻结装配参数不重置。恢复当前 Step 不增加 steps_started；达到 max_steps 时仍允许完成已开始 Step 的剩余工具，检查上限仅在开始下一 Step 时进行。Step 失败数由整批持久化结果计算。

原 pricing 内容用于后续请求，已确认响应仅累计一次。当前 RuntimeConfig 没有实际 token/cost budget 引擎，本功能不新增，不宣称完整成本上限。

业务/summary 请求已发出但无落库响应时可能已被 provider 计费：保留原请求计数，新请求增加计数，记录 unconfirmed_model_requests。只累计可信 tokens/cost，输出总量为“已确认部分”并显示未知请求；不能把未知消费当 0。恢复次数不限，因此总 Run 可能出现多次未知计费；每次人工 resume 允许新的当前请求 attempt，不在一次命令内无界重试。

## 12. Docker 候选提交与评估恢复

Docker 原 Agent 容器与 SWE-bench 评估容器是不同资源。接回 Agent 容器不等于评估进程已经结束；必须分别持久化和校验。

### 12.1 候选提交

保存 candidate text、submission ID、状态、patch ArtifactRef/hash/base revision 与 export 的活动 attempt。候选 patch 成功注册后，把引用与候选提交事实原子提交，再启动 evaluator。禁止从 Artifact 列表按“最新 changes.patch”猜测原提交。

已提交 patch 的 Resume 直接验证并复用该 artifact，不重新 export；保留 patch 字节与原 candidate。Patch 缺失或 hash/Run ownership 不符则拒绝，不能用当前目录重新生成一个不同候选替代。

export/注册尚未确认时，在旧 export 子进程已确认结束、原 Workspace 独占的前提下可以重新导出同一候选。export 使用当前实现的独立临时 Git index，不修改业务工作树或原 index；新 attempt 使用新临时路径，孤立 artifact 不当作确认提交。临时 index 路径及 export 进程身份记录为该 attempt 的资源，不扫描后随意删除。无法确认旧 export 已结束则 WORKSPACE_BUSY。

诊断 patch 与候选 patch 分开记录，不能因暂停时导出过诊断 patch 就把 CandidateSubmission 视为已完成，不能让诊断导出覆盖原候选引用。

candidate export 使用业务 active_operation；diagnostic export 使用独立 finalization attempt / observer，只向事件历史记录开始、临时 index、进程 / helper / exec 身份、许可、结果和 artifact 引用。特别是终态后诊断，必须保持原业务 State、phase、active_operation、status 和终止详情不变，不为诊断重开终态或创建可恢复业务游标。事件用原 State 与最新 sequence CAS 追加；身份落库失败不放行，诊断失败只报告 finalization 错误。暂停后的诊断若遗留未确认执行，下次 Resume 需先检查并排除，不能与其并发。此协议不新增终态 repair 命令。

### 12.2 评估 attempt

启动 harness 前持久化 evaluation attempt ID、deterministic harness_run_id、原 patch hash、instance/task identity、attempt directory 和资源匹配条件；创建进程后补记 host process 启动身份。即使进程身份未及时落库，仍能按保存的 harness attempt 识别其评估容器；host 进程归属无法验证时拒绝重跑。

- 已原子提交可信 EvaluationResult/证据：恢复原结果，提交相应终态，不重新评估。
- 评估未开始：用冻结 task/config 和已提交 patch 创建首次 attempt。
- 评估开始但结果未确认：旧 attempt 标记 interrupted；确认旧 harness 退出和其评估容器已停止/清理后，用同一 patch 创建新 attempt。
- 无法确认旧进程退出或评估容器清理：EVALUATION_ATTEMPT_BUSY，保留 EVALUATING/恢复点，不并发启动。

实际 Resume 可以对已经证明属于旧 attempt 的遗留评估资源做有边界清理，不删除原 Agent 容器，不按模糊名称清理其他 Run。容器身份匹配沿用 existing swebench cleanup 的严格 instance/harness_run_id 规则，补充持久化身份；不单凭 PID/name 认领资源。

孤立 report/artifact 不升级成成功判定。未知结果的新 attempt 重跑独立评估，原 patch 不变，原审计记录保留。SWE-bench 的最终 EvaluationResult（pass/fail/error、output、artifact refs）应与终态提交原子写入；若尚需分开提交，必须先持久化可恢复的完整结果，再提交终态。现有内存 stage_evaluation_result 不能是唯一证据。

恢复需要的 frozen task、digest、harness、daemon、评估镜像或 patch 不可用则明确拒绝，不能退回 PassThroughEvaluator。普通已确认的评估失败仍按现有契约终止为 FAILED，不能借 Resume 自动重试已终止评估。

## 13. 组件边界与错误

| 组件 | 责任 |
| --- | --- |
| CLI routing/rendering/signals | run/resume、旧入口、ID、dry-run、暂停意图、退出码 |
| Resume bootstrap | 从持久化定义装配、调用 attach、冻结 Docker evaluation preparation |
| Resume 判定 | 纯函数：record + 事件/审批/现场检查事实 → ResumePlan 或拒绝 |
| AgentRuntime | resume 入口，与 run 共用 phase dispatch；不调用 run 从头开始 |
| RunExecution/Transitions | 持久化 phase、attempt、暂停/恢复与整批 Step 完成 |
| Recorder/Journal | attach 已有 RunRecord/审批版本，原子提交，CAS |
| Workspace backend | attach 原身份、执行结束证据；create/recover 不参与 Resume |
| CandidateSubmission/Evaluator | 提交事实与 patch 绑定、evaluation attempt 生命周期和结果 |
| SQLite/codec/lock | 当前格式版本校验、Run 专属锁、原子 State/Event/Approval/Snapshot |

不为 Resume 复制一套 loop；把当前 _run_step 从模型请求开始的假设拆成可以独立 dispatch 的模型、工具、Step 完成、评估、终止阶段。只做此功能必需重构，不接入通用 RecoveryController。

新进程创建新的 Trace scope，关联原 Run/Step/Call 与 source sequence。旧未闭合 span 标记中断/未知，不补造成功结束。sequence/snapshot version 单调递增，不恢复内存 span 对象，不实现 Trace 产品。

稳定码：RUN_NOT_FOUND、RUN_IN_USE、RUN_NOT_RESUMABLE、RESUME_METADATA_MISSING、RESUME_SCHEMA_UNSUPPORTED、RESUME_STATE_INCONSISTENT、WORKSPACE_MISSING、WORKSPACE_IDENTITY_MISMATCH、WORKSPACE_BUSY、WORKSPACE_UNAVAILABLE、DOCKER_DAEMON_MISMATCH、DOCKER_CONTAINER_STATE_UNSUPPORTED、TOOL_CONTRACT_MISMATCH、MODEL_CONFIG_MISMATCH、ENVIRONMENT_REFERENCE_MISSING、UNCERTAIN_TOOL_EXECUTION、EVALUATION_CONTEXT_MISMATCH、CANDIDATE_ARTIFACT_INVALID、EVALUATION_ATTEMPT_BUSY。沿用已有评估准备错误码的具体原因，不添加 backend-unsupported/checkpoint/attempt-limit 拒绝。

未知 shell 输出 Run ID、Workspace 身份、Step/Call、tool name、开始 sequence，说明原现场保留但自动续跑拒绝。首版没有 force/结果认领，人工诊断后另起任务不被伪装成安全 Resume。

暂停只做有上限的诊断收集；旧执行未收尾时跳过 patch export 并记录原因。诊断失败不把 SUSPENDED 改为 FAILED，不销毁 Workspace。FAILED/CANCELLED/恢复失败均保留原现场；只有 SUCCEEDED 且现有 artifact finalization 成功后才销毁当前 Workspace。

SUCCEEDED 已提交但 artifact/cleanup 失败，Resume 不重开或重复业务执行；本轮没有终态 finalize/repair 命令。

## 14. 验收与验证

真实跨进程测试使用临时 SQLite/Artifact Store/Local 目录，以及专属 Docker 容器。用持久化边界屏障控制中断，再由另一个进程 Resume；provider/harness 可以在外部边界提供确定性 fake，Workspace/SQLite/Artifact 行为使用真实依赖。Docker backend attach 必须有真实 daemon 验收；正式 SWE-bench 支持至少一项已准备 development task 的端到端恢复证据。

| ID | 场景 | 观察结果 |
| --- | --- | --- |
| R01 | 初始化描述已提交，首次请求前中断 | 同 ID/Workspace/任务开始，消息不重复 |
| R02 | 模型请求中被杀 | 同 Step 新 attempt，未知计费可见 |
| R03 | batch 已提交未执行 | 不再请求模型，执行原调用 |
| R04 | 批内首个 shell 完成落库后中断 | 原目录/容器保留修改，不重跑，继续剩余调用 |
| R05 | shell 开始提交后，执行前或执行后未完成落库 | 两个窗口都拒绝，无新模型/工具/容器启动 |
| R06 | read_file 未提交，旧读取已结束 | 同 Call ID 新 attempt，一个最终 Tool Result |
| R07 | 全部结果已提交，Step 尚未完成 | StepCompleted 一次，成败覆盖整批 |
| R08 | final response 已提交，评估前中断 | 使用原候选，无新增模型请求 |
| R09 | 失败 response 已提交，终止前中断 | 原失败终态，无成功评估 |
| R10 | pending/approved-before-execution 时中断 | 旧授权失效，当前 Policy/新审批生效 |
| R11 | run/resume 或两个 Resume 并发 | 第二个锁冲突；OS 释放后才能进入恢复 |
| R12 | RECOVERING 或 ResumeCompleted 提交前后再次中断 | 原 Workspace/恢复点不变，无重复工具 |
| R13 | 首枚 SIGINT/SIGTERM 在安全边界 | SUSPENDED、130/143、可续跑；库取消仍 CANCELLED |
| R14 | 信号取消 shell 无可信结果 | 保留现场并拒绝，未造假 Tool Result |
| R15 | Local marker/path/基线错、目录删除、链接替换 | attach 前明确拒绝，不重建或 reset |
| R16 | Docker 原容器 running/exited | attach 相同 ID；实际 Resume 才启动 exited 容器 |
| R17 | Docker 容器删除/同名替换/label 或 daemon 变化 | 拒绝，不新建容器或降级 |
| R18 | Docker 安装了 workdir 外依赖后安全中断 | 同容器依赖保留，后续调用可用 |
| R19 | 旧受管理 command/子进程仍活动，或身份不可验证 | WORKSPACE_BUSY，不并发续跑 |
| R20 | 原 max_steps 已用完，Step 尚未结束 | 完成原 batch，不开始新 Step |
| R21 | CompactionCompleted 前/后中断 | 未提交 summary 作废；已提交版本/usage 保留 |
| R22 | dry-run，包含 stopped Docker 与旧评估资源 | 输出待启动校验/清理，无状态写入、启动、exec 或清理 |
| R23 | 旧入口/run/resume/help/trigger | 旧参数兼容，ID/退出码符合契约 |
| R24 | ERROR/TIMEOUT shell 的结果和收尾已提交 | 原文件修改保留，不重跑，失败计数正确 |
| R25 | cwd/默认配置/定价文件变化 | 使用原绝对路径、冻结定义/定价，不兼容则拒绝 |
| R26 | 多次正常暂停续跑超过 3 次 | 不因次数拒绝，审计递增，Step/usage 不重置 |
| R27 | Docker task/data/harness/image identity 不符 | 明确拒绝，不切任务/模型/evaluator |
| R28 | candidate patch 已提交后中断 | 同 ArtifactRef/hash 评估，不重新 export |
| R29 | patch export/注册未确认 | 旧 export 结束后新 attempt；不捞取孤立 artifact |
| R30 | SWE-bench harness 开始后被杀 | 旧资源已退出/受控清理后同 patch 新评估 attempt |
| R31 | 旧 harness 身份/清理无法验证 | EVALUATION_ATTEMPT_BUSY，无并发评估 |
| R32 | EvaluationResult 提交后、终态前中断 | 原结果用于终态，不重复评估 |
| R33 | Terminal、当前格式缺恢复证据或不支持的存储版本 | 拒绝，无状态重开、旧格式适配或猜测 attachment |
| R34 | 暂停诊断失败/恢复启动校验失败 | 原 Workspace 保留，恢复点保留，不改成业务 FAILED |
| R35 | 暂停诊断 patch 与 candidate patch 同时存在 | 诊断不替代候选，Resume 引用正确 |
| R36 | 父进程被杀，已登记 helper 尚存活或 start 结果未知 | helper 活动 / 发令可能性未排除时 WORKSPACE_BUSY；不因远端暂未运行而启动原容器或新执行链 |
| R37 | helper 已创建、身份或许可尚未提交时父进程被杀 | 门控 EOF / 超时退出，不发 Engine RPC；提交不明不放行，不自行重试 start |
| R38 | 终态后 diagnostic export 开始、身份落库或完成时中断 | 只追加 finalization 事实，业务 State 不变、active_operation 为空，终态不重开；暂停诊断残留未确认则后续 Resume 拒绝并发 |
| R39 | allow-image-pull 开启但评估镜像首次冻结时不存在 | EVALUATION_IMAGE_UNAVAILABLE / 退出 1；无拉取、Workspace 创建、模型请求或不完整 Run，不能 Resume |

同时检查实际副作用次数、请求数、消息协议、序列/快照版本、审批、usage、资源身份。故障注入使用确定性的外部边界屏障与真实进程终止，不靠任意 sleep 或 mock 内部实现。

实现阶段执行相关 Runtime/Storage/CLI/Environment/Evaluation 定向回归、跨进程验收和 Docker 验收；修改的 Python 文件遵循仓库 ruff check/format 与类型检查要求。本轮仅修改设计文档，不运行 runtime tests。

## 15. 自审与后续交付

已将 D1–D10 全部写成确定契约，清除原 Local-only、checkpoint、新 Workspace 和最多 3 次恢复的设计。自审重点包括原资源 ownership、未知 shell 的保守判定、Docker stopped 容器与 dry-run、评估 attempt 独立于 Agent 容器、冻结 patch/配置、暂停/终态区分，以及 batch 部分恢复。后续架构评审另补 helper 门控 / 收尾、独立 diagnostic finalization、缺失镜像首次冻结拒绝，并以 R36–R39 验收。

本文件与架构稿已按 2026-10-19 review 意见同步，可作为后续实现计划的输入。本轮只修改设计文档，不创建实现计划，不修改运行代码；后续进入 writing-plans 时再审阅计划并确定执行方式。
