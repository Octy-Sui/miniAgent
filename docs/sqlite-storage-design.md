# SQLite Run / Event / Snapshot / Approval 存储设计

状态：M1 存储基础已实现；M2 可恢复执行待实施；2026-09-17。本文定义数据结构、事务契约与后续验收顺序；当前 Runtime 尚未接入持久化或跨进程恢复。

## 1. 结论与当前边界

采用一个本机 SQLite 数据库，四张核心表：`runs`、`run_events`、`run_snapshots`、`approvals`。一个协调存储入口负责跨表事务；EventStore 和 SnapshotStore 作为兼容入口，不自行拼接业务事务。首版使用标准库 `sqlite3`，不引入 ORM。

单 Run 的已提交事件序列定义为连续的 `1..N`，比仅要求递增更强。由存储在写事务内分配；Run ID 隔离序列，UUID 标识事件，时间戳只用于展示。事务回滚不消耗序列。

当前代码依据：

- `runtime/events.py` 已定义 NewRunEvent、RunEvent、EventStore 和序列冲突异常，但未接入 Loop。
- `runtime/snapshots.py` 已定义 SnapshotStore、独立版本 CAS 和 Run ID 校验，但没有存储实现。
- `runtime/state.py` 的 `RunState.version` 只在 `transition()` 增加，消息、usage、cursor 的一般更新不增加它。不能将其当作全局写入 CAS。
- `policy/models.py` 已有 ApprovalRequest、ApprovalResolution 和 PolicyDecision；`ApprovalHandler` 是交互边界，不是持久化协议。
- 同进程审批已接入；`docs/policy-engine-design.md` 第 5.1 节关于 transition 尚不支持 pending ID 参数的描述已落后于代码，实施应以现有实现为准。
- RunState 尚不足以独立重建 Workspace，也没有完整的当前 Step 执行记录。四表落地不会自动补齐恢复能力。

本轮先交付存储基础，再接可恢复执行。V1 的唯一恢复来源是最新持久化 `RunRecord`；Snapshot 保存历史安全边界，但在实现完整 reducer 前不承担 Snapshot 加尾部 Event 的回放。跨进程执行认领、Workspace 恢复和 CLI 暂停返回契约单独验收；不能把“能读 Snapshot”当作“能安全 resume”。

## 2. 模型和版本语义

| 概念 / 字段 | 含义 | 更新规则 |
| --- | --- | --- |
| `runs.last_event_sequence` | 已提交事件头，也是业务提交的 CAS 令牌 | 每个新事件 +1 |
| `RunState.version` | 现有状态迁移计数 | 保持现有 transition 语义 |
| `run_snapshots.version` | 本 Run 的 Snapshot 保存次数 | 每次成功保存 +1 |
| `approvals.version` | 审批记录修订计数 | 创建为 1；解决或授权失效 +1 |
| `schema_version` | JSON 数据格式版本 | 仅随格式演进改变 |
| `PRAGMA user_version` | 当前数据库结构版本 | 初始化时写入；发布前不提供旧开发库升级 |

Run、审批的业务更新必须产生事件，因此可以用事件头保护整份 Run 数据，不再额外引入一个含义重复的 Run 写版本。Snapshot 本身使用独立 CAS。

新增 Pydantic 数据模型；共享恢复契约放在 Runtime 层，存储输入和返回模型放在 `storage/models.py`。简单内部事务返回容器可使用 dataclass：

