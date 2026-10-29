# miniAgent

miniAgent 是使用 Python 3.13 实现的最小 Coding Agent Runtime。模型在有界 Step 循环中调用工具；Runtime 管理状态、执行、终止、用量和费用；CLI 负责配置装配、Workspace 生命周期、评测和结果交付。

项目已接入 SQLite 持久化、跨进程恢复、上下文压缩、SWE-bench 独立评测和只读 Dashboard，仍处于开发阶段。已实现行为以代码和测试为准，长期设计见 [项目蓝图](docs/blueprint.md)。

## 当前能力

- **模型调用**：通过 `QwenProvider` 对接 OpenAI-compatible Chat Completions API，支持超时、可重试错误分类、退避和用量计费。
- **工具执行**：提供 `read_file`、`shell`，校验工具参数和授权范围，限制送回模型的观察内容。默认 Agent 仅授权 `shell`。
- **Workspace**：支持 Local 副本和 Docker Workspace，管理命令执行、取消、补丁导出、Artifact 收集与清理。
- **策略与审批**：默认 Local shell 调用需要人工审批；Docker Workspace 内的 shell 默认允许执行。
- **运行记录**：SQLite 保存 Run、Event、Snapshot、Approval 和 Event Span；模型请求/响应、工具原始返回及业务命令输出通过持久化记录和 Artifact 保存。
- **中断与恢复**：支持信号触发的受控中断、保留 Workspace，以及通过原 Run ID 跨进程恢复。
- **上下文压缩**：在已完成 Step 的边界验证压缩来源，并原子提交 Event 与 Snapshot。
- **评测与导出**：Local 使用透传 Evaluator；SWE-bench 使用独立测试验证候选补丁。可选导出 JSON trajectory。
- **查看与批量运行**：只读 Dashboard 查看运行证据；`trigger` 执行开发任务，`eval.py` 批量执行正式评测任务。

## 快速开始

