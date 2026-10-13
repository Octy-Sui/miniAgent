# Independent Evaluator：SWE-bench 独立校验设计

日期：2026-10-10

## 1. 目标与范围

将现有候选 patch 导出能力与通过 `uv tool` 安装的 SWE-bench 衔接。Docker Workspace 的 Agent 提交候选结果后，由独立评测容器校验；只有校验通过，Run 才进入 `SUCCEEDED`。Local Workspace 继续使用 `PassThroughEvaluator`。

第一版仅支持 `docs/swebench-task-base.json` 中列出的 25 个 SWE-bench Verified 任务：5 个开发任务、20 个正式评测任务。任务清单与 `docs/swebench-task-selection.md` 定义支持范围，Evaluator 不临时换题。

本次不新增 `--instance-id` CLI 参数，不修改或依赖开发触发脚本 `trigger`，不建设批量 benchmark runner、自动修复重试、跨进程恢复或通用命令 Evaluator。不锁定 Agent Workspace 镜像，不因显式传入不同镜像而拒绝运行。

## 2. 当前行为与衔接问题

当前执行路径为 Application → AgentRuntime → Evaluator → Run 终态 → Application 导出并登记 patch。

- `AgentRuntime` 已支持注入 `Evaluator`，默认使用 `PassThroughEvaluator`。
- `CandidateOutput` 已具有可选的 `patch: ArtifactRef`。
- `EvaluationResult` 已支持 checks、error、artifacts。
- `Application._finalize_patch()` 在 Runtime 返回后才执行，现有评测请求因此没有导出的 patch。
- `ApplicationDependencies` 尚未包含 Evaluator；Runtime 的评测结果目前只持久化通过与否，没有保存报告引用。

接入必须使候选 patch 在 Runtime 决定终态前可用，避免先记录成功再由 Application 改写结果。

## 3. 输入契约与信息来源

### 3.1 内部任务上下文

创建评测任务的调用方提供结构化的 `SWEbenchTaskContext`，必填字段只有 `instance_id`；可选字段为 `evaluation_image`。使用 Pydantic 验证外部结构化输入，不把任务身份塞入自由文本。

该上下文通过应用层配置传入 Bootstrap，Bootstrap 验证任务属于 25 项支持清单，并在构造 Evaluator 时绑定上下文。Runtime 的通用 `EvaluationRequest` 不必增加 SWE-bench 专属字段。

- Docker 模式缺少上下文或任务 ID 不受支持时，在模型调用前报出准备错误，不回退到 passthrough。
- Local 模式始终选择 passthrough，不要求此上下文。
- 不从 issue 文本、Agent 最终文本、patch 文件名或自定义镜像名猜测任务 ID。
- CLI 本次不增加新的任务身份入口；普通 Docker 调用如果没有内部上下文，将得到明确的准备错误。调用方接入上下文的方式属于内部应用配置契约。

### 3.2 SWE-bench predictions

| 字段 | 来源 | 是否需要调用方再次提供 |
|---|---|---|
| `instance_id` | 内部任务上下文 | 必须在创建任务时提供 |
| `model_name_or_path` | 应用层已有 Agent 模型配置 | 否 |
| `model_patch` | 本次候选 patch artifact 的完整 UTF-8 内容 | 否 |

模型名称只用于标识本次提交及结果目录，不承担判定任务身份的职责。外部文件和目录使用受控名称，不允许模型名称或任务 ID 逃逸评测工作目录。

### 3.3 SWE-bench 测试规格

本机已安装版本为 `swebench 5.0.2`。其 `make_test_spec()` 要求记录包含 `instance_id`、`image`、`repo`、`version`、`eval_script`、`log_parser`、`eval_type`、`FAIL_TO_PASS` 和 `PASS_TO_PASS`。

这些字段由预先准备的数据按 `instance_id` 查得，不需要创建任务时重复传入测试命令、测试清单或仓库信息。正常候选校验不需要 gold patch；5.0.2 的测试准备由 `eval_script` 承担，不能认为仅传入旧格式的 `test_patch` 就足以执行。

