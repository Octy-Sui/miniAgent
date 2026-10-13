# Independent Evaluator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Docker Workspace 接入 SWE-bench 5.0.2 独立校验，使唯一候选 patch、可信报告及必要证据决定 Run 终态，并保持 Local passthrough。

**Architecture:** 应用层 CandidateSubmission 冻结一次 patch，WorkspaceCandidateEvaluator 将通用请求交给 SWEbenchEvaluator。Bootstrap 在创建 Workspace 前绑定已就绪任务、数据快照和 Docker 连接；Runtime 只处理通用评测结果及终态事务。Application 从 ArtifactStore 的已验证 manifests 交付证据，不改写 Run 终态。

**Tech Stack:** Python 3.13.x、Pydantic 2、asyncio POSIX subprocess、pathlib、现有 FileArtifactStore / SQLiteRunStore / pytest；宿主机外部 `swebench==5.0.2` 与 Docker CLI。

**Spec:** [需求设计 R](../specs/2026-10-10-independent-evaluator-design.md)、[架构规格 A](../specs/2026-10-10-independent-evaluator-architecture.md)。测试依据为 [测试分析](../specs/2026-10-10-independent-evaluator-test-analysis.md) 和 [测试设计](../specs/2026-10-10-independent-evaluator-test-design.md)。发生冲突时，A 的逐任务门槛及细化契约优先；测试设计不能替代产品规格。

## Global Constraints

- “支持清单固定为 5 个开发任务、20 个正式任务；不临时换题。”
- “任务身份通过内部结构化配置提供，不从 issue、最终文本或镜像名称推断。”
- “不新增 CLI `--instance-id`，不修改或依赖 `trigger`，不建设批量 runner。”
- “不绑定 Agent 镜像与评测镜像，不要求 Agent 的 Git 基线等于清单基线。”
- “不自动修复或重试，不建设跨进程恢复、通用评测命令框架或评测服务。”
- “主项目不增加 SWE-bench、datasets、pyarrow 或 Docker Python SDK 依赖。”
- “数据、gold patch、隐藏测试、宿主机 Docker socket 不挂载给 Agent。”
- workers 固定为 `1`，harness 兼容版本固定为 `5.0.2`；macOS/Linux POSIX，不宣称 Windows 支持。
- EvaluationRequest、Evaluator、RunResult 保持通用；EvaluationCompleted payload 和 SQLite schema 不变，refs 使用 NewRunEvent.artifacts。
- 每 Run 最多一次提交尝试；共享准备包只读；必要证据或评测清理失败不得成功；取消重新抛出 CancelledError。
- 遵循根目录 AGENTS.md：不修改 README，不 mock 项目内部方法，Python 变更只对变更文件运行 Ruff，测试优先定向。

## Review Focus

1. 提交尚未创建 attempt 就取消，临时 index 清理卡住：总预算内返回取消、保留资源诊断，不二次提交（Task 4、5、11）。
2. context、HOST 和 TLS 冲突或 endpoint 后方 daemon 更换：预检/执行/清理绑定同一连接，身份改变不得删除新 daemon 的容器（Task 3、7）。
3. `resolved=true` 但布尔类型错误、漏测或 patch.diff 不一致：不得通过，报告原 bytes 留作证据（Task 1、6、7）。
4. patch 已发布但 put 的 await 被取消：成功 manifest 汇总按 submission_id 恢复唯一 patch，不重新导出（Task 5、9；线程发布窗口实验见 Task 11）。
5. 一份评测日志损坏或最终收集超时：全量汇总失败不交付虚构 evaluation refs；仅在原 deadline 剩余时间验证已有 patch，保留 Workspace（Task 8、9、11）。

---

## 执行状态与集中确认项

**本次只交付计划，不进入产品实现。** 架构稿仍标注待审阅；本计划将其推荐方案写成一个可审阅的完整执行版本，不把推荐项当作用户已批准。

| 项目 | 本计划采用的待确认值 | 改选后的影响 |
|---|---|---|
| D1 | `swebench_allow_image_pull=False` | 若默认允许，修改 Task 2 默认断言、Task 3/10 准备行为及使用说明 |
| D2 | 允许覆盖镜像，`image_preflight_verified=False`；实际候选通过可成功 | 若要求覆盖镜像单独 gold 预检，先补规格中的输入与证据绑定，不能由执行模型自创 |
| D3 | 取消证据从 artifact manifests/ApplicationResult 追溯，不要求取消 SQLite 事件携 refs | 若必须携 refs，先补通用证据通道设计，再改 Task 8/9 |
| 默认预算 | 提交 `60s`、导出内部清理 `5s`、测试 `1800s`、harness `2400s`、清理 `30s`、输入/评测登记阶段 `30s`、Application 收集 `30s` | 全部为配置值；子操作共享阶段 deadline，不逐项重置 |

以下属于使低阶执行者不必猜测的计划级细化，随计划一起审阅：

- Q4 报告集合：每组 success/failure 内无重复、二者无交集，二者并集精确等于对应任务规则；通过时 failure 为空。额外必需组测试、缺失 failure、集合矛盾为 REPORT_INVALID；缺必需测试为 REPORT_INVALID。允许上游额外未消费组；不强制 FAIL_TO_PASS 与 PASS_TO_PASS 互斥，分别与各自规则比较。规则为空时，该组仍必须存在且两列表为空。这与 5.0.2 按规则生成组的行为一致；不把空组当作“实际运行了测试”的独立证明。
- Q5 测试超时：固定实例 `test_output.txt` 的结尾 bytes 为 `\n\nTimeout error: {配置测试秒数} seconds exceeded.` 且 report 缺失时，才将正常退出分类为 EVALUATION_TEST_TIMEOUT；任意 stdout 中的 timeout 文本不成立。非零退出仍优先 HARNESS_PROCESS_FAILED。依据本机 5.0.2 `run_evaluation.py::run_instance`。
- Q7 提交状态增加 `cancelled`，重复 submit 返回已保存的不可变状态，无新副作用；并发调用等待同一锁，锁等待计入从第一次尝试开始的 deadline，不获得新预算。Application 不再尝试 cancelled/failed/empty 状态。
- Q6 时限测试使用公开进程握手和 monotonic；小预算测试统一使用 `3s` 测试监督容差并记录 elapsed，它不是产品预算加时。资源实验只报告峰值形态，不凭空设置内存 SLA；精确发布窗口无法稳定复现时记录未验证，不能换成内部 mock 后宣称通过。

**执行入口门槛：** 用户集中确认上述采用值并允许实现后才开始 Task 1。执行者读取规格与本计划，不重新 brainstorm 已确认边界；若发现本机 harness 版本/接口不符，停止对应兼容性工作并报告事实，不改装新版或放宽判定。

## 文件结构与依赖

