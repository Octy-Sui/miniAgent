# Policy Engine 与 Approval 设计

状态：第一阶段已实现；跨进程持久化、独立审批命令和恢复仍是后续工作。

## 1. 范围与现状

本期以单个 Tool Call 为授权单位，设计 `allow`、`deny`、`approval` 三类决策，覆盖规则评估、审计记录、人工审批以及 Runtime 接入。延续当前串行工具调用，不引入规则 DSL、命令解析器、外部策略服务或通用权限系统。

现有实现的接入依据：

- `runtime/loop.py` 在 Agent 工具白名单和 `ToolRegistry.prepare()` 参数校验之后执行 Policy Engine，再决定执行、拒绝或等待同进程审批。
- `models/tool.py` 已有 `ToolStatus.DENIED`，适合表达被拒绝的调用。
- `runtime/state.py` 已有 `WAITING_APPROVAL`、`pending_approval_id` 和 `APPROVAL_REJECTED`，但 `RunResult` 只接受终态。
- `runtime/events.py`、`snapshots.py` 只有契约；当前 Loop 未接入持久化。
- CLI 的 Workspace 上下文在退出时执行 `destroy()`；跨进程等待审批需要调整所有权和清理时机。

蓝图中的网络开启、宿主路径扩展、预算提升最终也需要授权，但当前没有对应的结构化操作。本期不将这些动作伪装成 Shell 字符串能力；未来增加实际操作接口后，再扩展授权对象。

## 2. 决策语义与职责

| 决策 | 含义 | Runtime 行为 | 是否产生 Tool Result |
| --- | --- | --- | --- |
| `allow` | 在当前条件下允许执行 | 执行已验证的调用 | 执行完成后产生 |
| `deny` | 当前调用被策略禁止 | 不执行，记录拒绝并继续本 Step | `DENIED / POLICY_DENIED` |
| `approval` | 执行前必须取得人工授权 | 暂停在当前调用，后续调用也不执行 | 等待期间不产生 |

Policy Engine 是同步、确定性的评估器：相同的规范化请求、可信环境事实和策略版本产生相同的判定内容。它不询问用户、不执行工具、不修改 Run State、不生成 ID 或读取时钟。Runtime 为结果附加 ID、时间并记录决策；Approval 服务负责身份核验、请求状态和并发更新。

`approval` 是策略结论；`approved` / `rejected` 是人对请求的回应。人工批准不把原始 Policy Decision 改写成 `allow`，也不覆盖 `deny`。执行记录引用原始决策和审批记录，保留完整因果关系。

工具白名单仍为不可绕过的前置约束：不在列表中返回现有 `TOOL_NOT_ALLOWED`；未知工具和无效参数仍走 Registry 的现有错误路径，均不创建 Approval Request。

## 3. 数据结构

以下是拟新增到 `policy/` 的契约草图。持久化模型使用 Pydantic；内部评估结果使用 dataclass。代码展示字段形状，后面的校验约束属于同一份实现要求。

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL = "approval"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    step_number: int = Field(ge=1)
    call_index: int = Field(ge=0)
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    agent_spec_digest: str
    tool_contract_digest: str
    workspace_id: str
    workspace_kind: Literal["local", "docker"]
    execution_context_digest: str


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    effect: PolicyEffect
    risk: RiskLevel
    matched_rule_ids: tuple[str, ...]
    decisive_rule_ids: tuple[str, ...]
    reason_code: str
    reason: str


class PolicyEngine(Protocol):
    @property
    def revision(self) -> str: ...

    def evaluate(self, request: PolicyRequest) -> PolicyEvaluation: ...


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    decision_id: UUID
    request: PolicyRequest
    request_digest: str
    policy_revision: str
    effect: PolicyEffect
    risk: RiskLevel
    matched_rule_ids: tuple[str, ...]
    decisive_rule_ids: tuple[str, ...]
    reason_code: str
    reason: str
    input_summary: str
    decided_at: AwareDatetime


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    approval_id: UUID
    decision_id: UUID
    run_id: UUID
    request_digest: str
    policy_revision: str
    requested_at: AwareDatetime
    expires_at: AwareDatetime


class ApprovalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    approval_id: UUID
    outcome: Literal["approved", "rejected", "expired", "invalidated"]
    actor_kind: Literal["human", "system"]
    actor_id: str
    reason: str
    resolved_at: AwareDatetime
