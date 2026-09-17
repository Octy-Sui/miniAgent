# Task Handoff: 完成 Policy Engine 并收尾 Workspace/CLI 遗留问题

<!-- handoff: captured-against 0bd0784 | worktree dirty | updated 2026-09-17 -->

## Goal and success criteria

完成 Tool Call 的 `allow`、`deny`、`approval` 策略与同进程人工审批，并收尾旧
handoff 中仍有效的 Workspace/CLI 问题。完成标准：策略三种结果、审批生命周期和
越界行为有可观察测试；BROKEN Workspace 的 patch 失败不覆盖 Run/退出语义；Local
Workspace 拒绝 Docker 专用 image；完整测试、类型检查、CLI 验收和最终 diff 均通过。

## Current state

Policy Engine 已接到参数校验与工具执行之间，CLI 可逐次展示完整参数并批准或拒绝。
默认策略目前对 Shell 一律请求审批；相对路径读取在 Docker 中允许、在 Local 中请求
审批；绝对路径、`..` 越界和无匹配规则直接拒绝。Local 读取是否应依赖工具层边界校验
而自动允许，已提出但尚未决定或修改。

旧 handoff 的三个问题中，`read_file` 已增加解析符号链接后的边界检查；另外两个问题
仍存在：正常退出路径的 patch 导出异常可能覆盖 Run 结果，Local Workspace 仍静默忽略
显式 `docker_image`。

## Completed

- 新增 `miniagent.policy` 数据模型、确定性默认规则、摘要绑定和 ApprovalHandler 协议。
- Runtime 已实现 fail-closed 策略评估、`WAITING_APPROVAL`、批准/拒绝/过期/取消、批准
  后重评，以及实际执行才累计 tool usage。
- CLI 已接入交互审批，展示完整参数；无人处理审批时返回
  `APPROVAL_UNAVAILABLE`。
- Policy Request 已绑定 Agent、工具契约/实现配置和 Workspace 上下文摘要；Run State
  保存决策、请求和 resolution。
- `read_file` 执行时解析实际目标，符号链接越界返回 `PATH_OUTSIDE_WORKSPACE`。
- 同一模型响应中的重复 Tool Call ID 会作为无效模型响应终止。
- 领域词汇、Policy 设计文档和 repository map 已同步当前实现边界。

## Current changes

- `src/miniagent/policy/`、`src/miniagent/runtime/{loop,state,termination}.py` — 当前
  session 的 Policy/Approval 实现与 Runtime 接入。
- `src/miniagent/cli/{bootstrap,main}.py` — 当前 session 的策略上下文装配与 CLI 审批。
- `src/miniagent/tools/` — 当前 session 的工具策略元数据及 `read_file` 解析后边界检查。
- `tests/test_policy.py`、`tests/test_runtime.py` — 当前 session 的策略、审批和执行回归测试。
- `docs/CONTEXT.md`、`docs/policy-engine-design.md`、`docs/repository-map.md` — 当前
  session 的术语、设计和导航更新。

以上修改均未提交。旧 handoff 中的 BROKEN patch 和 Local/docker-image 修复尚未进入
工作树；不要覆盖或回退现有 Policy 相关改动。

## Decisions and constraints

- 决策优先级为 `deny > approval > allow`；人工批准不能覆盖硬性拒绝。
- `deny` 用于不可覆盖边界；`approval` 用于可授予但超出默认权限的具体调用。
- 审批绑定单个规范化 Tool Call、策略版本、工具契约和执行上下文，不提供永久、命令
  前缀或整 Run 授权。
- 第一阶段仅支持同进程等待；SQLite、独立 approvals 命令和跨进程 resume 仍是 roadmap。
- Patch 导出失败不得覆盖已有 Run 结果或 signal exit semantics，Workspace 最终仍要销毁。
- `--workspace local` 与显式 `--docker-image` 必须 fail fast；Docker override 仍覆盖设置默认值。
- 保留当前未提交修改；不提交、不推送，不顺带实现跨进程审批。