| 文件 | 责任 | 所属任务 |
|---|---|---|
| `evaluation/swebench_report.py` | 严格实例报告解析与任务规则校验，供预检和候选共用 | 1 |
| `evaluation/swebench_data.py` | 上下文、准备错误、冻结记录与 manifest；返回只读快照 | 2 |
| `evaluation/data/swebench-verified-25.json` | 可安装的安全支持清单及来源摘要 | 2 |
| `evaluation/swebench_docker.py` | 冻结连接、显式 CLI/env、daemon/镜像检查和精准容器清理 | 3、7 |
| `environments/contracts.py`、`local.py`、`docker.py` | 公开 patch 导出总预算、内部清理和子进程回收 | 4 |
| `cli/candidate_submission.py` | Run-scoped 单次提交及通用评测适配器 | 5 |
| `evaluation/swebench.py` | attempt 输入、harness 生命周期、判定/清理/证据登记 | 6、7 |
| `artifacts/file.py` | 每次 list_for_run 顺序验证内容，完整性契约不变 | 8 |
| `runtime/loop.py`、`execution.py`、`trace_context.py` | artifacts 随 EvaluationCompleted 在终态事务保存 | 8 |
| `cli/application.py`、`rendering.py` | 一次最终收集、patch 恢复及 Workspace 策略、按类型展示 | 9 |
| `cli/bootstrap.py`、`settings.py`、`main.py` | 配置与启动前预检、注入、准备错误呈现 | 2、10 |
| `tests/evaluation/`、`tests/cli/`、`tests/environments/`、`tests/artifacts/`、`tests/runtime/` | 与责任匹配的行为测试 | 对应任务 |
| `tests/integration/test_swebench_evaluation.py`、`docs/superpowers/acceptance/independent-evaluator.md` | 显式选择真实验收和证据记录 | 11 |

上表产品路径均相对 `src/miniagent/`。新模块按上述职责拆分，不新增通用 subprocess framework、streaming store 或调度器。

顺序：`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11`。Task 1–6 的组件测试各自独立可运行；Task 7 才公开完整 SWEbenchEvaluator，不能先交付“只检查 resolved”的临时实现。

## 执行者共用规则

- 开始实现时依 using-git-worktrees 确认隔离；不要将现有用户的 staged/untracked 文件一并提交。此计划编写时架构/需求稿、测试稿、task-base、trigger 等已有用户变更。
- 每个任务先写测试、运行确认因该行为缺失而失败，再实现，跑定向测试；提交前检查 critical issues。不要以 fixture 自身异常作为 RED 证据。
- 命令统一用 `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest ...`。每任务 GREEN 后：`uv run ruff check --fix <本任务变更的 Python 文件>`，再 `uv run ruff format <同一列表>`；若 Ruff 改了行为相关内容，重跑对应测试。最后 `uv run ty check`。
- 以下代码块给出必须存在的测试名与关键断言；arrange 使用任务明确指定的真实文件/组件。独立参数化用 tuple 参数名及 list 值。不要把所有用例做成一个巨型参数表或新增测试 DSL。
- 新的 `tests/evaluation/conftest.py` 只放真实合成数据包和外部 executable fixture；每个 owning task 扩充所需行为。复用 fixture 不能代替对生产接口的独立 oracle。
- 受控 harness/Docker CLI 是磁盘上的可执行脚本，记录 argv/env/cwd 并通过文件握手，不替换内部 Workspace、Store、判定或 finalization。测试无需安装真正 swebench。
- `EvaluationPreparationError(code: str, message: str)` 是已知 bootstrap 准备异常，保留 `.code`，str 包含稳定码和上下文；所有评测错误 `retryable=False`。

### Task 1: 严格报告契约

**Files:**
- Create: `src/miniagent/evaluation/swebench_report.py`
- Create/Test: `tests/evaluation/test_swebench_report.py`

**Interfaces:**
- Produces: `parse_instance_report(content: bytes, *, instance_id: str, fail_to_pass: list[str], pass_to_pass: list[str]) -> InstanceReport`。
- `InstanceReport` frozen Pydantic，公开 `resolved: bool`；`ReportValidationError(ValueError)` 公开 `code: str`，仅 `REPORT_INVALID` / `REPORT_MISMATCH`。报告原 bytes 不在模型中重新序列化替代原文件。Task 2 调用时将 record 的 tuple 测试字段转换为 list，保持此签名一致。
- Consumes: 外部 5.0.2 报告；不依赖 SWE-bench 包，不执行进程。

- [ ] **Step 1: 写失败测试。** 基准 `report_bytes` 为标准 JSON bytes：顶层只有 `django__django-11477`；patch_exists/applied/resolved 为 true，patch_is_None/infra_failure 为 false；两测试组 success 分别为 `["f"]`、`["p"]`，failure 均为空。断言：

```python
def test_complete_report_passes(report_bytes):
    assert parse_instance_report(
        report_bytes, instance_id="django__django-11477",
        fail_to_pass=["f"], pass_to_pass=["p"],
    ).resolved is True

def test_string_resolved_is_invalid(report_with_string_resolved):
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse_instance_report(
            report_with_string_resolved, instance_id="django__django-11477",
            fail_to_pass=["f"], pass_to_pass=["p"],
        )
```