需要 Python 3.13、Git、[uv](https://docs.astral.sh/uv/) 和一个兼容 OpenAI Chat Completions API 的模型服务。Docker 与 SWE-bench harness 仅在评测路径中需要额外准备。

在本项目的 Git 工作树根目录执行：

```bash
uv sync
```

创建 `.env`：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
```

运行任务：

```bash
uv run miniagent "找出模型 Provider 的实现路径并说明调用关系"
# 等价入口
uv run miniagent run "找出模型 Provider 的实现路径并说明调用关系"
```

默认模型为 `qwen3.8-flash`。Local 模式要求启动目录是 Git 工作树根目录，并将 Git 跟踪文件及未被忽略的未跟踪文件复制到独立 Workspace；修改不会直接写回源仓库。完成后 CLI 输出结果、用量、费用及已保存补丁的位置。

Local shell 调用会提示人工审批，因此首次运行应使用交互式终端。

## 模型与提示词配置

默认 Agent 由 [default_agent.py](src/miniagent/agents/default_agent.py) 中的 `build_default_agent(system_prompt)` 构造，模型目标与生成参数分开建模：

| 配置 | 当前默认值或来源 |
| --- | --- |
| `model.provider` | `qwen`，用于选择 `QwenProvider` |
| `model.model` | `qwen3.8-flash`，可通过 `--model` 覆盖 |
| `generation.temperature` | `0` |
| `generation.max_output_tokens` | 工厂默认 `4096`；新 Run 装配时使用模型限制文件中的值 |
| `allowed_tools` | `("shell",)` |
| `system_prompt` | 运行时从提示词 YAML 加载 |

更换模型前，需在以下两个文件中同时配置对应的 `(provider, model)`：

- [config/model_limits.toml](config/model_limits.toml)：上下文窗口和最大输出 Token 数。
- [config/model_pricing.toml](config/model_pricing.toml)：币种、计费单位、输入、缓存输入和输出单价。

例如，仓库当前已配置 `deepseek-v4.1-flash-1017`：

```bash
uv run miniagent "检查工具执行的错误处理" --model deepseek-v4.1-flash-1017
```

`--model` 只替换发送给服务的模型名称；Provider 选择和 API 地址仍由当前装配与环境配置决定。

提示词位于 [config/deepseek_prompt.yaml](config/deepseek_prompt.yaml)，结构如下（示例为简化内容）：

```yaml
system_prompt: |
  You are a helpful assistant that can solve programming tasks.
instance_prompt: |
  Please solve this issue: {{ problem_statement }}
```

- `system_prompt` 用于新 Run 的系统指令。
- `instance_prompt` 由 Jinja 渲染，仅向 SWE-bench 任务注入 `problem_statement`；普通 CLI 任务直接使用用户输入。
- `trigger` 与 `eval.py` 共用同一份 instance prompt。批次开始时保存 `prompts.yaml` 快照，各 worker 使用该快照。
- 配置缺失、字段类型错误、空白提示词或未定义模板变量会报错。任务原文中的 Jinja 符号不会被二次解析。
- 新 Run 启动时读取文件；运行中不热更新。Resume 使用原 Run 保存的系统指令和任务文本。

可在环境变量或 `.env` 中设置 `PROMPT_FILE` 覆盖提示词路径。文件名中的 `deepseek` 不控制模型选择。

## 常用运行配置

CLI 从环境变量和 `.env` 加载配置。默认资源路径相对启动目录解析；从其他 Git 仓库启动时，需显式指定配置文件路径。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PROMPT_FILE` | `config/deepseek_prompt.yaml` | 系统指令与任务模板 |
| `MODEL_LIMITS_FILE` | `config/model_limits.toml` | 模型上下文和输出限制 |
| `MODEL_PRICING_FILE` | `config/model_pricing.toml` | 模型定价 |
| `WORKSPACE_BACKEND` | `local` | Workspace 类型，可由 `--workspace` 覆盖 |
| `LOCAL_WORKSPACE_ROOT` | `~/.miniagent/workspaces` | Local Workspace 根目录，须在源仓库外 |
| `RUN_DATABASE_PATH` | `~/.miniagent/runs.sqlite3` | SQLite Run 数据库 |
| `ARTIFACT_ROOT` | `~/.miniagent/artifacts` | 保存的输出、补丁和运行证据 |
| `EXPORT_TRAJECTORY` | `false` | 是否额外导出 JSON trajectory |

完整设置见 [settings.py](src/miniagent/cli/settings.py) 和 [prompts.py](src/miniagent/cli/prompts.py)。

### Trajectory 与运行日志

```bash
uv run miniagent "检查仓库中的测试入口" --trajectory
uv run miniagent "检查仓库中的测试入口" --no-trajectory
```

`--trajectory/--no-trajectory` 优先于 `EXPORT_TRAJECTORY`，resume 沿用原 Run 的选择。关闭 JSON 导出仍会保存必要的 Run 记录与执行证据；导出失败不会替换 Runtime 的原始结果。

正常结果输出到 stdout，进度输出到 stderr；默认 JSONL 日志写入 `~/.miniagent/logs`。日志级别、目录和心跳设置见 [日志说明](docs/logging.md)。

### 中断与恢复

CLI 创建 Run 后会输出 Run ID 和恢复命令。收到首次 `SIGINT` 或 `SIGTERM` 时尝试受控中断；可恢复性取决于持久化状态和 Workspace 是否完整保留。

先检查恢复条件，再执行恢复：

```bash
uv run miniagent resume --run-id <run-uuid> --dry-run
uv run miniagent resume --run-id <run-uuid>
# 使用非默认数据库时
uv run miniagent resume --run-id <run-uuid> --database /path/to/runs.sqlite3
```

恢复会校验冻结的运行定义、工具契约、原 Workspace 身份及执行证据，并通过 Run 锁防止并发恢复。无法确认 shell 是否已执行时会阻止重放；恢复不是重新运行一遍任务，也不是任意故障后的自动重试。

## SWE-bench 评测

仓库冻结了 5 个开发任务和 20 个正式评测任务，任务清单见 [swebench-task-base.json](docs/swebench-task-base.json)，筛选依据见 [任务筛选文档](docs/swebench-task-selection.md)。

运行前需要准备可用的 Docker daemon、任务固定的镜像，以及匹配的评测数据包和 `swebench` harness。当前加载器校验 harness 版本 `5.0.2`、数据摘要、任务成员、镜像和任务的 gold preflight 状态；任务清单本身不能代替准备完成的数据包。

相关配置：

| 环境变量 | 默认值 |
| --- | --- |
| `SWEBENCH_DATA_ROOT` | `~/.miniagent/evaluation/swebench-verified-25-20261022` |
| `SWEBENCH_WORK_ROOT` | `~/.miniagent/evaluation/runs` |
| `SWEBENCH_EXECUTABLE` | `swebench` |
| `EVALUATION_DOCKER_EXECUTABLE` | `docker` |

数据包需包含加载器要求的 `manifest.json`、`tasks.json` 及相应验证证据。运行时检查数据和镜像，不会因执行以下命令就自动完成数据准备。

执行一个开发任务：

```bash
uv run python trigger --instance-id django__django-11477 --trajectory
```

执行全部正式评测任务或选择其中一个：

```bash
uv run python eval.py all --concurrency 3
uv run python eval.py --instance-id django__django-16485 --concurrency 1
```

两个入口均支持 `--model`；分别可用 `MINIAGENT_TRIGGER_MODEL`、`MINIAGENT_EVAL_MODEL` 设置默认模型。批量并发范围为 1–3，结果写入 `eval/<批次目录>/`，包括任务选择、提示词快照、worker 日志、逐任务结果、可用 trajectory 和 `summary.json`。

Docker CLI 选项虽已提供，普通 `miniagent --workspace docker --docker-image ...` 缺少评测所需的内部任务上下文，会被拒绝；当前 Docker 评测应通过 `trigger` 或 `eval.py` 进入。

## 只读 Dashboard

安装可选依赖并启动：

```bash
uv sync --extra dashboard
uv run miniagent-dashboard
# 查看自定义保存位置
uv run miniagent-dashboard --database /path/to/runs.sqlite3 --artifacts /path/to/artifacts --port 8080
```

在浏览器打开 `http://127.0.0.1:8080`。Dashboard 读取已有数据库与 Artifact，展示 Run、Event/Span、Snapshot/压缩、评测结果和已保存文件；不执行任务，也不提供写入或恢复操作。自定义 CLI 保存路径时，需向 Dashboard 传入对应路径。

## Runtime 行为与边界

一次 Step 包含一次模型请求及其产生的全部 Tool Call。默认最大 Step 数为 **250**，连续不可推进的 Tool Step 阈值为 **3**；CLI 新 Run 还配置模型请求重试。上下文压缩默认在达到输入预算的 70% 时触发，目标为 50%，保留最近 3 个已完成 Step。模型窗口大小由模型限制文件提供。

模型返回非空最终文本、没有 Tool Call 且 `finish_reason == stop` 时，产生交给 Evaluator 的候选结果。终态为 `succeeded`、`failed` 或 `cancelled`，受控中断可以返回 `suspended`，等待后续恢复。

Local 使用 `PassThroughEvaluator`，成功仅表示候选文本通过透传评测，不代表代码已经通过独立测试。SWE-bench 路径会导出候选补丁并调用独立 harness，根据测试报告决定评测结果。

Runtime 库调用要求显式传入 `run_store` 与 `artifact_store`。CLI 已完成这些依赖的装配；领域术语见 [CONTEXT.md](docs/CONTEXT.md)。

## 项目结构

```text
config/                 # 模型限制、定价与提示词文件
src/miniagent/
├── agents/             # AgentSpec 与默认 Agent 工厂
├── cli/                # 命令入口、装配、信号、恢复和结果交付
├── runtime/            # Step 循环、Run 状态、journal、压缩与恢复
├── models/             # Provider 无关的消息、请求和响应契约
├── providers/          # 外部模型协议适配
├── tools/              # 工具定义、参数校验与执行适配
├── policy/             # 权限规则、决策与审批
├── environments/       # Local/Docker Workspace 与进程管理
├── evaluation/         # Evaluator、SWE-bench 数据与 harness
├── storage/            # SQLite、事务、查询和 Run 锁
├── artifacts/          # Artifact 内容与清单
├── metering/           # 用量、定价与费用
├── trajectory/         # 运行轨迹投影与 JSON 编码
└── dashboard/          # 只读 HTTP 查询与页面
tests/                  # ut / it / sit / e2e；support 保存共享 fixtures 与驱动
```

详细导航见 [仓库地图](docs/repository-map.md)。

## 开发与验证

```bash
uv sync --dev
uv run pytest -q
# 定向测试
uv run pytest tests/ut/cli/test_prompts.py tests/it/cli/test_prompts.py -q
uv run ty check
```

`ty`、`ruff` 为开发工具；若当前环境没有安装，可通过 `uv tool install ty`、`uv tool install ruff` 安装。修改 Python 文件后，仅对改动文件执行：

```bash
uv run ruff check --fix <changed-files>
uv run ruff format <changed-files>
```

测试按 `ut`、`it`、`sit`、`e2e` 分层；真实 SWE-bench 验证需要额外环境，并通过 `MINIAGENT_REAL_SWEBENCH=1` 显式启用。文档、注释和格式调整无需运行行为测试。

添加 Provider 时实现 `ModelProvider.generate()` 并在 CLI 装配中注册；添加工具时实现工具契约、加入 Registry，并在 Agent 白名单中授权。模型协议适配不承担 Agent 循环职责。

## 执行与数据边界

Local Workspace 是文件副本，shell 仍在宿主机权限下运行；人工审批不等同于操作系统沙箱。Docker Workspace 提供容器执行边界，能力还取决于宿主机、daemon 和镜像配置。

模型请求与工具观察会发送给配置的外部模型服务；运行数据库、Artifact 和可选 trajectory 也会保存任务及执行内容。请保护 `.env` 和保存目录，并按实际权限与数据要求选择运行环境。

当前没有接入通用 Agent handoff、多 Provider 路由和故障转移；通用 `RecoveryController` 与 `RunTrace` 聚合仍以契约为主。已实现的跨进程恢复、Event Span 和 trajectory 导出有各自的实际执行路径，后续方向见 [项目蓝图](docs/blueprint.md)。
