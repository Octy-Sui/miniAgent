# Independent Evaluator：架构规格

日期：2026-10-10

状态：完整评审稿，等待用户集中审阅；不授权进入实现
依据：[独立校验设计](2026-10-10-independent-evaluator-design.md)

## 1. 设计意图与已确认约束

为 miniAgent 的 Docker Workspace 接入独立 SWE-bench 校验。Agent 提交候选结果后，应用层先冻结 patch artifact，再由宿主机的外部 SWE-bench 进程在新容器中校验；Runtime 只有收到完整、可信的通过结果才结束为 `SUCCEEDED / TASK_VERIFIED`。Local Workspace 保持 `PassThroughEvaluator`。

用户已确认：采用**逐任务就绪门槛**，不等待全部 25 项 gold preflight 才允许已通过预检的任务运行。用户要求先完成文档，把需要决策的事项集中到最后。本稿中其余推荐选择均为供审阅的设计，未视为已批准。

沿用原设计的约束：

- 支持清单固定为 5 个开发任务、20 个正式任务；不临时换题。
- 任务身份通过内部结构化配置提供，不从 issue、最终文本或镜像名称推断。
- 不新增 CLI `--instance-id`，不修改或依赖 `trigger`，不建设批量 runner。
- 不绑定 Agent 镜像与评测镜像，不要求 Agent 的 Git 基线等于清单基线。
- 不自动修复或重试，不建设跨进程恢复、通用评测命令框架或评测服务。
- 主项目不增加 SWE-bench、datasets、pyarrow 或 Docker Python SDK 依赖。
- 数据、gold patch、隐藏测试、宿主机 Docker socket 不挂载给 Agent。

成功标准是：一个已就绪开发任务能够走通候选 patch → 单条 predictions → 独立实例报告 → SQLite 评测证据 → 正确 Run 终态；失败、超时、取消均有证据和有界资源清理。

首版的主导质量属性为**可信判定、证据可追溯、有界资源清理**。正常返回时，判定和必要证据完整性优先于可用性：无法确认通过、证据不全或评测清理失败，均不得成功。用户取消时，有界返回和取消语义优先于证据完整性：在既定清理/收集预算内尽力保存诊断，预算耗尽后报告未完成事项并返回取消，不无限等待证据、不改判成功或一般失败。

本稿细化原设计，且以逐任务门槛替代原设计第 4、9 节中的全量启动门槛。25 项全量就绪仍是完整冻结实验的前提，不是单任务执行的前提。本文不改变任务筛选文档中的正式实验冻结规则。

## 2. 当前事实与数据基线

### 2.1 代码现状

| 接入点 | 当前行为 | 需要改变的行为 |
|---|---|---|
| `cli/bootstrap.py` | 装配 Workspace、工具、provider、store | 预检任务数据并装配 Evaluator |
| `artifacts/file.py` | `list_for_run()` 并发读取并验证全部 artifact 内容 | 汇总时逐项验证，避免同一 Run 的多份完整日志同时进入内存 |
| `environments/contracts.py`、Docker/Local 实现 | `export_patch()` 无调用预算，内部清理可能无限等待 | 通过公开导出契约约束总时长和内部清理，不调用 backend 私有接口 |
| `cli/application.py` | Runtime 返回后导出 patch | Docker 在评测前提交一次 patch，结束后复用 |
| `evaluation/contracts.py` | 已有 patch 引用、checks、error、artifacts | 保持通用协议，补充实现侧严格模型 |
| `runtime/loop.py` | 通过布尔结果决定终态 | 将评测 artifacts 一并传入终态暂存 |
| `runtime/execution.py` | 暂存 passed/output，写 `EvaluationCompleted` | 在同一终态事务中保存 artifacts |
| `cli/rendering.py` | 将 artifacts 首项当作 patch | 按 `type == workspace_patch` 查找 |

`RunResult` 当前不包含 artifacts；本设计不新增该字段。`NewRunEvent` 已有 artifacts 字段，可以复用。当前 Runtime 的取消包装会等待被取消协程完成，因此 Evaluator 的清理必须有界。当前 Runtime 没有已接入的整 Run 时长配置，本稿不假定存在该限制。

### 2.2 宿主机数据事实

目录：`/Users/octy/.miniagent/evaluation/swebench-verified-25-20261010`。

| 文件 | 数量 | 本次读取核对的 SHA-256 |
|---|---:|---|
| `tasks.json` | 25 | `d25b693c84281c5f7ee697708f2cdc5c14237b5ed16a08a63215fa1c9793cce7` |
| `development.json` | 5 | `9f24aa4548a431119d20e165e42bbe3ce65311526663413356e44260a586964d` |
| `evaluation.json` | 20 | `e61a8cd901dc1f8bcdb85843ab448f1539f2d4ba76390ae35e43deac05d21a59` |

以上摘要与 manifest 一致。manifest 声明 harness 版本 `5.0.2`、数据验证 25/25，通过预检 7 项、取消 1 项、未运行 17 项，`ready=false`。本次读取到的 7 个实例报告均为 `resolved=true`；本轮未重新执行 Docker 或 gold preflight，也未逐一重新计算源 Parquet 与报告摘要。

已通过任务为：

- 开发：`astropy__astropy-12907`、`django__django-11477`、`pytest-dev__pytest-7571`、`sympy__sympy-13647`、`pydata__xarray-3677`。
- 正式：`astropy__astropy-14539`、`django__django-16485`。

这些是设计时快照，不在代码中硬编码为就绪集合。运行时以读取并校验后的 manifest 和报告为依据。`USAGE.md` 记录当前宿主机准备流程停止继续下载镜像；本轮不修改这一状态。

原始数据固定 revision 为 `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`，5.0.2 兼容数据固定 revision 为 `78f471bf655a3137b2e8a75af1501690ec009ec3`。来源摘要沿用准备 manifest。正常运行只读 `tasks.json`、manifest 和所选任务的预检报告，不读取 gold predictions、源 Parquet 或准备脚本。

## 3. 方案比较与选择

| 方案 | 好处 | 代价 | 判断 |
|---|---|---|---|
| 应用层提交适配器 + 独立 `SWEbenchEvaluator` | 复用 Evaluator 协议；Workspace 与 harness 各有明确边界；失败仍由 Runtime 决定 | 应用层需要一次提交状态与证据汇总 | 推荐 |
| Runtime 直接导出 patch 并启动 harness | 调用链短 | Runtime 依赖 Workspace、ArtifactStore 与 SWE-bench 特有配置，影响通用性 | 不采用 |
| Runtime 返回后由 Application 校验 | finalization 改动小 | 已写入成功终态后才评测，必须改写历史或另设最终状态 | 不采用 |

不增加任务调度层、通用 subprocess 框架或第二套 artifact 存储。进程执行、报告解析、Docker 清理使用评测包内的私有函数，只有清晰的独立职责才拆模块。

## 4. 组件与依赖方向