同文件逐个反例：整数 bool、每个必需 bool 缺失、可选 infra_failure 缺失可接受、额外顶层任务/错误任务→REPORT_MISMATCH、无效 JSON→REPORT_INVALID。resolved=true 配合 patch 字段矛盾/infra_failure=true/漏测/重复/多测/非空 failure/缺组或缺列表→REPORT_INVALID；两组重叠但分别满足规则、规则为空且报告为空组可接受。有效 unresolved 的失败列表覆盖规则时 `.resolved is False`；unresolved 且尚无 tests_status（5.0.2 无可解析测试输出报告）可解析但不能通过。U7 / TA-12、13。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation/test_swebench_report.py -q`；预期缺少模块/函数或新报告拒绝断言失败。
- [ ] **Step 3: 实现上述接口。** 消费布尔字段使用 StrictBool，消费模型 extra=allow，JSON 顶层只允许目标一个 key。resolved=true 才要求完整通过结构；unresolved 若提供 tests_status，仍校验其结构与所提供集合的矛盾，不因失败列表非空而报基础设施错误。集合规则采用集中确认项 Q4。
- [ ] **Step 4: GREEN。** 重跑 Step 2，全部 PASS；执行变更文件 Ruff。
- [ ] **Step 5: Review 后提交。** `git add src/miniagent/evaluation/swebench_report.py tests/evaluation/test_swebench_report.py`；`git commit -m "feat(models): validate independent evaluation reports"`。

### Task 2: 支持清单、设置及逐任务数据快照

**Files:**
- Create: `src/miniagent/evaluation/swebench_data.py`
- Create: `src/miniagent/evaluation/data/swebench-verified-25.json`
- Modify: `src/miniagent/cli/settings.py:Settings`
- Create/Test: `tests/evaluation/conftest.py`、`test_swebench_data.py`、`test_evaluation_settings.py`
- Modify: `pyproject.toml` 仅在 wheel 检查发现资源未打包时，使用当前 uv_build 配置修复。

**Interfaces:**
- Consumes: Task 1 parse_instance_report。
- Produces: frozen extra-forbid `SWEbenchTaskContext(instance_id: str, evaluation_image: str | None = None)`，两字符串非空。
- Produces: `load_prepared_task(data_root: Path, context: SWEbenchTaskContext) -> PreparedTask`；PreparedTask frozen 内部 dataclass，字段 `record: SWEbenchRecord`、`manifest_sha256: str`、`tasks_sha256: str`、`record_sha256: str`、`harness_version: str`、`default_image: str`、`actual_image: str`、`image_preflight_verified: bool`。record 必须 frozen，测试列表内部用 tuple，序列化回 harness JSON 时恢复 list。
- `SWEbenchRecord` frozen extra-forbid Pydantic，字段精确为 A§5.3 十一个字段；FAIL_TO_PASS/PASS_TO_PASS 用同名 alias 映射 `fail_to_pass` / `pass_to_pass` 属性。
- Produces: `EvaluationPreparationError`；缺 ID 的拒绝属于 Task 10，清单外 ID 在本函数返回 TASK_UNSUPPORTED；包损坏 EVALUATION_DATA_INVALID；预检状态不是 passed/resolved true 为 TASK_NOT_READY。
- Settings 增加 A§5.2 十字段及 `swebench_artifact_timeout_seconds: float = 30.0`、`artifact_collection_timeout_seconds: float = 30.0`，后两者分别提供输入/登记阶段与最终收集预算；timeout 正数，测试秒数正整数，process > test，submission > export_cleanup。
- Test fixture `prepared_bundle(tmp_path) -> Path`：安全支持清单全部 25 ID，5/20 分组；合成 eval_script、规则 f/p、基准通过报告；来源和摘要合法，所选 `django__django-11477` passed，其余 not_run，ready=false。不拷贝真实隐藏测试。

- [ ] **Step 1: 写失败测试。** 使用真实临时 bundle，断言：

```python
def test_selected_ready_task_does_not_require_global_ready(prepared_bundle):
    prepared = load_prepared_task(
        prepared_bundle, SWEbenchTaskContext(instance_id="django__django-11477")
    )
    assert prepared.record.instance_id == "django__django-11477"
    assert prepared.harness_version == "5.0.2"
    assert prepared.image_preflight_verified is True

def test_pull_is_disabled_by_default():
    assert Settings(openai_api_key="test", openai_base_url="https://example.test").swebench_allow_image_pull is False
```

参数化：空/缺上下文字段、extra、空覆盖；任务重复/缺失/多出、分组不是5/20、tasks/list摘要损坏、source revision/hash不符、gold/non评测字段、清单字符串不是 JSON list、空脚本→拒绝；所选 cancelled/failed/not_run→TASK_NOT_READY；报告损坏、路径含逃逸或外部 symlink→EVALUATION_DATA_INVALID。加载后改共享文件，prepared.record 保持旧规则。覆盖镜像仅改变 actual_image，image_preflight_verified=false，共享 bytes 不变。配置值 `0`、负数、cleanup==submission、process==test 拒绝。U1、U2、U10、U13 / TA-01～04、23。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation/test_swebench_data.py tests/evaluation/test_evaluation_settings.py -q`；预期新接口缺失。
- [ ] **Step 3: 实现资源、模型、加载与设置。** 从 `docs/swebench-task-base.json` 只复制白名单字段：instance_id、selection、repo、version、base_commit、image、platform、workspace_path；资源顶层保留原 task-base bytes 的 SHA-256 与两源 revision/hash。宿主 manifest.task_base_sha256 校验此来源摘要，不错误地与投影后的资源文件摘要比较。资源读取使用 importlib.resources，不依赖 cwd。manifest 消费字段严格建模、额外准备元数据允许扩展；只读 manifest/tasks/所选 report，不读取 gold、Parquet 或开发/正式输出副本。报告路径允许现有数据根内的相对路径，核对 report_sha256 和 Task 1 判定。
- [ ] **Step 4: GREEN 及打包检查。** 重跑 Step 2；`uv build --wheel --out-dir /private/tmp/miniagent-evaluator-wheel`，以 `python3 -m zipfile -l <实际生成wheel>` 确认包含 `miniagent/evaluation/data/swebench-verified-25.json`。预期全部 PASS、资源存在且不含 patch/test_patch/hints/problem_statement。执行 Ruff。
- [ ] **Step 5: Review 后提交。** 只 stage 上述实际改动文件；`git commit -m "feat(config): bind ready swebench task snapshots"`。不要 stage 原始 task-base 或准备数据。

### Task 3: 冻结评测 Docker 连接与准备检查

**Files:**
- Create: `src/miniagent/evaluation/swebench_docker.py`
- Create/Test: `tests/evaluation/test_swebench_docker.py`
- Modify/Test: `tests/evaluation/conftest.py` 外部 Docker/version executable fixture

**Interfaces:**
- Consumes: Task 2 EvaluationPreparationError、Settings，调用方传入一次 `dict(os.environ)` 快照。
- Produces: frozen dataclass `EvaluationDockerConnection(source_context: str | None, endpoint: str, cert_path: Path | None, tls_verify: bool, daemon_id: str)`。
- `async resolve_docker_connection(executable: Path, environment: dict[str, str]) -> EvaluationDockerConnection`。
- `docker_argv(executable: Path, connection: EvaluationDockerConnection, args: list[str]) -> list[str]`；`harness_environment(environment: dict[str, str], connection: EvaluationDockerConnection) -> dict[str, str]`。
- `async check_daemon(executable: Path, connection: EvaluationDockerConnection, *, timeout_seconds: float = 10.0) -> None`；`async inspect_image(executable: Path, connection: EvaluationDockerConnection, image: str, *, timeout_seconds: float = 10.0) -> str | None` 返回预检查 image ID，缺失返回 None，其他执行/连接失败不伪装成缺镜像。
- `async verify_harness_version(executable: Path) -> str` 调用实际绝对可执行文件 `--version`，限时10s，必须为5.0.2。
- 外部 fixture 写可执行脚本：context show/inspect、info、image inspect，从临时 JSON 场景配置响应，记录 argv/env，不读取/改变本机 Docker。

- [ ] **Step 1: 写失败测试。** context A 与 HOST B 同时存在，受控 CLI info ID为A；读取记录断言：

```python
async def test_context_wins_and_binding_survives_environment_change(docker_cli):
    environment = {"DOCKER_CONTEXT": "a", "DOCKER_HOST": "tcp://b:2375"}
    connection = await resolve_docker_connection(docker_cli, environment)
    environment["DOCKER_CONTEXT"] = "b"
    assert connection.endpoint == "unix:///tmp/daemon-a.sock"
    assert harness_environment(environment, connection)["DOCKER_HOST"] == connection.endpoint
    assert "DOCKER_CONTEXT" not in harness_environment(environment, connection)
    assert docker_argv(docker_cli, connection, ["info"])[1:3] == ["--host", connection.endpoint]
```

