# Task Handoff: 收紧 Docker Shell 自动放行条件

<!-- handoff: captured-against 7299bf6 | worktree dirty | updated 2026-09-17 -->

## Goal and success criteria

完成新的默认 Policy：Workspace 内相对路径读取自动允许，Local Shell 需要审批，只有经
可信上下文证明满足隔离条件的 Docker Shell 才自动允许。修复 review P1，覆盖启用网络、
注入环境变量和隔离事实缺失时的 fail-closed 行为；相关测试、类型检查和最终 diff 通过后
再准备提交。同时保留并收尾上一版 handoff 尚未完成的 Workspace/CLI 行为：patch 导出
失败不能覆盖 Run/退出语义、补齐越界 symlink 测试、Local 与显式 Docker image 冲突时
fail fast。

## Current state

当前未提交实现把相对路径读取改为 Local/Docker 均允许，并按 `workspace_kind` 将 Local
Shell 判为 `approval`、Docker Shell 判为 `allow`。Astropy 12907 SWE-bench Docker 实跑
证明无逐次 Shell 审批且任务成功，但 review 发现 P1：Policy 只检查可伪装或信息不足的
`workspace_kind="docker"`，没有核验 Docker 的网络和环境注入等实际隔离配置。

因此当前改动不能提交。`PolicyContext` 仍只有 workspace ID、kind 和 revision；
`DockerWorkspaceSpec` 已支持 `network_enabled` 与 `environment`，但 Bootstrap 没有把这些
可信事实显式传给 Policy Engine。

上一版 handoff 的三项遗留仍由当前代码证实：正常成功路径直接等待 patch 导出，导出异常
会替代已有 Run 结果；Local Workspace 分支忽略显式 `docker_image`；Local/Docker 尚无
`read_file` 越界 symlink 专项回归测试。这些没有进入当前代码 diff，但必须保留在待办中。

## Completed

- `DefaultPolicyEngine` 已将相对路径 `read_file` 改为自动允许，越界读取仍拒绝。
- Docker/Local Shell 已拆分规则 ID；策略版本已提升为 `default-v2`。
- 已增加 Local/Docker Shell 决策及 Docker 无 ApprovalHandler 自动执行的回归测试。
- Policy 设计文档已同步上述行为，但其中“Docker 隔离即可自动允许”的描述须随 P1 修复
  精确限定。
- Review 已确认 P1，尚未修改代码解决。

## Current changes

- `src/miniagent/policy/engine.py` — 用户要求的新默认读/Shell 策略；当前 Docker allow 条件
  过宽，是 P1 修复目标。
- `tests/test_policy.py` — 新策略单元和 Runtime 行为测试；尚缺不安全 Docker 配置用例。
- `docs/policy-engine-design.md` — 新策略设计说明；须补可信隔离事实和 fail-closed 规则。
- `docs/HANDOFF.md` — 本次续接状态。

除上述文件外工作树无其他修改。不要回退用户要求的新策略；应收紧 Docker 自动允许条件。

## Decisions and constraints

- 不解析 Shell 字符串或用命令前缀推断安全性；项目测试本身也能执行任意代码。
- Local Shell 始终审批。Docker Shell 仅在可信装配层证明网络关闭、没有注入环境数据，且
  所需隔离属性完整时自动允许；事实缺失必须审批。
- 隔离事实必须来自实际 `DockerWorkspaceSpec`/Workspace，而不是模型参数或调用者仅声明
  `workspace_kind="docker"`。
- `PolicyContext` 是外部来源结构化数据，继续使用 Pydantic；变更后所有构造者和摘要绑定
  必须同步。
- 不修改 README。保持实现最小，不新增 Shell 命令分类器或策略 DSL。
- Patch 导出失败不得覆盖已有 RunResult 或 signal exit semantics，Workspace 仍须销毁且
  失败应可诊断。
- `--workspace local` 与显式 `--docker-image` 的组合必须 fail fast；Docker image override
  仍应覆盖设置默认值。
- 用户此前要求提交本次修改，但本次 `$handoff create` 仅授权更新 handoff；恢复后的记录
  本身不构成新的 commit/push 授权。

## Validated evidence

- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q tests/test_policy.py` — 当前
  P1 修复前代码为 `10 passed`。
- `ty check` — 当前 P1 修复前 Python 代码通过。
- 用户指定的 Astropy 12907 SWE-bench Docker CLI — 当前默认 Docker 配置下成功退出，容器
  Shell 无审批，Agent 完成修复、相关测试和 patch artifact 导出。
- Review — P1 指出启用 `network_enabled=True` 或注入环境值时仍自动允许 Shell，会绕过
  审批；该结论与当前 `PolicyContext` 和 `engine.py` 代码一致。

## Not yet validated

- 尚未实现或测试可信 Docker 隔离事实传播。
- 尚未证明启用网络、注入环境数据或缺失隔离事实的 Docker Shell 会进入审批。
- P1 修复后尚未重新运行 policy 测试、类型检查或 SWE-bench 端到端验收。
- 尚无 Local/Docker `read_file` 越界 symlink 专项测试。
- 正常成功路径 patch 导出失败的 Run/退出语义，以及 Local/docker-image 冲突尚未修复或
  验证。
- 尚未运行完整测试套件；当前改动未提交。

## Remaining work

1. 扩展 `PolicyContext`/Policy Request 所绑定的执行上下文，使其表达来自实际 Workspace
   配置的 Docker 网络与环境注入事实；未知值 fail closed。
2. 在 Bootstrap 从实际 `DockerWorkspaceSpec` 或创建出的 `DockerWorkspace.spec` 填充可信
   事实，避免重复构造可能漂移的配置。
3. 仅在 Docker 隔离条件全部满足时应用 `shell.docker_workspace = allow`；否则使用审批规则。
4. 增加安全默认、网络开启、环境注入、事实缺失，以及 Local Shell 的行为测试；同步设计
   文档并将 policy revision 再提升。
5. 修复正常成功路径中 patch 导出失败覆盖 RunResult/退出语义的问题，并覆盖取消或 BROKEN
   Workspace 场景。
6. 为 Local 和 Docker `read_file` 增加越界 symlink 测试，确认均返回
   `DENIED / PATH_OUTSIDE_WORKSPACE`。
7. 对 Local Workspace 与显式 Docker image 的组合 fail fast，并验证 Docker override。
8. 运行 targeted/full tests、`ty check`、必要的端到端验收和 `git diff --check`，审查无
   关键问题后再根据用户当时授权处理提交。

## Risks, blockers, and open questions

- 仅加入 `network_enabled` 仍不足以证明长期安全；未来若 Docker Backend 支持 host mounts、
  secrets、Docker socket、privileged 或额外 capabilities，这些也必须成为可信隔离事实并使
  未满足条件时 fail closed。
- 将环境字典原值放入 PolicyContext/日志可能泄露秘密；应只传播“是否存在注入”或变量名等
  最小事实，现有 digest 可绑定完整可信上下文但不应把值展示在审批摘要中。
- `AgentRuntime` 可由 CLI 外部直接构造；默认/手工构造的 Docker PolicyContext 不能因字段
  默认值而被误判为安全。
- 正常退出时若 patch 导出失败直接传播异常，会掩盖已有 Run 结果；若简单吞掉又会隐藏候选
  修改丢失，修复必须同时保留结果和可诊断错误。
- `read_file` 已在执行层解析符号链接并拒绝越界，但缺少 Local/Docker 回归测试会让这条
  防线容易被后续重构破坏。

## Critical files

- `src/miniagent/policy/models.py` — `PolicyContext` 与请求摘要契约。
- `src/miniagent/policy/engine.py` — Docker Shell allow/approval 决策。
- `src/miniagent/cli/bootstrap.py` — Workspace 创建和可信 PolicyContext 装配。
- `src/miniagent/environments/workspace.py` — `DockerWorkspaceSpec` 的网络、环境和用户配置。
- `src/miniagent/environments/docker.py` — 实际容器安全参数和 `DockerWorkspace.spec`。
- `src/miniagent/cli/main.py` — RunResult、signal 和 patch 错误的 CLI 退出语义。
- `src/miniagent/tools/read_file.py` — 解析后路径边界检查。
- `tests/test_policy.py`、`tests/test_cli_bootstrap.py` — 策略与装配回归测试。
- `tests/test_local_workspace.py`、`tests/test_docker_workspace.py` — symlink、patch 和 Workspace
  后端行为测试。
- `docs/policy-engine-design.md` — 当前策略依据。

## Context to load

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. `src/miniagent/policy/models.py`
4. `src/miniagent/policy/engine.py`
5. `src/miniagent/cli/bootstrap.py`
6. `src/miniagent/environments/workspace.py`
7. `tests/test_policy.py`

## Next action

设计最小的 fail-closed `PolicyContext` 隔离字段，并逐一定位所有构造点后实施 P1 修复。

```shell
rg -n "PolicyContext\\(|DockerWorkspaceSpec\\(" src tests && sed -n '35,70p' src/miniagent/policy/models.py && sed -n '145,185p' src/miniagent/cli/bootstrap.py
```

## Resume prompt

`$handoff resume`