| 单元 | 职责与使用方式 | 依赖 | 不承担的职责 |
|---|---|---|---|
| `BootstrapConfig` | 接收可选 `SWEbenchTaskContext` | 应用配置模型 | 解析任务自由文本 |
| 数据加载与预检函数 | 读取冻结清单、manifest、任务记录与预检报告，返回绑定的已验证记录 | Path、Pydantic、JSON、摘要计算 | 下载、准备数据、执行 gold |
| Docker 连接预检函数 | 在 bootstrap 解析并冻结评测 endpoint/TLS 参数，产生内部连接值 | 宿主环境快照、Docker CLI context inspect | 改变当前 context、管理连接池 |
| `CandidateSubmission` | 按 Run 导出/登记一次 patch，记住提交状态 | Workspace、ArtifactStore | 理解 SWE-bench |
| `WorkspaceCandidateEvaluator` | 提交 patch，构造带 patch 的通用请求，调用下游 | CandidateSubmission、Evaluator | 决定 Run 状态 |
| `SWEbenchEvaluator` | 写 inputs、执行 harness、判断报告、登记证据并清理 | 已验证记录、宿主配置、ArtifactStore | 调用模型或 Agent 工具 |
| `AgentRuntime` / `RunExecution` | 管理 Evaluation 生命周期、取消与终态事务 | 通用 Evaluator 与 RuntimeStore | 解析 SWE-bench 报告 |
| `Application` | 汇总证据、补救导出、决定 Agent Workspace 销毁 | 原依赖与 CandidateSubmission | 改写已持久化 Run 状态 |

建议位置：

- `cli/candidate_submission.py`：提交状态与应用层适配器。
- `evaluation/swebench_data.py`：外部数据模型、逐任务就绪校验。
- `evaluation/swebench.py`：具体 Evaluator、报告模型与私有执行/清理函数。
- `artifacts/file.py`：仅调整 `list_for_run()` 内容校验的并发方式，保持公开返回及完整性契约。
- `environments/contracts.py`、Docker/Local Workspace：落实 patch 导出与内部清理预算，同步实现和调用方。
- 原有 bootstrap/settings/application/runtime/rendering：只做接入所需修改。

这些名称表达职责，不要求为每个函数新增类或接口。真实与受控 harness 均通过配置可执行文件实现，不替换项目内部实现。

## 5. 配置与数据契约

### 5.1 内部任务入口

`SWEbenchTaskContext` 使用 frozen、extra-forbid 的 Pydantic 模型：

| 字段 | 类型 | 语义 |
|---|---|---|
| `instance_id` | 非空 `str` | 必须精确匹配冻结清单 |
| `evaluation_image` | 非空 `str` 或 `None` | 覆盖本次评测镜像；不覆盖 Agent 镜像 |

`BootstrapConfig.swebench_task: SWEbenchTaskContext | None = None` 是内部程序调用入口。调用方自行提供与任务身份一致的 issue 文本和 Agent 基线，Evaluator 不猜测或纠正它们。

普通 Docker CLI 调用没有结构化任务入口，因此会在准备阶段明确失败；这是原设计已声明的产品限制。Local 不加载评测数据、不运行版本或 Docker 检查，传入任务上下文也不启用 SWE-bench。

### 5.2 宿主机配置

推荐在 Settings 中增加独立字段，由环境变量按现有规则覆盖：

| 字段 | 推荐默认 | 验证 |
|---|---|---|
| `swebench_data_root` | `~/.miniagent/evaluation/swebench-verified-25-20261010` | Docker 预检时目录可读 |
| `swebench_work_root` | `~/.miniagent/evaluation/runs` | 可创建独立目录，不在 Agent Workspace 内 |
| `swebench_executable` | `swebench` | 启动前解析为实际可执行文件并检查版本 |
| `evaluation_docker_executable` | `docker` | 启动前检查 daemon 可用 |
| `candidate_submission_timeout_seconds` | `60` | 包含导出、导出内部清理与 patch 登记，必须大于导出清理预留 |
| `patch_export_cleanup_timeout_seconds` | `5` | 正数，属于提交总预算内的预留，不追加到总时长 |
| `swebench_test_timeout_seconds` | `1800` | 正整数 |
| `swebench_process_timeout_seconds` | `2400` | 大于测试超时，包含 harness 启动和报告生成 |
| `swebench_cleanup_timeout_seconds` | `30` | 正数，作为总清理预算 |
| `swebench_allow_image_pull` | `false` | 首版仅表示是否允许 harness 自动拉取缺失镜像 |

workers 固定为 1，harness 兼容版本固定为 5.0.2。版本预检子进程限时 10 秒，Docker 准备检查每次限时 10 秒。评测 artifact 登记与 Application 最终证据收集各使用一个阶段总预算，推荐各 30 秒；候选 patch 登记归属提交总预算，不再获得额外登记预算。CLI 输出的错误摘要最多 4096 字符，完整输出保存到文件。以上时间为建议默认，统一在文末审阅。

### 5.3 外部记录与 manifest

正常任务记录只允许 `instance_id`、`repo`、`version`、`base_commit`、`image`、`eval_script`、`log_parser`、`eval_type`、`FAIL_TO_PASS`、`PASS_TO_PASS`。禁止 `patch`、`test_patch`、`hints_text`、`problem_statement` 等非评测输入；隐藏测试已经在 `eval_script` 内。

测试清单归一化为 `list[str]`：接受实际列表，或显式解析准备文件中的 JSON 列表字符串；拒绝其他类型，不用字符串包含判断。脚本保持原字符串不变，必须非空。任务 ID 唯一，25 项集合及 5/20 分组精确匹配正式清单。

manifest 模型严格验证本功能消费的字段、摘要和逐任务状态；允许未消费的准备元数据扩展，保留原 bytes 的摘要用于追溯。没有 schema version 的现有 manifest 按当前冻结格式读取，不强制改写数据；未来不兼容格式应另设版本并拒绝误读。

正式支持清单需要成为项目交付输入：推荐将现有安全的任务基础清单复制为包内资源，bootstrap 不依赖当前工作目录或未跟踪 docs 文件。实现前核对内容确实不带 gold 或隐藏测试。文档和清单的同步只涉及本功能，不修改 README。

## 6. 逐任务就绪与准备流程

Docker bootstrap 在创建 Agent Workspace、调用模型前顺序完成：

1. 校验任务上下文、支持清单与宿主配置。
2. 读取 manifest 和 `tasks.json` 为当前 Run 的内存快照，校验文件摘要、25 项唯一性及分组；校验 manifest 的清单摘要与正式清单一致。
3. 确认所选任务 `gold_preflight.status == passed` 且 `resolved is True`。
4. 将所选预检报告路径 resolve 后限制在数据目录内；检查文件摘要，按严格报告契约核对任务 ID 与通过结论。
5. 确认预检 manifest 的 harness 版本与实际版本均为 5.0.2。版本变化不能复用旧准备证据后继续运行。
6. 按第 6.1 节解析并冻结评测 Docker 连接，再通过该连接检查 daemon 和实际镜像引用。禁止拉取时，镜像缺失就准备失败；允许拉取时交给 harness，但整体超时仍生效。
7. 创建 Agent Workspace，装配提交适配器与 SWEbenchEvaluator，再进入 Runtime。