- `RunRecord`：`state: RunState`、`last_event_sequence: int`、`latest_snapshot_version: int`、`recovery_context: RunRecoveryContext | None`。查询 status 等字段从 state 得到，不在 Python 模型重复定义。
- `RunRecoveryContext`：版本化的持久 Workspace 描述、workspace_id、完整 WorkspaceCheckpoint、必要的策略/定价配置身份。当前 schema version 为 2；持久描述不含 environment 值，每个运行时环境变量必须映射到 `EnvironmentReference`，恢复时再由配置边界解析。
- `ApprovalRecord`：`request: ApprovalRequest`、`decision: PolicyDecision`、`resolution: ApprovalResolution | None`、`version: int`、创建/解决事件序号，以及独立的授权失效记录。
- `SnapshotEnvelope`：`schema_version`、`state`、`recovery_context`。数据库读取时执行 schema 分派和 `model_validate_json()`；不认识的版本明确报错。
- `ApprovalChange`：创建、解决或使已批准授权失效的封闭联合类型，携带期望 Approval 版本和关联事件身份。
- `SnapshotChange`：本次提交要创建的 Snapshot 身份及期望版本；其内容由目标 RunState 和目标 recovery_context 生成。
- `RunCommit`：完整目标 RunState、非空事件批次、`expected_sequence`、目标 recovery_context、Approval 变更和可选 Snapshot 变更。
- `CommitResult`：提交后的 RunRecord、已分配序列的 RunEvent、本次生成的 ApprovalRecord 和 RunSnapshot，以及是否命中审批幂等重试。幂等重试返回空事件列表和已有 ApprovalRecord。

同时扩展现有 `NewRunSnapshot` 和 `RunSnapshot`，增加可选 recovery_context 和默认 `schema_version=1`，兼容已有构造调用。Snapshot 的 state 仍复用 RunState；snapshot_id、version 和 created_at 仍由存储返回，调用方不能指定。

现有模型中的 frozen 并不冻结嵌套 list/dict。所有写 API 在返回 Awaitable 前先序列化为独立值并重新验证，不能保留调用方可变对象作为存储事实。

## 3. 四张核心表

UUID 保存为规范字符串；时间统一为 UTC ISO 8601，固定微秒精度与 `Z` 后缀；金额随 Pydantic JSON 保存 Decimal 字符串，不转为浮点数。JSON 使用 TEXT，并检查 `json_valid()`；对象结构、枚举、跨字段约束在 Pydantic/事务入口验证。以下是目标 DDL，实施时还应加入文后列出的触发器和应用层校验。

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'created', 'preparing', 'running', 'waiting_approval',
        'recovering', 'evaluating', 'succeeded', 'failed', 'cancelled'
    )),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    recovery_context_json TEXT CHECK (
        recovery_context_json IS NULL OR json_valid(recovery_context_json)
    ),
    last_event_sequence INTEGER NOT NULL DEFAULT 0
        CHECK (last_event_sequence >= 0),
    latest_snapshot_version INTEGER NOT NULL DEFAULT 0
        CHECK (latest_snapshot_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX runs_status_created ON runs(status, created_at, run_id);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    artifacts_json TEXT NOT NULL CHECK (json_valid(artifacts_json)),
    step_number INTEGER CHECK (step_number >= 1),
    span_id TEXT,
    parent_span_id TEXT,
    PRIMARY KEY (run_id, sequence),
    UNIQUE (run_id, event_id),
    CHECK (parent_span_id IS NULL OR span_id IS NOT NULL)
);

CREATE TABLE run_snapshots (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL CHECK (version >= 1),
    snapshot_id TEXT NOT NULL UNIQUE,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, version)
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    decision_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
    outcome TEXT CHECK (outcome IN (
        'approved', 'rejected', 'expired', 'invalidated'
    )),
    resolution_json TEXT CHECK (
        resolution_json IS NULL OR json_valid(resolution_json)
    ),
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    request_event_sequence INTEGER NOT NULL,
    resolution_event_sequence INTEGER,
    authorization_invalidated_event_sequence INTEGER,
    UNIQUE (run_id, decision_id),
    CHECK (expires_at > requested_at),
    CHECK (
        (outcome IS NULL AND resolution_json IS NULL
            AND resolution_event_sequence IS NULL)
        OR (outcome IS NOT NULL AND resolution_json IS NOT NULL
            AND resolution_event_sequence IS NOT NULL)
    ),
    CHECK (authorization_invalidated_event_sequence IS NULL
        OR (outcome IS NOT NULL AND outcome = 'approved')),
    FOREIGN KEY (run_id, request_event_sequence)
        REFERENCES run_events(run_id, sequence),
    FOREIGN KEY (run_id, resolution_event_sequence)
        REFERENCES run_events(run_id, sequence),
    FOREIGN KEY (run_id, authorization_invalidated_event_sequence)
        REFERENCES run_events(run_id, sequence)
);
CREATE UNIQUE INDEX approvals_one_pending_per_run
    ON approvals(run_id) WHERE outcome IS NULL;