再测 HOST优先于当前context、无两变量时context show；验证TLS三材料及SDK等价环境、无TLS删除残留变量、父env不变；SSH/npipe/SkipTLSVerify拒绝、材料缺失/损坏inspect拒绝、DOCKER_TLS单独启用拒绝；daemon ID缺失/变化、命令超时稳定错误，不回退B。版本不符→HARNESS_VERSION_MISMATCH，超时/不可执行明确准备失败。U3 / TA-05、06；U13禁止pull检查。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation/test_swebench_docker.py -q`；预期新接口缺失。
- [ ] **Step 3: 实现连接与检查函数。** 来源、TLS规则精确按 A§6.1，context证书从 inspect 的TLSPath/docker材料解析；Unix/证书路径绝对化。外部命令各10s上限，stdout有界，进程回收计入该调用预算。每个实际Docker调用显式endpoint/TLS，移除context/未采用TLS覆盖；harness环境可保留registry认证，不能保存完整env到artifact。准备后不再读取context。无法读取版本（含超时/不可执行）使用 HARNESS_VERSION_MISMATCH 并附实际原因，Docker CLI启动/检查失败使用 DOCKER_CONNECTION_INVALID；不增加未定义的准备错误码。
- [ ] **Step 4: GREEN。** 重跑 Step 2；全部 PASS、B 无访问/删除记录；执行 Ruff。
- [ ] **Step 5: Review 后提交。** stage 本任务四文件；`git commit -m "feat(env): freeze evaluator docker connections"`。

### Task 4: Workspace 导出与内部清理共享预算

**Files:**
- Modify: `src/miniagent/environments/contracts.py:Workspace.export_patch`
- Modify: `src/miniagent/environments/local.py:LocalWorkspace.export_patch` 与其子进程预算传递
- Modify: `src/miniagent/environments/docker.py:DockerWorkspace.export_patch` 与其子进程预算传递
- Modify/Test: `tests/environments/test_local.py`、`test_docker.py`
- Modify: 所有实现 Workspace.export_patch 的测试替身（用 `rg -n 'async def export_patch' tests src` 找全），仅同步 keyword-only 参数。

**Interfaces:**
- Produces: `async export_patch(self, *, timeout_seconds: float = 60.0, cleanup_timeout_seconds: float = 5.0) -> Patch`；两者有限正数，cleanup < timeout。
- 对普通超时抛 TimeoutError；新增 `PatchExportCleanupError(RuntimeError)` 于 contracts.py，公开给提交层映射 PATCH_EXPORT_CLEANUP_FAILED。原导出主错误优先，清理诊断 add_note；取消在原 CancelledError 的 notes 附未清理路径/container/PID。
- Consumes: 现有公开 Workspace/Command/Patch；适配器不能访问 backend 私有接口。

- [ ] **Step 1: 写失败测试。** Local使用真实Git临时仓库；Docker使用受控外部CLI（不要新增内部backend mock）。新测试 `test_export_patch_shares_budget_with_cleanup`、`test_cancel_during_index_cleanup_preserves_cancellation`、`test_cleanup_failure_rejects_generated_diff`、`test_export_without_arguments_remains_supported`、`test_export_lock_wait_counts_against_budget`。关键断言：

```python
with pytest.raises(asyncio.CancelledError) as caught:
    await exporting_task
assert "index" in " ".join(caught.value.__notes__)
assert monotonic() - started < timeout_seconds + 3.0
assert original_index_bytes == original_index.read_bytes()
```

测试进程以文件握手暴露卡住阶段，拒绝TERM时监督器记录PID；断言 CLI 已回收或明确记录未回收身份，不宣称远端Git已退出。diff已生成但清理失败仍抛 PatchExportCleanupError。保留既有 binary/full-index/真实base_revision行为测试。U10、U11 / TA-08。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/environments/test_local.py tests/environments/test_docker.py -q`；预期 keyword 参数不支持/新清理行为断言失败。
- [ ] **Step 3: 修改导出契约及两个实现。** 从进入函数起计算monotonic deadline（含锁）；工作截止=总deadline−清理预留，每个命令传剩余预算。finally用shield独立任务，最多min(预留,总剩余)，等待/流任务回收也受预算，不留下不受约束后台清理。停止后续命令并保留诊断；无参数调用也有界。不要对所有Workspace命令做无关重构。
- [ ] **Step 4: GREEN。** 重跑 Step 2，执行 Ruff 和 `uv run ty check`；协议实现/调用方均兼容，全部 PASS。
- [ ] **Step 5: Review 后提交。** 精确 stage 当前任务文件；`git commit -m "enh(env): bound patch export and cleanup time"`。

### Task 5: 唯一候选提交及通用适配器

**Files:**
- Create: `src/miniagent/cli/candidate_submission.py`
- Create/Test: `tests/cli/test_candidate_submission.py`

**Interfaces:**
- Consumes: Task 4 Workspace导出，ArtifactStore/ArtifactWrite，通用Evaluator协议。
- Produces: `CandidateSubmission(workspace: Workspace, artifact_store: ArtifactStore, *, timeout_seconds: float = 60.0, cleanup_timeout_seconds: float = 5.0)`。
- `async submit(self, *, kind: Literal["candidate", "diagnostic"]) -> SubmissionResult`；公开只读 `.result: SubmissionResult`。
- SubmissionResult frozen内部dataclass，字段 `status: Literal["not_attempted", "submitted", "empty", "failed", "cancelled"]`、`submission_id: UUID | None`、`patch_ref: ArtifactRef | None`、`error: EvaluationError | None`、`diagnostics: tuple[str, ...]`。首次尝试前ID为空，开始时创建，所有终态保留该ID。
- `WorkspaceCandidateEvaluator(submission: CandidateSubmission, evaluator: Evaluator)`；`async evaluate(self, request: EvaluationRequest) -> EvaluationResult`。

- [ ] **Step 1: 写失败测试。** 真实LocalWorkspace导出改动/真实FileArtifactStore，外部边界RecordingEvaluator只记录通用request。`test_submit_once_preserves_request_and_bytes` 断言：

```python
assert received.run_id == request.run_id
assert received.task == request.task
assert received.plan == request.plan
assert received.candidate.text == request.candidate.text
assert await store.read(received.candidate.patch) == exported_bytes
assert submission.result.patch_ref == received.candidate.patch
assert len([r for r in await store.list_for_run(run_id) if r.type == "workspace_patch"]) == 1
```

