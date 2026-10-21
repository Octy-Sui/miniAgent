# miniAgent Repository Map

<!-- repo-map: provisional-against 439ff6a | mapped 2026-10-21 -->

本文件是按需读取的 implementation map：`wired` 已接入当前执行链；
`contract-only` 有类型/协议/独立实现但未接入；`roadmap` 仅为设计。

miniAgent 是 Python 3.13 的最小 Agent Runtime：CLI task → 隔离 Workspace → bounded Step loop → `RunResult` / `RunSuspension`，原子持久化 Run/Event/Snapshot/Approval。
Docker 在评测前提交候选 patch 并独立校验；Local 登记诊断 patch；仅成功且 finalization 无错误时销毁 Workspace（`src/miniagent/cli/application.py`, `src/miniagent/runtime/recording.py`）。

## Execution and data flow

```text
miniagent [run] CLI (Local) / trigger (Docker development task + SWEbenchTaskContext)
  → Run lock → BootstrapConfig / Settings → model limits → ApplicationDependencies
  → Application
    → AgentRuntime + required RuntimeStore/ArtifactStore
      → context budget / optional compaction → QwenProvider.generate(ModelRequest)
      → ToolRegistry.prepare(ToolCall)
      → PolicyEngine → allow / deny / same-process approval
      → ReadFileTool / ShellTool → Workspace.execute(Command)
      → full command streams / original ToolResult evidence → bounded JSON ToolMessage
      → termination decision → Local PassThroughEvaluator / Docker candidate submission → SWEbenchEvaluator
      → EvaluationCompleted artifact refs + terminal SQLite commit
    → manifest-only collection + patch/evaluation delivery verification
    → optional trajectory JSON under Run lock, before Workspace cleanup
    → conditional Workspace.destroy()
  → ApplicationResult(RunResult | RunSuspension, artifacts, finalization errors, trajectory outcome)
miniagent resume → inspect/plan → original Workspace attach → rehydrate → Application.resume
miniagent-dashboard → FastAPI → read-only SQLite + saved artifacts → projections → Jinja/HTMX pages
```

Composition root：`src/miniagent/cli/bootstrap.py`；生命周期：`src/miniagent/cli/application.py`；signal/exit：`src/miniagent/cli/main.py`, `src/miniagent/cli/signals.py`。
Resume 编排/planner：`src/miniagent/cli/resume.py`, `src/miniagent/runtime/resume.py`；loop/转换/提交：`src/miniagent/runtime/loop.py`, `src/miniagent/runtime/transitions.py`, `src/miniagent/runtime/journal.py`。

## Capability status