`run_id` 来自当前 Run UUID，并附加独立的评测尝试 UUID，避免 harness 缓存复用。predictions 路径、工作目录、报告目录由 Evaluator 构造。可执行文件路径、评测数据位置、测试超时和整个进程的超时属于宿主机配置，首版 workers 固定为 1。

## 4. 提前准备评测数据

运行期间不动态改变任务集合或验证规则。先为这 25 项准备兼容 5.0.2 的本地数据与准备 manifest，再允许模型任务启动。

现有冻结依据为：

- dataset：`princeton-nlp/SWE-bench_Verified`，config / split：`default` / `test`。
- revision：`c104f840cc67f8b6eec6f759ebc8b2693d585d4a`。
- 原始 Parquet SHA-256：`a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`。

5.0.2 兼容数据可从官方 `SWE-bench/SWE-bench_Verified` 固定 revision `78f471bf655a3137b2e8a75af1501690ec009ec3` 提取。准备时校验源文件摘要，并逐项与现有冻结数据核对 ID、repo、version、base_commit、test_patch、FAIL_TO_PASS 和 PASS_TO_PASS；测试清单先按实际 JSON 列表解析后比较。差异必须报告并中止准备，不能自动接受新版规则。

将核对通过的任务与新版 `eval_script`、`log_parser`、`eval_type` 合并为本地评测记录。默认评测镜像采用任务清单中的镜像引用；运行时允许显式覆盖，因此它只是默认值，不是镜像匹配约束。对评测脚本与验证规则的兼容性通过 gold preflight 提供执行证据。

准备 manifest 记录两个源的 revision 与文件摘要、25 个任务 ID、生成数据摘要、harness 版本、默认评测镜像，以及各任务 gold preflight 的结果和日志位置。未经完整预检的数据不能标记为准备完成。覆盖评测镜像时，在本次结果中记录实际镜像引用与可取得的 Docker image ID，不把默认镜像的 preflight 当作覆盖镜像的验证证据。

正常评测数据仅保留 harness 运行需要的字段，不带 gold solution。gold preflight 使用单独准备的 gold predictions，仅在宿主机数据准备流程使用。隐藏验证数据、评测脚本与准备缓存不挂载到 Agent Workspace。

数据准备是本功能的必要配套流程，复用外部 SWE-bench 的环境读取官方数据，不为主运行项目引入 SWE-bench、datasets 或 pyarrow 依赖。准备输出放在宿主机配置的数据目录，不把隐藏测试或完整数据集提交到仓库。

## 5. 架构与职责

### 5.1 应用层组装

Bootstrap 根据有效 Workspace 类型选择 Evaluator：

- Local：`PassThroughEvaluator`。
- Docker：应用层的候选提交适配器，内部调用 `SWEbenchEvaluator`。

`ApplicationDependencies` 增加 Evaluator 依赖，Application 将其传入 AgentRuntime。Runtime 继续只依赖通用 Evaluator 协议，不直接导出 Workspace patch，也不构造 SWE-bench 命令。

### 5.2 候选 patch 提交

从现有 Application finalization 中提取必要的 patch 导出与登记逻辑，供评测前提交及终止后的补救导出共用。

应用层适配器在 `evaluate(request)` 中：

1. 导出 Workspace 相对其真实 `base_revision` 的 Git binary patch。
2. 对非空 patch 通过现有 ArtifactStore 登记内容、文件清单与基线元数据。
3. 将 artifact 引用放入新的 `CandidateOutput.patch`，保持候选文本不变。
4. 将带 patch 的请求交给 `SWEbenchEvaluator`。

同一次候选提交只登记一份 patch，校验和最终交付使用同一引用。Runtime 返回后，Application 复用已提交引用，不重新导出或替换校验过的内容。模型提前失败、取消或尚未提交候选结果时，仍尝试按原有路径导出诊断 patch。

不强制 Agent Workspace 镜像或其基线与清单相同，也不自动重写 patch。评测容器按任务评测记录提供基线，候选 patch 的适用性由独立评测实际检验。