其他测试：重复提交submitted/empty/failed/cancelled状态无新的record/ID；提交后改Workspace不替换patch；空字符串→EMPTY_PATCH且下游零调用，`" "`不当空；提前失败诊断metadata.kind=diagnostic、无需attempt；导出失败/清理失败/剩余预算不足/登记目录不可写分别映射规定错误。取消重新抛，status=cancelled且notes保存。U4、U10、U11 / TA-07～09。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/cli/test_candidate_submission.py -q`；预期模块缺失。
- [ ] **Step 3: 实现提交状态和适配器。** 同一deadline覆盖锁/导出/UTF-8/put；仅非空成功导出登记workspace_patch，metadata含submission_id/kind/stage/files/base_revision。Pydantic正常构造新candidate/request，只替换patch；提交失败返回passed=false/具体error，取消不转EvaluationResult。SubmissionResult不能保存SWE-bench结果或attempt。
- [ ] **Step 4: GREEN。** 重跑 Step 2及Task 4相关测试；全部 PASS，执行 Ruff。
- [ ] **Step 5: Review 后提交。** stage 两文件；`git commit -m "feat(cli): freeze candidate patches before evaluation"`。

### Task 6: Attempt 输入、路径及无损 predictions

**Files:**
- Create: `src/miniagent/evaluation/swebench.py`（本任务仅输入准备，不提供临时宽松evaluate）
- Create/Test: `tests/evaluation/test_swebench_inputs.py`

**Interfaces:**
- Consumes: PreparedTask、EvaluationRequest、ArtifactStore、Settings预算/路径。
- Produces: 内部 frozen dataclass `EvaluationAttempt`，字段 `attempt_id: UUID`、`directory: Path`、`harness_run_id: str`、`model_key: str`、`patch_ref: ArtifactRef`、`patch_bytes: bytes`、`submission_id: str`、`predictions_sha256: str`；属性 `instance_directory: Path` 根据绑定instance_id算固定日志目录（dataclass同时存 `instance_id: str`）。
- `async prepare_attempt(request: EvaluationRequest, *, prepared: PreparedTask, artifact_store: ArtifactStore, work_root: Path, model_name: str, timeout_seconds: float = 30.0) -> EvaluationAttempt`。
- 输入失败内部 `EvaluationInputError` 带 `.code`；无patch/无效引用/非UTF8→PATCH_ARTIFACT_INVALID，零bytes→EMPTY_PATCH，工作目录/输入I/O错误→EVALUATION_ARTIFACT_FAILED。空/坏patch不得创建attempt。

- [ ] **Step 1: 写失败测试。** 通过真实ArtifactStore.put写corpus；内容含CRLF、Unicode、引号、反斜杠、有/无末尾换行；加真实Git binary/mode/rename diff。`test_predictions_round_trip_exact_bytes`：

```python
content = (attempt.directory / "predictions.jsonl").read_bytes()
assert content.endswith(b"\n")
assert len(content.splitlines()) == 1
prediction = json.loads(content)
assert set(prediction) == {"instance_id", "model_name_or_path", "model_patch"}
assert prediction["model_patch"].encode("utf-8") == patch_bytes
assert prediction["model_name_or_path"] == "model-" + hashlib.sha256(model_name.encode()).hexdigest()[:16]
```

`test_attempt_does_not_reuse_directory` 两次相同Run调用目录/attempt不同；恶意模型名不逃逸，非UTF8/损坏patch失败且无attempt。task.json严格单记录list，默认/覆盖镜像正确，共享包bytes不变；config原模型名、ref/hash/submission_id、三快照摘要和基线正确。U5、U6 / TA-10、11、14。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation/test_swebench_inputs.py -q`；预期新接口缺失。
- [ ] **Step 3: 实现输入函数。** store.get_record/read验证归属Run、workspace_patch类型和submission_id；严格UTF8，标准JSON序列化bytes加LF。directory=`work_root/run_id.hex/attempt_id.hex`，mkdir不复用；harness_run_id=`run_id.hex-attempt_id.hex`。实例目录固定`logs/run_evaluation/<id>/<safe_model_key>/<instance_id>`。resolve限制所有inputs/evidence在attempt内；读写使用to_thread并受输入阶段deadline约束，线程取消限制照规格保留。
- [ ] **Step 4: GREEN。** 重跑 Step 2，全部 PASS，执行 Ruff。
- [ ] **Step 5: Review 后提交。** stage 两文件；`git commit -m "feat(models): prepare isolated swebench submissions"`。

### Task 7: 完整 Evaluator、清理和必要证据

**Files:**
- Modify: `src/miniagent/evaluation/swebench.py`、`swebench_docker.py`
- Create/Test: `tests/evaluation/test_swebench.py`、`test_swebench_cleanup.py`
- Modify/Test: `tests/evaluation/conftest.py` 受控harness场景

**Interfaces:**
- Consumes: Task 1报告、Task 2数据、Task 3连接、Task 6attempt、ArtifactStore。
- Produces: `SWEbenchEvaluator(*, artifact_store: ArtifactStore, prepared: PreparedTask, settings: Settings, model_name: str, executable: Path, docker_executable: Path, connection: EvaluationDockerConnection, environment: dict[str, str])`；`async evaluate(self, request: EvaluationRequest) -> EvaluationResult`。
- 进程/清理函数保持包内private，不新增对外runner协议。config序列化 Settings 仅明确非敏感字段，不整体dump Settings。
- Test fixture `harness_executable(tmp_path) -> Path`：读取单任务与predictions，从自身旁边scenario.json选择行为，写真实实例文件；argv/env/cwd及阶段握手写独立记录。支持完整通过、unresolved、坏/缺报告、错patch、非零、超时尾标、忽略TERM/子进程、大日志。Docker脚本支持邻居容器、数字后缀、inspect/remove及daemon改变。

- [ ] **Step 1: 写失败测试。** `test_complete_evidence_is_required_for_success` 使用合成就绪包、真实store、受控executables：

```python
result = await evaluator.evaluate(request)
assert result.passed is True
assert result.error is None
types = {ref.type for ref in result.artifacts}
assert {"workspace_patch", "evaluation_predictions", "evaluation_config", "evaluation_summary",
        "evaluation_report", "evaluation_stdout", "evaluation_stderr",
        "evaluation_test_output", "evaluation_instance_log"} <= types
for ref in result.artifacts:
    assert isinstance(await store.read(ref), bytes)
```

每种必要成功文件分别缺失→EVALUATION_ARTIFACT_FAILED；0+unresolved→TASK_UNRESOLVED；0+缺报告→REPORT_MISSING；无效报告→REPORT_INVALID；错误任务/外部symlink/patch.diff缺失或不同→REPORT_MISMATCH；非零+有效报告→HARNESS_PROCESS_FAILED。严格尾标→EVALUATION_TEST_TIMEOUT，伪timeout文本仍REPORT_MISSING。启动失败/读取失败/整体超时各有稳定error且retryable=false。

`test_cancel_reaps_process_group_and_only_attempt_containers`：文件握手后取消，assert CancelledError；原进程/子进程退出，本次精确名称及`.数字`冲突容器不存在，相似前缀/另Run/Agent容器保留。`test_daemon_change_prevents_deletion`：summary含DOCKER_DAEMON_CHANGED及未清理身份，脚本无rm记录，正常路径不通过。

