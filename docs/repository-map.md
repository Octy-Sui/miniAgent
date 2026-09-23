# miniAgent Repository Map

<!-- repo-map: verified-against d72dbd5 + refactor working tree | mapped 2026-09-23 -->

本文件是按需读取的 implementation map。状态含义：

- `wired`：已从当前 composition root 或 Run execution path 接入。
- `implemented`：已有可用实现和测试，但尚未接入当前 composition root 或 Run execution path。
- `contract-only`：已有类型、协议或独立实现，但当前执行链未使用。
- `roadmap`：只在设计文档中描述。

## What this repository is

miniAgent 是 Python 3.13 的最小 Agent Runtime。当前产品路径接受一个 CLI task，
创建隔离 Workspace，让模型在 bounded Step loop 中调用工具，输出结构化 `RunResult`，
并把 Run/Event/Snapshot/Approval 原子持久化；应用层在成功后登记 patch artifact，
仅当证据登记成功时销毁 Workspace。

## Execution and data flow

```text
miniagent CLI
  → BootstrapConfig / Settings → ApplicationDependencies
  → Application
    → AgentRuntime + SQLiteRunStore
      → QwenProvider.generate(ModelRequest)
      → ToolRegistry.prepare(ToolCall)
      → PolicyEngine → allow / deny / same-process approval
      → ReadFileTool / ShellTool → Workspace.execute(Command)
      → render_tool_result(ToolResult) → bounded JSON tool message
      → termination decision → PassThroughEvaluator
    → patch export → FileArtifactStore manifest
    → conditional Workspace.destroy()
  → ApplicationResult(RunResult, artifacts, finalization errors)
```

Composition root 位于 `src/miniagent/cli/bootstrap.py`；应用生命周期位于
`src/miniagent/cli/application.py`；CLI signal 和 exit semantics 位于
`src/miniagent/cli/main.py`；loop 位于 `src/miniagent/runtime/loop.py`。

## Capability status

| Status | Capability | Evidence |
| --- | --- | --- |
| wired | Typer CLI、model override、signal cancellation、result/finalization rendering | `src/miniagent/cli/main.py`, `src/miniagent/cli/rendering.py`, `tests/cli/` |
| wired | Qwen/OpenAI-compatible model request and tool-call translation | `src/miniagent/providers/qwen.py`, `src/miniagent/models/protocol.py` |
| wired | Step loop、tool allowlist、termination、usage/cost、cancellation | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/termination.py`, `src/miniagent/metering/` |
| wired | Tool Registry、严格参数校验、有界 JSON 工具消息和结构化错误 | `src/miniagent/tools/registry.py`, `src/miniagent/tools/rendering.py`, `tests/tools/` |
| wired | Tool Call policy allow/deny、同进程人工审批、CLI 审批交互 | `src/miniagent/policy/`, `src/miniagent/runtime/tool_runner.py`, `tests/policy/` |
| wired | Local/Docker Workspace create、execute、patch export、destroy | `src/miniagent/environments/`, `tests/environments/` |
| wired | `read_file` and unrestricted shell fallback inside the selected Workspace | `src/miniagent/tools/`, `src/miniagent/cli/bootstrap.py` |
| wired | Content-addressed artifacts、atomic manifest、Run query 与应用层 patch persistence | `src/miniagent/artifacts/`, `src/miniagent/cli/application.py`, `tests/artifacts/` |
| wired | Evaluator interface with default pass-through implementation | `src/miniagent/evaluation/`, `src/miniagent/runtime/loop.py` |
| contract-only | Workspace checkpoint/restore implementations, not orchestrated by the Run loop | `src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py` |
| wired | SQLite Run/Event/Snapshot/Approval 存储、原子提交和单 Run 连续事件序列 | `src/miniagent/runtime/journal.py`, `src/miniagent/storage/`, `tests/storage/` |
| wired | Run 内 Span scope 生成与 Event scope 持久化；尚无 Trace 聚合、导出或查询界面 | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py`, `src/miniagent/storage/sqlite.py` |
| contract-only | Recovery decision protocol and persistent Runtime integration | `src/miniagent/runtime/recovery.py`, `docs/sqlite-storage-design.md` |
| roadmap | 跨进程审批恢复、context compaction、dashboard、SWE-bench orchestration | `docs/blueprint.md`, `docs/policy-engine-design.md` |

## Task routes