| Status | Capability | Evidence |
| --- | --- | --- |
| wired | Typer CLI、model override、run/resume 分派、signal interruption、result/finalization rendering | `src/miniagent/cli/main.py`, `src/miniagent/cli/rendering.py`, `tests/{ut,it,sit,e2e}/cli/` |
| wired | Qwen/OpenAI-compatible model request and tool-call translation | `src/miniagent/providers/qwen.py`, `src/miniagent/models/protocol.py` |
| wired | Step loop、tool allowlist、termination、usage/cost（含摘要尝试）、cancellation | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/termination.py`, `src/miniagent/metering/` |
| wired | Tool Registry、严格参数校验、有界 JSON 工具消息和结构化错误 | `src/miniagent/tools/registry.py`, `src/miniagent/tools/rendering.py`, `tests/ut/tools/` |
| wired | Tool Call policy allow/deny、同进程人工审批、CLI 审批交互 | `src/miniagent/policy/`, `src/miniagent/runtime/tool_runner.py`, `tests/{ut,it}/policy/` |
| wired | Local/Docker Workspace create、execute、patch export、destroy | `src/miniagent/environments/`, `tests/{ut,it,sit}/environments/` |
| wired | `read_file` and unrestricted shell fallback inside the selected Workspace | `src/miniagent/tools/`, `src/miniagent/cli/bootstrap.py` |
| wired | Streaming content-addressed artifacts、取消后发布排空、manifest-only pagination、patch/evaluation delivery integrity | `src/miniagent/artifacts/`, `src/miniagent/cli/application.py`, `tests/it/artifacts/` |
| wired | Evaluator interface；Local pass-through，Docker 独立 SWE-bench 评测 | `src/miniagent/cli/bootstrap.py`, `src/miniagent/evaluation/`, `src/miniagent/runtime/loop.py` |
| wired | 完整模型/摘要请求响应、校验前 raw ToolResult、精确观察、Local/Docker 业务双流；Runtime 必需两种 store | `src/miniagent/runtime/execution.py`, `src/miniagent/runtime/tool_runner.py`, `src/miniagent/environments/output_capture.py`, `tests/it/runtime/test_tool_evidence.py` |
| wired | 可选 trajectory JSON、冻结开关、非致命导出、resume 异常收尾；固定边界/白名单/64MiB 编码上限 | `src/miniagent/trajectory/`, `src/miniagent/cli/trajectory.py`, `tests/it/trajectory/`, `tests/sit/cli/test_trajectory.py`, `tests/sit/resume/test_trajectory.py` |
| contract-only | Workspace checkpoint/restore implementations, not orchestrated by the Run loop | `src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py` |
| wired | SQLite Run/Event/Snapshot/Approval 存储、原子提交、单 Run 连续事件序列和 recovery context 持久化 | `src/miniagent/runtime/recording.py`, `src/miniagent/storage/`, `tests/it/storage/test_sqlite.py tests/ut/storage/test_sqlite.py` |
| wired | Run 内 Span scope 生成与 Event scope 持久化；dashboard 独立投影 Span 事实 | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py`, `src/miniagent/dashboard/projections/spans.py` |
| contract-only | `RunTrace` 聚合模型；已接线的 trajectory 直接投影 Event，不使用此模型 | `src/miniagent/runtime/tracing.py` |
| wired | 同 Run 跨进程恢复、冻结配置重建、原 Workspace attach、审批失效后重审、candidate/evaluation 恢复 | `src/miniagent/cli/resume.py`, `src/miniagent/cli/run_definition.py`, `src/miniagent/runtime/resume.py`, `tests/sit/cli/test_resume.py` |
| contract-only | 通用 RecoveryController 决策协议；当前 resume 使用独立 planner | `src/miniagent/runtime/recovery.py`, `src/miniagent/runtime/resume.py` |
| wired | 必填模型容量/输出配置、安全 Step 边界压缩、结构化摘要来源校验、原子压缩 Event/Snapshot | `src/miniagent/cli/model_limits.py`, `src/miniagent/runtime/context.py`, `src/miniagent/runtime/journal.py`, `tests/it/runtime/test_compaction.py` |
| contract-only | 独立 validate_compaction_record 校验函数未被产品调用；压缩中断续跑另由 loop 编排 | `src/miniagent/runtime/context.py`, `src/miniagent/runtime/loop.py`, `tests/it/runtime/test_compaction_resume.py` |
| wired | 测试专用压缩验收 freeze/live/report 工具；真实 Qwen 验收仍 PENDING，摘要语义质量 DEFER | `tests/support/context_compaction/`, `docs/superpowers/acceptance/context-compaction-protocol.md` |
| wired | Docker 任务就绪校验、单次候选提交、独立 SWE-bench 5.0.2、有界取消/清理、评测证据交付 | `src/miniagent/evaluation/swebench.py`, `src/miniagent/evaluation/swebench_data.py`, `src/miniagent/evaluation/swebench_docker.py`, `src/miniagent/cli/candidate_submission.py`, `tests/{ut,it,sit}/evaluation/`, `tests/sit/evaluation/test_swebench_evaluation.py` |
| wired | 执行链身份与结束证据、Docker exec supervisor、Run 互斥与有界 signal 清理 | `src/miniagent/environments/managed_execution.py`, `src/miniagent/environments/docker_exec.py`, `src/miniagent/environments/docker_supervisor.py`, `src/miniagent/storage/run_lock.py`, `src/miniagent/cli/signals.py` |
| wired | 本机只读 dashboard：Run 列表/过滤、Event/Span、Snapshot/compaction 来源、evaluation、保存的 patch/artifact | `src/miniagent/dashboard/cli.py`, `src/miniagent/dashboard/routes.py`, `src/miniagent/dashboard/pages.py`, `tests/{ut,it,sit,e2e}/dashboard/`, `tests/e2e/dashboard/` |
| roadmap | 批量 SWE-bench orchestration | `docs/blueprint.md` |

## Task routes