### 5.3 SWE-bench Evaluator

它依赖 ArtifactStore、已绑定任务上下文、准备好的评测数据和宿主机进程配置。它不调用模型，不使用 Agent 工具，不在 Agent 容器内执行隐藏验证。

每次调用创建独立工作目录，写入单任务评测数据与 predictions 文件，然后通过异步子进程启动外部 `swebench`。使用 argv 调用，不拼接 shell 命令。

```text
swebench eval <task-data.json>
  --predictions <predictions.jsonl>
  --instance <instance_id>
  --run-id <run_uuid-attempt_uuid>
  --workers 1
  --timeout <test_timeout_seconds>
  --report-dir <report_directory>
```

当前 5.0.2 没有 `--image` 参数。可选 `evaluation_image` 通过本次单任务数据的 `image` 字段覆盖，不修改共享准备数据。SWE-bench 先查当前 Docker daemon 的本地镜像，缺失时尝试拉取；本地 tag 可以直接使用。默认配置不承诺完全离线运行。

评测使用新建容器，不复用 Agent 已修改的容器。调用方指定的评测镜像须具有对应任务基线和测试依赖；Evaluator 允许该覆盖，不因它与清单不同而拒绝运行。

## 6. Patch 与 predictions.jsonl 的转换

predictions 文件只有一条 JSON 记录，每条记录占一个物理行，并以换行结束。例如：

```json
{"instance_id":"django__django-11477","model_name_or_path":"miniagent","model_patch":"diff --git a/example.py b/example.py\n...\n"}
```

必须使用标准 JSON 序列化器，把 ArtifactStore 读出的 UTF-8 patch 字符串作为 `model_patch` 值。不能直接复制 `.patch` 文件成为 JSONL，不能传入 URI 或路径，不能用手工转义拼接 JSON。

不 strip patch，不规范化行尾，不重新生成 diff，不截断内容；保留末尾换行、引号、反斜杠、Unicode、文件模式、重命名以及 Git binary patch 文本。验收条件是反序列化 predictions 后，`model_patch.encode("utf-8")` 与原 artifact bytes 完全相等。

空 patch 不启动 harness，因为该版本会跳过空提交。返回明确的 `EMPTY_PATCH` 失败，并保存该结论。

## 7. 结果、持久化与终态

SWE-bench 的进程退出码只表示执行层状态，不能替代任务判定。只有进程正常结束、目标任务报告存在且结构有效、对应任务的 `resolved` 为 true，才返回 `passed=True`。报告必须位于本次独立工作目录、对应当前任务和本次提交。

有有效报告但 `resolved=False` 是候选校验失败。进程非零退出、报告缺失、JSON 无效、任务不匹配或必需字段类型错误均返回明确的 EvaluationError，不回退成功。测试未运行或报告不完整不能当作通过。

保留 patch、predictions、实际评测配置、harness stdout/stderr、实例 report.json、test_output.txt 和 run_instance.log（存在时）。完整日志作为 artifact，摘要保持有界。配置 artifact 记录任务 ID、patch SHA-256、数据摘要、harness 版本、镜像和评测尝试 ID，以便追溯。

`EvaluationResult.artifacts` 携带这些引用。Runtime 在 `EvaluationCompleted` 对应事件的已有 artifacts 字段中保存引用；ApplicationResult 汇总 patch 与评测 artifact，CLI 按 artifact 类型查找 patch，避免继续依赖列表第一个元素代表 patch。Run 的最终状态与评测事件沿用现有终态持久化流程。

| 情况 | Run 状态 / 原因 |
|---|---|
| 校验通过 | `SUCCEEDED / TASK_VERIFIED` |
| 校验不通过、空 patch、导出或评测执行失败 | `FAILED / EVALUATION_FAILED`，附具体错误码 |
| 用户取消 | `CANCELLED / CANCEL_REQUESTED` |

artifact 登记失败意味着缺少完整交付证据，不能判成功；保留能够登记的诊断信息和原始工作目录。首版不自动重试评测。

