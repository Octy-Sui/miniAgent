# 模块与数据结构重构执行方案

编写日期：2026-09-22。核对基线：`d72dbd55a6184c292e7904330ad0e89ef28df886`。
当前状态：T00—T19 已于 2026-09-23 执行完成；逐卡范围、决策与验证结果见
[progress.md](progress.md)。本文后续“当前事实”保留执行前基线语境，最终现状以代码、测试和
[repository-map.md](../repository-map.md) 为准。

## 1. 目标与范围

让功能实现、数据模型和不变量归属同一模块；内部实现变化通常只影响所属模块，公开契约变化只影响明确的直接使用方。保留最小 Agent Runtime，不引入通用插件框架、事件总线、依赖注入容器或新的数据库框架。

成功标准：

1. Runtime 不依赖 `storage` 包；Storage 实现 Runtime 定义的持久化契约。
2. Workspace 运行配置、持久化描述共享非敏感选项及校验。
3. 工具参数校验后保留类型；执行证据在工具转换和持久化过程中不丢失。
4. RunState 只保存当前执行所需事实，不保存完整审批、策略和已完成工具历史。
5. 审批、工具及终止事件具有明确载荷类型；不通过魔法字典键关联审批变更。
6. State、Events、Approval、Snapshot 的事务一致性与提交后发布顺序不退化。
7. Patch、Artifact 元数据和收尾结果可以脱离 CLI 临时对象查询。
8. 每项变更有针对性验收，执行进度可从仓库文件恢复。

## 2. 已确认的执行决定

用户明确说明项目尚未上线，**不需要数据兼容**：

- 不开发旧数据库迁移、旧 JSON 解码、字段别名、双写或旧导入路径转发。
- 每张任务卡同时更新定义、实际调用方及相关测试，完成后删除已无引用的旧实现。
- 可以重写初始建表 SQL；保留新格式的明确版本检查，遇到旧数据库清楚报错，不自动清空或重建。
- 验收全部使用临时目录和新建数据库，不访问或删除用户默认目录中的运行数据。
- 不维护历史 schema fixture；仍覆盖当前格式往返、未知版本、损坏数据和非法引用。

每张改变持久化结构的卡，都在该卡内同步当前格式标记、必要建表 SQL、编解码及测试，使用新建数据库验收；不能等到 T13 才让存储恢复可用。T13 负责最终整理，不是前面任务留下破损存储的理由。

不需要数据兼容，不等于可以改变工具许可、审批安全、取消、终止和 Workspace 清理语义。行为变更只限任务卡明确列出的改进，其他差异记录为待决事项。

## 3. 文档入口与阅读顺序

| 文件 | 用途 |
| --- | --- |
| [plan.md](plan.md) | 范围、原则、执行协议和验收命令；每次启动都读 |
| [architecture.md](architecture.md) | 最终模块文件清单、数据所有权和关键契约 |
| [tasks.md](tasks.md) | 有依赖关系的任务卡、操作步骤和逐项验收 |
| [progress.md](progress.md) | 唯一执行进度、当前任务、验证记录、待决事项 |
| [handoff.md](handoff.md) | 中断交接模板与可直接复制的执行提示词 |

恢复时只读本入口、progress、当前任务卡和 architecture 对应章节；无需把全部代码和历史对话装入上下文。

文档冲突处理：用户最新要求优先于本方案；`AGENTS.md` 约束执行方式；代码和测试用于确认现状，不能把目标文档当作已实现证据。旧 `docs/HANDOFF.md` 属于其他任务，不恢复其中的待办、不覆盖其内容。`docs/runtime-loop-refactor.md` 已完成的控制流改造应保留。本方案不重复执行它的历史批次。

## 4. 当前事实与本次不做的事

当前 `cli/bootstrap.py` 已接入 SQLite；Runtime 已有 Execution、Journal、Recorder 和事件 Span scope。仓库导航中的“尚未接入存储”不准确。已有状态迁移、审批原子提交、参数校验、取消、价格计算和 Local/Docker 实现应迁移复用。

以下是目标扩展位置，不是本次新增能力：

- 跨进程恢复、重试控制器、模型切换、上下文压缩；保留并整理已有契约即可。
- Token/费用/耗时预算执行；目前只整理已有 usage 与 step/failure 限制，不创建空 `budget.py`。
- 完整 Trace 聚合、导出或查询界面；只整理现有 Trace 类型和事件关联。
- 新 Provider、新工具、新评估器、并行工具、Web API 和 UI。
- 当前默认提示词很短，不为目录完整性引入 Jinja 依赖；待提示词需要模板时再创建 `prompts/default.jinja`。

## 5. 全程不变量

