# Task Handoff

## Current status

LocalWorkspace、DockerWorkspace 和 CLI bootstrap 已实现并接入，但最新 review
发现以下三个尚未修复的问题。当前工作树包含未提交修改，后续 Agent 应保留现有改动，
只针对这些问题继续完善。

最近一次修复后的验证结果为：

- `pytest -q`：53 passed，1 skipped。
- `ty check`：通过。
- 指定的 CLI 端到端只读验收已通过：
  `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent 'model qwen的providers实现路径是？' --model deepseek-v4-flash`

以上结果早于本文件记录的最新 review comments；完成下述修复后需要重新验证。

## Latest review comments

### P1：BROKEN Workspace 导出 Patch 会覆盖 Run 结果

位置：`src/miniagent/cli/bootstrap.py`，约第 177-180 行。

命令超时或取消后，Workspace 会被标记为 `BROKEN`，但 `AgentRuntime.run()` 仍可能
正常返回失败或取消结果。`bootstrap` 当前会无条件调用 `export_patch()`，而 Local 和
Docker Workspace 的该方法要求状态为 `ACTIVE`。这会抛出 Workspace 异常、覆盖原有
Run 结果，并破坏 CLI 预期的失败或 signal exit code。

修复要求：

- Patch 导出失败不得覆盖已产生的 Run 结果或预期退出语义。
- 应明确处理 `BROKEN` Workspace，或允许进程终止后安全导出 Patch。
- 保持 Workspace 最终一定被销毁。
- 增加超时/取消后退出语义不被 Patch 导出覆盖的回归测试。

### P2：read_file 可通过 symlink 读取 Workspace 外文件

位置：`src/miniagent/tools/read_file.py`，约第 48-49 行。

当前仅检查绝对路径和词法上的 `..`。相对路径如果是指向 Workspace 外部的符号链接，
例如 `link -> /etc/passwd`，仍会被后端 Python 进程读取。旧实现会解析目标路径并返回
`PATH_OUTSIDE_WORKSPACE`。

修复要求：

- 在读取文件前验证解析后的实际目标仍位于 Workspace 根目录内。
- LocalWorkspace 和 DockerWorkspace 都应遵循相同的隔离契约。
- 越界 symlink 返回 `PATH_OUTSIDE_WORKSPACE`，不得返回目标内容。
- 增加 symlink 指向 Workspace 外部的回归测试。

### P2：Local Workspace 静默接受 Docker 专用参数

位置：`src/miniagent/cli/bootstrap.py`，约第 62-63 行。

使用 `--workspace local --docker-image ...` 时，当前实现会忽略 Docker image 并启动
LocalWorkspace。这违反 `blueprint.md` 中“不兼容配置必须拒绝而非静默降级”的约定。

修复要求：

- LocalWorkspace 与 `docker_image` 同时配置时应 fail fast，并返回明确错误。
- DockerWorkspace 仍允许 CLI 参数覆盖 settings 中的默认 image。
- 增加不兼容参数组合的回归测试。

## Required verification

完成修改后执行：

```shell
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache UV_TOOL_DIR=/private/tmp/miniagent-uv-tools uvx --offline ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent 'model qwen的providers实现路径是？' --model deepseek-v4-flash
git diff --check
```

最后检查完整 diff，避免覆盖用户已有修改或引入与上述问题无关的变更。