正常通过且交付无错误时销毁 Agent Workspace。失败、取消或 finalization 出错时保持现有保留 Workspace 的行为；独立评测容器则在各结束路径清理。

## 8. 超时、取消与资源清理

测试超时与整个外部进程超时分开配置；后者覆盖启动、镜像准备和报告生成，必须大于测试超时。达到整体超时返回评测超时错误，不能无限等待拉取或启动。

Runtime 已将 Evaluator 调用放在 cancellation 包装内。Evaluator 响应协程取消，在有界清理时间内终止并回收本次进程；必要时强制杀死，再清理本次评测容器。清理依据本次唯一 attempt ID 识别容器，包括 harness 冲突重试产生的后缀名称，不扫描删除其他评测或 Agent 容器，不删除共享镜像。

清理过程保护必要操作免于首次取消打断，并设置有限等待。取消保持取消语义，清理失败追加可观察诊断，不转为成功；超时或执行失败也保留已生成日志。无法清理的资源身份必须可追踪，不能静默吞掉错误。

## 9. 验证与验收

按行为选择有价值的测试，外部 swebench / Docker 边界允许使用受控测试进程，不 mock 项目内部实现。

1. Local 不调用外部 SWE-bench；Docker 必须携带受支持任务上下文；清单外任务在模型执行前被拒绝。
2. 候选 patch 在校验前登记；Evaluator 读取的 bytes 与最终交付 artifact 相同；正常提交不重复导出登记。
3. predictions 对多行 patch、Unicode、引号、反斜杠及真实 Git binary patch 的往返转换无损，产出单条有效 JSONL。
4. 进程退出 0 但 unresolved、缺少报告、错误任务或报告无效时均失败；有效 resolved 报告才成功，非零退出不成功。
5. 空 patch、patch 导出失败、artifact 读写失败具有可诊断结果；失败后仍可取得已生成 patch 和日志。
6. 测试超时、进程超时与取消分别验证终态、进程回收及评测容器清理，不影响其他 Run 的资源。
7. SQLite 事件关联报告 artifact，ApplicationResult 和 CLI 正确区分 patch 与评测报告，失败不清理 Agent Workspace。
8. 默认及自定义本地评测镜像引用均能写入单任务数据；覆盖不改变共享准备数据；不要求 Agent 镜像与默认值相同。
9. 25 项提前准备数据均通过 gold preflight；以一个开发任务完成真实外部 swebench 的 patch → predictions → report → Run 终态链路，并用无效候选验证失败链路。

文档阶段不运行测试；实现阶段运行受影响领域的定向测试、变更 Python 文件的 Ruff 检查与格式化，以及类型检查。真实 Docker 校验必须与受控进程测试分别报告，不把后者视作真实 SWE-bench 执行证据。

## 10. 实现边界与参考

主要接入位置：`src/miniagent/evaluation/`、`src/miniagent/cli/application.py`、`src/miniagent/cli/bootstrap.py`、`src/miniagent/cli/settings.py`、Runtime 的评测事件登记以及 CLI artifact 展示。保留现有 Evaluator 协议的通用性，不引入全新调度层或服务。

本设计依赖的 `docs/swebench-task-base.json` 当前为工作区未跟踪文件；文档提交不顺带纳入它或其他用户文件。实现时须使正式任务清单成为项目可交付的数据输入，不能依赖开发者临时文件。

依据：

- [SWE-bench 评测指南](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md)。
- [官方 Verified 数据元数据](https://huggingface.co/api/datasets/SWE-bench/SWE-bench_Verified)，本次核对 revision 为 `78f471bf655a3137b2e8a75af1501690ec009ec3`。
- 本机 `uv tool` 安装的 5.0.2：`swebench/cli/evaluate.py`、`swebench/harness/utils.py`、`swebench/harness/run_evaluation.py`、`swebench/harness/grading.py`。

当前上游 main 会变化；实施与兼容性验证以已安装 5.0.2 的实际接口及准备数据 manifest 为准。设计文档获审阅批准后，再编写实施计划。