- 一个 Step = 一次模型请求及该响应中的全部工具调用；工具按原顺序串行执行。
- 未许可工具、无效参数和未获授权调用不得执行。
- 审批只授权特定 Run、调用、参数、Agent/工具契约、执行条件和策略版本；变更后必须重新判定。
- 禁止凭客户端时间回填绕过审批到期；到期检查保留在提交事务内。
- 无 Store 和 SQLite 模式对相同输入产生相同业务终态。
- 每个 Run 事件序列连续；状态、事件、审批变化、快照在一次事务内提交。
- 先构造候选 State/Trace，成功提交后发布；失败时保留最后确认的状态和位置。
- 存储输入在线程派发前冻结；取消不能让未确定的事务在调用返回后继续改变状态。
- cancellation token 与任务级取消的现有不同语义保持；不能广泛捕获后伪装成功。
- 默认 evaluator 仍为 pass-through，不把重构描述成独立验证能力的实现。
- 环境变量秘密值不得进入持久化 Workspace 描述、策略摘要明文或错误日志。
- 保留 Local/Docker 的安全边界、超时后的 BROKEN 状态和失败后的证据保留策略。
- 渲染截断不破坏完整工具结果；不要以“减少状态体积”为由丢弃审计证据。

## 6. 每次执行的固定流程

1. 读 `AGENTS.md`、本文件和 progress；检查 `git status --short`、当前 HEAD 和未完成 diff。
2. 从 tasks 选择依赖全部 DONE 的第一张卡。一次只有一张 ACTIVE 卡，不并行改共享契约。
3. 读卡片列出的源文件、公共调用方及对应测试；用 `rg` 核对引用，不按旧行号修改。
4. 在 progress 写入任务号、当前 HEAD、预期文件和下一动作，再修改代码。
5. 先迁移所有权，再在独立小步骤调整结构或行为。类型迁移必须同步实际消费者，不留下两套实现。
6. 用 `apply_patch` 编辑；修改超出卡片列明的消费者范围时，先记录原因和具体文件。仅为更新 import 的直接消费者不视为新功能扩展。
7. 跑卡片验证，检查 diff 的关键问题；失败时只修本任务引入的问题，不删测试、不降低断言规避失败。
8. 写入实际变化、执行过的命令及结果、剩余风险、下一任务；通过验收才标 DONE。
9. 若上下文将不足，停止扩大修改面，写 handoff 后再结束；未完成任务保持 ACTIVE，不伪造完成状态。

任务卡可在 progress 中拆为 `Txx.a/Txx.b`。默认每个子步最多涉及约 6 个非机械实现文件；跨模块类型搬迁可能更新更多 import，但不能同时附带规则改写。子步应尽量恢复可导入、可测试状态；确实未恢复时必须注明具体失败命令和原因。

本方案不授权自动 git commit/push、删除本地数据库、运行真实付费模型或清理用户 Workspace。代码执行权限以用户后续任务授权为准。

## 7. 验证规则

命令从仓库根目录执行。任务卡中的测试集合使用实际尚未迁移的路径；测试目录重组完成后，按 progress 的路径映射替换，不能用错误路径运行后声称通过。

```sh
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q <相关测试文件>
ty check
git diff --check
```

任务卡标记“事务门禁”时，除定向测试外执行完整测试。这沿用已有 Runtime 重构对 Journal/审批/Event/Span 的验证要求：

```sh
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
```

最终执行完整测试、类型检查、CLI help 和打包：

```sh
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest -q
ty check
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run miniagent --help
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv build
git diff --check
```

记录 skipped 的原因；没有 Docker 或类型检查工具时标“未验证”，不能写通过。优先真实临时 SQLite、文件系统与 Local Workspace；Provider/人工交互可以使用外部边界 fake，不 mock 内部状态更新或事务函数。仅文档改动不运行 Python 测试。

## 8. 最终验收

- tasks 的必做卡均 DONE，progress 没有遗留 ACTIVE/未解释失败。
- 所有实际文件满足 architecture；标“后续”的文件无需存在，不能为空壳而创建。
- 新格式一次完成迁移，旧模型和 import 路径无生产调用方，无兼容层。
- Runtime 无 `miniagent.storage` 导入；功能契约不导入 Runtime 执行器或 CLI；SQL 只在 storage。
- 当前状态不含全量审批/策略历史；这些事实可从新建数据库查询，工具调用可按 Run+Step+索引关联。
- 新数据库可重开读取 State/Event/Approval/Snapshot 和运行制品，元数据与引用一致。
- 类型检查和必要测试通过；CLI 成功、失败、审批、信号和收尾错误路径都有证据。
- 更新 repository-map 的实际状态，不修改 README，不把未实现能力写为 wired。
