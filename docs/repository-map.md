# miniAgent Repository Map

<!-- repo-map: verified-against 13c87be | mapped 2026-09-17 -->

本文件是按需读取的 implementation map。状态含义：

- `wired`：已从当前 composition root 或 Run execution path 接入。
- `implemented`：已有可用实现和测试，但尚未接入当前 composition root 或 Run execution path。
- `contract-only`：已有类型、协议或独立实现，但当前执行链未使用。
- `roadmap`：只在设计文档中描述。

## What this repository is

miniAgent 是 Python 3.13 的最小 Agent Runtime。当前产品路径接受一个 CLI task，
创建隔离 Workspace，让模型在 bounded Step loop 中调用工具，输出结构化 `RunResult`，
并在 Workspace 销毁前尝试保存 patch artifact。

## Execution and data flow

```text
miniagent CLI
  → BootstrapConfig / Settings
  → LocalWorkspace or DockerWorkspace
  → AgentRuntime
      → QwenProvider.generate(ModelRequest)
      → ToolRegistry.prepare(ToolCall)
      → PolicyEngine → allow / deny / same-process approval
      → ReadFileTool / ShellTool → Workspace.execute(Command)
      → render_tool_result(ToolResult) → bounded JSON tool message
      → termination decision → PassThroughEvaluator
  → RunResult + optional workspace patch artifact
  → Workspace.destroy()
```

Composition root 位于 `src/miniagent/cli/bootstrap.py`；CLI signal 和 exit semantics 位于
`src/miniagent/cli/main.py`；loop 位于 `src/miniagent/runtime/loop.py`。

## Capability status

