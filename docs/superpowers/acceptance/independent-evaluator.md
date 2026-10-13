# Independent Evaluator 验收与使用记录

2026-10-13。按用户执行指令采用计划推荐的 D1–D3：默认不拉取镜像；允许覆盖镜像并记录其未预检状态；取消证据从 artifact manifests/ApplicationResult 追溯。逐任务就绪门槛有效，全局 ready=false 不阻止已就绪任务。设计稿中的历史“待批准”状态由本次执行指令及执行账本说明，不代表重新等待批准。

## 内部调用与配置

普通 Docker CLI 没有结构化任务入口，返回 TASK_CONTEXT_REQUIRED；Local 继续 passthrough。程序调用提供上下文，不从 task 文本推断身份：

```python
from pathlib import Path
from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.environments import WorkspaceKind
from miniagent.evaluation.swebench_data import SWEbenchTaskContext

application = await bootstrap(BootstrapConfig(
    agent=DEFAULT_AGENT,
    task="调用方提供的问题文本",
    source_dir=Path.cwd(),
    workspace_kind=WorkspaceKind.DOCKER,
    docker_image="调用方的 Agent 镜像",
    swebench_task=SWEbenchTaskContext(instance_id="django__django-11477"),
))
```

宿主设置由环境变量覆盖：SWEBENCH_DATA_ROOT、SWEBENCH_WORK_ROOT、SWEBENCH_EXECUTABLE、EVALUATION_DOCKER_EXECUTABLE、SWEBENCH_ALLOW_IMAGE_PULL。默认数据根为 `~/.miniagent/evaluation/swebench-verified-25-20261010`，工作根为 `~/.miniagent/evaluation/runs`。支持清单从安装包读取；不需要当前目录存在 docs/swebench-task-base.json。

预算默认值：候选提交60s（包含导出清理预留5s）、测试1800s、harness进程2400s、评测清理30s、输入/证据登记30s、最终收集30s。相关 Settings 字段均校验大小关系及正值，子操作共享阶段deadline。镜像覆盖只更改本次 task.json；默认预检不能当作覆盖镜像的 gold 证明。镜像缓存检查不是网络隔离，缓存被外部删除的竞态仍可能触发上游行为。

Docker context/HOST/TLS 在 bootstrap 冻结。支持 Unix、TCP 明文、验证TLS；不支持 SSH/npipe/跳过TLS验证。预检与清理核对 daemon ID，身份变更时不删除新 daemon 的容器。OrbStack 等 multicall 可执行文件保留绝对 symlink 调用名。5.0.2 没有 `--version` 时，通过所选 uv tool 的 shebang 解释器读取该环境的 distribution metadata；不向 miniAgent 安装 SWE-bench。

## 本轮证据

定向领域检查：261 passed、1 skipped（原有可选 Docker 集成）；Ruff 与 ty check 通过。最终全分支检查与独立 review 的结果记录在配套执行记录中。受控进程和真实 Docker 的证据分开报告。

仅使用当前已缓存的 `django__django-11477` 开发镜像；没有拉取其他镜像，没有恢复25项预检下载。真实测试驱动用宿主参考 patch 修改 Agent Workspace，然后由边界 Provider 提交候选文本；参考 patch、gold 文件和隐藏测试不进入 ModelRequest、不提交仓库。这证明校验链路，不证明模型解决能力。

最终真实测试：`tests/integration/test_swebench_evaluation.py`，2 passed / 53.70s。

| 候选 | Run ID | 终态 | 已验证交付 |
|---|---|---|---|
| 有效参考 patch | 1d626b6d-9ac4-499d-9e3f-05dec77c7fab | SUCCEEDED / TASK_VERIFIED | 9份 artifact，SQLite评测引用与交付一致 |
| 无效非空 patch | e0937a5f-8a15-4ed1-aab4-4c3f87952fc8 | FAILED / EVALUATION_FAILED | 9份 artifact，原报告/日志保留 |

宿主证据目录：`/private/tmp/miniagent-real-evaluation/`；日志 `/private/tmp/miniagent-real-evaluation-tests-final.log`。daemon ID：`9fda18c4-1d24-4886-991b-04ad37640350`。测试配置显式禁止拉取；成功路径销毁 Agent Workspace，失败路径验证保留后由测试驱动清理。Evaluator 清理独立容器并保留 attempt 目录。原始隐藏报告和日志只留在宿主证据目录。

另一轮同镜像验证的两个 Run `3a6c43f4-aa29-4a8a-9880-6deb8ac0dda3`、`8755a6ad-5cdd-411e-bd9e-11ee269aaadf` 还逐字节核对了 patch→predictions，并保留唯一 attempt / daemon 关联。前期失败来自真实工具的无 --version 接口和 OrbStack 调用名；相应回归测试均先失败后通过。

跨组件取消：提交清理中、harness运行中、证据收集中三条路径均保持 CANCELLED；取消不生成 EvaluationCompleted。证据收集取消的原缺陷（Run已取消，summary仍passed）由公开 stdout manifest 发布窗口复现并修复。

发布中取消实验：以真实16MiB候选及 FileArtifactStore 的公开 manifest 出现同步；命中发布完成但 await 尚未保存 ref 的窗口，成功恢复同 submission_id 的唯一 patch，无第二次导出。该结果来自 `test_published_patch_is_recovered_after_submission_cancellation`，不是内部 _publish mock。

资源实验为隔离进程的真实 FileArtifactStore 顺序 put/list_for_run；测量的是存储边界，实际 Evaluator 的大日志保留另由受控 harness 验证。无内存 SLA 数值，以下为实际RSS形态，不构造自动验收阈值：

| 文件数×最大单文件 | 基线RSS | 登记峰值RSS | 汇总峰值RSS |
|---|---:|---:|---:|
| 1×16MiB | 37,273,600 | 54,738,944 | 54,886,400 |
| 4×16MiB | 37,322,752 | 54,755,328 | 54,984,704 |
| 8×16MiB | 37,289,984 | 54,755,328 | 54,984,704 |
| 4×8MiB | 37,339,136 | 46,415,872 | 46,612,480 |
| 4×32MiB | 37,306,368 | 71,499,776 | 71,712,768 |

## 交付边界与未执行项

正常交付只来自 list_for_run 的全量完整性校验。任一内容损坏/收集超时都会令全量汇总失败，仅在原deadline剩余时间内独立验证内存已有patch ref后交付patch；不能用Runtime私有refs补齐报告。已持久化Run终态不改写，finalization错误保留Agent Workspace并令CLI非零。

大文件读写/校验仍有最大单文件级内存成本；同步单文件I/O及已开始的to_thread不能被强制抢占。不同Run可叠加资源成本。取消证据登记限时尽力完成，manifest与journal没有跨存储事务；强制退出/宿主崩溃不保证完整回收。无法清理的身份、未登记文件和错误保存在summary/原attempt目录；目录不自动GC。

按用户最新限定只验证一个缓存镜像：未建立真实TLS A/B环境，不能把Unix链路或受控TLS参数测试视为真实TLS验收；未执行25项全量gold预检，不能将单任务成功当作全量冻结实验完成。