多故障测试保留首次主错误：非零+清理失败、unresolved+登记失败、坏报告+登记失败、超时+清理失败；resolved+清理/登记失败分别以EVALUATION_CLEANUP_FAILED/EVALUATION_ARTIFACT_FAILED为主。真实磁盘故障用artifact根被文件占据等公开I/O故障；取消仍保持取消。顺序保存长stdout/stderr原bytes，摘要≤4096。U6～8、U10～13 / TA-11～15、18～20、22、23。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation/test_swebench.py tests/evaluation/test_swebench_cleanup.py -q`；预期完整Evaluator缺失。
- [ ] **Step 3: 实现完整生命周期。** 执行前核对daemon ID/缓存镜像；argv精确按A§8.3，cwd=attempt，stdout/stderr直接文件，start_new_session=True。整体deadline从启动前起含启动/等待；报告固定实例路径，验证patch.diff bytes与predictions关联。成功必须符合Task 1全部条件，不读汇总report代替。
- [ ] **Step 4: 实现有界清理。** 正常结束也检查容器；异常先TERM组≤5s，必要KILL并有界回收，然后同连接info/查询/精确名称匹配/按ID删/确认消失，全阶段共享30s预算。名称regex锚定`^sweb\.eval\.<转义任务小写>\.<转义harness_run_id>(?:\.[0-9]+)?$`；禁止前缀批量删、禁止删镜像。记录ID/name/image ID及失败原因；shield任务仍在预算内await完成，取消notes/summary记录诊断再重新抛原取消。
- [ ] **Step 5: 实现顺序证据登记。** 成功文件清单按测试要求，所有路径resolve在attempt内。每次读一份、put一份、释放bytes；登记整体30s，不能每文件30s。config含A§10全字段与image观察来源，未观察实际image ID可null并解释；summary仅有界摘要/主附错误/未清理未登记身份，不放隐藏详情。先写config和summary原文件；其他文件登记完成后更新summary再登记，summary引用不自指。必要登记失败不能成功，尽力登记更新的summary，不重试原失败文件。attempt目录一律保留。
- [ ] **Step 6: GREEN。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation tests/cli/test_candidate_submission.py -q`；全部 PASS；执行 Ruff、ty check。记录受控进程证据，不称真实Docker通过。
- [ ] **Step 7: Review 后提交。** stage 本任务文件；`git commit -m "feat(models): verify candidates with independent swebench evidence"`。

### Task 8: Runtime 终态证据与顺序 manifest 验证

**Files:**
- Modify: `src/miniagent/runtime/loop.py:AgentRuntime._evaluate`
- Modify: `src/miniagent/runtime/execution.py:_PendingEvaluationResult/stage_evaluation_result/finish`
- Modify: `src/miniagent/runtime/trace_context.py:evaluation_event/_event`
- Modify: `src/miniagent/artifacts/file.py:FileArtifactStore.list_for_run`
- Modify/Test: `tests/runtime/test_runtime.py`、`tests/artifacts/test_file_store.py`

**Interfaces:**
- Consumes: EvaluationResult.artifacts 和已存在NewRunEvent.artifacts。
- Produces: `RunExecution.stage_evaluation_result(*, passed: bool, final_output: str | None = None, artifacts: list[ArtifactRef] | None = None) -> None`；内部复制refs。
- `RunTraceContext.evaluation_event(payload: EventPayload, *, artifacts: list[ArtifactRef] | None = None) -> NewRunEvent`；_event同样接收optional artifacts，默认空列表，现有调用保持兼容。
- FileArtifactStore.list_for_run签名与全量内容校验契约不变；不新增RunResult字段/SQLite表/payload字段。

- [ ] **Step 1: 写失败测试。** 用真实store写报告artifact，外部Evaluator返回ref，现有Runtime测试工厂注入，重开SQLite并读取公开events/snapshot；`test_evaluation_artifacts_commit_with_terminal_state`：

```python
assert completed.artifacts == [report_ref]
assert completed.sequence < finished.sequence
assert completed.span_id != started.span_id
assert snapshot.state.lifecycle.status is RunStatus.SUCCEEDED
assert result.reason is TerminationReason.TASK_VERIFIED
```

失败评测也保存refs且FAILED/EVALUATION_FAILED；取消不产生EVALUATION_COMPLETED。复用SQLite公开事务冲突方式证明terminal batch失败不会只保存completed；不patch内部commit。store新增多大文件验证完整refs与单项损坏fail-fast；实际峰值实验归Task11。U9、U12 / TA-16、22。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/runtime/test_runtime.py tests/artifacts/test_file_store.py -q`；预期事件refs缺失。
- [ ] **Step 3: 接入refs与顺序验证。** _evaluate暂存artifacts，finish在原同一commit的EvaluationCompleted事件带refs；不产生独立提前commit。list_for_run将内容校验gather改逐项await，仅records列表常驻，异常不返回部分records。
- [ ] **Step 4: GREEN。** 重跑Step2和 `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/storage/test_sqlite.py -q`；全部 PASS；执行 Ruff、ty check。
- [ ] **Step 5: Review 后提交。** stage 上述六文件；`git commit -m "enh(cli): persist evaluation evidence with run completion"`。

### Task 9: Application 唯一交付来源、收集回退与展示

**Files:**
- Modify: `src/miniagent/cli/application.py`
- Modify: `src/miniagent/cli/rendering.py`
- Modify/Test: `tests/cli/test_application.py`、`tests/cli/test_main.py`
- Create/Test: `tests/cli/test_evaluation_delivery.py`（真实LocalWorkspace/store/SQLite，外部Provider/Evaluator）

**Interfaces:**
- Consumes: Task 5 CandidateSubmission状态，Task 8 list_for_run通用记录；Runtime公开result。
- Produces: ApplicationDependencies增加 `evaluator: Evaluator = field(default_factory=PassThroughEvaluator)`、`candidate_submission: CandidateSubmission | None = None`、`artifact_collection_timeout_seconds: float = 30.0`；新增字段放required字段后，兼容现有直接构造。
- FinalizationError.stage增加 `artifact_collection`；`ApplicationResult`公开签名不变。
- Application在依赖无submission时为Local/直接调用创建默认CandidateSubmission；注入Runtime的evaluator来自dependencies。

- [ ] **Step 1: 写失败测试。** `test_delivery_reuses_validated_patch_after_workspace_changes`：评测后再改Workspace，store最终bytes仍为评测patch、只有一份patch记录。`test_collection_failure_only_returns_verified_patch`：先通过Evaluator登记有效refs，再用真实文件写坏一份evaluation artifact：

```python
assert result.run_result.status is RunStatus.SUCCEEDED
assert [ref.type for ref in result.artifacts] == ["workspace_patch"]
assert any(error.stage == "artifact_collection" for error in result.finalization_errors)
assert workspace.root.exists()
```

另测patch也损坏→artifacts=()；预算耗尽不得开新全量扫描或独立完整patch预算；模型提前失败仅一次diagnostic导出；取消已submitted不重导出；Local成功且没有改动时无patch、无finalization错误并正常destroy；成功无交付错误destroy、失败/取消/收集错误保留；destroy失败保留Run成功且有finalization错误。Run异常路径同样一次诊断/收集，原异常不被覆盖。

展示测试artifacts故意排为report、summary、patch，assert `Patch:` URI为patch；无patch不将report称patch。CLI不展示隐藏报告正文，长错误摘要≤4096；保持普通失败及signal退出规则。U4、U8、U9、U14 / TA-09、14、17、21、24。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/cli/test_evaluation_delivery.py tests/cli/test_application.py tests/cli/test_main.py -q`；预期未注入Evaluator/终态后重新导出/列表顺序断言失败。
- [ ] **Step 3: 实现finalization与汇总。** 只not_attempted才诊断submit；取消提交diagnostics映射patch_export错误。diagnostic空patch沿用原Local行为：无artifact、无finalization错误；EMPTY_PATCH仅由正式评测适配器判候选失败。提交导出/超时/清理错误映射patch_export，put错误映射artifact_registration。每Run仅一次list_for_run，共享30s deadline；成功列表按artifact_id去重、按metadata.submission_id恢复发布完成patch引用。全量失败时只允许内存已有patch_ref在同deadline剩余内get_record验证；无剩余/失败→空artifacts，追加诊断，不用EvaluationResult或journal补refs。Run异常路径给原异常add_note，正常路径返回FinalizationError，不改Run状态。
- [ ] **Step 4: 实现展示。** 按type==workspace_patch查找；report/summary最多显示URI；错误消息单条最多4096字符，完整原文件仍保存。不要扩大CLI功能。
- [ ] **Step 5: GREEN。** 重跑Step2，现有应用直接构造测试若用了极简store，改为真实FileArtifactStore，不新增对内部finalization mock；全部PASS。执行 Ruff、ty check。
- [ ] **Step 6: Review 后提交。** stage 本任务五文件；`git commit -m "enh(cli): deliver verified evaluation artifacts"`。