CREATE INDEX approvals_pending_expiry
    ON approvals(expires_at, approval_id) WHERE outcome IS NULL;
CREATE INDEX approvals_run_history
    ON approvals(run_id, request_event_sequence);
```

设计约束：

- `runs.state_json` 是当前持久状态，status/时间是同事务维护的查询投影。读时验证列与 JSON 一致；终态必须具有合法 RunResult，不额外保存另一份可漂移的 result_json。
- Event 和 Snapshot 只追加，增加禁止 UPDATE/DELETE 的保护触发器；V1 无删历史或 Run 级联删除接口。
- 存储入口在同一事务中显式分配连续序列、插入事件并推进事件头。主键和唯一约束阻止重复序号；直接 SQL 写入不属于受支持的写入契约。
- Event 不保存通用 `parent_event_id`。领域关联使用 tool_call_id、approval_id、checkpoint_id、snapshot_id 等具体身份；操作层级使用 span_id 和 parent_span_id。
- Snapshot 允许创建 Run 后、第一条事件前的 sequence=0，因此不直接外键引用 Event；事务入口拒绝未来序号和版本跳跃。
- Approval 创建时验证 request、decision 的 ID、run_id、digest、policy_revision 一致且 effect=approval；解决事件必须匹配 approval_id。不可覆盖 request、decision 或已有 resolution。
- 不把 Tool Call ID 当作全 Run 唯一身份。沿用 PolicyRequest 的 `(run_id, step_number, call_index)` 定位调用，同时核对 tool_call_id 和请求摘要。
- 大型输出放 Artifact Store，事件只存有界事实和 ArtifactRef。恢复所需数据应保存在受控持久状态或 Artifact 中；不能用脱敏摘要替代工具参数后仍宣称可恢复。

### 3.1 Run Trace 与存储边界

V1 不增加独立的 Trace 表或 Trace Store。Run Trace 是按 `run_id`、Event
sequence、`span_id` 和 `parent_span_id` 查询 Run Event，并结合 Trace Span
模型形成的可观测性投影，
不是另一份权威历史。Event sequence 仍是 Run 内事实顺序的唯一依据；Trace
的时间和父子关系不能用于决定恢复位置、状态迁移或外部副作用是否已经完成。

目标关联语义如下：

- Run Trace 复用 Run 的 128-bit 身份，标准 Trace ID 由 `run_id.hex` 派生；
  Event 不重复保存 trace_id。
- STEP Span 携带明确的 step_number；MODEL 和 TOOL 属于 STEP，APPROVAL_WAIT
  属于 TOOL。Workspace 生命周期与 Evaluation 属于 RUN。
- 创建 Checkpoint 时其 Span 属于 RUN；恢复或回滚 Checkpoint 时属于 RECOVERY；
  Checkpoint 不计为 Tool Call。
- Event 不保存 `parent_event_id`。Span 使用 `parent_span_id` 表达单一父操作，
  领域因果关系继续使用有语义的业务身份。父 Span 表示逻辑归属，不强制时间包含。
- Span 时间交叠或子 Span 超出父 Span 时保留并输出 Trace，将异常留给线上诊断；
  Trace 时间质量不能阻塞 Run 主线。
- Event payload 和 Span 属性必须有界并在写入前脱敏。完整请求、响应、日志和
  Trace 导出写入 Artifact Store，数据库只保存摘要与 ArtifactRef。
- Trace Artifact 是从已提交 Event 和 Span 数据生成的派生导出，不参与
  Snapshot 校验或恢复。导出失败可以重试，不能回滚或改写已提交的 Run 事实。

当前 M1 已实现 Span Category、Trace Span、Run Trace 契约，以及 Event 上
step_number、span_id、parent_span_id 的 SQLite 往返保存。产品尚未上线且
Runtime 尚未接入该数据库，因此不维护旧开发 schema 的自动升级；版本不匹配时
明确拒绝打开并重建开发数据库。
Runtime 尚未创建 Span、传播 Span Context，也没有 Trace 导出、查询接口或
Dashboard。补齐这些能力前，只能声明 Trace 契约和关联字段可用，不能声明
端到端 Tracing 已实现。

## 4. 严格递增序列与事务入口

`expected_sequence` 明确定义为调用方观察到的当前事件头：空 Run 传 0，成功返回的首条事件 sequence=1。与现有 EventStore 签名兼容。

统一提交的逻辑：

```text
BEGIN IMMEDIATE
  读取 runs；不存在则 RunNotFoundError
  若解决审批与已有回应完全相同，返回幂等 CommitResult，不写入任何内容
  检查 last_event_sequence == expected_sequence
    不符：回滚并抛 EventSequenceConflictError(expected, actual)
  检查输入作用域、父事件、Approval 版本和 Snapshot 版本
  为事件批次分配 expected_sequence+1 .. expected_sequence+k
  依次 INSERT events
  保存目标 RunState、recovery_context、Approval 变更和可选 Snapshot
  UPDATE runs.last_event_sequence = expected_sequence+k
