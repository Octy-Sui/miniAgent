# miniAgent Repository Map

<!-- repo-map: verified-against 391fa2a | mapped 2026-10-14 -->

本文件是按需读取的 implementation map：`wired` 已接入当前执行链；
`contract-only` 有类型/协议/独立实现但未接入；`roadmap` 仅为设计。

## What this repository is

miniAgent 是 Python 3.13 的最小 Agent Runtime。当前产品路径接受一个 CLI task，
创建隔离 Workspace，让模型在 bounded Step loop 中调用工具，输出结构化 `RunResult`，
并把 Run/Event/Snapshot/Approval 原子持久化；Docker 在评测前提交候选 patch 并独立校验，Local 结束时登记诊断 patch，
仅当 Run 成功且 finalization 无错误时销毁 Workspace
（`src/miniagent/cli/application.py`, `src/miniagent/runtime/recording.py`）。

## Execution and data flow

```text
miniagent CLI (Local) / trigger (Docker development task + SWEbenchTaskContext)
  → BootstrapConfig / Settings → model limits → ApplicationDependencies
  → Application
    → AgentRuntime + SQLiteRunStore
      → context budget / optional compaction → QwenProvider.generate(ModelRequest)
      → ToolRegistry.prepare(ToolCall)
      → PolicyEngine → allow / deny / same-process approval
      → ReadFileTool / ShellTool → Workspace.execute(Command)
      → render_tool_result(ToolResult) → bounded JSON tool message
      → termination decision → Local PassThroughEvaluator / Docker candidate submission → SWEbenchEvaluator
      → EvaluationCompleted artifact refs + terminal SQLite commit
    → one verified manifest collection → ApplicationResult
    → conditional Workspace.destroy()
  → ApplicationResult(RunResult, artifacts, finalization errors)
```

Composition root 位于 `src/miniagent/cli/bootstrap.py`；应用生命周期位于
`src/miniagent/cli/application.py`；CLI signal 和 exit semantics 位于
`src/miniagent/cli/main.py`；loop 位于 `src/miniagent/runtime/loop.py`，
状态转换与持久化提交分别位于 `src/miniagent/runtime/transitions.py` 和
`src/miniagent/runtime/journal.py`。

## Capability status