### Task 10: Bootstrap 在模型与 Workspace 前预检及注入

**Files:**
- Modify: `src/miniagent/cli/bootstrap.py:BootstrapConfig/bootstrap`
- Modify: `src/miniagent/cli/main.py:main` 已知准备错误呈现
- Modify/Test: `tests/cli/test_bootstrap.py`、`tests/cli/test_main.py`
- Create/Test: `tests/cli/test_swebench_bootstrap.py`

**Interfaces:**
- Consumes: Task 2 prepared snapshot/Settings，Task 3冻结连接/version，Task 5适配器，Task 7Evaluator，Task 9依赖字段。
- Produces: `BootstrapConfig.swebench_task: SWEbenchTaskContext | None = None`；bootstrap签名不变。
- 不添加CLI任务参数；宿主配置遵循当前BaseSettings环境变量名（例如SWEBENCH_ALLOW_IMAGE_PULL）。

- [ ] **Step 1: 写失败测试。** Local在不存在data/harness/docker配置下成功，不产生评测预检命令；Docker无context→TASK_CONTEXT_REQUIRED、清单外→TASK_UNSUPPORTED、not_ready→TASK_NOT_READY、版本不符→HARNESS_VERSION_MISMATCH、禁止pull缺image→EVALUATION_IMAGE_UNAVAILABLE。所有准备失败断言Workspace创建记录/Provider请求为空，没有伪造Run/terminal事件。

内部入口已就绪+ready=false允许；通过外部Provider产生candidate及受控harness回报，assert最终TASK_VERIFIED、同patch refs和SQLitecompleted。上下文T但task/model/Agent镜像含其他ID，仍评测T；自定义Agent镜像不做清单匹配。覆盖评测image记录false但可真实候选通过，共享tasks不变。冻结后外部场景切换context不换endpoint。CLI普通Docker准备错误简明非零、无新`--instance-id`；未知异常继续传播。U1、U3、U13、U14 / TA-01、03～06、23、24。