| Task | Start here | Then inspect | Tests |
| --- | --- | --- | --- |
| CLI option, output, signal, exit code | `src/miniagent/cli/main.py` | `src/miniagent/cli/rendering.py`, `src/miniagent/cli/application.py` | `tests/{ut,it,sit,e2e}/cli/` |
| Dashboard entry, HTTP boundary, UI | `src/miniagent/dashboard/cli.py`, `src/miniagent/dashboard/app.py` | `src/miniagent/dashboard/routes.py`, `src/miniagent/dashboard/pages.py`, `src/miniagent/dashboard/templates/`, `src/miniagent/dashboard/static/` | `tests/it/dashboard/test_http.py`, `tests/e2e/dashboard/` |
| Dashboard data, evidence and source navigation | `src/miniagent/dashboard/queries/`, `src/miniagent/dashboard/projections/` | `src/miniagent/dashboard/artifacts.py`, `src/miniagent/dashboard/evaluation_evidence.py`, `src/miniagent/dashboard/cursors.py`, `src/miniagent/dashboard/redaction.py` | `tests/{ut,it,sit,e2e}/dashboard/` |
| Agent contract, default prompt/model, tool permissions | `src/miniagent/agents/spec.py` | `src/miniagent/agents/default_agent.py`, `src/miniagent/models/config.py` | `tests/it/cli/test_main.py tests/ut/cli/test_main.py`, `tests/it/runtime/test_runtime.py tests/ut/runtime/test_runtime.py` |
| Provider request/response and error mapping | `src/miniagent/providers/qwen.py` | `src/miniagent/models/messages.py`, `src/miniagent/models/protocol.py` | `tests/it/providers/test_qwen.py`, `tests/ut/models/test_protocol.py` |
| Run state or lifecycle | `src/miniagent/runtime/state.py` | `src/miniagent/runtime/transitions.py`, `src/miniagent/runtime/execution.py`, `src/miniagent/runtime/loop.py` | `tests/it/runtime/test_runtime.py tests/ut/runtime/test_runtime.py` |
| Cancellation / resumable interruption | `src/miniagent/runtime/interruption.py`, `src/miniagent/cli/signals.py` | `src/miniagent/runtime/cancellation.py`, `src/miniagent/runtime/execution.py` | `tests/it/runtime/test_interruption.py tests/ut/runtime/test_interruption.py`, `tests/it/cli/test_resume_signals.py tests/sit/cli/test_resume_signals.py tests/ut/cli/test_resume_signals.py` |
| Resume readiness, attach and continuation | `src/miniagent/cli/resume.py`, `src/miniagent/runtime/resume.py` | `src/miniagent/cli/run_definition.py`, `src/miniagent/environments/attachment.py`, `src/miniagent/runtime/progress.py` | `tests/sit/cli/test_resume_dry_run.py`, `tests/ut/runtime/test_resume_planner.py`, `tests/sit/resume/` |
| Candidate submission, evaluation, delivery | `src/miniagent/cli/candidate_submission.py`, `src/miniagent/evaluation/swebench.py` | `src/miniagent/cli/application.py`, `src/miniagent/runtime/execution.py` | `tests/it/cli/test_candidate_submission.py`, `tests/it/cli/test_evaluation_delivery.py tests/sit/cli/test_evaluation_delivery.py`, `tests/{ut,it,sit}/evaluation/` |
| SWE-bench task entry, frozen data and preflight evidence | `trigger`, `src/miniagent/cli/bootstrap.py` | `src/miniagent/evaluation/swebench_data.py`, `src/miniagent/evaluation/swebench_docker.py`, `docs/swebench-task-base.json` | `tests/sit/scripts/test_trigger.py`, `tests/sit/cli/test_swebench_bootstrap.py`, `tests/sit/evaluation/test_swebench_evaluation.py` |
| Model capacity/output limits | `src/miniagent/cli/model_limits.py` | `config/model_limits.toml`, `src/miniagent/cli/bootstrap.py`, `src/miniagent/runtime/config.py` | `tests/it/cli/test_model_limits.py tests/ut/cli/test_model_limits.py` |
| Context compaction and evidence | `src/miniagent/runtime/context.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/journal.py`, `src/miniagent/runtime/templates/compaction_v1.j2` | `tests/it/runtime/test_context.py tests/ut/runtime/test_context.py`, `tests/it/runtime/test_compaction.py` |
| Compaction acceptance tooling | `tests/support/context_compaction/protocol.py` | `tests/support/context_compaction/live.py`, `docs/superpowers/acceptance/context-compaction-protocol.md` | `tests/{ut,it,sit}/context_compaction/test_protocol.py` |
| Pricing and token usage | `src/miniagent/metering/` | `src/miniagent/runtime/execution.py`, `config/model_pricing.toml` | `tests/it/metering/test_pricing.py tests/ut/metering/test_pricing.py`, `tests/it/cli/test_main.py tests/ut/cli/test_main.py` |
| Workspace public contract/config | `src/miniagent/environments/contracts.py` | `src/miniagent/environments/config.py`, `src/miniagent/environments/persistence.py` | `tests/ut/models/test_protocol.py` |
| Local execution/isolation | `src/miniagent/environments/local.py` | `src/miniagent/environments/process.py`, `src/miniagent/tools/` | `tests/it/environments/test_local.py` |
| Docker execution/isolation | `src/miniagent/environments/docker.py` | `src/miniagent/environments/docker_connection.py`, `src/miniagent/environments/docker_exec.py`, `src/miniagent/environments/docker_supervisor.py` | `tests/it/environments/test_docker.py tests/sit/environments/test_docker.py tests/ut/environments/test_docker.py`, `tests/it/environments/test_docker_attach.py tests/sit/environments/test_docker_attach.py`, `tests/it/environments/test_docker_supervisor.py` |
| Tool registration, arguments, result/error behavior | `src/miniagent/tools/registry.py`, `src/miniagent/tools/contracts.py` | `src/miniagent/tools/read_file.py`, `src/miniagent/tools/shell.py`, `src/miniagent/tools/rendering.py` | `tests/ut/tools/test_contracts.py`, `tests/it/runtime/test_runtime.py tests/ut/runtime/test_runtime.py` |
| Policy and same-process approval | `src/miniagent/policy/` | `src/miniagent/runtime/tool_runner.py`, `src/miniagent/runtime/approval.py`, `src/miniagent/cli/console_approval.py` | `tests/it/policy/test_policy.py tests/ut/policy/test_policy.py` |
| Patch/artifact persistence | `src/miniagent/artifacts/` | `src/miniagent/cli/application.py`, Workspace `export_patch()` | `tests/it/artifacts/test_file_store.py`, `tests/it/cli/test_application.py` |
| SQLite Run/Event/Snapshot/Approval persistence | `src/miniagent/runtime/journal.py` | `src/miniagent/runtime/recording.py`, `src/miniagent/runtime/store_contracts.py`, `src/miniagent/storage/sqlite.py`, `src/miniagent/storage/codecs.py`, `src/miniagent/storage/queries/` | `tests/it/storage/test_sqlite.py tests/ut/storage/test_sqlite.py` |
| Run Trace/Span and typed Event scope | `src/miniagent/runtime/tracing.py` | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py` | `tests/ut/models/test_protocol.py`, `tests/it/storage/test_sqlite.py tests/ut/storage/test_sqlite.py` |
| Frozen definitions, recovery metadata and Run lock | `src/miniagent/cli/run_definition.py`, `src/miniagent/runtime/recovery.py` | `src/miniagent/cli/bootstrap.py`, `src/miniagent/storage/run_lock.py` | `tests/it/cli/test_run_definition.py tests/ut/cli/test_run_definition.py`, `tests/it/storage/test_resume_format.py`, `tests/it/storage/test_run_lock.py` |
| Trajectory schema/projection/export/flags | `src/miniagent/trajectory/`, `src/miniagent/cli/trajectory.py` | `src/miniagent/cli/application.py`, `src/miniagent/cli/settings.py`, `trigger` | `tests/it/trajectory/`, `tests/ut/trajectory/`, `tests/sit/cli/test_trajectory_options.py`, `tests/it/cli/test_trajectory_finalization.py` |
| Raw model/tool/command evidence and bounded history | `src/miniagent/runtime/events.py`, `src/miniagent/runtime/history.py` | `src/miniagent/runtime/tool_runner.py`, `src/miniagent/environments/output_capture.py`, `src/miniagent/storage/queries/events.py` | `tests/it/runtime/test_model_evidence.py`, `tests/it/runtime/test_tool_evidence.py`, `tests/sit/environments/test_docker_output_evidence.py`, `tests/sit/trajectory/test_history_resources.py` |
| Codex repository context | `.agents/skills/repo-map/SKILL.md` | `AGENTS.md`, `docs/repository-map.md` | `$repo-map refresh`, `git diff --check` |

## Load-bearing invariants and gotchas

- A `Step` is one model request plus every tool call in that response (`docs/CONTEXT.md`).
- Runtime requires explicit Run/Artifact stores; capture is always on. Raw ToolResult is inline at <=65,536 serialized bytes, otherwise an Artifact; the effective result/ToolMessage stays bounded. Received business stdout/stderr bytes are saved before preview truncation (`src/miniagent/runtime/loop.py`, `src/miniagent/runtime/execution.py`, `src/miniagent/environments/output_capture.py`).
- JSON export defaults off; direct/run/trigger CLI flags override Settings, resume uses the frozen flag. Export has a total deadline and <=67,108,864 encoded bytes; failures do not change the original Run outcome. Content is faithful, not Dashboard-style redaction; reasoning body/framework credentials are excluded, attachment bodies are not bundled (`src/miniagent/cli/trajectory.py`, `src/miniagent/trajectory/export.py`, `src/miniagent/cli/run_definition.py`).
- History consumers freeze N and page at most 16 full Events, retaining compact facts rather than full history. Catalog pagination does not validate body reachability; candidate patch and evaluation delivery validation remains separate (`src/miniagent/runtime/history.py`, `src/miniagent/storage/queries/events.py`, `src/miniagent/artifacts/file.py`, `src/miniagent/cli/application.py`).
- A model final answer becomes `SUCCEEDED/TASK_VERIFIED` only after evaluator success.
  Local passes through; Docker requires a nonempty submitted patch and matching resolved report/test evidence (`src/miniagent/cli/bootstrap.py`, `src/miniagent/evaluation/swebench_report.py`).
- Docker bootstrap requires internal task context, prepared data/gold preflight evidence, harness 5.0.2 and Docker connectivity; image pull defaults off. Plain CLI Docker lacks task context. `trigger` selects development tasks, but its default `deepseek-v4-pro` lacks matching config. Current working-tree limits omit `deepseek-v4-pro-0813`; use `--model deepseek-v4.1-flash-1017`, present in both configs (`trigger`, `src/miniagent/cli/bootstrap.py`, `config/model_limits.toml`, `config/model_pricing.toml`).
- Candidate submission is reused by finalization; no second patch export after an attempt. Evaluation refs are on `EvaluationCompleted`; manifests feed `ApplicationResult`, and evaluation summaries are evidence checkpoints, not terminal Run verdicts (`src/miniagent/cli/candidate_submission.py`, `src/miniagent/cli/application.py`, `src/miniagent/runtime/execution.py`, `src/miniagent/evaluation/swebench.py`).
- `--model` replaces only the model name; provider selection still comes from `AgentSpec.model.provider` (`src/miniagent/cli/main.py`, `src/miniagent/cli/bootstrap.py`).
- Bootstrap requires a matching limits entry before creating Workspace; `--model` also
  needs pricing. Config paths are relative to the current directory unless overridden
  (`src/miniagent/cli/bootstrap.py`, `src/miniagent/cli/settings.py`). DEFAULT_AGENT and both working-tree configs contain `qwen3.8-flash`; check both limits/pricing when changing the model (`src/miniagent/agents/default_agent.py`, `config/model_limits.toml`, `config/model_pricing.toml`).
- Compaction preserves the system/task prefix, recent complete tool groups and tool
  contracts; token estimation is approximate. Summary retries are bounded; business
  context rejection retries once after compaction. Normal-loop Provider/Tool retry and
  backoff are absent; SDK retries are disabled (`src/miniagent/runtime/context.py`,
  `src/miniagent/runtime/loop.py`, `src/miniagent/providers/qwen.py`).
- Resume checks frozen configuration, committed tool-result prefixes and original Workspace identity; terminal Runs and uncertain shell outcomes are rejected. Only explicitly read-only tools may retry unknown outcomes (`src/miniagent/runtime/resume.py`, `src/miniagent/tools/base.py`, `src/miniagent/tools/read_file.py`).
- `--dry-run` holds the same Run lock and inspects dependencies, but does not attach/start the Workspace or mutate Run data; a stopped Docker container can require live checks (`src/miniagent/cli/resume.py`, `src/miniagent/storage/run_lock.py`).
- SIGINT/SIGTERM request resumable interruption with 2s/15s cleanup budgets; repeated signals force cancellation. This differs from terminal business cancellation (`src/miniagent/cli/signals.py`, `src/miniagent/runtime/interruption.py`).
- Storage accepts Run/Event/Snapshot v3, Approval v1, recovery context v2 and CLI application v1; older versions are rejected without migration (`src/miniagent/storage/codecs.py`).
- Local Workspace isolates code state in a Run-owned directory but is not a security
  sandbox; Docker Workspace adds container and resource controls
  (`src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py`).
- Command timeout/cancellation can mark a Workspace `BROKEN`; later operations enforce
  backend state (`src/miniagent/environments/local.py`,
  `src/miniagent/environments/docker.py`).
- `RunTrace.trace_id` is `run_id.hex`; `sequence` orders events and Span parentage expresses hierarchy. Dashboard derives bounded Span facts independently of `RunTrace` (`src/miniagent/runtime/tracing.py`, `src/miniagent/dashboard/projections/spans.py`).
- Dashboard uses SQLite `mode=ro`/`query_only`, serves only local same-origin GET/HEAD, and reads saved artifacts rather than live Workspace files. Content is bounded, hash-checked, redacted and escaped; navigation tokens expire and are process-local (`src/miniagent/dashboard/queries/connection.py`, `src/miniagent/dashboard/app.py`, `src/miniagent/dashboard/artifacts.py`, `src/miniagent/dashboard/presentation.py`, `src/miniagent/dashboard/cursors.py`).
- `Settings` requires OpenAI-compatible credentials and reads `.env`; never commit secrets (`src/miniagent/cli/settings.py`, `.gitignore`).

## Build, test, and run

本次 REFRESH 核对 `d823d58..439ff6a` 与实际工作区；模型配置/设计文档未提交，故使用 provisional stamp。以下命令已执行成功：
```shell
uv --cache-dir /private/tmp/miniagent-uv-cache run ty check
uv --cache-dir /private/tmp/miniagent-uv-cache run miniagent --help
uv --cache-dir /private/tmp/miniagent-uv-cache run miniagent resume --help
uv --cache-dir /private/tmp/miniagent-uv-cache run python trigger --help
uv --cache-dir /private/tmp/miniagent-uv-cache run --extra dashboard miniagent-dashboard --help
uv --cache-dir /private/tmp/miniagent-uv-cache run pytest --collect-only -q
git diff --check
```

测试使用 `pytest`（`pyproject.toml`），按实际边界分为 `tests/ut/`（局部逻辑/契约）、`tests/it/`（组件与 I/O 集成）、`tests/sit/`（应用及 Local/Docker 系统链路）、`tests/e2e/`（浏览器或安装后的用户入口到可见结果）。每层下再按模块组织；fixture、builder、进程 driver 与验收工具放在 `tests/support/`。混合文件已拆分，原有断言和参数化 case 保留。历史数量快照及拆分清单见 `tests/inventory.json`；本次实际收集1846项，新增 trajectory 用例未回写该快照。数量不代表代码或需求覆盖率。
本次仅刷新文档，未重跑测试执行、真实模型/Docker/SWE-bench 或浏览器验收；下列为按需命令与前置条件。
按层运行示例：`UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/ut -q`；替换为 `it`、`sit`、`e2e` 可运行对应层，`uv run pytest --collect-only -q` 可复核参数化展开后的清单。
Dashboard 可聚焦 `tests/{ut,it,sit,e2e}/dashboard/`；浏览器验收 `tests/e2e/dashboard/` 需 dashboard extra 和 Playwright Chromium（`pyproject.toml`, `tests/support/dashboard/fixtures_browser.py`）。启动命令为 `uv run --extra dashboard miniagent-dashboard [--database <path>] [--artifacts <root>] [--port 8080]`。
Resume 可聚焦 `tests/sit/cli/test_resume.py tests/ut/runtime/test_resume_planner.py tests/it/storage/test_resume_format.py`；验收为 `tests/sit/resume/`，Docker/外部数据需 `MINIAGENT_RESUME_DOCKER_IMAGE`/`MINIAGENT_RESUME_UNRESOLVED_MANIFEST`（`tests/sit/resume/conftest.py`, `tests/it/resume/test_swebench.py tests/sit/resume/test_swebench.py`）。
真实模型验收按 `docs/superpowers/acceptance/context-compaction-protocol.md`；Docker 需 `MINIAGENT_DOCKER_IMAGE`（`tests/sit/environments/test_docker.py`）；SWE-bench 需 `MINIAGENT_REAL_SWEBENCH=1` 及数据/镜像（`tests/sit/evaluation/test_swebench_evaluation.py`）。原有环境门控和 skip 条件保留；SIT/E2E 中的模型替身不证明真实模型效果。

Code/tests define behavior; `docs/CONTEXT.md` defines domain language; `README.md` is the overview; `docs/blueprint.md` is roadmap. Refresh after entry point, command, ownership or wiring changes.
