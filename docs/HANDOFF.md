# Task Handoff: 修复 Workspace 边界与 CLI 失败语义

<!-- handoff: captured-against 73a0837 | worktree dirty | updated 2026-09-17 -->

## Goal and success criteria

修复 review 发现的三个 Workspace/CLI 问题，同时保留现有 Run 结果、Workspace
隔离契约和配置的 fail-fast 行为。完成标准是三个回归场景均有测试覆盖，完整测试、类型
检查和 CLI 验收重新通过。

## Current state

LocalWorkspace、DockerWorkspace 和 CLI bootstrap 已实现并接入。以下三个 review
问题在 `73a0837` 上经静态复核仍然存在，尚未开始业务代码修复：

1. BROKEN Workspace 的 patch 导出可能覆盖既有 Run 结果或预期退出语义。
2. `read_file` 仅做词法路径检查，可通过越界 symlink 读取 Workspace 外文件。
3. Local Workspace 会静默忽略 `docker_image`。

## Completed

- Local/Docker Workspace、CLI bootstrap、patch artifact 路径已在此前工作中实现。
- 已重新检查相关实现与测试入口，确认三个问题没有被后续 repo-map 提交修复。
- 已将旧根目录 handoff 迁移为项目约定的 `docs/HANDOFF.md`。

## Current changes

- `.agents/skills/handoff/` — 新增项目级 Codex handoff 工作流；不包含业务代码修改。
- `HANDOFF.md` → `docs/HANDOFF.md` — 迁移并规范化现有任务状态。
- `CONTEXT.md`、`blueprint.md` → `docs/` — 迁移项目文档并同步仓库内路径引用。

当前没有针对三个 review 问题的未提交业务代码修改。

## Decisions and constraints

- Patch 导出失败不得覆盖已经产生的 Run 结果或 signal exit semantics，且 Workspace
  最终仍必须销毁。
- LocalWorkspace 与 DockerWorkspace 必须遵守相同的 `read_file` 越界 symlink
  拒绝契约，并返回 `PATH_OUTSIDE_WORKSPACE`。
- `--workspace local` 与 `--docker-image` 同时使用时必须 fail fast；Docker CLI 参数
  仍可覆盖 settings 中的默认 image。
- 保留用户已有修改，只处理上述问题，避免顺带重构。

## Validated evidence

- `src/miniagent/cli/bootstrap.py` — 正常返回路径仍无条件调用
  `persist_workspace_patch()`；Local 分支仍未拒绝 `docker_image`。
- `src/miniagent/tools/read_file.py` — 当前只拒绝绝对路径和包含 `..` 的路径，未验证
  symlink 解析后的目标。
- `rg -n "export_patch|docker_image|PATH_OUTSIDE_WORKSPACE" src tests docs/blueprint.md` —
  未发现覆盖上述三个回归场景的测试。
- 修复前基线曾通过 `pytest -q`（53 passed，1 skipped）和 `ty check`，但这些结果早于
  review comments，不能证明问题已修复。

## Not yet validated

- 三个修复及其回归测试均尚未实现。
- 修改后尚未运行完整测试、类型检查或 CLI 端到端验收。

## Remaining work

1. 修复 BROKEN Workspace 的 patch 导出语义，并增加超时/取消后的结果与退出码回归测试。
2. 阻止 `read_file` 跟随越界 symlink，覆盖 Local 和 Docker Workspace 契约。
3. 拒绝 Local Workspace 与 Docker 专用 image 的不兼容组合，并保留 Docker override。
4. 执行要求的完整验证并 review 最终 diff。

## Risks, blockers, and open questions

- Patch 导出的修复需要区分“Run 已有结果”和“bootstrap 自身异常”，避免吞掉真正的
  artifact 或 Workspace 错误。
- symlink 校验必须在实际 Workspace 内完成，不能依赖宿主机路径推断 Docker 文件系统。

## Critical files

- `src/miniagent/cli/bootstrap.py` — Workspace 选择、生命周期与 patch 持久化。
- `src/miniagent/tools/read_file.py` — 文件路径隔离逻辑。
- `tests/test_cli_bootstrap.py` — bootstrap 和配置组合回归测试。
- `tests/test_local_workspace.py`、`tests/test_docker_workspace.py` — Workspace 行为测试。

## Context to load

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. `docs/repository-map.md`
4. 上述 Critical Files

## Next action

先处理最高优先级的 BROKEN Workspace patch 导出问题，读取实现和现有 bootstrap
测试后确定最小回归测试边界。

```shell
sed -n '110,190p' src/miniagent/cli/bootstrap.py && sed -n '1,320p' tests/test_cli_bootstrap.py
```

## Required verification

```shell
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache UV_TOOL_DIR=/private/tmp/miniagent-uv-tools uvx --offline ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent 'model qwen的providers实现路径是？' --model deepseek-v4-flash
git diff --check
```

## Resume prompt

`$handoff resume`