- [ ] **Step 2: RED。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/cli/test_swebench_bootstrap.py tests/cli/test_bootstrap.py tests/cli/test_main.py -q`；预期新config字段/预检缺失。
- [ ] **Step 3: 修改装配顺序。** 有效kind优先config，否则settings；Local短路不读取评测数据；Docker按A§6顺序检查上下文、数据、报告、版本、冻结连接、daemon、实际image，在create_workspace前完成。resolve可执行文件绝对路径，一次宿主env快照。Workspace创建后将同store/submission/evaluator注入ApplicationDependencies，复用现有装配失败destroy规则；不把隐藏配置/数据放入Workspace或modelrequest。
- [ ] **Step 4: CLI已知错误呈现。** 仅捕获EvaluationPreparationError，输出含稳定码的≤4096字符摘要并现有失败退出码；Settings/未知错误不吞。准备失败不启动Runtime。
- [ ] **Step 5: GREEN。** 重跑Step2；`UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent --help`，预期原CLI参数面不变。执行 Ruff、ty check。
- [ ] **Step 6: Review 后提交。** stage 本任务五文件；`git commit -m "feat(cli): require ready independent evaluation for docker runs"`。

### Task 11: 跨组件取消、资源实验和真实开发任务验收

**Files:**
- Create/Test: `tests/integration/test_swebench_evaluation.py`
- Create/Test: `tests/cli/test_evaluation_cancellation.py`
- Create/Test: `tests/artifacts/test_evaluation_memory.py`
- Create: `docs/superpowers/acceptance/independent-evaluator.md`
- Modify: `docs/repository-map.md` 仅更新已实现事实；不修改README/CONTEXT域术语。

**Interfaces:**
- Consumes: 前十任务公开入口，原宿主准备包。无新增产品接口。
- 真实测试默认skip，显式环境 `MINIAGENT_REAL_SWEBENCH=1` 启用；`MINIAGENT_SWEBENCH_REFERENCE_PATCH` 为宿主开发任务有效参考patch路径，`MINIAGENT_SWEBENCH_INSTANCE_ID` 必须是安全资源selection=development且已就绪的ID；这仅是pytest驱动配置，不是产品CLI入口。
- fixture外部Provider在AgentWorkspace使用工具应用参考patch然后提交，不将gold bytes/路径放modelrequest；这是测评链路驱动，不证明模型解决能力。无效非空候选单独运行，不用正式任务调优。

- [ ] **Step 1: 写失败的跨组件验收测试。** `test_cancel_before_attempt_preserves_run_and_workspace`：外部Docker导出清理卡住后cancel token；assert CANCELLED/CANCEL_REQUESTED、无harness/attempt、稳定submission ID、finalization notes含资源身份、Workspace保留、elapsed≤配置预算+3s监督容差。`test_cancel_during_harness_collects_diagnostics_without_completed_event`：完整Runtime/Application/SQLite链路cancel，assert诊断manifests可追溯，无EvaluationCompleted，独立进程组回收、邻居不受影响。

大日志隔离子进程实验：在子进程基线后创建/读取真实store的`1/4/8`份各`16 MiB`日志，每份内容不同；分别测登记/汇总峰值RSS（macOS/Linux单位归一），固定数量再改最大单文件`8/16/32 MiB`。oracle为refs/hash完整、损坏仍拒绝及峰值增长形态记录；不设置无来源绝对阈值，不mock_read_content，不用tracemalloc代替真实RSS。发布中取消实验用真实大型put与manifest公开出现握手；已发布且list成功时恢复同submission_id的唯一patch，无第二次导出。不能稳定命中发布窗口时标记该精确实验未验证。

真实测试 `test_real_ready_development_patch_is_verified` / `test_real_invalid_patch_fails`：分别assert Run状态/reason、predictions逐byte一致、实例报告/必要refs/SQLite、评测容器清理、成功销毁/失败保留AgentWorkspace。TLS验收单独用真实验证TLS daemon A及B邻居，不能用Unix成功替代TLS证据。U11、U12、U15 / TA-08、09、18～22、25。

- [ ] **Step 2: 验证RED或精确缺口。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/cli/test_evaluation_cancellation.py tests/artifacts/test_evaluation_memory.py -q`。新增测试先针对之前未覆盖路径；若前十任务实现已满足则如实记录首跑PASS，不人为破坏代码。真实测试默认SKIP不算GREEN。
- [ ] **Step 3: 修复新验收揭示的缺陷。** 仅在对应责任文件作最小修复，每个失败先确认根因再改；不为使实验通过绕开完整性校验、加内部mock或提高产品预算。出现范围变更先记录并更新计划/规格。
- [ ] **Step 4: 定向最终GREEN。** Run: `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/evaluation tests/cli tests/environments tests/artifacts tests/runtime/test_runtime.py tests/storage/test_sqlite.py -q`，再变更文件Ruff和`uv run ty check`。全部通过才报告受控层完成；记录skip/环境缺口。
- [ ] **Step 5: 显式执行真实验收。** 确认宿主5.0.2/所选开发任务就绪/本机缓存镜像；设置上述测试驱动环境后运行 `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/integration/test_swebench_evaluation.py -q -rs`。真实输入输出和隐藏日志仅保存宿主证据目录。不得恢复目前已停止的镜像下载；无缓存/daemon/TLS环境则记录未执行，不改task集合、不以受控测试代替。父环境变更前后/daemon A/B邻居/实际image观察来源留证。
- [ ] **Step 6: 写验收记录和使用说明。** acceptance文件写内部BootstrapConfig调用示例、宿主设置/目录、逐任务门槛、普通Docker CLI限制、镜像覆盖和取消证据位置、原目录诊断入口、单文件内存/线程取消限制。逐项标明受控测试、真实Unix/TLS链路、发布窗口实验、25项准备的结果或未执行；附命令、版本、摘要、Run/attempt/submission IDs及宿主证据位置，不把隐藏日志贴入repo。
- [ ] **Step 7: Review 后提交。** 精确stage验收文件和实际修复；测试/修复与文档分别提交：`test: verify independent evaluation lifecycle`、`docs: document independent evaluation evidence and usage`。无缺陷则没有额外产品代码提交。

## 数据准备与全量实验的独立交付边界

R§4 的数据准备是必要配套，但宿主已存在 `prepare_data.py`、`refresh_manifest.py`、两源固定数据、25项tasks和部分gold预检。正常运行不能下载/重写它们，也不能假装需要全部重建；本计划Task2消费并验证这些准备产物，Task11使用已就绪开发任务。

正式完整实验的验收仍为 `TC-U15-03`：两源revision分别为 `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`、`78f471bf655a3137b2e8a75af1501690ec009ec3`，源SHA-256分别为 `a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`、`030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`。按instance_id比较ID/repo/version/base_commit/test_patch及JSON解析后的两测试列表，生成正常记录只含A§5.3字段；gold仅宿主准备流程使用。

**全量实验不属于首个开发任务上线门槛。** 宿主目前保留“不继续下载镜像”的状态；需要可移植准备工具或恢复25项gold时，另写“数据准备”子计划，在外部uv tool环境运行，不向miniAgent引入datasets/pyarrow/swebench依赖。该子交付必须把来源核对、25/25报告/日志/摘要和manifest.ready=true作为验收；未完成则记“全量冻结实验未验收”。不能由低阶执行者为了完成本计划自行恢复下载或把7项样本写成25项成功。

## 覆盖映射与自检

| 规格义务 / 测试单元 | 任务 |
|---|---|
| R§1–3、A§5–6：范围/任务入口/Local、U1 | 2、10 |
| R§4、A§5.3–6：冻结包、逐任务门槛/快照、U2 | 2；全量准备独立边界 |
| A§6.1：连接/TLS/daemon、U3 | 3、7、11真实TLS |
| R§5、A§7–7.1：唯一提交/预算、U4/U10 | 4、5、9、11 |
| R§6、A§8：predictions/attempt路径、U5/U6 | 6、7 |
| R§7、A§9：严格判定/优先级、U7/U8 | 1、7 |
| A§10：SQLite/唯一交付来源、U9 | 8、9 |
| R§8、A§11：取消/进程组/精准清理、U11 | 4、7、11 |
| A§10、14：日志/顺序I/O资源边界、U12 | 7、8、9、11 |
| 镜像覆盖/缓存/拉取、U13 | 2、3、7、10 |
| 信息隔离/包资源/兼容性、U14 | 2、8、9、10、11 |
| 真实成功/失败/TLS/全量准备、U15 | 11；TC-U15-03独立全量准备 |

编写后已逐项对照R/A：覆盖产品链路与所有Review Focus，接口按前置任务一致传递，取消不伪造completed，Application不借用Runtime私有refs，清理/登记不重置阶段预算。Q4/Q5/Q7已写出计划级推荐契约；D1–D3、默认预算及计划级细化待用户集中审阅。Q6资源不新增虚构阈值，真实TLS/线程发布窗口/25项全量准备缺证据时必须保留未验证状态。

## 交接给低阶模型

每次执行一个Task，提供：根AGENTS.md、R/A相关章节、本计划Global Constraints/集中确认项、该Task完整Files/Interfaces/步骤、前置Task实际公开接口。不要只传任务标题或让执行者从测试设计表推测产品决策。

建议Subagent-driven：每任务低阶模型实现，新的reviewer核对规格与行为，Task4/7/9重点检查预算、取消、证据与资源身份，最后review整条链路。Native也可使用本计划逐任务执行，但仍应安排最终独立review。本次不启动任何执行者、不改产品代码、不运行运行时测试；由用户审阅计划后选择执行方式。