| Status | Capability | Evidence |
| --- | --- | --- |
| wired | Typer CLI、model override、signal cancellation、result/finalization rendering | `src/miniagent/cli/main.py`, `src/miniagent/cli/rendering.py`, `tests/cli/` |
| wired | Qwen/OpenAI-compatible model request and tool-call translation | `src/miniagent/providers/qwen.py`, `src/miniagent/models/protocol.py` |
| wired | Step loop、tool allowlist、termination、usage/cost（含摘要尝试）、cancellation | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/termination.py`, `src/miniagent/metering/` |
| wired | Tool Registry、严格参数校验、有界 JSON 工具消息和结构化错误 | `src/miniagent/tools/registry.py`, `src/miniagent/tools/rendering.py`, `tests/tools/` |
| wired | Tool Call policy allow/deny、同进程人工审批、CLI 审批交互 | `src/miniagent/policy/`, `src/miniagent/runtime/tool_runner.py`, `tests/policy/` |
| wired | Local/Docker Workspace create、execute、patch export、destroy | `src/miniagent/environments/`, `tests/environments/` |
| wired | `read_file` and unrestricted shell fallback inside the selected Workspace | `src/miniagent/tools/`, `src/miniagent/cli/bootstrap.py` |
| wired | Content-addressed artifacts、atomic manifest、Run query 与应用层 patch persistence | `src/miniagent/artifacts/`, `src/miniagent/cli/application.py`, `tests/artifacts/` |
| wired | Evaluator interface；Local pass-through，Docker 独立 SWE-bench 评测 | `src/miniagent/cli/bootstrap.py`, `src/miniagent/evaluation/`, `src/miniagent/runtime/loop.py` |
| contract-only | Workspace checkpoint/restore implementations, not orchestrated by the Run loop | `src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py` |
| wired | SQLite Run/Event/Snapshot/Approval 存储、原子提交、单 Run 连续事件序列和 recovery context 持久化 | `src/miniagent/runtime/recording.py`, `src/miniagent/storage/`, `tests/storage/test_sqlite.py` |
| wired | Run 内 Span scope 生成与 Event scope 持久化；尚无 Trace 聚合、导出或查询界面 | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py`, `src/miniagent/storage/sqlite.py` |
| contract-only | Recovery decision protocol；跨进程恢复尚未编排 | `src/miniagent/runtime/recovery.py`, `src/miniagent/runtime/loop.py` |
| wired | 必填模型容量/输出配置、安全 Step 边界压缩、结构化摘要来源校验、原子压缩 Event/Snapshot | `src/miniagent/cli/model_limits.py`, `src/miniagent/runtime/context.py`, `src/miniagent/runtime/journal.py`, `tests/runtime/test_compaction.py` |
| contract-only | 压缩历史重载校验函数；产品未接入跨进程重载入口 | `src/miniagent/runtime/context.py`, `tests/runtime/test_compaction.py` |
| wired | 测试专用压缩验收 freeze/live/report 工具；真实 Qwen 验收仍 PENDING，摘要语义质量 DEFER | `tests/acceptance/context_compaction/`, `docs/superpowers/acceptance/context-compaction-protocol.md` |
| wired | Docker 任务就绪校验、单次候选提交、独立 SWE-bench 5.0.2、有界取消/清理、评测证据交付 | `src/miniagent/evaluation/swebench.py`, `src/miniagent/evaluation/swebench_data.py`, `src/miniagent/evaluation/swebench_docker.py`, `src/miniagent/cli/candidate_submission.py`, `tests/evaluation/`, `tests/integration/test_swebench_evaluation.py` |
| roadmap | 跨进程审批恢复、dashboard、批量 SWE-bench orchestration | `docs/blueprint.md`, `docs/policy-engine-design.md` |

## Task routes