COMMIT
返回 CommitResult
```

SQLite 同时只有一个写事务；`BEGIN IMMEDIATE` 在读事件头之前取得写事务，竞争者等待或收到 busy。WAL 改善读写并发，不提供多个并行写者。[SQLite 事务文档](https://www.sqlite.org/lang_transaction.html)、[WAL 文档](https://www.sqlite.org/wal.html)。

两个连接同时提交 expected=7：先提交者得到 8；后提交者在获得写事务后发现 actual=8，返回冲突。跨 Run 仍受数据库写锁约束，但各自从 1 编号。不得用 AUTOINCREMENT 作为 Run 序列，也不得在事务外读取 MAX(sequence) 后插入。

批量事件使用一个事务和一次 CAS，依次插入 `N+1..N+k`，中间任意失败全部回滚。成功但响应丢失时，旧 expected 重试只能返回冲突，不能盲目追加；基础 append 不承诺幂等响应。调用方应重读已提交状态。审批利用 approval_id 和固定动作身份提供专门的幂等语义。

Runtime 和审批协调逻辑负责决定状态迁移、审批后续行为、工具结果和事件内容；SQLite 存储只负责 CAS、作用域与引用校验、序列分配和原子写入。新增窄的协调存储接口：

```python
async def create(state: RunState, *, recovery_context: RunRecoveryContext | None) -> RunRecord: ...
async def get(run_id: UUID) -> RunRecord: ...
async def commit(change: RunCommit) -> CommitResult: ...
```

`RunCommit.recovery_context` 表示提交后的完整值：传入读取到的值表示保留，传入新值表示替换，传入 None 表示清空。Checkpoint 引用、状态和事件因此可在一个事务中变化。Runtime 不传数据库连接，也不使用通用任意 SQL 回调。

`commit` 拒绝调用方主动提交的空事件批次、错 Run、非法引用及错误版本。它不尝试从任意事件 payload 推导状态或判定业务迁移是否合法；这些由受控的 Runtime/审批命令构造和行为测试保证。Approval 变化只能通过 `ApprovalChange` 表达。取消、终止与审批回应也使用该提交入口，确保 Approval、RunState、recovery_context、Event 和 Snapshot 一起成功或回滚。只有纯审计 Event 可以单独调用 EventStore.append，影响执行的事实必须调用协调入口。

首版架构是“持久状态与原子审计事件”，不声称 Event payload 已支持完整 replay。先对运行所需的核心 Event 定义版本化 Pydantic payload；当前 RunState 与事实在同一事务落盘。未来实现 reducer 后，再以 replay 后状态等于持久状态作为验收，不能直接将任意 dict 视为足够的事件溯源契约。

## 5. Snapshot 一致性与安全边界

独立 `SnapshotStore.save(snapshot, expected_version=V)` 在写事务内验证：

1. Run 存在且 state.run_id 一致；当前 Snapshot 版本等于 V。
2. 输入 event_sequence 等于当前事件头；将输入和当前持久值反序列化并通过 Pydantic 验证后，比较 state 和 recovery_context 的语义值。不得比较 JSON 文本，集合顺序差异不构成状态变化。V1 不支持事后提交任意历史状态。
3. 保存版本 V+1，并同步更新 runs.latest_snapshot_version；失败全部回滚。

`load_latest` 按 version 降序读取一条，未知 Run 抛 RunNotFoundError，存在但无 Snapshot 返回 None。允许同一事件头多次保存；不使用 event_sequence 作为 Snapshot 主键。

Runtime 的安全边界使用协调提交：先构造业务事件，再通过 `RunCommit` 同时写入 State 和可选 Snapshot。Runtime 预分配 snapshot_id，存储校验期望 Snapshot 版本并分配下一版本。Snapshot.event_sequence 使用本事务最后一个业务事件的序号；创建 Snapshot 不额外生成元事件或消耗事件序号。独立 save 同样不追加事件，避免隐藏地使调用方 CAS 过期。

“保存的状态一致”与“外部操作可以安全重启”是两个条件。进入执行中也可以保存诊断状态，但恢复必须检查未完成调用。RunRecoveryContext 应带完整 Checkpoint 身份与 Artifact 引用；创建 Checkpoint 在数据库事务之外完成，成功后再提交引用。崩溃可能留下未引用 Artifact，允许后续清理，不能出现指向尚未完成文件的已提交安全边界。

V1 恢复只读取最新原子 `RunRecord`，并校验其执行记录和 Workspace Checkpoint；Snapshot 不参与执行位置重建。Snapshot 落后于事件头不阻止恢复，例如批准事件已经提交而暂停 Snapshot 尚旧时，以 RunRecord 中的批准后状态为准。最新 RunState 损坏、Checkpoint 不匹配或存在遗留 `executing` 调用时明确停止，不自动回退旧 Snapshot 或重放操作。Snapshot 加尾部 Event 回放需要完整 reducer，作为后续独立能力。

## 6. Approval 事务及竞争规则

Approval 表保存请求与唯一回应；`outcome IS NULL` 表示未解决。批准后变更环境导致授权失效，追加 `approval_invalidated` 事件和独立失效指针，保留原 `approved` 回应。未解决时失效则记录 outcome=invalidated。

| Runtime / 审批协调命令 | 生成一个 RunCommit，其中包含 |
| --- | --- |
| 请求审批 | tool_start、approval_requested、进入 WAITING_APPROVAL 的状态、Approval 行、暂停 Snapshot |
| approve | 唯一人类 resolution、approval_resolved、RunState 审批历史；Run 仍等待 resume |
| reject / expire | resolution、拒绝 Tool Result、相关事件、清空 pending ID、终态及 Snapshot |
| cancel | 未决请求 invalidated，或使已批准授权失效；清空 pending ID、CANCELLED 和终态事件/Snapshot |
| 批准后失效 | 保留原回应，追加失效事件；恢复流程重新评估并决定是否创建新请求 |

`WAITING_APPROVAL.pending_approval_id` 表示等待继续处理的请求，不要求其 outcome 仍为空；approve 不执行工具也不将 Run 切回 RUNNING。申请新审批必须处理旧引用，单待审索引不能替代 Run 状态校验。

协调逻辑决定审批结果和 Run 后续状态，存储通过 ApprovalChange 原子写入。所有读取和更新同时限定 run_id 与 approval_id，并在事务内校验事件头及 Approval.version。审批时间采用存储事务的当前时间，不能通过回填 resolved_at 绕过过期。`now >= expires_at` 的批准提交抛 `ApprovalExpiredError` 并全部回滚，协调逻辑随后构造系统 expired 结果重新提交。认领执行时还必须再次检查期限。

幂等检查先于过期及事件头 CAS：同 approval_id、同 actor_kind/actor_id、同 outcome/reason 的重复提交返回带当前 RunRecord 和已有 ApprovalRecord 的幂等 CommitResult，不应用重试携带的目标状态、Event 或 Snapshot，也不改变时间；其他操作者或相反结果返回 ApprovalConflictError。返回原批准只证明历史已记录，不代表仍可执行。取消后迟到的新批准必须失败。

跨进程 resume 必须补充执行认领；四张表不提供外部副作用 exactly-once。后续最小增加 `tool_executions`，主键 `(run_id, step_number, call_index)`，保存 tool_call_id、decision_id、approval_id、请求摘要、执行状态、claim_token、结果及关联事件。CAS `ready → executing` 与 Run 更新、tool_execution_start 同事务提交；完成事件和结果同事务提交。

同 Run 的执行所有权还需覆盖模型请求与整个恢复过程。首版可使用同主机 Run 独占进程锁，恢复前取得并持有到暂停/终态；不能只锁数据库事务。不能仅凭 lease 到期自动重放工具：`executing` 遗留表示结果未知，停止自动执行并要求核对外部状态。若引入租约，需要 fencing 和明确的旧执行者退出机制，作为后续独立设计。

## 7. SQLite 生命周期与错误

- 初始化时先以 `BEGIN IMMEDIATE` 串行化版本检查和首次建表，提交后在 busy timeout 内重试并验证 WAL 模式；发布前只接受空库或当前 schema，旧开发库直接拒绝并要求重建。不得使用会隐式提交当前事务的 `executescript()`。每个连接启用 `foreign_keys=ON`、设置 `busy_timeout`（建议初始 5000ms）和 `synchronous=FULL`。foreign_keys 是连接级设置，不能仅初始化时执行一次。[SQLite 外键文档](https://www.sqlite.org/foreignkeys.html)。
- 数据库放在 Workspace 之外的持久目录；使用本地文件系统。WAL 要求共享数据库的进程在同一主机，不支持网络文件系统这一部署方式。[SQLite WAL 限制](https://www.sqlite.org/wal.html)。
- 使用显式事务模式，避免 Python 隐式事务与 BEGIN 重叠。异步接口将整个同步事务作为一个工作单元交给线程，在该线程中创建、使用并关闭连接，不跨线程共享连接。
- 不在事务中等待模型、工具、人工输入或 Artifact 写入。序列 CAS 冲突不透明重试业务；busy 超时与 CAS 冲突分开报告。
- asyncio 调用方取消不能保证工作线程停止。必须等待/核对事务最终结果后释放 Run 执行所有权；重新读取 Run，禁止假定取消等于回滚。数据库事务中途异常显式回滚，保持连接资源释放。
- `RunNotFoundError`、`EventSequenceConflictError`、`SnapshotVersionConflictError`、`ApprovalConflictError`、格式不支持、持久状态损坏和存储 I/O 错误分开。只有明确可翻译的约束冲突才映射业务异常，不能把所有 IntegrityError 都解释为 CAS 失败。
- 磁盘满或提交失败时禁止继续执行外部操作。若无法落盘终态，CLI 报存储错误，重启后从最后提交点检查；不能宣称失败状态已经持久化。
- `user_version` 不匹配时拒绝打开，不做自动升级或降级。产品上线并承诺持久数据兼容后，再独立设计和验收迁移链。SQLite 启动能力检查包含 JSON 函数与所用 SQL 语法；备份采用 SQLite backup API，不能只复制仍在使用的主数据库文件。
- list_events 使用 `sequence > after_sequence ORDER BY sequence ASC`，limit 必须为正数，保留现有 None=不限量契约；用户界面使用分页。已知空 Run 的 latest_sequence 返回 0，未知 Run 明确报错。

## 8. 开发拆分与验收

| 里程碑 | 开发内容 | 验收门槛 |
| --- | --- | --- |
| M1 存储基础 | 契约与版本化 payload；`storage/sqlite.py`、DDL/迁移、四表序列化、RunCommit、Event/Snapshot CAS、Approval 竞争和查询 | 真文件 SQLite 关闭重开；连续序列、独立 Run、约束、幂等审批和原子回滚测试通过。此时只声明存储可用，不声明跨进程续跑 |
| M2 可恢复执行 | 先实现 tool_executions 和 Run 独占执行，再接 Runtime、Workspace 恢复、暂停返回值、resume 和 CLI | 双 resume 只有一个执行者；未知副作用不重放；Workspace 暂停保留；故障注入通过后才声明支持安全恢复 |

建议代码落点：`storage/models.py`、`storage/store.py`、`storage/sqlite.py`、`storage/migrations/001_initial.sql`。共享 `RunRecoveryContext` 放在 Runtime 契约层。SQLite 内部可以共用连接辅助函数，无需为四张表分别建立独立连接池或通用 Repository 基类。Runtime 依赖协议，CLI bootstrap 装配数据库。

测试文件建议 `tests/test_sqlite_storage.py`、`tests/test_storage_transactions.py`，接入阶段再扩展现有 runtime/policy/bootstrap 测试。使用 pytest、真实临时数据库、独立连接和进程同步屏障；不 mock sqlite 内部方法。

必须覆盖：

1. 同 Run 两连接携带相同 expected，只一个成功；竞争方重读后可继续形成 1..N；不同 Run 独立编号。
2. 批量写入中途约束失败后事件、事件头、审批和状态全部回滚，下一次提交复用未提交序号。
3. 子进程在 COMMIT 前被终止：重开无半笔数据；COMMIT 后、响应前终止：数据完整且重试不重复审批。
4. 非法 Span 层级、未来 Snapshot、Snapshot 状态不匹配、错误 Snapshot 版本、修改历史记录均被拒绝。
5. approve/reject、approve/expire、approve/cancel 竞争符合状态机；批准后取消保留历史批准并阻止执行。
6. 审批重复提交返回原回应；执行前过期、策略/参数/Workspace 改变均不复用授权。
7. 同 Step 第二个调用暂停，恢复不重放第一个调用；保留失败统计、消息、usage 与调用位置。
8. executing 后中断不自动重放；双 resume 不重复模型请求或工具副作用。
9. UTC/UUID/Decimal/集合字段重开后语义一致；不认识 schema、损坏 JSON 与外键断裂有明确错误。
10. 事务线程运行时取消调用方，提交结果被核对；存储故障不触发工具执行。
11. 批准事件已提交而暂停 Snapshot 尚旧时，从最新 RunRecord 恢复批准后的状态，不回放旧调用。
12. recovery_context 更新失败时，Event、RunState、Approval 和 Snapshot 全部回滚。
13. 相同集合以不同顺序序列化时，Snapshot 语义校验仍通过。
14. CommitResult 返回最终事件头和已分配序列的 Event，可直接驱动下一次 CAS。
15. 最新 RunState 损坏、Checkpoint 不匹配或存在结果未知的 executing 调用时明确停止，不回退旧 Snapshot。
16. 相同审批回应在事件头已推进后重试仍返回幂等结果，不应用重试携带的状态或事件；相反回应返回冲突。

按里程碑运行目标测试和 `ty check`。纯设计文档阶段不运行 Runtime 测试。M1 完成代表存储能力可用；只有 M2 和故障注入通过，才能对外声明持久审批及安全恢复。
