# Task Handoff: 完成 SQLite 存储修复并收尾既有 Runtime/Policy 问题

<!-- handoff: captured-against 065d10b | worktree dirty | updated 2026-09-17 -->

## Goal and success criteria

优先完成当前 SQLite Run/Event/Snapshot/Approval 存储实现的两项剩余 review 修复：重复取消时 API 必须等待数据库工作线程结束，持久化 Docker workspace spec 必须保持运行时 workdir 的安全校验。随后继续处理上一版 handoff 尚未完成的 Policy、CLI 与 Workspace 问题。完成标准是各项行为都有针对性回归测试，完整测试、类型检查和 diff 检查通过，且不回退当前工作树中的 SQLite 实现。

## Current state

SQLite M1 已实现但尚未提交，第一次 review 的 8 项问题已修复；最新 review 仍有 2 项 P2。当前 `_in_thread()` 在第二次取消时可能提前返回，而 SQLite 事务仍在后台运行；`PersistedDockerWorkspaceSpec.workdir` 也没有复用 `DockerWorkspaceSpec` 的绝对路径、非根目录及禁止 `..` 校验。

上一版 handoff 不能视为已完成。HEAD `065d10b` 提交了默认 Policy 的放宽，但代码仍仅凭 `workspace_kind="docker"` 自动允许 Shell，没有绑定可信的网络、环境注入等隔离事实；正常成功路径的 patch 导出异常、Local workspace 与显式 Docker image 冲突、Local/Docker 越界 symlink 回归测试也仍待处理。

## Completed

- 新增 SQLite schema、迁移、存储模型和 `SqliteRunStore`，支持 Run/Event/Snapshot/Approval、单 Run 连续事件序列、CAS 和原子 `RunCommit`。
- 持久化恢复上下文已改用环境变量引用，不落库 workspace 环境值；Run、Event、Snapshot、Approval 的数据版本检查可区分不支持的新版本与损坏数据。
- 第一次 review 的审批跨 Run 越权、过期审批、可变提交输入、并发初始化、审批幂等语义、Snapshot 审计事件和版本错误等 8 项问题已修复并增加测试。
- SQLite 设计、领域术语、runtime 导出和仓库导航已随实现更新；Runtime 主循环尚未接入 SQLite，这属于设计中的后续 M2，不是当前 review 修复范围。
- 旧任务中 Local/Docker 默认工具策略已进入 HEAD `065d10b`，但 Docker Shell 的可信隔离 P1 尚未解决。

## Current changes

- `src/miniagent/storage/` — 新 SQLite 存储实现、协议模型与 migration；当前两项 P2 中的取消问题位于 `sqlite.py`。
- `src/miniagent/runtime/persistence.py` — 无秘密的恢复上下文与持久化 workspace spec；当前缺少 Docker workdir 校验。
- `src/miniagent/runtime/events.py`、`src/miniagent/runtime/snapshots.py`、`src/miniagent/runtime/__init__.py` — 存储所需事件、快照契约和公开导出。
- `tests/test_sqlite_storage.py` — SQLite 行为和第一次 review 修复的回归覆盖。
- `docs/sqlite-storage-design.md`、`docs/CONTEXT.md`、`docs/repository-map.md` — SQLite 设计、术语和导航更新。

以上 dirty worktree 内容均来自当前 SQLite 任务；没有发现需单独保留的无关用户改动。不要回退这些文件。`docs/HANDOFF.md` 仅记录续接状态。

## Decisions and constraints

- SQLite 写入输入在交给线程前序列化并重新校验，避免排队期间的嵌套可变对象破坏事件序列。
- 调用方取消后可以返回 `CancelledError`，但必须等对应 SQLite 操作达到最终结果后才能释放 Run 所有权；重复取消也不能打断清理等待。
- 持久化恢复模型不保存环境值，只保存引用；持久化 Docker spec 必须与运行时 `DockerWorkspaceSpec` 接受相同的 workdir 集合并使用相同规范化结果。
- Approval 的读取和变更绑定 `run_id + approval_id`，过期判断使用事务内可信时间，幂等比较忽略首次写入后固定的 `resolved_at`。
- Snapshot 与当前批次最后一个业务事件共享事务和 event_sequence，不生成独立 Snapshot 元事件。
- Local Shell 始终审批。Docker Shell 只有在可信装配层证明隔离条件完整且安全时才允许；不能仅相信可伪装的 `workspace_kind`，未知事实必须 fail closed。
- 不解析 Shell 文本来推断安全性，不把环境值写入 PolicyContext 或日志，不新增策略 DSL。
- 不修改 README。保留最小依赖和 Python 3.13/Pydantic 约定；本 handoff 不授权提交、推送或其他外部操作。

## Validated evidence