| Task | Start here | Then inspect | Tests |
| --- | --- | --- | --- |
| CLI option, output, signal, exit code | `src/miniagent/cli/main.py` | `src/miniagent/cli/rendering.py`, `src/miniagent/cli/application.py` | `tests/cli/` |
| Agent prompt, model target, tool permissions | `src/miniagent/agents/spec.py` | `src/miniagent/agents/default.py`, `src/miniagent/models/config.py` | `tests/models/test_protocol.py`, `tests/runtime/test_runtime.py` |
| Provider request/response mapping | `src/miniagent/providers/qwen.py` | `src/miniagent/models/messages.py`, `src/miniagent/models/protocol.py` | `tests/models/test_protocol.py`, `tests/runtime/test_runtime.py` |
| Run state or lifecycle | `src/miniagent/runtime/state.py` | `src/miniagent/runtime/transitions.py`, `src/miniagent/runtime/loop.py` | `tests/runtime/test_runtime.py` |
| Cancellation | `src/miniagent/runtime/cancellation.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/cli/main.py` | `tests/runtime/test_runtime.py`, `tests/cli/test_main.py` |
| Evaluation | `src/miniagent/evaluation/` | `src/miniagent/runtime/loop.py` | `tests/runtime/test_runtime.py`, `tests/models/test_protocol.py` |
| Pricing and token usage | `src/miniagent/metering/` | `src/miniagent/runtime/execution.py`, `config/model_pricing.toml` | `tests/metering/test_pricing.py`, `tests/cli/test_main.py` |
| Workspace public contract/config | `src/miniagent/environments/contracts.py` | `src/miniagent/environments/config.py`, `src/miniagent/environments/persistence.py` | `tests/models/test_protocol.py` |
| Local execution/isolation | `src/miniagent/environments/local.py` | `src/miniagent/environments/process.py`, `src/miniagent/tools/` | `tests/environments/test_local.py` |
| Docker execution/isolation | `src/miniagent/environments/docker.py` | `src/miniagent/environments/process.py`, `src/miniagent/environments/config.py` | `tests/environments/test_docker.py` |
| Tool registration, arguments, result/error behavior | `src/miniagent/tools/registry.py`, `src/miniagent/tools/contracts.py` | `src/miniagent/tools/read_file.py`, `src/miniagent/tools/shell.py`, `src/miniagent/tools/rendering.py` | `tests/tools/test_contracts.py`, `tests/runtime/test_runtime.py` |
| Policy and same-process approval | `src/miniagent/policy/` | `src/miniagent/runtime/tool_runner.py`, `src/miniagent/runtime/approval.py` | `tests/policy/test_policy.py` |
| Patch/artifact persistence | `src/miniagent/artifacts/` | `src/miniagent/cli/application.py`, Workspace `export_patch()` | `tests/artifacts/test_file_store.py`, `tests/cli/test_application.py` |
| SQLite Run/Event/Snapshot/Approval persistence | `src/miniagent/storage/sqlite.py` | `src/miniagent/storage/codecs.py`, `src/miniagent/storage/queries/`, `src/miniagent/runtime/store_contracts.py` | `tests/storage/test_sqlite.py` |
| Run Trace/Span and typed Event scope | `src/miniagent/runtime/tracing.py` | `src/miniagent/runtime/trace_context.py`, `src/miniagent/runtime/events.py` | `tests/models/test_protocol.py`, `tests/storage/test_sqlite.py` |
| Recovery contracts | `src/miniagent/runtime/recovery.py` | `src/miniagent/environments/persistence.py`, `docs/sqlite-storage-design.md` | `tests/models/test_protocol.py` |
| Codex repository context | `.agents/skills/repo-map/SKILL.md` | `AGENTS.md`, `docs/repository-map.md` | `$repo-map refresh`, skill validator |

## Load-bearing invariants and gotchas

- A `Step` is one model request plus every tool call in that response; canonical terms
  live in `docs/CONTEXT.md`.
- `AgentRuntime` validates the Agent tool allowlist before starting and serially executes
  tool calls in response order. `ToolRegistry` strictly validates arguments, then the
  Policy Engine allows, denies, or requests approval before tool code runs. Runtime sends
  bounded structured results back to the model
  (`src/miniagent/runtime/loop.py`, `src/miniagent/tools/registry.py`,
  `src/miniagent/tools/rendering.py`).
- A model final answer becomes `SUCCEEDED/TASK_VERIFIED` only after evaluator success,
  but the default evaluator passes the candidate through without independent checks
  (`src/miniagent/evaluation/passthrough.py`).
- `--model` replaces only the model name; provider selection still comes from
  `AgentSpec.model.provider` (`src/miniagent/cli/main.py`,
  `src/miniagent/cli/bootstrap.py`).
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

Verified on the mapped working tree:

```shell
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent --help
```

Run focused tool-loop tests with
`uv run pytest tests/runtime/test_runtime.py tests/tools/test_contracts.py`. Live CLI tasks
require configured credentials and may invoke external model/network services.

## Sources of truth

1. Current code and behavioral tests.
2. This map for navigation; revalidate claims before relying on them for a change.
3. `docs/CONTEXT.md` for canonical domain language only.
4. `README.md` for user-facing overview.
5. `docs/blueprint.md` for intended future design, not current wiring.

Use `$repo-map refresh` after entry points, dependency direction, commands, module
ownership, or implementation status changes.