`manifest.ready=false` 不阻止满足步骤 1–6 的单任务。未运行、取消、失败、报告缺失/损坏都不能视为就绪。数据不合法时整包拒绝，未预检任务只拒绝该任务。启动时不执行 gold preflight、不下载数据、不修改共享 manifest。

内存快照避免 Run 中途因共享文件更新而悄悄换规则。独立工作目录中的单任务输入由这份快照生成，结果记录快照摘要。后续 Run 可以读取新 manifest，但任务身份、来源和规则变更必须有明确的数据版本。

对默认镜像，预检证据绑定 manifest 内的镜像引用。对覆盖镜像，所选任务仍须已就绪，但默认镜像的 gold 证据不被当作覆盖镜像证明。推荐保持原设计允许覆盖：记录 `image_preflight_verified=false`，实际独立校验通过仍可成功；正式冻结实验不得把覆盖结果混入默认镜像批次。是否额外要求覆盖镜像预检列为文末决策。

这里的准备失败发生在 Runtime 启动前，沿用 bootstrap 异常契约，不伪造一个已运行的 Run 或终态。内部异常应包含稳定错误码与任务/路径上下文，CLI 只捕获已知准备错误并输出简明失败，未知异常继续传播。

### 6.1 Docker 连接绑定契约

已安装 harness 使用 `docker.from_env()`，其 SDK 读取 `DOCKER_HOST`、`DOCKER_CERT_PATH`、`DOCKER_TLS_VERIFY`，不会解析 Docker CLI 的当前 context。Docker CLI 的 `DOCKER_CONTEXT` 又会覆盖 `DOCKER_HOST`。因此不能以“继承相同环境”作为连接一致性证明。[Docker CLI 环境变量](https://docs.docker.com/reference/cli/docker/)、[Docker contexts](https://docs.docker.com/engine/manage-resources/contexts/) 定义了 CLI 的选择行为。

bootstrap 从一次宿主环境快照按以下顺序解析连接来源，生成 frozen 内部连接值，不增加对外连接管理框架或新的用户配置入口：

1. 非空 `DOCKER_CONTEXT`：通过 `docker context inspect <name>` 读取该 context 的 `Endpoints.docker.Host`、`SkipTLSVerify`、`TLSMaterial` 与 `Storage.TLSPath`，不采用同时存在的 DOCKER_HOST/TLS 环境覆盖该 context。
2. 没有显式 context 且 `DOCKER_HOST` 非空：使用该 endpoint，并按本机 SDK 的规则解析证书目录和 TLS 验证参数；证书目录或 TLS 验证启用时按 TLS 连接处理。不明确或只有 `DOCKER_TLS` 启用的配置拒绝，不能让 CLI 启用 TLS 而 SDK 使用明文。
3. 两者均未设置：用 `docker context show` 得到当前 context 的名字，再按步骤 1 inspect；不能让 SDK 自行回退到 `/var/run/docker.sock`。

冻结值只包括来源 context（如有）、endpoint、TLS 模式和证书路径。首版接受 `unix://` 无 TLS、`tcp://` 无 TLS，以及 `tcp://` 且验证开启的 TLS；Unix socket 路径和证书路径解析为绝对路径。TLS 要求可读的 `ca.pem`、`cert.pem`、`key.pem`。context 的证书取自 inspect 指定的 TLS 存储目录下 docker endpoint 的材料；环境输入的证书目录为空但 TLS 验证已设置时，按 SDK 使用 `~/.docker`。不导出或复制私钥到 attempt/artifact。

拒绝 SSH/npipe endpoint、缺失 endpoint、损坏 context、缺少 TLS 材料，以及需要跳过证书验证的 TLS context/环境配置；返回 `DOCKER_CONNECTION_UNSUPPORTED` 或 `DOCKER_CONNECTION_INVALID`，不静默退回另一个 daemon。这里明确收窄首版连接兼容范围，避免为不需要的连接类型新增代理或 SDK 依赖。

实际执行统一从冻结值构造连接参数：

- 对所有评测 Docker CLI 预检、image inspect、容器查询/删除，显式传 `--host <endpoint>`；TLS 时显式传 `--tlsverify` 及 `--tlscacert/--tlscert/--tlskey` 路径。
- 对 harness，构造进程环境副本，设置 `DOCKER_HOST`；TLS 时设置 `DOCKER_TLS_VERIFY=1` 和 `DOCKER_CERT_PATH`。无 TLS 时移除这些 TLS 变量。
- 两类子进程都移除原 `DOCKER_CONTEXT`、`DOCKER_TLS` 及未采用的 TLS 环境覆盖，不修改父进程环境。CLI 不再通过当前 context 决定 endpoint。保留必要的 Docker 客户端配置用于 registry 认证，但 daemon 连接不从其中重新选择。
- 后续步骤不得再次读取“当前 context”或动态宿主环境；准备后 context 改变不改变此 Run 的镜像检查、harness 与清理连接。

在该显式连接上执行有界 `docker info`，记录 daemon ID（缺失时准备失败）。执行前及清理前再次核对 ID，若 endpoint 后方 daemon 已改变，禁止对新 daemon 删除容器，记录 `DOCKER_DAEMON_CHANGED` 与未确认资源身份，不宣称本次容器已清理。相同 endpoint/参数绑定保证正常期间的目标选择，不宣称能处理远端 daemon 被替换或连接断开的自动恢复。

该绑定属于评测链路；Agent Workspace 后端保持现有连接方式，不要求两个 Workspace 在同一 daemon。artifact 记录评测连接来源、endpoint、TLS 是否验证与 daemon ID，证书只记来源/路径信息，不保存私钥、证书内容或完整宿主环境。

## 7. 候选 patch 的唯一提交

`CandidateSubmission` 是 Run-scoped 的简单内部对象，保存以下状态：未尝试、提交成功、空 patch、提交失败，以及 patch ref 和诊断错误。状态不能只由 `patch_ref is None` 推断，因为空 patch 与未尝试不同。

`CandidateSubmission` 在首次提交尝试开始时创建一个 `submission_id: UUID`，在导出/登记前保存，正式候选与提前终止后的诊断导出均使用该规则。重复读取状态不创建新标识。该对象不创建或保存 SWE-bench attempt；诊断 patch 可以完全没有评测 attempt。

提交流程：

1. 通过第 7.1 节的公开 `Workspace.export_patch()` 有界契约，相对于 Workspace 的真实 `base_revision` 导出 Git binary patch。
2. 空字符串记录为空候选，不启动 harness；不 strip 空白、不把仅含空格的非空内容擅自当作空 patch。
3. 非空内容按 UTF-8 bytes 登记 `workspace_patch`，元数据保留 files、真实 base_revision。
4. 保存返回 ref，后续校验和交付均使用该 ref。
5. 通过 Pydantic 构造新 CandidateOutput/EvaluationRequest，保留 text/run_id/task/plan，只替换 patch。

首次导出或登记失败后不自动重试；适配器返回 `passed=false` 和具体错误码，让 Runtime 进入评测失败。记录已有 artifact 和原始工作目录，避免同一次提交产生第二份不同 patch。

正常提交无论通过、失败或取消，Application 都复用已提交引用，不再次导出。只有 Runtime 尚未进入候选提交（例如模型失败、工具失败、提前取消），Application 才调用同一提交对象执行一次诊断导出。Local 继续在 Runtime 结束后导出，行为与现有路径一致。

取消若落在 artifact 发布过程中，登记操作可能已经完成。Application 通过 Run 下 artifact 清单识别已完成登记的提交；按 metadata 中的提交标识去重，不仅依靠内存赋值是否发生。FileArtifactStore 的线程操作取消不会自动终止线程，不声称它与 asyncio 取消具有事务关系；无法确认时保留 Workspace 和工作目录并报告证据收集错误，不重新生成已校验 patch。

### 7.1 提交总预算与 Workspace 公开契约

候选提交从首次尝试（创建 submission_id）开始，以单调时钟计算一个总 deadline，推荐 60 秒；锁等待、导出、导出内部清理、UTF-8 转换及 patch 登记均属于这一预算。提前终止后的诊断导出使用相同规则，每个 Run 仍只尝试一次。不能在每个 Git 命令、清理或 put 前重新开启完整预算。

Workspace 公开接口扩展为带默认值的 keyword-only 参数：

```python
async def export_patch(
    self,
    *,
    timeout_seconds: float = 60.0,
    cleanup_timeout_seconds: float = 5.0,
) -> Patch: ...
```

`timeout_seconds` 是从调用到返回/抛出的总预算，包含锁等待及内部 finally 清理；`cleanup_timeout_seconds` 是此总预算内的最大清理预留，须为正且小于总预算。调用方可不传参数，但导出不再无限等待。CandidateSubmission 传入提交 deadline 的剩余时间，若已不足以保留清理预算就直接报告提交超时，不启动导出或登记。正常导出提前结束后，put 只能使用提交剩余时间。

Workspace 在自己的调用预算内为清理预留时间，工作阶段在总 deadline 减去预留时截止；正常完成或被取消/超时后，finally 最多使用预留与总剩余时间中的较小值。Docker 临时 index 删除（含相应 CLI 进程终止/回收）使用该剩余 deadline；Local 临时索引/文件清理与 Git 子进程回收遵循同一契约。所有子进程等待、流任务取消回收都计入所属 deadline，不能用无时限的 `process.wait()` 或 gather 延长清理。该约束通过 Workspace 实现向下传递，提交适配器不访问 backend 私有方法。

导出内部清理在受保护任务中执行，首次取消不能跳过清理，但 shield 不解除 deadline。预算耗尽后停止追加清理命令，记录临时 index/文件路径、容器 ID、尚未回收的 CLI PID 及原因；保留 Agent Workspace，不自动销毁、不开始 harness attempt。尚未确认结束的远端 Git 命令也不能宣称已回收；独立临时 index 不应覆盖仓库真实 index。这里不新增持久清理任务或跨进程恢复。

非取消路径：提交总预算耗尽返回 `CANDIDATE_SUBMISSION_TIMEOUT`；导出内部清理失败且尚无其他主错误时返回 `PATCH_EXPORT_CLEANUP_FAILED`，即使 diff 已生成也不提交为成功。已有导出/登记主错误保持原错误，附加清理诊断。

取消路径：Workspace 将清理失败/超时的资源诊断附到原 CancelledError，CandidateSubmission 在公开提交状态中保留这些诊断，然后重新抛取消；不得用清理 TimeoutError 覆盖 CancelledError。Application 将其作为 `patch_export` finalization 诊断交付，Runtime 仍为 CANCELLED。取消发生在 attempt 创建前，因而不能依赖 SWEbenchEvaluator 的 harness 清理预算。

Runtime 的取消包装仍等待提交协程退出；等待有界性来自上述 Workspace 契约，不只来自适配器外层的 asyncio 超时，因为外层超时也会等待被取消协程的 finally。线程形式的文件读写只能停止后续操作，不能强杀在执行的线程；不得阻塞事件循环或等待线程无限结束。单个同步文件操作无法被抢占，系统停顿或不可中断 I/O 不在严格墙钟保证之内，必须保留这个限制。

## 8. Harness 执行与输入输出

### 8.1 独立目录

`SWEbenchEvaluator` 收到已登记的非空 patch 并验证引用后，在创建目录/生成输入前创建一个 `attempt_id: UUID`；空 patch 或提交失败不创建 harness attempt。使用 `work_root/<run_uuid_hex>/<attempt_uuid_hex>/`，以不复用的目录创建语义保证独立性：

```text
task.json
predictions.jsonl
evaluation-config.json
stdout.log
stderr.log
logs/run_evaluation/<harness_run_id>/<safe_model_key>/<instance_id>/
    patch.diff
    eval.sh
    report.json
    test_output.txt
    run_instance.log
reports/
evaluation-summary.json
```

`harness_run_id` 由 Run UUID 与 attempt UUID 拼成受控字符串。`safe_model_key` 推荐使用 `model-<模型名 UTF-8 SHA-256 的前 16 位>`；原始模型名保存在配置中，predictions 的 `model_name_or_path` 使用安全标识。模型名只表示提交来源，不表示任务身份。

instance_id 必须来自冻结清单，禁止任意路径片段。报告路径固定计算，不递归寻找“某个 report”。输入、日志与报告路径 resolve 后都须在本次目录内，不接受 symlink 指向外部的报告。

### 8.2 Predictions 无损契约

ArtifactStore 验证并读出完整 patch bytes，严格 UTF-8 解码。使用标准 JSON 序列化生成一条记录，加一个物理换行后按 bytes 写文件；不用文本模式的通用换行转换。字段只有 `instance_id`、`model_name_or_path`、`model_patch`。

反序列化后的 `model_patch.encode('utf-8')` 必须与 artifact bytes 完全一致，包括末尾换行、CRLF、Unicode、引号、反斜杠、模式、重命名及 Git binary patch 文本。转换无损不等于 harness 能应用所有 patch；可应用性由真实评测决定。

### 8.3 调用契约

通过 `asyncio.create_subprocess_exec` 使用 argv，`cwd` 为 attempt 目录：

```text
swebench eval <absolute-task.json>
  --predictions <absolute-predictions.jsonl>
  --instance <instance_id>
  --run-id <harness_run_id>
  --workers 1
  --timeout <test_timeout_seconds>
  --report-dir <absolute-reports-directory>
```

本机 5.0.2 的实例报告在 `cwd/logs/run_evaluation/.../report.json`；`--report-dir` 是汇总报告目录，不能据此定位实例判定。该版本没有 `--image`，镜像覆盖仅修改本次 task.json 的 `image`，不改共享 tasks.json。

stdout/stderr 直接落文件，避免 PIPE 填满或无限内存缓冲。使用独立进程组，在取消/超时时终止整个组，回收主进程；首版支持本项目实际使用的 macOS/Linux POSIX 宿主，不宣称 Windows 支持。

默认不允许镜像拉取时，准备和执行前各通过第 6.1 节冻结连接检查一次镜像存在；harness 使用该连接生成的显式环境。检查与 harness 使用之间无法在外部 CLI 模式下做到原子，因此缓存被其他进程删除的竞态仍可能触发上游拉取。该配置不宣称强网络隔离；需要绝对离线保证时应另做执行约束，不在首版伪造保证。

## 9. 报告判定与错误模型

报告使用严格 Pydantic 模型验证消费字段，允许上游增加未消费字段。`resolved` 等布尔字段使用严格 bool，不能把字符串 `"true"` 或整数 1 强制转换为通过。顶层必须只包含本次 instance_id。

一个通过结果必须同时满足：进程退出 0；本次固定路径实例报告存在且有效；patch_exists=true、patch_is_None=false、patch_successfully_applied=true、resolved=true；若报告含 infra_failure 则必须为 false；tests_status 的 FAIL_TO_PASS/PASS_TO_PASS 成功与失败列表结构有效，覆盖任务要求的测试且无失败。矛盾、遗漏、重复或不在规则内的必需测试证据不能用 resolved=true 掩盖。

报告与提交的关联依靠唯一 cwd/run_id/model_key/instance_id、Evaluator 写入的 predictions 摘要，以及 harness 生成的 patch.diff bytes 与提交内容一致；报告本身没有 patch digest，不能声称它提供签名或独立摘要绑定。正常运行缺少 patch.diff 或不一致时返回报告关联错误。

有效且 `resolved=false` 返回候选失败；可以含测试失败或 patch 未适用，不把 unresolved 一律归为基础设施错误。测试未运行、数据结构不完整或执行层异常都不能通过。

| 阶段/条件 | 推荐错误码 | 结果 |
|---|---|---|
| Docker 缺上下文/不支持 ID | `TASK_CONTEXT_REQUIRED` / `TASK_UNSUPPORTED` | 准备失败，模型不调用 |
| 数据/摘要/版本不合法 | `EVALUATION_DATA_INVALID` / `HARNESS_VERSION_MISMATCH` | 准备失败 |
| Docker 连接非法/不支持 | `DOCKER_CONNECTION_INVALID` / `DOCKER_CONNECTION_UNSUPPORTED` | 准备失败，不回退其他 daemon |
| 绑定 endpoint 的 daemon ID 改变 | `DOCKER_DAEMON_CHANGED` | 启动前拒绝；清理时停止删除并记录未清理诊断 |
| 任务预检未通过 | `TASK_NOT_READY` | 准备失败 |
| 禁止拉取且镜像不存在 | `EVALUATION_IMAGE_UNAVAILABLE` | 准备失败，或执行前评测失败 |
| 导出/登记 patch 失败 | `PATCH_EXPORT_FAILED` / `ARTIFACT_REGISTRATION_FAILED` | 评测失败 |
| 提交总预算耗尽/导出清理失败 | `CANDIDATE_SUBMISSION_TIMEOUT` / `PATCH_EXPORT_CLEANUP_FAILED` | 正常路径评测失败；取消路径保留取消语义并附诊断 |
| 空候选 | `EMPTY_PATCH` | 评测失败，不启动 harness |
| patch 无法读取/非 UTF-8 | `PATCH_ARTIFACT_INVALID` | 评测失败 |
| 进程无法启动/退出非零 | `HARNESS_START_FAILED` / `HARNESS_PROCESS_FAILED` | 评测失败 |
| 整体进程超时 | `EVALUATION_PROCESS_TIMEOUT` | 评测失败并清理 |
| harness 测试超时证据 | `EVALUATION_TEST_TIMEOUT` | 评测失败并清理 |
| 报告缺失/无效/关联错误 | `REPORT_MISSING` / `REPORT_INVALID` / `REPORT_MISMATCH` | 评测失败 |
| 完整报告不通过 | `TASK_UNRESOLVED` | 候选失败 |
| 证据登记失败/超时 | `EVALUATION_ARTIFACT_FAILED` | 评测失败 |
| 评测容器清理失败 | `EVALUATION_CLEANUP_FAILED` | 正常返回路径不判成功 |
| 用户取消 | 不转换为 EvaluationError | Run 取消 |

harness 测试超时可能退出 0 但不生成 report；只在本版本明确的超时标记存在时分类为测试超时，否则保留 REPORT_MISSING 和完整日志，不用任意模糊字符串猜测。若非零退出伴随有效报告，仍以进程失败为主，保留报告供诊断。

多重故障保留首次主错误，清理/收集失败写入 summary 的附加诊断；若原判定通过但清理或必要证据登记失败，该错误成为主失败原因。所有错误首版 `retryable=false`，不增加重试路径。

## 10. Artifacts、持久化与 ApplicationResult

| Artifact type | 内容 | 产生条件 |
|---|---|---|
| `workspace_patch` | 唯一候选或诊断 patch | 非空导出成功 |
| `evaluation_predictions` | 单条 predictions.jsonl | 输入生成成功 |
| `evaluation_config` | 实际配置与来源摘要 | attempt 已创建 |
| `evaluation_summary` | verdict、错误、清理/登记诊断 | 正常或异常/取消结束，尽力登记 |
| `evaluation_report` | 实例 report.json 原 bytes | 文件存在，含无效报告也保存 |
| `evaluation_stdout` / `evaluation_stderr` | harness 原始进程日志 | 进程启动后 |
| `evaluation_test_output` / `evaluation_instance_log` | 隐藏验证日志 | 文件存在 |

evaluation_config 包含 Run/attempt/instance ID、原始模型名与 safe key、patch ref/SHA-256、manifest/tasks/所选记录摘要、harness 实际版本/路径、默认与实际镜像引用、可取得的 Docker image ID、image_preflight_verified、冻结 Docker 连接的非敏感信息、daemon ID、超时/拉取策略和 argv。额外记录 Agent Workspace 的真实基线用于诊断，不把它当作评测基线。

`workspace_patch` metadata 使用 submission_id、提交类型（candidate/diagnostic）、阶段，以及已有 files/base_revision，不要求 attempt_id 或 instance_id。`evaluation_*` metadata 使用 attempt_id、instance_id、阶段；evaluation_config/summary 记录 patch artifact_id、patch SHA-256 与从 patch record 读取的 submission_id，关联提交和评测。该关联由 SWEbenchEvaluator 建立，不让 CandidateSubmission 理解 SWE-bench 状态；不存在评测 attempt 的诊断 patch 也具有完整提交身份。

摘要不包含完整隐藏测试。完整报告/日志可作为宿主机 artifact 交付，但不会进入下一次模型上下文。本次无自动修复，所以不回传隐藏测试给 Agent。

成功路径必须登记 patch、predictions、config、summary、有效报告、stdout/stderr、test_output、run_instance.log；缺失必要证据不能成功。失败路径保存已经生成的文件，不要求尚未产生的测试日志存在。登记失败保留原目录与已完成引用。

Runtime 正常收到 EvaluationResult 后，暂存 passed/output/artifacts，在已有 finish 事务内写入携带 artifacts 的 EvaluationCompleted、RunFinished 和终态 snapshot。不新增 SQLite 表或 schema。必要时扩展 trace_context 的事件构造参数，让 artifacts 进入 NewRunEvent；不要将 SWE-bench 专属字段塞入事件 payload。

取消时 Evaluator 清理并登记已生成的诊断，然后重新抛出 CancelledError，由现有取消包装转换为 CancellationRequested。此时没有 EvaluationResult，不写伪造的 EvaluationCompleted。Application 在 Runtime 返回取消结果后通过 ArtifactStore.list_for_run 汇总已登记证据；取消终态的 SQLite 事件不保证有全部 artifact 引用，artifact manifest 通过 run_id/attempt_id 仍可追溯。若要求取消事件也必须携带引用，需新增通用证据通道，见文末决策。

Application 获取评测 artifacts 的**唯一交付来源是 `ArtifactStore.list_for_run(run_id)` 返回的已验证 manifests**。EvaluationResult.artifacts 只进入 Runtime 的评测事件，不经 RunResult 或额外可变状态回传 Application；Application 不读取 Runtime 私有状态、不从 journal 反查引用，也不在评测适配器中新增结果暂存通道。CandidateSubmission 公开状态仅保存自己提交的 patch ref、submission_id 和提交诊断。

ApplicationResult 正常汇总时从该 manifests 列表构造 refs，按 artifact_id 去重，恢复提交 patch 身份并交付所有已登记评测证据。读取/汇总失败作为新增 `artifact_collection` finalization stage 报告，保留 Workspace；不让汇总错误掩盖已有 Run 失败或取消。RunResult 不增加 artifact 字段。

`list_for_run()` 保持全量验证通过才返回的契约，失败或超时不返回部分记录。此时 ApplicationResult 不承诺交付其他 evaluation refs：它们仍可通过 Run/attempt 对应的磁盘目录、artifact manifests 或正常完成的 SQLite 评测事件追溯。若 CandidateSubmission 在内存中已经保存 patch ref，Application 可在**同一收集 deadline 的剩余时间内**通过公开 `get_record(ref)` 单独验证该 patch；验证成功才将其作为唯一剩余 artifact 交付，失败/无剩余时间则 artifacts 为空并附诊断。不重复全量扫描，不读取未经验证的部分 manifests 来凑齐交付；put 取消时未在内存保存 ref 的 patch 也不保证恢复。

汇总继续通过公开的 `ArtifactStore.list_for_run()` 校验并读取记录，不绕过完整性校验。针对当前 FileArtifactStore 的最小修改是把内容校验从 `asyncio.gather()` 改为逐个 await：同一次调用只校验一个 artifact 内容，释放该文件 bytes 后再开始下一个，不把内容放入返回列表。返回仍是 ArtifactRecord 列表，所有内容都校验通过才返回，损坏内容仍 fail-fast。

Evaluator 登记完整文件也按顺序执行，读一份、put 一份、释放 bytes 后再处理下一份；不批量读入所有日志，不并行 put。每个 Run 的 Application 仅执行一次最终全量证据汇总，并复用成功返回的列表完成提交恢复、去重与展示；全量失败后的单 patch 验证遵守上述剩余预算，不构成第二次全量扫描。早期异常路径若需要诊断汇总，也执行同一规则，不重复读取所有日志。

顺序校验消除了同一次汇总中多份完整日志并存的峰值，但仍会重新读取每份内容，总 I/O 与总 artifact bytes 成正比；峰值仍是最大单文件级，而非固定小内存。不同 Run、独立 store 调用或取消后尚未完成的 `to_thread()` 可并存，首版不提供全宿主内存配额。不得以顺序 await 宣称底层线程可以被强制取消；收集预算耗尽后不再启动后续校验，保留 Workspace/目录并报告错误，已有线程最多继续当前单文件操作。

CLI 按 type 查 patch，可附加显示 evaluation_report/evaluation_summary 的 URI；不打印隐藏测试详情，不依赖列表顺序。继续沿用现有失败与信号退出码规则。

## 11. 超时、取消与清理

进入 harness 前的候选导出/登记按第 7.1 节提交总预算执行，包含导出内部清理；提交失败、超时或取消不创建 attempt。整体 harness 超时从启动前开始覆盖进程创建、镜像拉取、容器启动、测试与报告生成。输入与评测 artifact I/O 单独有界；harness 清理预算独立于进程超时，所以单次 Evaluator 最长还可能增加证据收集和清理时间。Application 最终汇总（含可选单 patch 验证）只使用一个收集总预算。所有内部子操作都受所属阶段剩余预算限制，不能每一步重新获得完整预算。

正常退出也检查本次容器已消失；失败、取消、超时按以下顺序清理：

1. 对进程组发送 TERM，最多等待 5 秒；未退出则 KILL，在剩余预算内回收。
2. 通过第 6.1 节冻结 endpoint/TLS 查询并核对 daemon ID，再查询容器名称；宿主机做完整名称匹配，只接受 `sweb.eval.<instance_id.lower()>.<harness_run_id>` 或该名称后加一个数字时间戳的上游冲突后缀。连接失败或 daemon ID 改变时，不在其他 daemon 上尝试删除。
3. 保存待清理容器的 ID、名称、image ID，再按 ID 删除；不使用宽泛前缀直接批量删除。
4. 验证目标容器已不存在，记录删除失败与未清理身份；不删除共享镜像、不清理其他 Run/Agent 容器。
5. 汇总现有文件并按顺序登记诊断，使用独立证据收集预算，保留工作目录。预算耗尽则记录尚未登记的文件和原因，不因取消要求完整证据而无限等待。

清理在独立任务中执行并 shield 首次取消，仍受总预算限制；仅 shield 后马上放任后台任务继续运行不满足本设计。第二次信号是 CLI 的强制退出语义，只做剩余预算内的尽力清理，不承诺 SIGKILL/宿主崩溃后的回收；原目录与 attempt 身份可供人工排查，不实现恢复守护进程。

image ID 优先来自实际评测容器 inspect；若容器已被 harness 删除，记录执行前后 inspect 与来源，无法观察实际 ID 时允许为 null 并记录原因，不把可变 tag 的预检查 ID 冒充实际容器 ID。

正常通过且 Application 交付/收集无错误才销毁 Agent Workspace。失败、取消、提交/收集/销毁错误均保持现有保留策略；已结束成功 Run 遇到 Agent Workspace 销毁失败仍是 SUCCEEDED，但 ApplicationResult 有 finalization error、CLI 非零，沿用现有契约。独立评测容器清理则在 Evaluator 返回前完成，因此清理失败可阻止 Run 成功。

attempt 目录首版一律保留，不自动删除，生命周期归宿主机操作管理；不增加新的 GC 命令。

## 12. 关键数据流与不变量

正常路径：Bootstrap 预检 → AgentRuntime 有界 Steps → EvaluationStart → 应用层唯一提交 → SWEbenchEvaluator 独立执行/清理/登记 → EvaluationResult → 终态事务 → Application 汇总 → 成功时销毁 Agent Workspace。

模型提前失败路径：Runtime 失败终态 → Application 一次诊断导出 → 汇总 → 保留 Agent Workspace。

评测中取消路径：取消 evaluator 协程 → 有界进程/容器清理及诊断登记 → 重新抛取消 → Runtime CANCELLED → Application 复用已提交 patch 并汇总 → 保留 Agent Workspace。

提交中取消路径：取消提交协程 → Workspace 在提交预算内清理临时 index/CLI 进程并保留诊断 → 重新抛取消 → Runtime CANCELLED → Application 在收集预算内交付可验证证据 → 保留 Agent Workspace；没有 harness attempt，不启动第二次提交。

必须维持以下不变量：

1. 每个 Run 最多提交一个正式候选 patch；校验内容与最终交付内容一致。
2. Run 成功前已完成评测和必要证据登记；Application 不改写终态。
3. 一个 attempt 只对应一个任务、一个候选和一个唯一 harness run_id。
4. 未就绪、无报告、不完整报告、错误任务、非零退出、证据失败均不能成功。
5. Local 不依赖 SWE-bench 或评测数据存在。
6. 共享准备数据只读；取消不转换为一般评测失败。
7. Agent Workspace 与评测容器分别管理，不复用已修改容器。

## 13. 验证与验收证据

测试以公开行为、真实文件/ArtifactStore/SQLite/Workspace 为主。外部 harness/Docker 边界允许受控可执行文件；不 mock 内部导出、持久化或报告判断函数。

| 覆盖目标 | 最小充分证据 |
|---|---|
| 逐任务门槛 | 小型真实数据包：全局 ready=false 但所选任务通过可启动；未预检/取消/摘要错误拒绝，模型未调用 |
| 冻结清单与数据快照 | 重复/清单外 ID、分组差异、规则文件摘要不符拒绝；启动后共享文件变动不改变已绑定输入 |
| Local 与 Docker 装配 | Local 在无 harness/数据目录时仍通过；Docker 缺上下文准备失败；内部结构化入口正确绑定 |
| Docker 连接绑定 | 受控 CLI context 指向 endpoint A、SDK 默认指向 B：预检/执行/清理全部显式使用 A；DOCKER_CONTEXT 覆盖 DOCKER_HOST；启动后 context 改变不换连接；无 TLS/验证 TLS 参数一致；SSH、跳过 TLS 验证或材料缺失拒绝；daemon ID 改变时不删除新 daemon 容器 |
| 唯一提交 | 真实 Git Workspace 产生 patch，受控 harness 读取与交付 ref 的 bytes 相等；正常结束不二次登记 |
| 提交预算与提前取消 | 在 attempt 创建前取消，受控外部 Docker CLI 的临时 index 删除不响应：在导出清理/提交预算内停止等待，Run 保持取消、附资源诊断、Agent Workspace 保留且 harness 未启动；正常总预算耗尽及 diff 已生成但清理失败分别失败；Local 与无参数导出调用也满足公开预算契约 |
| 标识关联 | 正式与诊断 patch 均有稳定 submission_id 且无强制 attempt 字段；evaluation artifacts 共用一个 attempt_id；config/summary 的 patch ref/submission_id 与原 record 一致 |
| JSONL 保真 | 多行、CRLF、Unicode、引号/反斜杠、真实 binary diff 往返，单条物理行且末尾换行 |
| 判定真实性 | 受控进程：退出 0 unresolved/缺报告/错误任务/错误 bool/漏测试/patch 不一致皆失败，完整 resolved 才成功；非零退出不能被报告覆盖 |
| 故障可诊断 | 导出/存储边界故障、无效 UTF-8、空 patch、启动失败、必要日志缺失；错误码与已生成 artifact 保留 |
| 超时/取消 | 可启动子进程的受控 harness 验证 TERM/KILL 与组回收；受控 Docker CLI 验证准确名称与冲突后缀，其他 attempt 不受影响 |
| SQLite 与展示 | EvaluationCompleted refs 与 Run 终态原子提交；取消不产生 completed；按 artifact type 展示，不依赖顺序 |
| 唯一评测交付来源 | 正常 refs 只来自真实 list_for_run；一份评测 artifact 损坏使全量汇总失败时，ApplicationResult 不虚构其他 evaluation refs，仅在剩余预算内独立验证已有 patch ref 后交付；patch 也损坏/预算耗尽则 artifacts 为空，Run 结果不改写且 Workspace 保留 |
| 汇总资源成本 | 真实 FileArtifactStore 下多份日志完整性与 refs 均可验证，内容损坏仍拒绝；隔离进程中以多份大文件测量峰值内存，证据覆盖 list_for_run 的顺序校验及顺序登记，避免仅验证函数调用顺序；收集超时停止后续工作、保留已登记证据，取消在预算内返回 |
| 生命周期 | 成功销毁 Agent Workspace；失败/取消/证据收集失败保留；评测容器清理失败阻止通过 |
| 镜像覆盖 | 单任务 image 替换不改共享数据；记录覆盖未预检，按最终选择的覆盖策略执行 |
| 真实集成 | 仅用已预检开发任务与本机缓存镜像，执行已知有效 patch 和无效非空候选，验证 report/artifact/终态与容器回收 |

真实集成的有效参考 patch 只在宿主机测试驱动中使用，不进入模型输入或仓库。5 个开发任务可反复调试；2 个已预检正式任务不用于实现/Prompt 调优。完整 25 项冻结实验等待全量预检，不能用 7 项报告替代全量证据。

文档阶段不运行测试。实现时运行受影响 evaluation/cli/runtime/artifacts/environments 定向测试、变更 Python 文件 Ruff check --fix 与 format，以及 ty check。受控进程测试和真实 Docker 验证分别报告；缺真实运行证据时不得宣称真实链路已验收。

## 14. 范围、兼容性与剩余限制

- 通用 EvaluationRequest/Evaluator/RunResult 保持兼容；ApplicationDependencies 增加有 passthrough 默认的 Evaluator，以兼容直接构造依赖的现有调用方。
- 新增内部 BootstrapConfig 任务字段，不改 CLI 参数；Docker 普通 CLI 准备失败必须有对应测试和说明。
- EvaluationCompleted payload 与 SQLite schema 保持原样，artifacts 放事件已有字段。
- FinalizationError 的 stage 增加 artifact_collection，相关调用方与 rendering 一起更新。
- Workspace.export_patch 增加带默认值的 keyword-only 总预算/清理预算；Local/Docker 实现及所有实现该协议的测试 Workspace 同步更新。无参数调用仍可用，但原先无限等待的导出变为超时失败，这是有意的行为收窄，需要对应契约测试。
- FileArtifactStore 的公开协议、返回内容与完整性校验语义保持不变；本次仅将 Run 内内容校验改为顺序执行，换取较低峰值内存，代价是大日志汇总可能更慢。
- 原始隐藏测试/准备包不提交到仓库；仅安全支持清单作为包内资源交付。
- 外部 harness 当前创建的容器权限、网络与资源配置遵循已安装 harness；不假设它继承 Agent Workspace 的隔离选项，也不在本功能中重新实现 Docker harness。
- 同一宿主的调用方可以并发启动不同 Run；workers=1 只限制单次 harness，不提供跨进程资源配额。首版不新增调度器。
- 大日志先落磁盘，登记与 list_for_run 内容验证均顺序执行，但 put/read/校验仍使用完整 bytes，有最大单文件级内存成本，汇总还会再次产生总文件大小级 I/O。并发 Run 的峰值可叠加，取消后的单文件线程操作可能继续；首版不新增流式 store 协议或全宿主配额。可捕获的资源/I/O 失败保留诊断，不能静默截断证据后成功；进程被系统 OOM 杀死时不承诺完成诊断或 finalization。
- 评测连接固定为明确 Unix/TCP endpoint 和已验证 TLS 参数；首版不支持 SSH/npipe 或跳过 TLS 验证。daemon 不可达或身份改变会中止相应操作并报告，不能通过切换 context 恢复。
- 取消时已登记证据可追溯，但 artifact manifest 与 Runtime journal 不构成跨存储事务；强制终止和宿主崩溃可能留下仅落盘未登记的证据。
- Application 的全量 manifest 汇总为 fail-fast：某一内容损坏或汇总超时会导致其他评测引用也不在 ApplicationResult 中交付，不能把 Runtime 内收到 refs 当作应用层已有 refs；磁盘/SQLite 追溯与有界单 patch 回退是本稿明确的交付边界。

## 15. 集中决策清单

逐任务就绪门槛已获用户确认，下面三项仍需集中决策。本稿各节按推荐项写成一致的完整方案，选择其他项时应同步修改相关契约与验收。

| 编号 | 待决策事项 | 推荐选择 | 另一选择及影响 |
|---|---|---|---|
| D1 | 首版是否默认允许拉取评测镜像 | 默认禁止，先使用缓存镜像；允许通过宿主配置显式开启 | 默认允许，延续原设计的不承诺离线行为，但缺镜像可能造成下载、磁盘占用与长等待 |
| D2 | 覆盖评测镜像是否要求单独 gold preflight | 沿用原设计允许覆盖，标记未经镜像预检，候选实际校验通过仍可成功 | 必须有覆盖镜像的绑定预检证据才能启动；更严格，但需要补充覆盖预检证据输入和准备流程 |
| D3 | 取消时 artifacts 是否必须附在 SQLite 终态事件 | 首版从 Run 下 artifact manifests 和 ApplicationResult 追溯，保持协议简单 | 必须附在取消终态事件；需增加通用的进行中证据通道，且解决取消/强制退出时的持久化契约 |

可一并调整的运行默认值：候选提交总预算 60 秒（包含导出清理最多 5 秒）、测试 1800 秒、harness 进程 2400 秒、harness 清理总预算 30 秒、评测证据登记与 Application 最终收集各 30 秒；工作目录保留。它们属于配置选择，不改变组件边界。

没有要求重新决策的既定约束：不新增任务 CLI 参数、不依赖 trigger、不绑定 Agent 镜像、不自动重试、不做批量 runner 或恢复。本稿对这些按原设计执行。

## 16. 依据与评审状态

本稿依据仓库当前代码、原设计文档、宿主机 manifest/tasks/实例报告，以及本机安装的 SWE-bench 5.0.2 源码：`cli/evaluate.py`、`harness/run_evaluation.py`、`harness/grading.py`。Docker SDK 环境解析依据本机 `docker/utils/utils.py::kwargs_from_env`，CLI context 选择依据第 6.1 节链接的官方文档。接口描述以本机版本为准，不使用变化中的上游 main 代替兼容性证据。

完成文档自检：就绪门槛、覆盖镜像、取消证据、唯一提交及内部清理预算、Application 唯一评测证据来源、报告路径、Docker 连接绑定、汇总资源成本、失败终态与清理要求已在各节对齐；没有将未运行的验证写成已通过。本轮只修订架构稿，未修改产品代码、数据准备目录或原设计文档，未运行 Docker、gold preflight 或运行时测试。

用户审阅本稿并集中决定 D1–D3 后，先修订并确认书面规格，再通过 writing-plans 编写实现计划；这份文档本身不授权产品实现。

### 16.1 Review 意见处理

本次采用仓库根目录 [architecture-critique.md](../../../architecture-critique.md) 的独立 Evaluator review；`docs/superpowers/specs/architecture-critique.md` 评审的是先前 context compaction 设计，不属于本次修改范围。

| 前轮 Review 意见 | 本稿处理 | 对应章节 |
|---|---|---|
| should-fix：CLI/SDK Docker daemon 可能不同 | 明确来源优先级、冻结 endpoint/TLS、显式子进程参数、拒绝不支持连接、daemon ID 变化处理和边界验收 | 4、6.1、8.3、9、10、11、13、14 |
| should-fix：证据汇总可能同时读取多份完整日志 | list_for_run 顺序验证、顺序登记、一次汇总复用；说明最大单文件与并发 Run/线程取消限制，增加资源验收 | 2.1、4、10、11、13、14 |
| nit：submission/attempt 创建责任含糊 | CandidateSubmission 创建 submission_id；Evaluator 创建 attempt_id；patch 不要求 attempt，评测 config/summary 建立关联 | 7、8.1、10、13 |
| nit：质量属性及冲突优先级未明确 | 正常路径可信与证据完整优先；取消路径有界返回与取消语义优先，登记限时尽力完成 | 1、11 |

最新 review 的两项 should-fix 按以下方式修订：

| Review 意见 | 本稿处理 | 对应章节 |
|---|---|---|
| 候选提交取消清理未纳入预算 | 提交总预算包含导出/登记；公开 Workspace 参数预留内部 finally 清理时间，涵盖锁等待和 CLI 回收；取消优先且保留未清理身份，增加 attempt 前取消验收 | 2.1、4、5.2、7.1、9、11、12、13、14、15 |
| 已返回评测 refs 缺少进入 Application 的通道 | 选择公开 manifests 为唯一评测交付来源，删除 EvaluationResult refs 直接回传承诺；全量收集失败仅允许剩余预算内验证已有提交 patch，明确无部分结果与追溯边界 | 10、11、13、14 |

D1–D3 保留原待决策状态；review 的 PROCEED 不替代用户对书面规格或实现计划的批准。