## Validated evidence

- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q` — Policy 运行代码和最终
  测试改动后为 `69 passed, 1 skipped`；其后仅修改了文档。
- `ty check` — 最终 Python 实现与测试通过类型检查；其后仅修改了文档。
- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent 'model qwen的providers实现路径是？' --model deepseek-v4-flash`
  — 最终 Runtime 实现上真实 Qwen 调用成功；四个工具调用逐次审批，返回
  `./src/miniagent/providers/qwen.py`。裸 `miniagent` 因当前 shell PATH 无入口而失败。
- `git diff --check` — repository-map 文档更新后的工作树通过 whitespace 检查。
- 当前代码静态复核确认 `read_file.py` 同时执行词法检查和 `resolve()/is_relative_to()`
  检查；默认策略覆盖 deny、Local approval、Docker allow 三种读取结果。

## Not yet validated

- 尚无 Local 和 Docker 各自针对越界 symlink 的专项回归测试；现有测试只覆盖 `..`。
- BROKEN Workspace patch 导出与 Local/docker-image 冲突尚未修复或验证。
- 尚未根据最新讨论决定 Local 相对路径读取应继续审批还是自动允许。
- 当前工作树尚未 commit；跨进程审批和持久化未实现。

## Remaining work

1. 修复正常退出时 patch 导出失败覆盖 Run/退出语义的问题，保留可诊断错误并覆盖取消或
   BROKEN Workspace 场景。
2. 为 Local 和 Docker `read_file` 添加越界 symlink 回归测试，确认均返回
   `DENIED / PATH_OUTSIDE_WORKSPACE`。
3. 对 Local Workspace 与显式 Docker image 的组合 fail fast，并验证 Docker override。
4. 与用户确认 Local 相对路径读取策略；若自动允许，保持工具层解析后边界拒绝作为执行
   防线，并更新设计与测试。
5. 重新运行完整测试、类型检查、真实 CLI 验收和最终 diff review。

## Risks, blockers, and open questions

- 正常退出路径若吞掉 patch 失败而无诊断，会隐藏候选修改丢失；需要保留 Run 结果同时
  向 CLI 暴露 patch 错误。
- Local Workspace 的通用 Shell 并非宿主安全边界，不能仅凭命令前缀自动允许。
- Local `read_file` 已在工具层拒绝解析后越界，但 Policy Engine 在执行前不能证明目标；
  是否将这种执行层约束视为足够的自动 allow 仍待用户决定。

## Critical files

- `src/miniagent/policy/{models,engine,approvals}.py` — 当前策略契约与默认规则。
- `src/miniagent/runtime/loop.py`、`src/miniagent/runtime/state.py` — 策略执行和审批状态。
- `src/miniagent/cli/bootstrap.py`、`src/miniagent/cli/main.py` — Workspace 生命周期、patch
  和交互审批。
- `src/miniagent/tools/read_file.py` — 解析后路径边界。
- `tests/test_policy.py`、`tests/test_cli_bootstrap.py`、`tests/test_local_workspace.py`、
  `tests/test_docker_workspace.py` — 下一步回归测试入口。

## Context to load

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. `docs/policy-engine-design.md`
4. 上述 Critical Files

## Next action

先修复最高优先级的 patch 导出失败语义：读取 bootstrap 的正常/异常退出分支和 CLI
结果渲染，增加一个证明 patch 失败不会替代既有 RunResult 或 signal exit code 的回归测试。

```shell
sed -n '120,220p' src/miniagent/cli/bootstrap.py && sed -n '120,230p' src/miniagent/cli/main.py && sed -n '1,260p' tests/test_cli_bootstrap.py
```

## Required verification

```shell
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent 'model qwen的providers实现路径是？' --model deepseek-v4-flash
git diff --check
```

## Resume prompt

`$handoff resume`