```

采用三值枚举而非三种子类：当前三类决策没有不同的专属数据，不需要判别联合。Approval Request 独立存储，避免 `allow` / `deny` 上出现无效的审批字段。

### 3.1 身份、可信输入和不可变性

调用身份采用 `(run_id, step_number, call_index)`，其中 `call_index` 为模型响应中的原始位置。Provider 的 `tool_call_id` 用于协议关联，不单独承担跨 Step 唯一性；同一响应中的重复 ID 在进入执行前按无效响应处理。

`arguments` 来自 `prepare()` 补齐默认值后的参数。Workspace、Agent 和工具版本信息由装配层/Runtime 填入，模型不能提供或覆盖。执行必须使用与判定一致的参数副本。

`frozen=True` 不冻结嵌套字典。创建请求时深拷贝；保存和执行前重新计算摘要并检查一致性，不能把可变字典作为不可变授权凭据。

摘要规范定义为版本 1：JSON 对象键排序、UTF-8、无多余空格、禁止 NaN/Infinity，数组保留顺序，字符串不做语义改写，再计算 SHA-256。Shell 字符串保留原文，不能通过删除空白、排序参数等方式合并授权。任何摘要规则变化都提升 `schema_version`。

`request_digest` 覆盖完整 PolicyRequest；三个内部摘要分别覆盖：

- `agent_spec_digest`：当前 Agent Specification，包含 permitted tools。
- `tool_contract_digest`：工具名称、参数 Schema、可信实现版本以及执行配置，例如超时。
- `execution_context_digest`：Workspace 身份、后端实际控制配置、工作目录、环境配置版本和可验证的代码状态标识。代码状态需覆盖未提交及未跟踪文件，单独使用 Git HEAD 不够。

这些摘要不是现有 Workspace API 已提供的能力。跨进程恢复前需要实现上下文验证；无法验证时使旧审批失效。摘要比较不能消除执行瞬间的文件竞争，也不能使 Local Workspace 成为安全沙箱。执行阶段仍需后端访问控制和单 Run 独占执行。

### 3.2 校验约束

- 非空标识、原因和版本；摘要必须是 64 位小写十六进制；所有时间带时区并统一写入 UTC。
- `matched_rule_ids` 去重且稳定排序；`decisive_rule_ids` 非空并且是前者的子集。默认规则也有固定 ID。
- `input_summary` 为有界、脱敏的可读摘要；不参与规则匹配或授权绑定。
- Approval Request 必须引用同 Run 的 `approval` 决策，其摘要、策略版本必须与决策一致，且 `expires_at > requested_at`。
- 每个请求至多一条 Resolution；`approved` / `rejected` 的 actor 必须为 human，`expired` / `invalidated` 必须为 system。人类身份来自可信 CLI/认证上下文，不能接受模型提供的审批人字段。
- 批准/拒绝仅允许在请求未解决、Run 仍等待该请求且当前时间严格早于过期时间时提交；到期边界按过期处理。
- Resolution 记录“人作出了什么决定”；是否已执行由调用执行记录表达。已批准后条件变化，不改写这条 Resolution，而是记录授权失效事件并重新判定。

原始参数是恢复执行所需的受保护数据，只在受控状态存储中保留；通用事件、日志和模型反馈只携带 ID、摘要和脱敏原因。审批界面应让有权限的操作者查看确切命令与目标，不能只展示可能掩盖行为的摘要。

## 4. 规则与默认策略

第一版使用小规模的代码规则，不暴露任意表达式配置。`policy_revision` 标识整套规则、配置和分类逻辑，必须随任何决策行为变化而变化。规则匹配由可信代码计算，不能相信模型声明的 `risk`、`read_only` 或 `needs_network`。

对全部匹配规则采用固定优先级 `deny > approval > allow`，不依赖注册顺序。记录所有匹配规则，以及产生最终 effect 的全部规则。风险只用于解释，不用于隐式覆盖 effect；多个风险取 `unknown > high > medium > low` 的保守展示顺序。

建议默认策略：

| 规则 ID | 条件 | effect / risk |
| --- | --- | --- |
| `path.outside_workspace` | `read_file` 为绝对路径或含 `..` 路径段 | deny / high |
| `read.workspace` | `read_file` 通过相对路径检查 | allow / low |
| `shell.docker_workspace` | 通用 Shell 在隔离的 Docker Workspace 中执行 | allow / medium |
| `shell.local_human_review` | 通用 Shell 在 Local Workspace 中执行 | approval / unknown |
| `default.deny` | 没有规则匹配 | deny / unknown |

`default.deny` 只在零匹配时生效，否则会吞掉所有允许规则。硬性禁止规则可以和其他规则同时匹配，并优先决定结果。

当前 `read_file` 在工具层同时检查词法路径和解析后的符号链接目标，因此 Local 和 Docker Workspace 都自动允许相对路径读取；解析后越界仍返回拒绝结果。

当前 Shell 使用 `/bin/sh -lc` 执行任意字符串。Docker Workspace 默认关闭网络、不挂载宿主路径、丢弃 capabilities、禁止提权并限制资源，因此允许通用 Shell；Local Workspace 进程仍可访问宿主用户有权访问的路径和网络，继续逐次审批。策略不通过命令前缀或正则推断安全性，因为项目测试本身也可以执行任意代码。

不增加隐式 AllowAll 回退。接入默认策略会改变现有 CLI 的自动执行行为，必须同时提供审批交互或明确的不可用错误，不能将该行为变化藏在依赖默认值中。

## 5. Runtime 接入与审批流转

```text
Agent 工具白名单 → Registry.prepare → 构造请求 → PolicyEngine.evaluate
                                                     │
                       ┌─────────────────────────────┼──────────────────┐
                     allow                          deny             approval
                       │                             │                  │
                    执行工具                   记录拒绝结果        保存请求及暂停状态
                       │                             │                  │
                    工具结果                  本 Step 下一调用      WAITING_APPROVAL
                                                                        │
                                                        人工回应 / 过期 / 取消