| Status | Capability | Evidence |
| --- | --- | --- |
| wired | Typer CLI、model override、signal cancellation、result rendering | `src/miniagent/cli/main.py`, `tests/test_cli.py` |
| wired | Qwen/OpenAI-compatible model request and tool-call translation | `src/miniagent/providers/qwen.py`, `src/miniagent/providers/__init__.py` |
| wired | Step loop、tool allowlist、termination、usage/cost、cancellation | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/termination.py`, `src/miniagent/runtime/usage.py` |
| wired | Tool Registry、严格参数校验、有界 JSON 工具消息和结构化错误 | `src/miniagent/tools/registry.py`, `src/miniagent/runtime/tool_output.py`, `tests/test_tool_contracts.py` |
| wired | Tool Call policy allow/deny、同进程人工审批、CLI 审批交互 | `src/miniagent/policy/`, `src/miniagent/runtime/loop.py`, `tests/test_policy.py` |
| wired | Local/Docker Workspace create、execute、patch export、destroy | `src/miniagent/environments/`, `tests/test_local_workspace.py`, `tests/test_docker_workspace.py` |
| wired | `read_file` and unrestricted shell fallback inside the selected Workspace | `src/miniagent/tools/`, `src/miniagent/cli/bootstrap.py` |
| wired | Content-addressed file artifacts and CLI patch persistence | `src/miniagent/artifacts/`, `src/miniagent/cli/bootstrap.py` |
| wired | Evaluator interface with default pass-through implementation | `src/miniagent/runtime/evaluator.py`, `src/miniagent/runtime/loop.py` |
| contract-only | Workspace checkpoint/restore implementations, not orchestrated by the Run loop | `src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py` |
| implemented | SQLite Run/Event/Snapshot/Approval 存储、原子提交和单 Run 连续事件序列 | `src/miniagent/storage/`, `tests/test_sqlite_storage.py` |
| contract-only | Run Trace/Span 层级契约及 Event Span Scope 的 SQLite 往返持久化；Runtime 尚未生成 Span、导出 Trace 或提供查询界面 | `src/miniagent/runtime/tracing.py`, `src/miniagent/runtime/events.py`, `src/miniagent/storage/sqlite.py` |
| contract-only | Recovery decision protocol and persistent Runtime integration | `src/miniagent/runtime/recovery.py`, `docs/sqlite-storage-design.md` |
| roadmap | 跨进程审批恢复、context compaction、dashboard、SWE-bench orchestration | `docs/blueprint.md`, `docs/policy-engine-design.md` |

## Task routes

| Task | Start here | Then inspect | Tests |
| --- | --- | --- | --- |
| CLI option, output, signal, exit code | `src/miniagent/cli/main.py` | `src/miniagent/cli/bootstrap.py`, `src/miniagent/models/settings.py` | `tests/test_cli.py`, `tests/test_cli_bootstrap.py` |
| Agent prompt, model target, tool permissions | `src/miniagent/agents/spec.py` | `src/miniagent/agents/default.py`, `src/miniagent/runtime/loop.py` | `tests/test_protocol_models.py`, `tests/test_runtime.py` |
| Provider request/response mapping | `src/miniagent/providers/qwen.py` | `src/miniagent/providers/__init__.py`, `src/miniagent/models/model.py`, `src/miniagent/models/tool.py` | `tests/test_runtime.py`, `tests/test_protocol_models.py` |
| Run state or lifecycle | `src/miniagent/runtime/state.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/runtime/termination.py` | `tests/test_runtime.py` |
| Cancellation | `src/miniagent/runtime/cancellation.py` | `src/miniagent/runtime/loop.py`, `src/miniagent/cli/main.py` | `tests/test_runtime.py`, `tests/test_cli.py` |
| Evaluation | `src/miniagent/runtime/evaluator.py` | `src/miniagent/runtime/loop.py` | `tests/test_runtime.py`, `tests/test_protocol_models.py` |
| Pricing and token usage | `src/miniagent/models/pricing.py` | `src/miniagent/runtime/usage.py`, `config/model_pricing.toml` | `tests/test_pricing.py`, `tests/test_cli.py` |
| Workspace public contract | `src/miniagent/environments/workspace.py` | `src/miniagent/environments/__init__.py` | `tests/test_protocol_models.py` |
| Local execution/isolation | `src/miniagent/environments/local.py` | `src/miniagent/artifacts/`, `src/miniagent/tools/` | `tests/test_local_workspace.py` |
| Docker execution/isolation | `src/miniagent/environments/docker.py` | `src/miniagent/environments/workspace.py` | `tests/test_docker_workspace.py` |
| Tool registration, arguments, result/error behavior | `src/miniagent/tools/registry.py`, `src/miniagent/tools/read_file.py`, `src/miniagent/tools/shell.py` | `src/miniagent/models/tool.py`, `src/miniagent/runtime/tool_output.py`, selected Workspace | `tests/test_tool_contracts.py`, `tests/test_local_workspace.py`, `tests/test_runtime.py` |
| Policy and same-process approval | `src/miniagent/policy/` | `src/miniagent/runtime/loop.py`, `src/miniagent/cli/main.py`, `src/miniagent/runtime/state.py` | `tests/test_policy.py` |
| Patch/artifact persistence | `src/miniagent/artifacts/` | `src/miniagent/cli/bootstrap.py`, Workspace `export_patch()` | `tests/test_cli_bootstrap.py`, `tests/test_local_workspace.py`, `tests/test_docker_workspace.py` |
| SQLite Run/Event/Snapshot/Approval persistence | `src/miniagent/storage/sqlite.py` | `src/miniagent/storage/models.py`, `src/miniagent/runtime/events.py`, `src/miniagent/runtime/snapshots.py` | `tests/test_sqlite_storage.py` |
| Recovery contracts | `src/miniagent/runtime/recovery.py` | `src/miniagent/runtime/state.py`, `docs/sqlite-storage-design.md` | `tests/test_protocol_models.py` |
| Codex repository context | `.agents/skills/repo-map/SKILL.md` | `AGENTS.md`, `docs/repository-map.md` | `$repo-map refresh`, skill validator |

## Load-bearing invariants and gotchas

- A `Step` is one model request plus every tool call in that response; canonical terms
  live in `docs/CONTEXT.md`.
- `AgentRuntime` validates the Agent tool allowlist before starting and serially executes
  tool calls in response order. `ToolRegistry` strictly validates arguments, then the
  Policy Engine allows, denies, or requests approval before tool code runs. Runtime sends
  bounded structured results back to the model
  (`src/miniagent/runtime/loop.py`, `src/miniagent/tools/registry.py`,
  `src/miniagent/runtime/tool_output.py`).
- A model final answer becomes `SUCCEEDED/TASK_VERIFIED` only after evaluator success,
  but the default evaluator passes the candidate through without independent checks
  (`src/miniagent/runtime/evaluator.py`).
- `--model` replaces only the model name; provider selection still comes from
  `AgentSpec.model.provider` (`src/miniagent/cli/main.py`,
  `src/miniagent/cli/bootstrap.py`).
- Local Workspace isolates code state in a Run-owned directory but is not a security
  sandbox; Docker Workspace adds container and resource controls
  (`src/miniagent/environments/local.py`, `src/miniagent/environments/docker.py`).
- Command timeout/cancellation can mark a Workspace `BROKEN`; later operations enforce
  backend state (`src/miniagent/environments/local.py`,
  `src/miniagent/environments/docker.py`).
- `Settings` requires OpenAI-compatible credentials and reads `.env`; never commit
  secrets (`src/miniagent/models/settings.py`, `.gitignore`).

## Build, test, and run

Verified on the mapped working tree:

```shell
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
ty check
uv run miniagent --help
```

Run focused tool-loop tests with
`uv run pytest tests/test_runtime.py tests/test_tool_contracts.py`. Live CLI tasks
require configured credentials and may invoke external model/network services.

## Sources of truth

1. Current code and behavioral tests.
2. This map for navigation; revalidate claims before relying on them for a change.
3. `docs/CONTEXT.md` for canonical domain language only.
4. `README.md` for user-facing overview.
5. `docs/blueprint.md` for intended future design, not current wiring.

Use `$repo-map refresh` after entry points, dependency direction, commands, module
ownership, or implementation status changes.