- `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q` — 当前 SQLite 代码在第二次 review 前为 `92 passed, 1 skipped in 1.96s`；review 证明该测试集尚未覆盖重复取消和持久化 Docker workdir 非法值。
- `ty check` — 当前 SQLite 代码在第二次 review 前通过；此后尚无代码修改。
- `uv build` — 当前 SQLite 代码在第二次 review 前成功，wheel 包含 migration SQL。
- `git diff --check` — 当前 SQLite 代码在第二次 review 前通过；重建 handoff 后需再次检查。
- 旧 Policy 状态：`tests/test_policy.py` 曾为 `10 passed` 且 `ty check` 通过；Astropy 12907 Docker CLI 实跑成功，但这些结果不能证明不安全 Docker 配置会进入审批。

## Not yet validated

- 尚未证明同一调用被连续取消两次后，API 仍等待 SQLite 工作线程完成再返回。
- 尚未证明持久化 Docker spec 会拒绝相对路径、`/` 和含 `..` 的 workdir，并与运行时模型保持相同规范化。
- 两项 P2 修复后尚未重新运行 targeted/full tests、`ty check`、build 或 `git diff --check`。
- Docker Policy 尚未验证网络开启、环境注入或隔离事实缺失时 fail closed。
- 正常成功路径 patch 导出失败的 Run/退出语义、Local/Docker 越界 symlink，以及 Local workspace 与显式 Docker image 冲突尚未修复或验证。

## Remaining work

1. 修复 `SqliteRunStore._in_thread()`：记录取消请求并反复屏蔽等待，直到 worker 得到最终结果；确保 worker 异常被取回，最终向调用方保留取消语义。
2. 用公开存储行为增加重复取消测试：阻塞真实 SQLite 写事务，连续取消两次，确认调用未提前结束；释放锁后确认返回取消且数据库状态确定。
3. 为 `PersistedDockerWorkspaceSpec.workdir` 应用与 `DockerWorkspaceSpec` 相同的校验和规范化，并覆盖构造与反序列化的非法/规范化用例。
4. 运行 SQLite targeted tests、完整测试、`ty check`、必要时 `uv build` 和 `git diff --check`，检查当前实现无新的关键问题。
5. 扩展可信 Policy 上下文和 CLI 装配，只在实际 Docker 配置证明网络关闭、无环境注入且隔离事实完整时自动允许 Shell；补安全默认、网络开启、环境注入、事实缺失和直接构造 Runtime 的测试，同步设计文档和 policy revision。
6. 修复正常成功路径中 patch 导出失败覆盖 RunResult/退出语义的问题，同时保留可诊断错误，并覆盖取消或 BROKEN Workspace 场景。
7. 为 Local 和 Docker `read_file` 增加越界 symlink 回归测试，确认返回 `DENIED / PATH_OUTSIDE_WORKSPACE`。
8. 对 Local workspace 与显式 `docker_image` 组合 fail fast，并验证 Docker image override 仍覆盖设置默认值。

## Risks, blockers, and open questions

- 捕获重复取消时，既不能让事务在 API 返回后提交，也不能遗失 worker 异常或产生未检索异常警告；需按 Python 3.13 `Task.cancel()`/`shield()` 语义写测试。
- 两个 Docker workdir 模型若复制校验逻辑，未来可能漂移；若抽取共享函数，应保持实现小且不引入循环导入。
- 当前 Policy 只看 `workspace_kind`，外部直接构造 `AgentRuntime` 时可伪装 Docker；安全事实必须来自实际 workspace 装配且缺失时审批。
- Docker 后端未来若增加 host mount、secret、socket、privileged 或 capability，这些也必须进入可信隔离判定。
- 正常退出的 patch 导出异常若直接传播会掩盖已有 Run 结果，若静默吞掉又会隐藏候选修改丢失。

## Critical files

- `src/miniagent/storage/sqlite.py` — 重复取消与事务完成不变量。
- `src/miniagent/runtime/persistence.py` — 持久化 Docker workdir 模型。
- `src/miniagent/environments/workspace.py` — 运行时 Docker workdir 的规范校验来源。
- `tests/test_sqlite_storage.py`、`tests/test_protocol_models.py` — 当前 P2 回归测试位置。
- `docs/sqlite-storage-design.md` — SQLite 事务、取消和恢复契约。
- `src/miniagent/policy/models.py`、`src/miniagent/policy/engine.py`、`src/miniagent/cli/bootstrap.py` — 旧 Policy P1 和 CLI/patch 遗留。
- `tests/test_policy.py`、`tests/test_cli_bootstrap.py`、`tests/test_local_workspace.py`、`tests/test_docker_workspace.py` — 旧任务待补行为覆盖。

## Context to load

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. `src/miniagent/storage/sqlite.py`
4. `src/miniagent/runtime/persistence.py`
5. `src/miniagent/environments/workspace.py`
6. `tests/test_sqlite_storage.py`
7. `tests/test_protocol_models.py`

## Next action

先定位取消辅助函数和两套 Docker workdir 校验及现有测试位置，然后实现当前两项 P2 与回归测试。

```shell
sed -n '155,185p' src/miniagent/storage/sqlite.py && sed -n '35,70p' src/miniagent/runtime/persistence.py && sed -n '45,80p' src/miniagent/environments/workspace.py && rg -n "cancel|workdir" tests/test_sqlite_storage.py tests/test_protocol_models.py
```

## Resume prompt

`$handoff resume`