```

### 5.1 暂停与恢复

每个串行 Run 最多一个待审批请求。进入等待时，原子保存决策、审批请求和 Run State：`status=WAITING_APPROVAL`、`cursor=None`、`pending_approval_id=str(approval_id)`。保留尚未执行的调用、原始位置，以及该 Step 已完成调用的结果状态。

当前 Loop 依赖临时 `results` 列表统计 Step 失败，恢复后不能只统计剩余调用。实施时持久化当前 Step 的调用记录与结果状态，整批结束后只更新一次 `consecutive_failed_steps`。仅靠 `completed_tool_call_ids` 不足以表达跨 Step 身份和执行状态，需要以调用身份保存记录。

| 事件 | 审批处理 | Run 后续行为 |
| --- | --- | --- |
| 人工批准 | 原子保存 `approved` | 恢复前重验；满足条件才执行原调用 |
| 人工拒绝 | 保存 `rejected` | 当前调用记录 `DENIED / APPROVAL_REJECTED`，Run 以 `FAILED / APPROVAL_REJECTED` 结束 |
| 到期未答复 | 保存 `expired` | 当前调用记录 `DENIED / APPROVAL_EXPIRED`，Run 失败；新增同名 TerminationReason |
| 用户取消 | 未解决的请求保存 `invalidated`，原因 `RUN_CANCELLED` | 清理 pending 引用，Run 进入 `CANCELLED` |
| 参数、策略或环境变化 | 未解决请求保存 `invalidated`；已批准请求追加授权失效事件 | 重建请求并重新判定，旧批准不可复用 |

拒绝审批终止 Run 是本方案的产品选择，与普通策略拒绝后允许模型调整方案不同：明确拒绝应阻止同批后续行为。终态不再向 Provider 发送未完成批次；普通 deny 会补齐 Tool Result 后继续，沿用当前连续失败统计规则。

批准后的恢复顺序：核验 Run 及调用身份 → 检查取消和有效期 → 重验白名单和参数 → 验证请求摘要及策略版本 → 重新评估策略 → 获取唯一执行权 → 执行。审批授权仅满足相同请求下的 `approval`；重评为 `deny` 时禁止执行。有效期限制到执行认领时刻，工具开始后使用自身超时，不因审批随后到期而强制中止。

等待和恢复不能增加 Step 次数或重复发起已完成的模型请求；恢复从当前工具调用开始。等待期间没有模型/工具 usage 增量；工具调用计数只在实际开始执行时增加。审核中的调用不产生虚假的 SUCCESS 或占位 Tool Result。

现有 `transition()` 没有同步修改 `pending_approval_id` 的参数，需要扩展为验证完整候选状态后一次提交，避免先修改原状态再因校验失败留下半成品。取消、拒绝和正常恢复都必须在同一更新中清空 pending 引用。

### 5.2 并发、崩溃与持久化

审批提交使用请求版本和 Run 版本的 compare-and-swap；相同请求、相同操作者、相同动作的重试返回已有结果，相反动作或竞争操作者收到冲突。approve、reject、cancel、expire 必须竞争同一份权威状态，不能各自成功。

执行记录至少包含调用身份、决策 ID、可选审批 ID，以及 `ready → executing → completed` 状态；以原子认领保证同一调用不会被两个 resume 同时执行。认领前再次检查 Run 未取消。

`executing` 后崩溃无法判断外部副作用是否已发生。恢复时标记执行结果未知并停止自动重放，转人工处理；不承诺任意 Shell 的 exactly-once。批准记录不能充当重复执行凭据。

SQLite 落地时，在同一事务内保存审批/执行记录、Run State 和对应事件；现有 EventStore、SnapshotStore 独立调用不能提供这项保证，需要协调的事务边界。决策记录成功前不执行工具；存储或评估异常使 Run 以 `INTERNAL_ERROR` 终止，并提供 `POLICY_EVALUATION_FAILED` 或 `POLICY_RECORD_FAILED` 错误码，不降级为 allow。

### 5.3 CLI 与返回契约

分两步接入，保持当前 `run() -> RunResult` 的终态契约：

1. 已支持同进程等待：注入 Approval 交互边界，`run()` 在等待期间保持挂起，批准后继续，返回时仍为终态；取消可以打断等待。此阶段不支持进程退出后的恢复。无人值守且没有审批处理器时，返回明确的 `APPROVAL_UNAVAILABLE` 失败。
2. 持久化完成后增加独立的可暂停入口，返回 `RunResult | RunSuspension`，以及 `resume(run_id)`。`RunSuspension` 仅包含 Run ID、Approval ID 和暂停时间，不伪装成 RunResult。同步更新 CLI、bootstrap 和全部调用方；暂停时保留 Workspace，终态或显式清理时才销毁。

对非交互 CLI，计划提供 `approvals list / approve / reject` 和 `resume`。approve 只记录授权，resume 执行；具体命令接入作为后续实现，不修改现有 CLI 契约以假装已经具备恢复能力。

## 6. 审计契约

拟新增 `policy_decided` 事件记录每次 allow/deny/approval 的 decision ID、调用身份、策略版本、规则 ID、风险、脱敏输入摘要、effect 和时间。现有 `approval_requested`、`approval_resolved` 分别引用请求和回应，记录操作者、决定及时间；增加 `approval_invalidated` 表达批准后授权失效，避免篡改历史回应。

`tool_called` 只在实际启动执行时写入，并引用决策及可选审批。`tool_completed` 表达调用最终结果，包括从未执行的 deny；通过缺少 `tool_called` 区分禁止执行与执行失败。待审批不是完成，不写 `tool_completed`。

策略决策当时没有审批人，因此不强求一个记录同时包含蓝图列出的所有字段；通过 decision ID / approval ID 联结决策与回应即可获得完整审计链。

## 7. 实施顺序与验收

1. 已新增 `policy/models.py`、`engine.py`、`approvals.py`：数据验证、确定性默认规则和内存审批契约。
2. 已将策略接到 prepare 与 execute 之间，并增加同进程审批交互；覆盖 deny 不调用工具、approval 不提前执行、批准只执行对应调用、拒绝、超时和无处理器失败。
3. 接入事务存储、暂停返回值、CLI 恢复和 Workspace 保留；验证暂停前已有调用不重放、usage 与 Step 统计不重复、并发 resume 只有一个执行者、执行中崩溃不会自动重试。
4. 根据真实需求增加具备明确输入和后端约束的工具，逐步允许自动执行；网络/预算/宿主路径扩展使用专门的授权对象，不扩大单次 Shell 审批的含义。

关键反例必须覆盖：allow 与 deny 同时匹配；批准后参数被修改；相同命令换了 Workspace；旧策略批准后出现新 deny；批准到执行之间过期；同 Step 多调用中第二个等待审批；审批与取消竞争；工具已产生外部副作用但结果尚未持久化；模型伪造审批人或风险字段。

当前实现的默认审批有效期由 `RuntimeConfig` 明确指定，不提供永久授权、按命令前缀授权或本 Run 全部允许；这些属于后续独立的产品决策。