| Task | Start here | Then inspect | Tests |
| --- | --- | --- | --- |
| CLI option, output, signal, exit code | `src/miniagent/cli/main.py` | `src/miniagent/cli/rendering.py`, `src/miniagent/cli/application.py` | `tests/cli/` |
| Agent contract, default prompt/model, tool permissions | `src/miniagent/agents/spec.py` | `src/miniagent/agents/default_agent.py`, `src/miniagent/models/config.py` | `tests/cli/test_main.py`, `tests/runtime/test_runtime.py` |
| Provider request/response and error mapping | `src/miniagent/providers/qwen.py` | `src/miniagent/models/messages.py`, `src/miniagent/models/protocol.py` | `tests/providers/test_qwen.py`, `tests/models/test_protocol.py` |
| Run state or lifecycle | `src/miniagent/runtime/state.py` | `src/miniagent/runtime/transitions.py`, `src/miniagent/runtime/execution.py`, `src/miniagent/runtime/loop.py` | `tests/runtime/test_runtime.py` |
| Cancellation | `src/miniagent/runtime/cancellation.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/cli/main.py` | `tests/runtime/test_runtime.py`, `tests/cli/test_main.py` |
| Candidate submission, evaluation, delivery | `src/miniagent/cli/candidate_submission.py`, `src/miniagent/evaluation/swebench.py` | `src/miniagent/cli/application.py`, `src/miniagent/runtime/execution.py` | `tests/cli/test_candidate_submission.py`, `tests/cli/test_evaluation_delivery.py`, `tests/evaluation/` |
| SWE-bench task entry, frozen data and preflight evidence | `trigger`, `src/miniagent/cli/bootstrap.py` | `src/miniagent/evaluation/swebench_data.py`, `src/miniagent/evaluation/swebench_docker.py`, `docs/swebench-task-base.json` | `tests/scripts/test_trigger.py`, `tests/cli/test_swebench_bootstrap.py`, `tests/integration/test_swebench_evaluation.py` |
| Model capacity/output limits | `src/miniagent/cli/model_limits.py` | `config/model_limits.toml`, `src/miniagent/cli/bootstrap.py`, `src/miniagent/runtime/config.py` | `tests/cli/test_model_limits.py` |
| Context compaction and evidence | `src/miniagent/runtime/context.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/journal.py`, `src/miniagent/runtime/templates/compaction_v1.j2` | `tests/runtime/test_context.py`, `tests/runtime/test_compaction.py` |
| Compaction acceptance tooling | `tests/acceptance/context_compaction/protocol.py` | `tests/acceptance/context_compaction/live.py`, `docs/superpowers/acceptance/context-compaction-protocol.md` | `tests/acceptance/context_compaction/test_protocol.py` |
| Pricing and token usage | `src/miniagent/metering/` | `src/miniagent/runtime/execution.py`, `config/model_pricing.toml` | `tests/metering/test_pricing.py`, `tests/cli/test_main.py` |
| Workspace public contract/config | `src/miniagent/environments/contracts.py` | `src/miniagent/environments/config.py`, `src/miniagent/environments/persistence.py` | `tests/models/test_protocol.py` |
| Local execution/isolation | `src/miniagent/environments/local.py` | `src/miniagent/environments/process.py`, `src/miniagent/tools/` | `tests/environments/test_local.py` |
| Docker execution/isolation | `src/miniagent/environments/docker.py` | `src/miniagent/environments/process.py`, `src/miniagent/environments/config.py` | `tests/environments/test_docker.py` |
| Tool registration, arguments, result/error behavior | `src/miniagent/tools/registry.py`, `src/miniagent/tools/contracts.py` | `src/miniagent/tools/read_file.py`, `src/miniagent/tools/shell.py`, `src/miniagent/tools/rendering.py` | `tests/tools/test_contracts.py`, `tests/runtime/test_runtime.py` |
| Policy and same-process approval | `src/miniagent/policy/` | `src/miniagent/runtime/tool_runner.py`, `src/miniagent/runtime/approval.py`, `src/miniagent/cli/console_approval.py` | `tests/policy/test_policy.py` |
| Patch/artifact persistence | `src/miniagent/artifacts/` | `src/miniagent/cli/application.py`, Workspace `export_patch()` | `tests/artifacts/test_file_store.py`, `tests/cli/test_application.py` |
| SQLite Run/Event/Snapshot/Approval persistence | `src/miniagent/runtime/journal.py` | `src/miniagent/runtime/recording.py`, `src/miniagent/runtime/store_contracts.py`, `src/miniagent/storage/sqlite.py`, `src/miniagent/storage/codecs.py`, `src/miniagent/storage/queries/` | `tests/storage/test_sqlite.py` |
| Run Trace/Span and typed Event scope | `src/miniagent/runtime/tracing.py` | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py` | `tests/models/test_protocol.py`, `tests/storage/test_sqlite.py` |
| Recovery context persistence and decision contracts | `src/miniagent/runtime/recovery.py` | `src/miniagent/cli/bootstrap.py`, `src/miniagent/runtime/recording.py`, `src/miniagent/environments/persistence.py` | `tests/storage/test_sqlite.py`, `tests/models/test_protocol.py` |
| Codex repository context | `.agents/skills/repo-map/SKILL.md` | `AGENTS.md`, `docs/repository-map.md` | `$repo-map refresh`, `git diff --check` |

## Load-bearing invariants and gotchas

- A `Step` is one model request plus every tool call in that response; canonical terms
  live in `docs/CONTEXT.md`.
- `AgentRuntime` validates the Agent tool allowlist before starting and serially executes
  tool calls in response order. `ToolRegistry` strictly validates arguments, then the
  Policy Engine allows, denies, or requests approval before tool code runs. Runtime sends
  bounded structured results back to the model
  (`src/miniagent/runtime/loop.py`, `src/miniagent/tools/registry.py`,
  `src/miniagent/tools/rendering.py`).
- A model final answer becomes `SUCCEEDED/TASK_VERIFIED` only after evaluator success.
  Local passes through; Docker requires a nonempty submitted patch and matching resolved report/test evidence (`src/miniagent/cli/bootstrap.py`, `src/miniagent/evaluation/swebench_report.py`).
- Docker bootstrap requires internal task context, prepared data/gold preflight evidence, harness 5.0.2 and Docker connectivity; image pull defaults off. Plain CLI Docker lacks task context. `trigger` selects development tasks, but its default `deepseek-v4-pro` has no current limits/pricing entry: use `--model deepseek-v4-pro-0813` (`trigger`, `src/miniagent/cli/bootstrap.py`, `config/model_limits.toml`, `config/model_pricing.toml`).
- Candidate submission is reused by finalization; no second patch export after an attempt. Evaluation refs are on `EvaluationCompleted`; manifests feed `ApplicationResult`, and evaluation summaries are evidence checkpoints, not terminal Run verdicts (`src/miniagent/cli/candidate_submission.py`, `src/miniagent/cli/application.py`, `src/miniagent/runtime/execution.py`, `src/miniagent/evaluation/swebench.py`).
- `--model` replaces only the model name; provider selection still comes from
  `AgentSpec.model.provider` (`src/miniagent/cli/main.py`,
  `src/miniagent/cli/bootstrap.py`).
- Bootstrap requires a matching limits entry before creating Workspace; `--model` also
  needs pricing. Config paths are relative to the current directory unless overridden
  (`src/miniagent/cli/bootstrap.py`, `src/miniagent/cli/settings.py`).
- Compaction preserves the system/task prefix, recent complete tool groups and tool
  contracts; token estimation is approximate. Summary retries are bounded; business
  context rejection retries once after compaction. General Provider/Tool retry and
  backoff are absent; SDK retries are disabled (`src/miniagent/runtime/context.py`,
  `src/miniagent/runtime/loop.py`, `src/miniagent/providers/qwen.py`).
- Storage supports Run/Event/Snapshot schema v2 and Approval v1; older versions are
  rejected, without automatic migration (`src/miniagent/storage/codecs.py`).
- Local Workspace isolates code state in a Run-owned directory but is not a security
  sandbox; Docker Workspace adds container and resource controls
  (`src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py`).
- Command timeout/cancellation can mark a Workspace `BROKEN`; later operations enforce
  backend state (`src/miniagent/environments/local.py`,
  `src/miniagent/environments/docker.py`).
- A Run Trace reuses `run_id.hex` as `trace_id`; `sequence` orders persisted events,
  while Span parentage expresses the logical hierarchy. Runtime creates event scope,
  but Trace aggregation/export is not implemented (`src/miniagent/runtime/tracing.py`,
  `src/miniagent/runtime/trace_context.py`).
- `Settings` requires OpenAI-compatible credentials and reads `.env`; never commit
  secrets (`src/miniagent/cli/settings.py`, `.gitignore`).

## Build, test, and run

本次 REFRESH 在 `391fa2a` 验证通过：
```shell
ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent --help
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run python trigger --help
git diff --check
```

测试命令来自 `pyproject.toml`，按 `AGENTS.md` 的 docs-only 规则本次未运行：
`UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q`。
工具循环可聚焦 `tests/runtime/test_runtime.py tests/tools/test_contracts.py`；
压缩可聚焦 `tests/runtime/test_context.py tests/runtime/test_compaction.py tests/cli/test_model_limits.py`。
真实模型验收须显式按 `docs/superpowers/acceptance/context-compaction-protocol.md` 执行；
Docker 实测依赖 `MINIAGENT_DOCKER_IMAGE`（`tests/environments/test_docker.py`）。
真实 SWE-bench 测试须显式设置 `MINIAGENT_REAL_SWEBENCH=1`，并提供 instance/reference patch，及就绪数据、缓存镜像（`tests/integration/test_swebench_evaluation.py`）；本次未执行真实模型、Docker 或 SWE-bench 评测，也未验证外部 25-task 准备数据；help 不验证 bootstrap 或任务成功。

## Sources of truth

Code and behavioral tests define current behavior; this map provides navigation.
`docs/CONTEXT.md` defines domain language; `README.md` is the user overview;
`docs/blueprint.md` is roadmap. Refresh after entry point, command, ownership or wiring changes.
