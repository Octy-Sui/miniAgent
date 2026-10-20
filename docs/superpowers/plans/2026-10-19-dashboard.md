# Read-only Trace Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付本机只读 Run 列表、Run 详情、Workspace 三页面，能够解释已持久化事实、来源、补丁、评测证据和费用，并可将本计划逐项交给低阶模型执行。

**Architecture:** 独立 Dashboard 包直接读取现有 SQLite 与登记 Artifact，查询负责短只读事务和有界加载，纯投影负责事实解释，统一脱敏后才交给 Jinja。FastAPI 只编排读取和渲染；重内容请求从读取到解码、投影、脱敏均在最多两个实际工作任务的名额内完成。页面边界与用途隔离的签名游标防止跨请求混入新状态。

**Tech Stack:** Python 3.13.x、SQLite、Pydantic 2、dataclass、Typer、FastAPI、Uvicorn、Jinja、随包 HTMX/CSS/JavaScript；pytest、httpx、Playwright 仅用于测试。

**Spec:** [功能设计 D](../specs/2026-10-14-dashboard-design.md)、[架构设计 A](../specs/2026-10-14-dashboard-architecture-design.md)、[测试分析 TA](../specs/2026-10-14-dashboard-test-analysis.md)、[测试设计 TD](../specs/2026-10-14-dashboard-test-design.md)，均为 2026-10-19 工作区版本。另读取 [测试审计](../specs/2026-10-14-dashboard-test-strategy-audit.md) F1–F5；TA/TD 已补设计，尚未独立复核，不能称审计已关闭。代码核对基线：`e228f70`。

## Global Constraints

- “中文界面，状态、事件类型、模型名保留代码原值以便检索。”
- “单用户 loopback 访问、独立 `miniagent-dashboard` 入口和 optional extra”；固定 `127.0.0.1`、单 worker、不默认开浏览器。
- “首版不增加 React/Vue 应用或 Node 构建链”；HTMX/CSS/JS 随包分发，不依赖 CDN。
- “当前数据库 `PRAGMA user_version=2`；Run/Event/Snapshot schema 为 2，Artifact manifest 为 1”；摘要模板 1，报告仅支持 `harness_version=5.0.2`。
- “不调用 initialize，不创建目录或空 DB，不执行 DDL/写入，不设置 journal mode，不用 immutable。”
- 不实例化现有 CLI Settings，不读取 `.env`、Provider 凭证、模型 limits/pricing；不装配 Provider、Workspace Backend、Evaluator 或 SQLiteRunStore。
- “Run Status、Evaluation 结论与类型、Artifact 可用性”独立保留；缺失、未记录、未配置、未加载、冲突不得相互替代。
- “累计模型成本（USD）”；“已记录费用，完整性未证明”；使用持久化 Decimal，不按当前价格重算。
- 评测通过标题“已记录独立评测通过”，并显示“目标测试清单未持久化，无法复核完整覆盖”；不从 report 反推目标集合。
- “根目录以下拒绝全部 symlink”；同 FD fstat/hash/read；manifest/content 仅普通文件，FIFO 打开不得阻塞。
- “先完整脱敏再截短/分页”；无原文下载、跨请求内容缓存、通用 Markdown 渲染器或任意 `safe` HTML。
- 固定限制：busy **1 秒**；单读取单元 SQL/扫描/解析 **5 秒**；state/Snapshot **20 MiB**；Event payload/artifacts/recovery context/manifest **1 MiB**；单请求结构化原文合计 **64 MiB**。
- 固定限制：列表 task **128 KiB**、其他展示标量 **4 KiB**；Artifact **20 MiB**；读取块 **64 KiB**；预览 **256 KiB 且 2,000 行**；metadata 页 **100**；来源定位前后各 **20**。
- 固定限制：Span 父链 **64**、scope **1,000**、压缩版本链 **32**、JSON 深度 **64**；重内容任务 **2**；编码游标 **4 KiB**、有效期 **30 分钟**。
- Run 列表默认 **25**，允许 **10/25/50/100**；Event 默认 **100**、最多 **200**；task 搜索最长 **200 字符**。
- 不实现 Compare、Checkpoint、Recoverable、resume、恢复工作区、审批操作、任务执行、完整请求级模型记录、外部观测平台或新增生产者证据。
- 遵循根 `AGENTS.md`：不改 README；Python 用类型注解；外部结构 Pydantic，内部容器 frozen dataclass；仅格式化变更文件。

## Review Focus

1. 当前页损坏行和超限正文仍须保留可证明身份与状态，不能退化为空结果、零 usage 或无摘要：Tasks 4、5、8 的 `test_oversized_run_preserves_envelope`。
2. 合法 hash 的评测 report 使用字符串/数字布尔值时必须在消费入口被拒绝，不能被完整性失败掩盖：Tasks 14、20 的 `test_report_type_variants_reach_workspace`。
3. manifest 虚报小 size、实际文件超过 20 MiB 时必须在首次内容 read/hash/mmap 前退出：Tasks 12、23 的 `test_actual_oversize_reads_no_content`。
4. 取消 HTTP 不代表后台线程停止，旧页面响应也不能覆盖新边界：Tasks 17、21、24 分别固定名额与页面代次。
5. 超限来源与不存在来源、扫描未完成与唯一 patch、字符串内括号与实际 JSON 层级必须区分：Tasks 1、7、10、11、16 各有正反对照。

---

## 执行范围与交接规则

这是一个共享页面边界和证据链的只读子系统，保留一个计划，不将查询、Artifact 和页面拆成可独立宣布完成的产品。每个 Task 交付可测试、可独立审查的内部能力；只有 Task 26 满足整体 DoD。默认按编号执行，依赖列给出可提前准备的任务；本轮仅写计划，不执行实现、安装、测试或提交。

工作区已有 staged/unstaged 设计文档与 Resume 计划，均为用户现有工作。执行时先读 `git status --short`，只提交本 Task 文件，不能 `git add .`、重置或顺带提交这些文档。Resume 计划拟将 schema 升至 v3，而当前代码仍为 v2；如果执行时已落地 Resume 或生产者格式变化，先报告实际差异并修订本计划的版本、SQL 与 fixture，不能给 Dashboard 偷加旧格式兼容层或改写库。

交给执行模型的最小上下文：根 AGENTS.md、本计划头部/本节/公共契约、所属 Task、其依赖 Task 的 Interfaces、对应 D/A 章节和完整 TD 用例行。每次只实现一个 Task；一个任务内按具名测试/variant 小循环 RED→GREEN，不一次实现后续任务。测试片段是应补入指定函数的断言核心，setup 使用本 Task 明确的 fixture；不是可复制运行的完整文件。

缺模块/ImportError 不算 RED。先建立可导入、签名正确的最小接口，再观察指定行为断言失败；不要为了 RED 破坏已经成立的既有行为。Task 的多个具名反例按一个反例一个循环增加，禁止“先实现整个包再补测试”。测试数据与期望不调用被测投影/脱敏/排序函数生成；坏数据在持久化边界构造，不 patch 内部查询、codec、hash 或安全打开逻辑。

每项 Step 4 先执行以下收尾，再运行该项定向测试；`<changed-python-files>` 必须替换为实际路径：

```bash
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run ruff check --fix <changed-python-files>
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run ruff format <changed-python-files>
UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run ty check
```

任务中的 pytest 命令统一加 `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache`。Ruff/ty 若当前环境未装，使用仓库已配置的开发工具或登记确切缺依赖；不要把工具缺失当产品 RED。依赖Web的测试模块在导入app前`pytest.importorskip('fastapi')`，conftest中的Web导入放fixture内部，避免原最小安装pytest collection因optional extra缺失而崩溃；纯查询/投影测试不跳过。浏览器/OS设施不可用的variant记录DEFER，明确extra验收时这些skip不能算PASS。每项提交前按 AGENTS.md 检查 critical：未处理异常、逻辑错误、接口不一致；critical 修正后才提交，minor 记录。不加 AI attribution。每个提交步骤先`git add`该Task Files中实际新增/修改的逐个路径，再执行给定commit；绝不添加整个工作区。计划中的 commit 命令仅在该 Task 验证结束后执行。

## 本计划固定的局部接口决定

下表是供本次计划审阅的实施决定，不修改已确认范围或输入设计文件。它们补齐 TD 局部 Q；计划确认后，低阶模型按表执行，不能自行选择另一种语义。

| TD 项 | 本计划决定与验证归属 |
| --- | --- |
| Q-1 | UTC aware 时间；`issued_at <= now < expires_at` 有效，未来签发不容忍；寿命固定 1,800 秒。相等到期拒绝。Task 3。 |
| Q-2 | 页面美元金额固定 6 位小数，`Decimal.quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN)`；原 Decimal 保真供详情查看，不经过 float。Task 8。 |
| Q-3 | 可同时保留多个问题；总标题有明确矛盾时为“证据冲突”，同时逐项显示“证据不完整”；不丢任一事实。Task 15。 |
| Q-4 | SQLite 注入标准 `sqlite3.connect` 兼容 callable；Reader 注入标准 `os.open` 兼容 callable；时钟与工作调度使用显式依赖。测试委托真实操作、记录/握手，不替换结果。G-STAGE 用线程 profile 记录真实公开函数及 hash C 调用；Task 24 必须证明观测设施确实命中预期阶段。 |
| Q-5 | Task 24 先登记实测环境、monotonic 分辨率、空载握手开销，独立选择并记录 ε；监督上限固定为测试隔离进程启动后 15 秒。超出监督上限记录 FAIL 后清理，不能靠加大 ε 凑绿。精确 1/5 秒策略另用受控时钟验证；真实计时不可用的部分保留 DEFER。 |
| Q-6 | 首轮验证记录实际 macOS/Python/SQLite/Playwright Chromium 版本；Linux/其他浏览器未实测不作兼容承诺。Task 26 指定执行者负责人工观察记录，不虚构负责人/已完成结果。 |
| Q-7 | Event `limit` 为 1–200；Snapshot `start` 默认 0、`end` 缺省为 `min(start+100, message_count)`，半开 `[start,end)`；负数、反向、越末尾 422。空 Snapshot 仅 `(0,0)` 合法，非空 Snapshot 空区间拒绝；每次最多 100 条消息，字节预算仍适用。Task 5。 |
| Q-8 | 路径/类型/身份先验证；安全打开后实际或登记 size 超 20 MiB 先返回 200 元信息+未校验，随后才比较限额内 size 不符并返回 409。即使二者并存，实际超限仍须零内容读取。Tasks 12、13。 |
| Q-9 | 每批≤200 Event 不变；父类别仍沿用现有有向有限层级。异常 envelope 可测上限，可达性不成立就记录不可达证明，不以跳过冒充上限通过。Task 7。 |
| Q-10 | SQL/busy/重任务名额耗尽为 503；目录发现或 diff/来源遍历能保留有效部分时为 200+未完成，禁止默认 patch/完整结论；无安全可展示内容的文件校验超时为 503。共享截止不重置。Tasks 2、11–13、16。 |
| 深度/offset/隐藏页 | JSON 根容器深度 1，标量根 0，仅对象/数组入栈；直接提交 UTF-8 字符中间 offset 为 422。隐藏页允许同代次在途请求完成，不再发新自动请求；恢复可见后按保留间隔等待，非立即补发。Tasks 1、13、21。 |

## 文件结构与责任

以下生产路径均以 `src/miniagent/dashboard/` 为前缀。查询拆成小包以避免单个 queries.py 同时维护连接、列表与来源；保持 A§3 依赖方向，不新增 service/repository 基类。

| 文件 | 责任 / 所属 Task |
| --- | --- |
| `__init__.py`, `config.py` | 空导出面、独立配置与固定限额；1 |
| `json_content.py`, `redaction.py` | 深度/重复键/安全解析；文本与结构化统一脱敏；1 |
| `reads.py` | 只供查询和纯投影使用的原始读取容器；2 起按消费增量添加 |
| `views.py` | 脱敏展示 dataclass、受控来源链接；8 起增量添加 |
| `queries/__init__.py`, `queries/connection.py` | 只读连接、显式事务、schema、共享请求预算；2 |
| `parameters.py`, `cursors.py` | 外部参数模型、规范化筛选、用途隔离 HMAC；3 |
| `queries/runs.py` | 列表标量查询与一致详情首屏；4、5 |
| `queries/events.py`, `queries/snapshots.py` | 有界事件/精确来源/消息范围/scope/摘要来源查询；5–7、10 |
| `projections/events.py`, `projections/spans.py` | Event/Tool/Step、页面 scope；6、7 |
| `projections/run.py`, `projections/compaction.py`, `projections/snapshots.py` | Run/usage、摘要完整性/来源/尝试、限定消息范围的安全来源字段；8–10、19 |
| `artifacts.py` | 目录发现、manifest、同 FD 内容验证；11、12 |
| `previews.py` | 已验证内容的完整脱敏与 UTF-8 分段；13 |
| `evaluation_evidence.py`, `projections/evaluation.py` | 外部证据形状与纯评测链；14、15 |
| `projections/workspace.py` | diff 结构、patch 选择与清单对照；16 |
| `workers.py` | 两个实际重任务的生命周期，取消/关闭回收；17 |
| `app.py`, `routes.py`, `pages.py`, `presentation.py` | 应用、参数/HTTP、页面读取编排、脱敏到模板的唯一边界；18–20 |
| `templates/base.html`, `runs.html`, `run.html`, `workspace.html`, `source.html`, `error.html`, `partials/*.html` | 完整页与片段；18–20 |
| `static/dashboard.css`, `dashboard.js`, `vendor/htmx.min.js`, `vendor/htmx.LICENSE` | 中文可访问界面、轮询代次、本地 HTMX；18、21 |
| `cli.py` | 延迟 Web 导入的独立 Typer 入口；22 |
| `pyproject.toml`, `uv.lock`（仓库根） | extra、script、测试依赖、实际锁定；18、21、22 |
| `tests/dashboard/conftest.py`, `builders.py`, `observation.py` | 合成数据、真实边界委托、RO 后置条件；随所属任务建立，不预建空框架 |
| `tests/dashboard/test_*.py`, `tests/acceptance/dashboard/test_*.py` | 组件/集成与真实 OS、安装、浏览器证据；见各 Task |

除上表外不修改 Runtime、storage schema、Artifact producer、Evaluator、旧 CLI 的接口。Python 包目录 `projections/`、`queries/` 的 `__init__.py` 只在首次需要时添加。

## 公共契约字典

本节名称、字段、类型为下游对接依据。只在所属 Task 创建被消费的类型；没有使用者不提前建模。`JsonValue` 来自 Pydantic；所有 `datetime` 必须 aware UTC；原始容器不得传入模板。

| Task / 文件 | 固定契约 |
| --- | --- |
| 1 / config | `DashboardSettings(BaseModel)`：`database: Path=Path.home()/'.miniagent/runs.sqlite3'`、`artifacts: Path=Path.home()/'.miniagent/artifacts'`、`port: int=8080`（1–65535）；extra forbid，不继承 BaseSettings。`ReadLimits` frozen dataclass 保存 Global Constraints 数值，默认实例 `LIMITS`。 |
| 1 / JSON | `parse_json(content: str\|bytes, *, budget: ReadBudget\|None=None) -> JsonValue`：词法深度预检、重复键拒绝后标准 JSON 解码；拒绝 NaN/Infinity。`ContentError(code: str)` 只含受控 code。`redact_text(text: str) -> str`；`redact_json(value: JsonValue, *, schema: Literal['run_usage','event','summary','metadata','unknown']='unknown') -> JsonValue`。 |
| 2 / reads | `ReadIssue(code:str,message:str,subject_id:UUID\|None=None,field_path:tuple[str\|int,...]=())` frozen dataclass，message 为固定中文说明，后两项定位证据/安全字段位置，不带原始值。`ReadBudget(deadline:float,remaining_bytes:int=67108864,monotonic:Callable[[],float]=time.monotonic)`，方法 `check()->None`、`reserve(size:int)->bool`；拒绝扣费不改变余额。`ReadFailure(status_code:int,code:str)` 控制 HTTP 边界，不装原始异常文本。 |
| 2 / connection | `read_transaction(database:Path, budget:ReadBudget, *, connect:ConnectionFactory=sqlite3.connect) -> ContextManager[sqlite3.Connection]`；`validate_database(database:Path, *, connect:ConnectionFactory=sqlite3.connect)->None`。`ConnectionFactory` 兼容 sqlite3.connect 的参数，返回真实 Connection；不开跨线程连接。 |
| 3 / parameters | `RunFilters(statuses: tuple[RunStatus,...]=(), model:str\|None=None, workspace:Literal['local','docker','unknown']\|None=None, created_from:datetime\|None=None, created_to:datetime\|None=None, run_id:UUID\|None=None, task:str\|None=None)`；`RunListParams(filters:RunFilters, page_size:Literal[10,25,50,100]=25, cursor:str\|None=None)`。`EventFilters(types:tuple[RunEventType,...]=(), step:int\|None=None, tool_name:str\|None=None, failures_only:bool=False)`；`EventParams(filters:EventFilters, limit:int=100, cursor:str\|None=None, boundary:str\|None=None, event_id:UUID\|None=None, grouped:bool=False)`。所有外部模型 extra forbid，step≥1。 |
| 3 / cursors | `PageBoundary(BaseModel)`：`run_id:UUID, as_of_sequence:int, latest_snapshot_version:int, run_status:RunStatus, read_at:datetime`。上界≥0；不是整个历史 RunState。 |
| 3 / cursors | 各 cursor 都有 `version:Literal[1]=1, issued_at:datetime, expires_at:datetime`。`RunPosition(created_at:datetime, run_id:UUID)`；`RunListCursor(purpose='runs', filter_hash:str, position:RunPosition)`；`EventCursor(purpose='events', filter_hash:str, boundary:PageBoundary, after_sequence:int)`；`SourceCursor(purpose='source', boundary:PageBoundary)`；`ManifestCursor(purpose='manifests', boundary:PageBoundary, after_id:UUID)`；`PreviewCursor(purpose='preview', boundary:PageBoundary, artifact_id:UUID, sha256:str, redaction_version:Literal[1]=1, offset:int)`。purpose 均 Literal。 |
| 3 / cursors | `CursorSigner(key:bytes, *, now:Callable[[],datetime])`；`encode(cursor:DashboardCursor)->str`、`decode(token:str, *, purpose:str)->DashboardCursor`；`DashboardCursor` 是上行 Pydantic discriminated union。`filter_digest(filters:RunFilters\|EventFilters)->str`；规范化后 SHA-256，不存搜索词。参数/绑定不合法统一 `ReadFailure(422,...)`。 |
| 4–5 / reads | `RunEnvelope(run_id:UUID, schema_version:int, status:RunStatus, created_at:datetime, updated_at:datetime, finished_at:datetime\|None, last_event_sequence:int, latest_snapshot_version:int)`；`StoredRun(envelope:RunEnvelope, record:RunRecord\|None, workspace_kind:Literal['local','docker','unknown'], issues:tuple[ReadIssue,...])`。recovery 坏/超限单独问题，不能使合法 state 消失。 |
| 4 / reads | `RunListItem(envelope:RunEnvelope, task:str\|None, model:str\|None, workspace_kind:str, reason:str\|None, active_duration_ms:int\|None, cost_usd:Decimal\|None, issues:tuple[ReadIssue,...])`；`RunListRead(items:tuple[RunListItem,...], total:int, counts:dict[str,int], next_position:RunPosition\|None, read_at:datetime)`；task/model 等此时为有界原始值。 |
| 5 / reads | `StoredEvent(event_id:UUID, run_id:UUID, sequence:int, type:str, occurred_at:datetime, step_number:int\|None, span_id:str\|None, parent_span_id:str\|None, event:RunEvent\|None, issues:tuple[ReadIssue,...])`；`EventBatch(items:tuple[StoredEvent,...], next_sequence:int\|None)`。`StoredSnapshot(snapshot_id:UUID, run_id:UUID, version:int, event_sequence:int, snapshot:RunSnapshot\|None, issues:tuple[ReadIssue,...])`。 |
| 5 / reads | `RunPageRead(run:StoredRun,boundary:PageBoundary,events:EventBatch,evaluation_event:StoredEvent\|None=None)`；evaluation_event为同事务上界内最后一条EvaluationCompleted，只表示已记录事实。`SnapshotSlice(snapshot:StoredSnapshot,start:int,end:int,message_count:int\|None,messages:tuple[Message,...])`。仅 messages 范围进入来源页面投影。 |
| 6 / reads, views | `ToolFacts(events:tuple[StoredEvent,...],counts:dict[tuple[int,str,str],int],issues:tuple[ReadIssue,...])`，counts键是step/call_id/event_type。`EventView(event_id:UUID,sequence:int,type:str,step_number:int\|None,summary:str,payload:JsonValue\|None,issues:tuple[ReadIssue,...])`；`ToolView(run_id:UUID,step_number:int,tool_call_id:str,status:str\|None,completeness:str\|None,exit_code:int\|None,issues:tuple[ReadIssue,...])`。字符串/payload 字段均已脱敏。 |
| 7 / reads, views | `ScopeFacts(span_id:str, events:tuple[StoredEvent,...], start_count:int, end_count:int, loaded:bool)`；`ScopeRead(scopes:dict[str,ScopeFacts], complete:bool, issues:tuple[ReadIssue,...])`。`SpanView(span_id:str, category:str, state:str, duration_ms:int\|None, start_event_id:UUID\|None, end_event_id:UUID\|None, issues:tuple[ReadIssue,...])`；state 为配对结果，不是 RunStatus。 |
| 8 / views | `SourceTarget(run_id:UUID,event_id:UUID\|None=None,snapshot_version:int\|None=None,start:int\|None=None,end:int\|None=None)` frozen dataclass，Event与Snapshot二选一，构造前已校验归属/范围。`FieldView(key:str,label:str,value:str,source_label:str,source:SourceTarget\|None=None)`；`RunView(run_id:UUID,status:str,terminal:bool,identity:tuple[FieldView,...],result:tuple[FieldView,...],usage:tuple[FieldView,...],budgets:tuple[FieldView,...],issues:tuple[ReadIssue,...])`。稳定 key 供测试，不把中文显示文本反解析成类型。 |
| 9–10 / reads, views | `CompactionRead(events:tuple[StoredEvent,...],snapshots:dict[int,StoredSnapshot],source_events:dict[UUID,StoredEvent],unloaded_versions:tuple[int,...],unloaded_event_ids:tuple[UUID,...],issues:tuple[ReadIssue,...])`。`EvidenceView(claim:str,state:Literal['verified','inconsistent','not_loaded'],source_label:str,source:SourceTarget\|None,issues:tuple[ReadIssue,...])`；`MemoryView(version:int\|None,fields:tuple[FieldView,...],evidence:tuple[EvidenceView,...],groups:tuple[CompactionGroupView,...],issues:tuple[ReadIssue,...])`。 |
| 9 / views | `CompactionAttemptView(attempt:int, cost_usd:Decimal\|None, fields:tuple[FieldView,...], issues:tuple[ReadIssue,...])`；`CompactionGroupView(first_requested_sequence:int\|None, attempts:tuple[CompactionAttemptView,...], total_cost_usd:Decimal\|None, issues:tuple[ReadIssue,...])`。成本 None 表示不确定，不补 0。 |
| 11–12 / reads | `ManifestItem(artifact_id:UUID, record:ArtifactRecord\|None, issues:tuple[ReadIssue,...])`；`ManifestPage(items:tuple[ManifestItem,...], next_id:UUID\|None, read_at:datetime, complete:bool)`；complete 指本次目录发现是否穷尽，出现下一页本身不表示扫描未完成。`PatchDiscovery(items:tuple[ManifestItem,...], complete:bool, issues:tuple[ReadIssue,...])`。 |
| 11–12 / artifacts | `ArtifactReader(root:Path, *, open_fd:OpenFd=os.open)`；`list_manifests(run_id:UUID, *, after_id:UUID\|None, budget:ReadBudget)->ManifestPage`、`read_manifest(run_id:UUID, artifact_id:UUID, *, budget:ReadBudget)->ManifestItem`、`discover_patches(run_id:UUID, *, budget:ReadBudget)->PatchDiscovery`、`read_content(run_id:UUID, artifact_id:UUID, *, expected:ArtifactRef\|None=None, budget:ReadBudget)->VerifiedContent`。OpenFd 兼容 `os.open(path, flags, mode=..., *, dir_fd=...)`；返回真实 FD，Reader 自己关闭。 |
| 12–13 / reads | `VerifiedContent(record:ArtifactRecord, content:bytes\|None, read_at:datetime, issues:tuple[ReadIssue,...])`；content None 不得参与证据判断。`PreviewView(artifact_id:UUID, fields:tuple[FieldView,...], text:str\|None, offset:int, next_offset:int\|None, inline_continuation:bool, read_at:datetime, issues:tuple[ReadIssue,...])`，全为脱敏值，无 URI。 |
| 14 / evaluation_evidence | `EvaluationConfig`、`EvaluationSummary` 为当前 producer 必要字段的 Pydantic 模型，字段清单见 Task 14；`EvaluationEvidence(configs:dict[UUID,EvaluationConfig], summaries:dict[UUID,EvaluationSummary], reports:dict[UUID,InstanceReport], contents:dict[UUID,VerifiedContent], issues:tuple[ReadIssue,...])`。键均 Artifact ID。 |
| 15 / views | `EvaluationAttemptView(attempt_id:UUID\|None, outcome:Literal['recorded_pass','conflict','incomplete','unsupported','recorded_failure'], fields:tuple[FieldView,...], artifact_ids:tuple[UUID,...], issues:tuple[ReadIssue,...])`。 |
| 16 / views | `DiffFileView(old_path:str\|None, new_path:str\|None, kind:str, text:str)`；`DiffView(files:tuple[DiffFileView,...], complete:bool, issues:tuple[ReadIssue,...])`；`PatchChoice(artifact_id:UUID\|None, reason:str, issues:tuple[ReadIssue,...])`。 |
| 17 / workers | `HeavyWorkPool(limit:int=2)`；`async run[T](operation:Callable[[],T])->T`；`async aclose()->None`。满额立即 `ReadFailure(503,'heavy_work_busy')`；实际线程任务完成时释放名额，shield 保持取消不误释。 |
| 18 / app | `create_app(settings:DashboardSettings,*,connect:ConnectionFactory=sqlite3.connect,open_fd:OpenFd=os.open,now:Callable[[],datetime]=utc_now,monotonic:Callable[[],float]=time.monotonic,work_pool:HeavyWorkPool\|None=None)->FastAPI`；None创建默认2名额pool。密钥进程内随机生成，不提供 CLI 密钥选项。测试游标签名向量直接测 CursorSigner；ASGI 用响应令牌或 app.state.signer 获取合法边界，不用伪造产品入口。 |

`ReadBudget` 类型注解可用 TYPE_CHECKING和`from __future__ import annotations` 避免 Task 1 的 parser 与 Task 2 的读取结构循环依赖；Task 1 parser 不得导入 queries。Task 8 起的展示字段只存脱敏值，纯投影如需比较身份/hash先用 reads 原始容器完成，再调用统一 redaction。`presentation.py` 只接收 views，负责已校验相对 href/签名参数；不允许模板拿 `record.state`、`ArtifactRecord` 或任意异常对象。

## 任务依赖与交付

| 阶段 | Tasks | 可审查产物 |
| --- | --- | --- |
| 只读与参数基础 | 1–5 | 安全 JSON、短事务、签名边界、列表、一致首屏与来源 |
| 执行事实投影 | 6–10 | Event/Tool/Span、usage、摘要完整性与内容证据 |
| 持久化文件证据 | 11–16 | manifest、FD 内容、脱敏分页、评测、diff/patch |
| 页面与执行治理 | 17–22 | 重任务生命周期、三页面、刷新、独立 CLI/打包 |
| 真实边界与组装验收 | 23–26 | 文件竞争、SQL/线程/取消、HTTP治理、单条跨页旅程 |

Tasks 6/8 在 5 后可独立实现；11 在 2 后可准备，14 在 1/12 后可实现；17 依赖 2 的预算错误契约。共享 reads.py/views.py/pyproject 的任务默认顺序修改，不能同时编辑。Task 23/24 的监督工具可在接口固定后提前准备，不能提前宣称验收通过。

### Task 1: 固定配置、JSON 安全解析与统一脱敏

**Files:** Create `src/miniagent/dashboard/{__init__,config,json_content,redaction}.py`；Test `tests/dashboard/test_content.py`、`tests/dashboard/test_config.py`。

**Interfaces:** Consumes 现有 Pydantic `JsonValue`；Produces 公共字典 Task 1 的全部符号，`REDACTION_VERSION=1`，固定脱敏占位 `[已脱敏]`。

- [ ] **Step 1: 写行为测试。** `test_json_strings_do_not_increase_depth` 分别用 TC-U9-10/11/12 三份精确浅层输入，断言叶文本保留、secret 遮盖、无 depth 问题。`test_json_limits_and_duplicates` 承接 B18 五值、顶层/嵌套重复键、NaN；`test_redaction_preserves_only_usage_counts` 承接 TC-U13-03/04/05。

  ```python
  assert redact_json({"token": 123456789}) == {"token": "[已脱敏]"}
  assert redact_json({"input_tokens": 100}, schema="run_usage")["input_tokens"] == 100
  assert parse_json('{"message":"' + '[' * 65 + ']' * 65 + '"}') == {"message": '[' * 65 + ']' * 65}
  ```

  键匹配大小写不敏感，`authorization/api_key/token/secret/password` 及 `_`/`-` 变体遮值；不能只做 substring 把所有 token 统计删掉。usage 白名单仅 RunUsage/ModelUsage 明确统计字段；未知结构同名键不豁免。文本覆盖合成 `sk-`/`sk-proj-`/`ghp_`/`github_pat_`/`AKIA` 前缀、Bearer/Authorization 行、敏感赋值、完整 PEM 私钥块，保留普通文字对照。
- [ ] **Step 2: 运行 RED。** `uv run pytest tests/dashboard/test_content.py tests/dashboard/test_config.py -q`；预期上述行为断言失败，不能只记录模块不存在。
- [ ] **Step 3: 实现固定接口。** config 不读环境；JSON 单遍词法扫描追踪字符串/escape 与容器栈，深度检查在 json.loads 前，object_pairs_hook 拒绝重复键；循环每 64 KiB 检查可选 budget。脱敏处理完整文本，结构化遍历只接收已通过深度门禁的值。统计白名单为RunUsage/ModelUsage现有整数计数：schema=run_usage的根路径；schema=event且type=compaction_attempt_recorded时`result.usage.*`，completed/failed时`attempts[*].usage.*`；其他路径的token类敏感键不豁免。此处不引入 Web 依赖。
- [ ] **Step 4: Ruff/ty 后运行 Step 2 命令，预期 PASS。** 另检查默认路径及 port 0/1/65535/65536（TC-U10-22 参数语义）。
- [ ] **Step 5: 审查并提交。** `git add src/miniagent/dashboard tests/dashboard/test_content.py tests/dashboard/test_config.py`，`git commit -m "feat(cli): add bounded dashboard content handling"`。此时 dashboard 目录仅含本任务文件；后续 add 必须逐路径。

### Task 2: 只读连接、结构检查与主动读取预算

**Files:** Create `src/miniagent/dashboard/reads.py`、`queries/{__init__,connection}.py`；Test `tests/dashboard/test_connection.py`；Create `tests/__init__.py`、`tests/dashboard/__init__.py`（仅空包标记）、`tests/dashboard/{conftest,builders,observation}.py` 中本任务所需内容。后续跨目录测试通过`tests.dashboard.*`显式复用支持，不复制fixture或依赖pytest碰巧设置的搜索路径。

**Interfaces:** Consumes `DashboardSettings`/`LIMITS`；Produces `ReadIssue`、`ReadBudget`、`ReadFailure`、`ConnectionFactory`、`read_transaction`、`validate_database`。仅本模块直接创建 SQLite 连接。

- [ ] **Step 1: 写 `test_missing_database_is_never_created`、`test_read_transaction_is_read_only`、`test_deadline_interrupts_real_sql`。** 使用生产 SQLiteRunStore 只在 fixture 准备阶段建库，关闭后读；SQL observer 委托真实 sqlite3.connect，trace 记录 BEGIN/SELECT/PRAGMA，不能把初始化阶段算产品语句。

  ```python
  assert not missing_db.exists()
  assert not missing_db.parent.exists()
  assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
  assert after.logical_rows == before.logical_rows
  assert after.schema == before.schema
  ```

  缺版本/表/必需列分别拒绝，单行 schema 错不全库扫描；文件名含空格、`?`、`#`、中文仍能读。预算测试执行真实递归 SELECT：独立受控 monotonic 跨截止后 SQLite progress handler 中断，捕获为 503，不由外层 async timeout 结束。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_connection.py -q`；预期缺路径保护、只读/预算行为失败。
- [ ] **Step 3: 实现签名。** 现存 Path 正确转 file URI+mode=ro，timeout=1.0，query_only=ON，row_factory=Row，显式 BEGIN；finally rollback/close，同线程开用关。截止 `now>=deadline`；progress handler 每 1,000 VM 指令检查共享 budget，退出前清除 handler。只翻译 busy/locked/interrupted/结构错误，物理损坏与未知 OSError 保留到页面错误边界。validate_database 校验 user_version=2、三张必需表列、JSON 能力，不读审批或扫描历史。
- [ ] **Step 4: Ruff/ty 后 `uv run pytest tests/dashboard/test_connection.py -q`，预期 PASS。** observation 定义测试内部`ReadOnlySnapshot(logical_rows:dict[str,tuple[tuple[object,...],...]],schema:tuple[tuple[object,...],...],artifacts:dict[str,str])` frozen dataclass和`capture_read_only(database:Path,artifact_root:Path)->ReadOnlySnapshot`。logical_rows包含全部业务表稳定有序内容，schema包含sqlite_master与user_version，artifacts记录全部相对路径（含目录标记）和文件SHA-256；前后比较仅测试准备/后置阶段读取。测试片段的before/after都用此容器，不比较WAL/SHM bytes。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): add read-only dashboard database sessions"`。

### Task 3: 参数、页面边界与用途隔离签名游标

**Files:** Create `src/miniagent/dashboard/{parameters,cursors}.py`；Test `tests/dashboard/test_cursors.py`、`test_parameters.py`。

**Interfaces:** Consumes `ReadFailure`；Produces 公共字典 Task 3 符号；额外 `SnapshotParams(boundary:str,start:int=0,end:int|None=None)`、`ArtifactParams(boundary:str,cursor:str|None=None,offset:int|None=None)`、`WorkspaceParams(boundary:str|None=None,cursor:str|None=None,patch:UUID|None=None)`。Artifact无cursor/offset时从0开始；有cursor时以其offset为准，显式offset若不等则422。

- [ ] **Step 1: 写 `test_cursor_matches_independent_hmac_vector`、`test_signed_cursor_rejects_semantic_misuse`、`test_cursor_time_boundaries`。** TC-U4-01～09、B19/B20、TC-U10-21/23/26 按具名 variant 展开；一组状态乱序/重复规范化后相等。HMAC 固定 key+手写 JSON bytes 独立计算，不能调用产品 encoder 生成 oracle。

  ```python
  assert signer.decode(token, purpose="events").boundary.as_of_sequence == 240
  assert "literal % done" not in decoded_payload_text
  with pytest.raises(ReadFailure, match="cursor_expired"):
      signer_at_expiry.decode(token, purpose="events")
  ```

  未来签发、寿命非 1800、错 purpose/run/filter、negative/倒置 position、未知字段各拒绝；格式正确但 4097 字节在 decode 前拒绝。无法到达合法 4096 字节的 cursor 只算长度入口，不冒充合法最大 token 覆盖。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_cursors.py tests/dashboard/test_parameters.py -q`；预期签名/语义/范围行为失败。
- [ ] **Step 3: 实现固定接口。** 稳定 JSON：sort_keys=True、separators=(',',':')、ensure_ascii=False、禁 NaN；无 padding URL-safe Base64 payload/signature 以 `.` 分隔，HMAC-SHA256+compare_digest。时间编码复用 encode_datetime。规范化多选去重排序，时区转 UTC；不改 task 字面内容。HTTP 参数名固定 `status,model,workspace,from,to,run_id,task,page_size,cursor`；Event 固定 `type,step,tool_name,failures_only,limit,cursor,boundary,event_id,grouped`。重复单值、未知键、无时区时间、from≥to 拒绝；来源定位 `event_id` 不与分页 cursor 同用。
- [ ] **Step 4: Ruff/ty 后重跑 Step 2，预期 PASS。** 当前提交上界/Artifact hash 等数据语义由消费查询/Reader 再验证，不在 signer 中打开 DB。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): define signed dashboard navigation boundaries"`。

### Task 4: Run 列表的标量 SQL、筛选、计数与 keyset 分页

**Files:** Create `src/miniagent/dashboard/queries/runs.py`；Modify `reads.py`（RunEnvelope/RunListItem/RunListRead）；Test `tests/dashboard/test_run_list.py`；Modify `tests/dashboard/builders.py`。

**Interfaces:** Consumes Task 2/3；Produces `list_runs(database:Path, filters:RunFilters, *, page_size:int=25, after:RunPosition|None=None, budget:ReadBudget, connect:ConnectionFactory=sqlite3.connect)->RunListRead`。

- [ ] **Step 1: 写 `test_list_keyset_survives_new_insert`、`test_literal_task_filters_and_counts`、`test_oversized_run_preserves_envelope`。** L=30 个 Run，page_size=10，UUID 顺序手写期望；第一页后插入前置 Run，余页原30项各一次。TC-U2-01～08、B05/B07/B08/B20、TC-U10-21/23；坏身份/状态/时间与外列不符逐项问题，筛选不能冒充匹配。

  ```python
  assert listed_ids == expected_original_ids
  assert literal_percent_ids == [percent_run_id]
  assert result.total == sum(result.counts.values())
  assert oversized.items[0].envelope.run_id == run_id
  assert "read_limit_exceeded" in {i.code for i in oversized.items[0].issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_run_list.py -q`；预期位置/字面筛选/异常行断言失败。
- [ ] **Step 3: 实现签名。** 固定 `created_at DESC,run_id DESC` 二元 `<`；一事务同谓词计数/分状态数/页 `limit+1`。SQL 固定 json_valid/形状守卫+必要路径：identity.task、definition.agent_spec.model.model、lifecycle、usage、recovery 的 workspace_spec.kind；长度以 length(CAST(... AS BLOB)) 计算，先门禁再返回标量。不得 SELECT 整个 state_json/conversation 或逐 Run Event/Artifact。LIKE escape `\`，将 `%/_/\` 按字面转义，参数绑定。usage 缺字段为 None；cost SQL 提取原始 Decimal 字符串，禁 CAST REAL。列表展示所需文字留待 Task 18 脱敏后截短。
- [ ] **Step 4: Ruff/ty 后重跑 Step 2，预期 PASS。** trace 证明无 Event/Artifact N+1，RO；跨请求状态成员变化按 TC-U2-06 接受当前事务事实。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): query dashboard runs with stable pagination"`。

### Task 5: 一致首屏、有界正文、Event 定位与 Snapshot 范围

**Files:** Modify `queries/runs.py`、`reads.py`；Create `queries/{events,snapshots}.py`；Test `tests/dashboard/test_page_reads.py`、`test_sources.py`；Modify `tests/dashboard/observation.py`。

**Interfaces:** Produces `read_run_page(database, run_id:UUID, filters:EventFilters, *, limit:int=100, budget:ReadBudget, connect=sqlite3.connect)->RunPageRead`；`read_events(database, boundary:PageBoundary, filters:EventFilters, *, after_sequence:int=0, limit:int=100, budget, connect=...)->EventBatch`；`locate_event(database,boundary,event_id:UUID,*,budget,connect=...)->EventBatch`；`read_snapshot(database,boundary,version:int,*,start:int=0,end:int|None=None,budget,connect=...)->SnapshotSlice`，database 均 Path，budget 均 ReadBudget。本项 EventFilters 先支持空筛选，非空消费在 Task 6 完成。

- [ ] **Step 1: 写 `test_first_page_uses_one_committed_view`、`test_old_boundary_never_loads_future_sources`、`test_oversized_run_preserves_envelope`。** F0/F1 精确沿用 TD；G-DB 委托真实 Connection/Cursor，第一次实际 fetch 固定 WAL 视图后握手 writer 原子提交 F1，随后继续读取。TC-U3-01～04、B01～06、TC-U10-24 及 Snapshot 两上界单故障变体。

  ```python
  assert (page.boundary.as_of_sequence, page.boundary.latest_snapshot_version) == (240, 2)
  assert page.run.record.state.usage.total_tokens == f0_tokens
  assert all(e.sequence <= 240 for e in old_events.items)
  assert len(located.items) <= 41
  assert [m.content for m in snapshot.messages] == expected_requested_messages
  ```

  Snapshot start/end 使用本计划固定语义，≤100 条；空、首尾、负、反向、越界各例。state/recovery 分开读，recovery 坏/超限不得丢合法 Run。对超限 state 的 RunRecord=None 显示 envelope，不能伪造 context.current_version=None。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_page_reads.py tests/dashboard/test_sources.py -q`；预期一致性/边界/超限读取断言失败。
- [ ] **Step 3: 实现接口。** 首屏同事务 Run+序列/Snapshot 指针+首批 Event+最后一条EvaluationCompleted（按sequence DESC LIMIT 1，上界相同），先 envelope/UTF-8 lengths，reserve 后再取允许正文。复用 codecs；recovery 独立受控解码，用 recovery 置空的 row 解码合法 state 后组合真实 context，不吞 codec 文本。codec前通过安全JSON解析检查结构及字段是否实际存在：usage所有展示字段、execution两个累计值、config的Step/容量限制、generation输出预留、context.current_version；缺字段记`field_not_recorded`+精确field_path，不把Pydantic当前默认值当历史事实。无需保留额外原始JSON树，提取问题后释放再调用codec；Snapshot同样处理。Event 单项坏 schema/未知 type 保留 StoredEvent；payload/artifacts 分别≤1MiB，合计预算覆盖所有正文并且仅 reserve 一次。Snapshot 每项先≤20MiB，再解析/校验，只有请求消息范围进入来源投影。新事务验证边界不超过当前已提交指针/事实，并验证 run_id；超范围422、边界内不存在404。定位绕过筛选、按 sequence 取每侧20。
- [ ] **Step 4: Ruff/ty 后 Step 2 全 PASS。** SQL observer 记录返回正文总 bytes≤64MiB，先 reserve 再 fetch；不能只检查 HTML 小。事务退出后才文件/投影/渲染。损坏 JSON fixture 使用独立已损坏库或明确关闭 fixture CHECK，不能放宽产品校验。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): preserve dashboard page read boundaries"`。

### Task 6: Event SQL 筛选与 Tool/Step 展示

**Files:** Modify `queries/events.py`、`reads.py`、`views.py`；Create `projections/{__init__,events}.py`；Test `tests/dashboard/test_events.py`。

**Interfaces:** Consumes StoredEvent/EventBatch/EventFilters；Produces `project_events(batch:EventBatch)->tuple[EventView,...]`、`project_tools(events:tuple[StoredEvent,...],*,facts:ToolFacts|None=None)->tuple[ToolView,...]`、`step_is_partial(events:tuple[StoredEvent,...],step_number:int)->bool`；`read_tool_facts(database:Path,boundary:PageBoundary,keys:tuple[tuple[int,str],...],*,budget:ReadBudget,connect:ConnectionFactory=sqlite3.connect)->ToolFacts`。keys为当前Run的step/call_id；read_events 完成全部筛选契约。

- [ ] **Step 1: 写 `test_failure_filter_matches_exact_known_facts`、`test_sparse_matches_are_filtered_before_limit`、`test_tool_ids_do_not_cross_steps`、`test_success_and_truncation_are_independent`。** TC-U5-01～08 全部具名变体；前100不匹配、201/220匹配；坏匹配行占位且后续游标前进。

  ```python
  assert [e.event_id for e in failed.items] == expected_failure_ids
  assert no_match.next_sequence is None
  assert [(t.step_number, t.tool_call_id) for t in tools] == [(1, "same"), (2, "same")]
  assert (tool.status, tool.completeness) == ("success", "truncated")
  assert "source_missing" not in {i.code for i in tool_with_start_on_previous_page.issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_events.py tests/dashboard/test_sources.py -q`；预期失败集/关联/坏行位置断言失败。
- [ ] **Step 3: 实现固定接口。** SQL 按 A§6.2/TD U5-R1～R11：model error_code非空；tool_end/tool_execution_end status∈error/timeout/denied；step failed=true；evaluation passed=false；run_finished failed；compaction_failed；approval_resolved rejected/expired/invalidated；approval_invalidated。JSON bool 使用实际 JSON 类型，不把字符串 true 当 bool；tool_name 固定已知 payload 路径。PolicyDecided 的真实 effect 位于 decision.effect，仍不单独入失败集；未知类型/状态不推断。Tool 关联键为 Run/Step/call，精确补查不受筛选影响：每角色先COUNT，最多读2条用于唯一/重复判定，单项与64MiB预算不变；call_index/span/name冲突保留问题，不选最后结果。facts未加载时不能把分页缺起点当真实历史缺失。未保存参数/model IO 明确“未记录”；ToolExecutionEnded 不替代 ToolEnded 的 result；Step 边界未见完整起止就标当前局部加载。
- [ ] **Step 4: Ruff/ty 后重跑 Step 2，预期 PASS。** EventView 采用固定类型摘要映射，payload 统一结构化脱敏；shell exit 仅称命令退出状态。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): project dashboard events and tool outcomes"`。

### Task 7: 按需补查 scope 与页面 SpanView

**Files:** Modify `queries/events.py`、`reads.py`、`views.py`；Create `projections/spans.py`；Test `tests/dashboard/test_spans.py`。

**Interfaces:** Consumes 当前 EventBatch、PageBoundary；Produces `read_scope_facts(database:Path,boundary:PageBoundary,span_ids:tuple[str,...],*,budget:ReadBudget,connect:ConnectionFactory=sqlite3.connect)->ScopeRead`、`project_spans(facts:ScopeRead,boundary:PageBoundary)->tuple[SpanView,...]`。

- [ ] **Step 1: 写 `test_scope_pairs_survive_filters_and_pages`、`test_span_issues_preserve_events`、`test_unloaded_parent_is_not_missing`。** TC-U6-01～08；7类各2秒配对；重复开始位于第三页仍发现；父缺失/层级错/时间倒退/终态分类分别单故障。

  ```python
  assert legal.duration_ms == 2000
  assert open_at_f0.state == "open"
  assert same_scope_at_f1.state == "incomplete"
  assert duplicate.duration_ms is None
  assert "not_loaded" in {i.code for i in limited.issues}
  assert "missing_parent" not in {i.code for i in limited.issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_spans.py -q`；预期跨页、重复计数或边界状态断言失败。
- [ ] **Step 3: 实现接口。** 固定映射 run_started/finished、step_started/completed、model_requested/responded、tool_start/end、tool_execution_start/end、evaluation_start/completed、approval_requested/resolved。按 span/type先计数再有界取事实，重复不能被 LIMIT 隐藏；只查当前涉及 span 与父链，不应用时间线筛选。复用 tracing.py 父类别规则的语义，不导入私有 `_ALLOWED_PARENTS` 或修改 RunTrace；Dashboard 本地七类固定映射并逐类测试。父链最大64、总scope1000，cycle/无效层级明确问题；缺根不造根，approval_invalidated不闭合等待，compaction不新建span。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** B15/B16 的62～66、998～1002先证明实际查询路径可达；若因有向类别层级/200事件页提前拒绝，在本任务证据中写出不可达路径及仍被保护的异常遍历，不以构造合法64层链蒙混。新终态不得改写旧游标的 open；同边界 RunFinished 矛盾保留 conflict。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): explain bounded dashboard span facts"`。

### Task 8: Run 身份、结果、预算与 Decimal 费用

**Files:** Create `projections/run.py`；Modify `views.py`；Test `tests/dashboard/test_run_projection.py`。

**Interfaces:** Consumes StoredRun/PageBoundary/可选最近 CompactionCompleted；Produces `project_run(run:StoredRun,boundary:PageBoundary,*,latest_compaction:CompactionCompleted|None=None,evaluation_event:StoredEvent|None=None)->RunView`、`format_usd(value:Decimal)->str`；Task 18 复用 `FieldView` 安全渲染。

- [ ] **Step 1: 写 `test_usage_uses_persisted_totals`、`test_unknown_evaluator_is_not_inferred`、`test_wall_duration_and_step_limits`、`test_oversized_run_preserves_envelope`。** TC-U8-01～08 全部；真实各 RunStatus 合法状态样本需满足运行 cursor/终态 invariants。

  ```python
  assert fields["total_tokens"].value == "120"
  assert fields["cost_usd"].value == "0.300000"
  assert fields["active_duration_ms"].value == "12000"
  assert fields["wall_duration_ms"].value == "20000"
  assert format_usd(Decimal("0.0000005")) == "0.000000"
  assert format_usd(Decimal("0.0000015")) == "0.000002"
  ```

  三个未配置硬限制无百分比；steps 9/10/11 的独立测试显示90%/100%/110%且11不钳制；反向时钟“不可计算”。删除usage.total_tokens/usage.cost_usd/config.max_steps等字段但schema仍为2的独立样本必须显示“未记录”，不能采用codec补出的0/默认max_steps。Local/unknown succeeded 仍是 Runtime成功+“该 Run 验证类型未记录，未证明独立验证”；candidate/final 分开，状态陈旧也不判死。evaluation_event.passed显示“最后记录的评测结果”并链接该Event，不能称已验证独立通过；无事件为“未记录”，Artifact可用性为“尚未检查，Workspace查看”。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_run_projection.py -q`；预期费用/事实分离/缺失标记失败。
- [ ] **Step 3: 实现接口。** 字段 key 固定为 RunState 原字段名；预算 key `steps_started,max_steps,consecutive_failed_steps,max_consecutive_failures,context_window_tokens,max_output_tokens,time_limit,token_limit,cost_limit`。RunUsage所有计数和cost分项直接读取；以Task5的field_not_recorded优先覆盖codec补出的默认值，此时显示“未记录”且不算百分比；显式存储的0保持0。cached_input_priced_separately决定缓存费用是否单列；另以`cost_recorded_decimal`折叠字段显示str(原Decimal)，保留6位展示以下精度。摘要0.10已在0.30中，只说明贡献。RunRecord缺失时保留 envelope身份/状态，其他字段为“未完成读取”或对应 schema 问题，不能变零/无压缩。所有展示值脱敏，长错误完整脱敏后限4KiB；超长 task 全文在详情有界显示，超过20MiB正文则只元信息。
- [ ] **Step 4: Ruff/ty 后重跑 Step 2，预期 PASS。** 外部当前 pricing 文件不可读不影响历史 cost；context容量/输出预留与累计Token分开；旧压缩估计标事件来源时点。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): display recorded run usage without inference"`。

### Task 9: 摘要版本完整性与压缩尝试归组

**Files:** Create `projections/compaction.py`；Modify `reads.py`、`views.py`；Test `tests/dashboard/test_compaction_projection.py`；Modify `tests/dashboard/builders.py`（M_good）。

**Interfaces:** Produces `project_memory(run:StoredRun,boundary:PageBoundary,facts:CompactionRead)->MemoryView`（本项完成摘要/分组，来源内容由 Task 10）；`group_compactions(events:tuple[StoredEvent,...])->tuple[CompactionGroupView,...]`。Consumes 实际 CompactionSummary/CompactionCompleted/Snapshot，不调用 validate_compaction_record。

- [ ] **Step 1: 写 `test_summary_hashes_full_rendered_message`、`test_current_and_historical_summary_are_distinct`、`test_compaction_attempts_do_not_cross_groups`。** TC-U7-01～04、08～10；固定前缀取现有 compaction_v1.j2 的完整首行+换行，使用真实模板生成 M_good，但期望hash独立 hashlib计算。无成功摘要但存在Requested/Failed时仍显示已加载失败组和attempt成本；context字段未记录时不能声称“尚未生成”。

  ```python
  assert current.version == 2
  assert historical.version == 1
  assert [g.first_requested_sequence for g in groups] == [20, 80]
  assert groups[0].total_cost_usd == Decimal("0.30")
  assert "summary_hash_mismatch" in {i.code for i in suffix_hash_memory.issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_compaction_projection.py -q`；预期hash对象/版本/归组失败。
- [ ] **Step 3: 实现接口。** 只读 messages[2] AssistantMessage，必须无 tool_calls、固定模板v1前缀+JSON后缀严格 CompactionSummary。查同版本唯一Completed；校验 previous/current+1、event_sequence、source/new snapshot ID/version、覆盖消息/Step/retained_first_step、前两消息与保留消息、当前消息前缀与new Snapshot一致。按现有 validate_compaction_record 的规则实现有界纯检查，不适配 RuntimeStore 写接口。事实尚未加载先 not_loaded，不把超限Run当无摘要。首次Requested(attempt1)标组直到Completed/Failed；孤立/重复起止保留未关联。Recorded优先，同attempt结束列表只补缺；不同金额/usage冲突不任选，total None。普通字段只准备承载Snapshot来源，VerifiedEvidence内容检查留 Task10。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** 完整原消息hash合法对照成立；JSON后缀hash/脱敏hash/错hash分别失败。历史摘要由对应new Snapshot确定，不从当前conversation补旧历史。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): project persisted compaction history"`。

### Task 10: 精确摘要来源读取与 claim 内容校验

**Files:** Modify `queries/events.py`、`queries/snapshots.py`、`projections/compaction.py`；Test `tests/dashboard/test_compaction_sources.py`。

**Interfaces:** Produces `read_compaction_sources(database:Path,boundary:PageBoundary,*,run:StoredRun|None=None,version:int|None=None,visible_events:tuple[StoredEvent,...]=(),budget:ReadBudget,connect:ConnectionFactory=sqlite3.connect)->CompactionRead`；当前摘要使用已读取run，历史摘要使用显式压缩version，两者不能都缺；历史请求不重新读取可变Run。visible_events由当前有界时间线传入，只有其中的压缩事实参与归组。project_memory 完成 EvidenceView。额外 `project_historical_memory(snapshot:StoredSnapshot,boundary:PageBoundary,facts:CompactionRead)->MemoryView`，不得构造伪当前 RunRecord。

- [ ] **Step 1: 写 `test_existing_source_must_support_claim`、`test_unloaded_evidence_is_not_missing`、`test_compaction_source_chain_is_bounded`。** TC-U7-05～08、B17 五值：错Run/sequence/version/message/祖先范围、缺失/断链、叶文本与excerpt、claim与excerpt、field_path和值分别单故障。

  ```python
  assert valid.evidence[0].state == "verified"
  assert wrong_claim.evidence[0].state == "inconsistent"
  assert oversized_source.evidence[0].state == "not_loaded"
  assert all(field.source_label == "摘要记录" for field in ordinary_fields)
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_compaction_sources.py tests/dashboard/test_compaction_projection.py -q`；预期来源存在即成功/范围/未加载分类失败。
- [ ] **Step 3: 实现接口。** SQL 按current_version精确查Completed，逐步previous最多32版本；ID/版本分批IN查不可变Event/Snapshot，所有查询守PageBoundary和共享64MiB预算。每个所选Completed或visible_events中的Failed还读取其前最近Requested(attempt=1)及到该结束事件的压缩记录；检查中间是否存在另一次起止，不明确就保留未关联；以event_id去重合并visible_events，只读相关组，超过Event批次200则标未加载并提供Event来源导航，不载全Run压缩历史。无current_version仍保留已加载失败/未关联组。仅在消息范围覆盖历史的Step/祖先范围内承认来源；叶字符串包含excerpt且claim=excerpt（不匹配JSON键）；field_path逐对象/列表解析，value与真实值一致且claim等于 `orjson.dumps(value).decode()` 的规范表达。数字/字符串/bool不可用Python宽松相等掩盖类型差异。只验证加载部分，超限不判真伪；真缺失显示source_missing。纯投影只产生已校验SourceTarget，页面层用签名边界生成href，普通字段不造逐项来源。
- [ ] **Step 4: Ruff/ty 后重跑 Step 2，预期 PASS。** 源请求不加载全部conversation或日志；33版本只限制本次链，允许显式历史版本继续浏览；摘要尝试成本仍不加回 RunUsage。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): verify bounded task memory sources"`。

### Task 11: 流式 manifest 发现与单项隔离

**Files:** Create `src/miniagent/dashboard/artifacts.py`；Modify `reads.py`；Test `tests/dashboard/test_manifests.py`。

**Interfaces:** Produces ArtifactReader 的 `list_manifests/read_manifest/discover_patches`；Consumes ArtifactRecord schema1、PageBoundary只由页面持有，不把manifest发现时间伪称DB一致视图。

- [ ] **Step 1: 写 `test_manifest_page_does_not_validate_all_contents`、`test_corrupt_manifest_does_not_hide_other_items`、`test_partial_discovery_cannot_prove_unique_patch`。** TC-U9-01/02/09、B09/B13、TC-U11-02/05：101登记、一坏项、页外event引用可精确查、晚发布；一个content哨兵无权限/坏hash仍可列metadata且不访问content。

  ```python
  assert len(first.items) == 100
  assert second.items[0].artifact_id == expected_101st_id
  assert good_id in {item.artifact_id for item in page.items}
  assert content_reads == []
  assert not interrupted_discovery.complete
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_manifests.py -q`；预期页数/坏项隔离/内容零读取失败。
- [ ] **Step 3: 实现接口。** 根启动expanduser/resolve但不创建；以根FD逐级O_DIRECTORY/O_NOFOLLOW打开manifest目录与普通manifest（O_NONBLOCK+fstat），不复用FileArtifactStore.list_for_run。使用真实os.scandir(fd)流式枚举，用容量101的有界堆保留cursor之后最小UUID项，禁止list全目录再sort。候选文件名UUID排序、run目录hex，schema/ID/Run一致；非UUID目录项不进入可导航项但记录目录问题。manifest≤1MiB、词法深度/重复键+request budget后解码。discover_patches完整流式发现合法workspace_patch，仅保留patch metadata（同样受64MiB和截止限制）；可能patch项损坏/未读时不证明唯一。扫描超时保留部分+complete=False，不能默认选择。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** manifest文件类型/符号链接正反检查；找不到run manifest目录为空当前发现，不创建。登记列表读时不校验content，精确补查同Run artifact_id无需全目录搜索。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): discover dashboard artifacts independently"`。

### Task 12: 同 FD 的安全内容读取与 size/hash 门禁

**Files:** Modify `artifacts.py`、`reads.py`；Test `tests/dashboard/test_artifact_content.py`；Modify `tests/dashboard/observation.py`（公开OpenFd边界委托）。

**Interfaces:** Produces `ArtifactReader.read_content(...)->VerifiedContent`；Consumes `ArtifactRef`、ReadBudget、真实 FD；无内部全量 read_bytes。

- [ ] **Step 1: 写 `test_actual_oversize_reads_no_content`、`test_content_identity_and_layout`、`test_internal_symlinks_are_always_rejected`。** TC-U9-02～04/13/14、B10、TC-U10-27；实际M+1、登记M−1、真实hash路径合法，仅size虚报；限额内12/11为409、12/12合法对照。

  ```python
  assert oversized.content is None
  assert content_read_calls == []
  assert content_hash_updates == []
  assert "read_limit_exceeded" in {i.code for i in oversized.issues}
  assert valid.content == expected_bytes
  assert max(read_sizes) <= 65536
  ```

  I/O观测从产品请求起算：测试子进程用thread profile观察真实 `os.read`/`hash.update`/mmap调用；open_fd委托真实os.open，标记content FD，准备hash不计入记录。若观测尚不能区分manifest/content，先修设施，不用409代替零读取证据。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_artifact_content.py -q`；预期实际fstat门禁、symlink/URI/身份失败。
- [ ] **Step 3: 实现接口。** 只接受file scheme、空authority、无query/fragment、一次合法percent解码后精确匹配规范根下`content/{sha[:2]}/{sha}`；NUL/非法编码/逃逸拒绝。逐级dir_fd打开根下目录，O_NOFOLLOW，末级O_NONBLOCK后fstat普通文件；同一FD先双size上限门禁，再限额内登记size比较，再64KiB读取/hash，循环check deadline，最后累计size/hash一致。expected ref给定时逐项匹配record，不跨Run补查。安全打开/已登记缺内容等为409，未登记404，权限/非预期I/O交上层500，不吞未知异常。只保留一个当前内容bytes；超过20MiB不read/hash/mmap。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** 根内/根外symlink、普通文件作中间节点、目录作末级、file://host、https、同根非hash布局、metadata.files根外哨兵全覆盖；关闭全部FD。真实FIFO/rename留Task23。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): validate dashboard artifacts through safe descriptors"`。

### Task 13: 校验后完整脱敏与 UTF-8 预览分页

**Files:** Create `src/miniagent/dashboard/previews.py`；Modify `views.py`；Test `tests/dashboard/test_previews.py`。

**Interfaces:** Consumes VerifiedContent；Produces `project_preview(content:VerifiedContent,*,offset:int=0,budget:ReadBudget)->PreviewView`；Cursor绑定在Task20消费，纯投影不验HMAC。已知类型白名单：workspace_patch/evaluation_config/evaluation_summary/evaluation_report/evaluation_predictions/evaluation_stdout/evaluation_stderr/evaluation_test_output/evaluation_instance_log，且media_type为text/*或application/json；未知type只metadata。

- [ ] **Step 1: 写 `test_redacted_preview_chunks_reassemble_exactly`、`test_invalid_json_never_falls_back_to_raw_text`、`test_preview_respects_both_limits`。** TC-U9-05～07/10～12、B11/B12/B18、TC-U10-25/28；私钥跨256KiB或2000行、多字节/超长单行；期望脱敏后文本为手写非产品结果。

  ```python
  assert "".join(chunks) == expected_redacted_text
  assert all(len(text.encode("utf-8")) <= 262144 for text in chunks)
  assert all(len(text.splitlines()) <= 2000 for text in chunks)
  assert offsets == expected_byte_offsets
  assert invalid_json.text is None
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_previews.py tests/dashboard/test_content.py -q`；预期分段边界/坏JSON回退/双约束失败。
- [ ] **Step 3: 实现接口。** 有效UTF-8且无除tab/CR/LF之外的控制内容才预览；JSON先parse_json再redact_json并以固定缩进2、ensure_ascii=False序列化。JSON合法但业务未知仍可统一脱敏；坏JSON/重复键/深度问题只metadata。文本完整redact_text后计算UTF-8偏移，不把完整redacted副本和所有bytes分段同时保留；字符边界、行数、字节取先到上限。offset=总字节允许空末页无next，负/超过/字符中间422。超长单行分段标inline_continuation，next严格前进。投影check budget，不提供下载或cache。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** 小登记/实际超限优先200+未校验，限额内size不符409仍由Reader负责；每次预览都要Reader重校验，纯PreviewView不得回传URI/宿主路径。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): paginate redacted artifact previews"`。

### Task 14: 当前评测证据适配器与严格 report 类型

**Files:** Create `evaluation_evidence.py`；Test `tests/dashboard/test_evaluation_adapter.py`；Modify `tests/dashboard/builders.py`（C_good）。

**Interfaces:** Produces `adapt_evaluation(contents:dict[UUID,VerifiedContent])->EvaluationEvidence`、`read_recorded_report(content:bytes,*,instance_id:str)->InstanceReport`。只能消费已验证bytes，不接宿主路径；严格复用现有 InstanceReport/TestGroup。

- [ ] **Step 1: 写 `test_report_native_booleans_and_optional_infra`、`test_report_rejects_each_invalid_field_type`、`test_report_container_shapes_are_strict`。** TC-U12-04/05/10/13～16；15个bool类型反例、4个必需bool缺失、2个infra对照；tests_status/组/list错误、缺组/list、每个success/failure非字符串元素逐路径独立variant。

  ```python
  assert report.resolved is True
  assert report_without_infra.infra_failure is False
  with pytest.raises(ContentError, match="report_fields_invalid"):
      read_recorded_report(string_bool_report, instance_id=instance_id)
  assert unknown_harness_id not in adapted.reports
  ```

  每次重建合法bytes/manifest/hash/ref，不能让完整性失败掩盖字段错误；failure=[1]必须报字段类型，不能只报有失败测试。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_evaluation_adapter.py -q`；预期严格消费/形状问题失败。若严格模型现有行为已绿，保留回归记录，不声称新RED。
- [ ] **Step 3: 实现接口。** `EvaluationConfig`必要字段：run_id/attempt_id/submission_id/patch_artifact_id UUID、instance_id非空str、patch_sha256 64hex、patch_ref ArtifactRef、harness_version str；`EvaluationSummary`必要字段：scope='evaluation_evidence_snapshot'、terminal_status_source='Run'、verdict∈collecting/cancelled/failed/report_resolved、同Run/attempt/instance/submission/patch字段。保留producer其他字段extra allow并统一脱敏，不发明schema_version。未知结构标unsupported；已知结构字段类型坏标字段问题，不能自动通过。报告JSON唯一顶层instance key；InstanceReport严格bool及list[str]，必要tests_status存在，组内无重复/成功失败交集，resolved不得记录失败。跨组同名可合法。不调用parse_instance_report来伪造fail_to_pass/pass_to_pass目标；不读取task.json。未知harness仅预览，不套5.0.2。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** `C_good` config/summary必须从当前 `evaluation/swebench.py`生产字段核对，patch metadata没有run/attempt等未生产字段时不得强求；报告不要求Run/attempt字段。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): parse recorded evaluation evidence strictly"`。

### Task 15: attempt 分组与独立评测链判定

**Files:** Create `projections/evaluation.py`；Modify `views.py`；Test `tests/dashboard/test_evaluation_projection.py`。

**Interfaces:** Consumes StoredRun、PageBoundary、同边界EvaluationCompleted事件、ManifestItem与EvaluationEvidence；Produces `project_evaluations(run:StoredRun,boundary:PageBoundary,events:tuple[StoredEvent,...],manifests:tuple[ManifestItem,...],evidence:EvaluationEvidence)->tuple[EvaluationAttemptView,...]`。

- [ ] **Step 1: 写 `test_recorded_pass_requires_the_whole_evidence_chain`、`test_missing_and_conflicting_facts_both_remain_visible`、`test_missing_logs_do_not_replace_verdict`。** TC-U12-01～12逐必要条件破坏；多个summary较小UUID的final被Event引用，不能选大UUID；A1/A2分开，无/非法attempt未关联。

  ```python
  assert good.outcome == "recorded_pass"
  assert mismatched_patch.outcome == "conflict"
  assert missing_report.outcome == "incomplete"
  assert missing_stdout.outcome == "recorded_pass"
  assert {"evidence_conflict", "evidence_incomplete"} <= {i.code for i in mixed.issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_evaluation_projection.py -q`；预期全链/summary选择/多attempt关联失败。
- [ ] **Step 3: 实现接口。** 仅manifest metadata有效UUID attempt_id分组；同Run，config/summary内容与metadata的instance/attempt一致。Event artifacts明确引用相关config/final summary/report/patch；逐ref身份/sha/size/URI与已验证manifest一致，最终summary report_resolved；Run边界succeeded、Event passed=true、supported report resolved且非空patch/成功apply/无infra。patch ID/hash/submission全链匹配，hash确实验证。不同attempt不得拼齐；多个引用冲突保留。缺必要项incomplete、未知形状unsupported、明确否定与其他声称通过conflict；已记录失败可recorded_failure。不改Run/Evaluation事实。Local不显示SWEbench卡片；unknown只有明确持久化评测链才展示，不能从task猜instance。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** outcome recorded_pass的FieldView必须含固定通过标题、目标全集免责声明、“报告记录的测试”；日志缺失单独问题；完整性失败content=None不能进入通过判定。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): explain independent evaluation evidence chains"`。

### Task 16: diff 结构、文件清单与默认 patch 决策

**Files:** Create `projections/workspace.py`；Modify `views.py`；Test `tests/dashboard/test_workspace_projection.py`。

**Interfaces:** Produces `parse_diff(text:str,*,budget:ReadBudget)->DiffView`（text须先由已校验原文验证结构，最终各显示字段脱敏）、`choose_patch(discovery:PatchDiscovery,associated_patch_ids:tuple[UUID,...],*,association_conflict:bool=False)->PatchChoice`；Consumes 同边界评测链已确定的patch引用。

- [ ] **Step 1: 写 `test_patch_selection_uses_explicit_evidence`、`test_partial_scan_never_selects_a_default`、`test_diff_paths_and_incomplete_parse`。** TC-U11-01～07、TC-U9-08：P/P2较大UUID不代表新；无/唯一/多个/部分扫描/多个attempt冲突；新增删除rename/空格或Git引用路径/binary/未知格式分别样本。

  ```python
  assert linked.artifact_id == p_id
  assert ambiguous.artifact_id is None
  assert partial.artifact_id is None
  assert renamed.files[0].kind == "rename"
  assert interrupted.complete is False
  assert "files_mismatch" not in {i.code for i in interrupted.issues}
  ```

- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_workspace_projection.py -q`；预期选取/格式/不完整误判失败。
- [ ] **Step 3: 实现接口。** 按行状态解析Git header/文件header/hunk/rename/binary，不用shell/git/apply，不按普通空格split含引号路径。未知结构保留统一diff原文预览+解析不完整，不当空patch。原始路径仅当文本，不映射宿主目录；metadata.files和推导files独立，完整解析才断言不一致。scan complete且明确一致关联优先；无关联且唯一合法patch其次；partial或冲突均无默认，用户显式patch选择仅同Run登记项。大diff循环check同一deadline；达到预算保部分，不称完整。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** 逐文件text完整脱敏后由Task13限制预览；未知格式仍可查看安全有界原文，不保证可复制应用patch。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): explain saved patch changes and selection"`。

### Task 17: 重内容工作任务名额、取消与关闭回收

**Files:** Create `src/miniagent/dashboard/workers.py`；Test `tests/dashboard/test_workers.py`。

**Interfaces:** Consumes ReadFailure；Produces HeavyWorkPool 字典契约。线程operation为同步callable；同一请求读取、解码、投影、脱敏、渲染整个闭包只提交一次，不在阶段间释放/重取名额。

- [ ] **Step 1: 写 `test_third_heavy_work_is_rejected`、`test_http_cancellation_keeps_actual_work_slot`、`test_close_waits_for_actual_workers`。** 使用真实threading.Event握手运行两个真实文件解析工作；HTTP取消暂在本项模拟调用协程取消，ASGI真实后盾在Task24。

  ```python
  with pytest.raises(ReadFailure, match="heavy_work_busy"):
      await pool.run(third_operation)
  cancelled.cancel()
  with pytest.raises(ReadFailure, match="heavy_work_busy"):
      await pool.run(third_operation)
  release_first.set()
  assert await pool.run(third_operation) == expected_value
  ```

  等待“第一个实际结束”用握手/已完成future，不以release event发出推断完成。operation内真实异常须传播，结束仍归还名额。
- [ ] **Step 2: RED。** `uv run pytest tests/dashboard/test_workers.py -q`；预期排队/取消早释/关闭遗漏断言失败。
- [ ] **Step 3: 实现接口。** event loop内同步检查活跃数，不排队等待名额；创建有强引用的Task包裹asyncio.to_thread operation，用shield等待，完成callback/实际任务finally释放。HTTP取消不取消该Task；aclose停止接收新任务并等待全部实际worker；避免只shield没有保留引用。取消不需要强杀线程，由预算/有限工作结束；不新增线程池基类或持久后台服务。
- [ ] **Step 4: Ruff/ty 后 Step 2 PASS。** task异常被观察、无“Task exception was never retrieved”；完成名额峰值≤2，关闭后操作拒绝；本项不声称完整重阶段线程证据。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): bound actual dashboard heavy work lifetimes"`。

### Task 18: FastAPI 应用、HTTP 安全边界与 Run 列表页面

**Files:** Create `app.py`、`routes.py`、`pages.py`、`presentation.py`、`templates/{base,runs,error}.html`、`templates/partials/run_list.html`、`static/dashboard.css`；Modify `pyproject.toml`、`uv.lock`；Test `tests/dashboard/test_http.py`、`test_list_page.py`。

**Interfaces:** Produces create_app 字典签名；`PageDependencies(settings:DashboardSettings,signer:CursorSigner,reader:ArtifactReader,connect:ConnectionFactory,now:Callable[[],datetime],monotonic:Callable[[],float])` frozen dataclass in pages.py；`RenderedPage(html:str,status_code:int=200)` frozen dataclass；`render_runs(deps:PageDependencies,params:RunListParams,*,fragment:bool=False)->RenderedPage`；`render_page(template:str,context:dict[str,object])->str`、`source_href(target:SourceTarget,boundary:PageBoundary,signer:CursorSigner)->str` in presentation.py，仅已脱敏views+受控链接/值可进入context。source_href只在目标Run与boundary匹配后生成本地Event/Snapshot路径；签名不进入纯投影。

- [ ] **Step 1: 写 `test_list_page_shows_only_recorded_facts`、`test_host_origin_and_methods`、`test_error_responses_never_echo_input`。** 真实ASGI httpx客户端base_url为`http://127.0.0.1:8080`；`/`307至/runs，/runs完整与HX-Request片段、health和HEAD。TC-U13-01/06/07/08当前可达入口、TC-U2-04/08、TC-U1-07/08。

  ```python
  assert response.status_code == 200
  assert "详情查看证据" in response.text
  assert "已记录费用，完整性未证明" in response.text
  assert hostile_host.status_code == 403
  assert invalid_parameter.status_code == 422
  assert secret_sentinel not in response.text + captured_logs
  assert head.content == b""
  ```

- [ ] **Step 2: RED。** 添加 dashboard extra `fastapi>=0.115,<1`、`uvicorn>=0.30,<1`；dev加入 `httpx>=0.28,<1`，uv实际解析/更新lock，再 `uv run --extra dashboard pytest tests/dashboard/test_http.py tests/dashboard/test_list_page.py -q`。安装失败不是RED；预期HTTP/展示行为断言失败。
- [ ] **Step 3: 实现应用和HTTP边界。** startup工作线程validate_database，无write/init；lifespan持有reader/signer/pool并在shutdown aclose；健康页仅固定状态不含路径配置。HTTP精确Host `{127.0.0.1,localhost}:port`（默认端口时允许规范省略形式），Origin仅等于请求当前本地origin；cross-site拒绝，普通无Origin导航允许，无CORS。GET/HEAD同参数/安全规则，其他已知业务路由405；Jinja PackageLoader+autoescape+StrictUndefined。所有响应包括错误设置no-store、nosniff、DENY、no-referrer及A§11.2完整CSP。自定义参数错误不含input/ctx；已知ReadFailure固定中文/导航，未知500随机错误ID+脱敏诊断，不把异常repr交模板。
- [ ] **Step 4: 实现列表渲染。** render_runs在工作线程中完成查询/投影/脱敏/模板，不读完整Run；task完整允许字段先脱敏再截为200字符摘要。列与筛选按D§5：short ID、完整copy值、模型、Workspace、status/reason、验证类型/详情证据、active duration、USD、created_at；顶部total/状态计数，空结果清除筛选、next链接保参数，无cost排序/resolved rate。完整页/片段使用同一view，UTC time datetime标签；状态文字+颜色，表格横滚，键盘原生控件。
- [ ] **Step 5: Ruff/ty 后重跑Step2命令，预期PASS。** context不含reads/RunState/ArtifactRecord，来源href只经presentation生成；完整页与HX片段使用同一事实投影。
- [ ] **Step 6: 提交所属文件。** `git commit -m "feat(cli): serve the read-only dashboard run list"`。

### Task 19: 详情、Event/Snapshot 来源与同边界导航

**Files:** Modify `routes.py`、`pages.py`、`presentation.py`；Create `projections/snapshots.py`、`templates/{run,source}.html`、`templates/partials/{run_database,timeline,memory,snapshot}.html`；Test `tests/dashboard/test_detail_pages.py`。

**Interfaces:** Produces `render_run(deps:PageDependencies,run_id:UUID,params:EventParams,*,fragment:bool=False)->RenderedPage`、`render_events(deps:PageDependencies,run_id:UUID,params:EventParams)->RenderedPage`、`render_snapshot(deps:PageDependencies,run_id:UUID,version:int,params:SnapshotParams)->RenderedPage`；`project_snapshot(source:SnapshotSlice)->tuple[FieldView,...]`在projections/snapshots.py，仅投影限定消息/必要元信息。全部同步操作在Task17一个heavy闭包内。来源URL参数统一`boundary=<SourceCursor>`，分页`cursor=<EventCursor>`。

- [ ] **Step 1: 写 `test_detail_refresh_changes_all_database_facts`、`test_event_pagination_keeps_original_overview`、`test_source_links_keep_boundary_and_filters`。** TC-U3-01～03、TC-U5-05、TC-U7-08、TC-U8-01/02、TC-U16-02 Event/Snapshot局部路径；分页fragment不能含新Run概览，完整GET仍有上级导航。

  ```python
  assert 'data-as-of="240"' in f0_page.text
  assert 'data-snapshot-version="2"' in f0_page.text
  assert 'data-event-sequence="241"' not in old_fragment.text
  assert 'data-as-of="241"' in refreshed_page.text
  assert requested_message in source.text
  assert unrequested_message not in source.text
  ```

- [ ] **Step 2: RED。** `uv run --extra dashboard pytest tests/dashboard/test_detail_pages.py -q`；预期边界/普通GET/来源范围断言失败。
- [ ] **Step 3: 实现接口及路由。** `/runs/{run_id}`读取同事务首屏+同边界tool/scope/compaction精确来源；identity/result、usage/budget、memory与timeline一起替换`#run-database`。主页面不接受旧boundary/cursor来固定概览；保留筛选/锚点参数，刷新总是新边界。`/events`已验证cursor或boundary，精确event_id定位时绕过筛选；Step切换只改变呈现，未加载完整Step标局部。snapshot路径version≥1、合法边界和消息范围；当所选Snapshot确为对应new Snapshot时可显示该历史memory，其余仅来源范围；不重新读可变Run。模型/tool payload用details/pre纯文本，无模型Markdown；Tool结果status/completeness同时可见。来源返回链接保存规范筛选与锚点，禁止任意return_url开放跳转；安全相对路径由presentation生成。
- [ ] **Step 4: Ruff/ty 后 Step2 PASS。** 详情所有必需文案/read_at/as_of/version可见；非终态最后记录状态；已持久化异常项保留，过大Run不声称无摘要。HEAD与GET同状态无body；更换主页面不沿用旧详情边界，文件不在本项加载。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): connect dashboard run and source views"`。

### Task 20: Workspace 页面、评测链读取与 Artifact 预览路由

**Files:** Modify `routes.py`、`pages.py`、`presentation.py`、`queries/runs.py`；Create `templates/workspace.html`、`templates/partials/{workspace_facts,artifact_metadata,artifact_preview,evaluation,diff}.html`；Test `tests/dashboard/test_workspace_pages.py`。

**Interfaces:** Produces `render_workspace(deps:PageDependencies,run_id:UUID,params:WorkspaceParams,*,fragment:bool=False)->RenderedPage`、`render_artifact(deps,run_id:UUID,artifact_id:UUID,params:ArtifactParams)->RenderedPage`；`read_workspace_page(database:Path,run_id:UUID,*,budget:ReadBudget,connect:ConnectionFactory=sqlite3.connect)->RunPageRead`（新边界Run+同边界EvaluationCompleted，放queries/runs.py，events字段仅评测引用）。

- [ ] **Step 1: 写 `test_workspace_survives_deleted_active_directory`、`test_report_type_variants_reach_workspace`、`test_workspace_partial_requests_preserve_database_boundary`、`test_preview_rechecks_content_and_cursor`。** C_good由真实FileArtifactStore登记，Workspace目录不存在；TC-U3-04/05、TC-U9-07/09、TC-U12-01～16消费者后盾、TC-U4-07。

  ```python
  assert "已记录独立评测通过" in complete.text
  assert "目标测试清单未持久化，无法复核完整覆盖" in complete.text
  assert "已记录独立评测通过" not in invalid_bool.text
  assert "report_fields_invalid" in invalid_bool.text
  assert hash_changed.status_code == 409
  assert wrong_preview_binding.status_code == 422
  ```

  TC-U12每个类型variant必须经实际JSON→合法manifest/ref→页面，断言错误是字段类型而非hash；不能只挑一个resolved-string后声称全部消费者路径覆盖。
- [ ] **Step 2: RED。** `uv run --extra dashboard pytest tests/dashboard/test_workspace_pages.py -q`；预期Workspace组装/逐项冲突/绑定失败。
- [ ] **Step 3: 实现接口。** Workspace首屏短事务取新边界Run/Evaluation引用，关闭DB后manifest；评测引用按sequence取最近最多200条并检查额外一条，事件正文仍按预算加载，若还有历史或超限则显式“评测关联未完成加载”，保留详情时间线筛选入口且不得默认patch。首先metadata100项和完整patch发现，页外引用按ID精确补查，按需读取config/summary/report/所选patch，按一个共享64MiB预算，不读取全部日志。预算不足证据not_loaded/不完整，允许用户单项查看。patch默认按Task16，一次只读选中patch；被评测引用patch验证用于链，不重复在同请求读同ID（请求内去重非跨请求cache）。metadata分页`/workspace?boundary=...&cursor=...`只更新metadata，评测/Run不重读替换；首次无boundary与整页刷新新边界。Artifact预览必须携带SourceCursor或PreviewCursor；校验同Run、artifact/hash/redaction_version/offset，不接受另一用途token。合法登记hash变更但旧游标为422，bytes违背现manifest为409；每次请求重校验。URI及宿主路径不入模板。
- [ ] **Step 4: Ruff/ty 后 Step2 PASS。** 三个事实与两种读取时间独立；日志只在用户打开时读，source ref错误单项问题不拖垮整个Workspace。Local无SWEbench卡，unknown不猜instance，晚发布刷新可见；GET/HEAD无原文下载。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): browse saved workspace and evaluation evidence"`。

### Task 21: 本地 HTMX、轮询代次与刷新保位

**Files:** Create `static/dashboard.js`、`static/vendor/{htmx.min.js,htmx.LICENSE}`、`tests/acceptance/dashboard/__init__.py`；Modify templates/partials、dashboard.css；Modify `pyproject.toml`、`uv.lock`（dev `playwright>=1.50,<2`）；Test `tests/acceptance/dashboard/test_refresh.py`、`test_content_execution.py`。浏览器测试直接使用Playwright同步API，不增加pytest-playwright/Node；实际ASGI服务在隔离子进程运行。

**Interfaces:** 所有交互由本地dashboard.js注册，无内联事件。DOM固定：`#run-list`、`#run-database`、`#workspace-facts`、`#artifact-metadata`、`#artifact-previews`、`#refresh`、`#auto-refresh`；数据属性`data-run-status,data-read-at,data-as-of,data-snapshot-version,data-generation`，Event为`id="event-{uuid}"`、`data-event-sequence`，展开控件`data-expand-id`。来源/分页全部保留真实GET href，HTMX只是优化。

- [ ] **Step 1: 写 `test_poll_backoff_and_visibility`、`test_old_response_cannot_replace_new_filter`、`test_refresh_restores_far_anchor`、`test_workspace_preview_survives_metadata_refresh`。** Playwright受控clock+传输调度，TC-U15-01～08、TC-U13-02/07。只控制浏览器网络时序，不替换数据库查询。

  ```python
  assert next_delays == [10000, 20000, 30000, 30000, 5000]
  assert failed_read_at == original_read_at
  assert old_response_delivered_dom == new_filter_dom
  assert locator_window_event_count <= 41
  assert preview_after_page_refresh == original_preview
  ```

- [ ] **Step 2: RED。** 安装dev依赖及本机Playwright Chromium后 `uv run --extra dashboard pytest tests/acceptance/dashboard/test_refresh.py tests/acceptance/dashboard/test_content_execution.py -q`；缺浏览器不算RED。预期退避/乱序/锚点断言失败。
- [ ] **Step 3: 实现刷新状态机。** 默认手动；启用5秒，失败10/20/30封顶、成功5，失败不替换DOM/read_at；隐藏暂停发新请求，恢复按保留间隔等待；读终态停自动但手动有效。每页generation递增，筛选/整页刷新使旧pending响应不能swap，HTMX请求同步与beforeSwap双重校验；代次通过有界非负整数`X-Dashboard-Generation`请求头传递并原样回响应头，不增加未知query参数或混入游标。详情整体数据库区域新边界，记筛选/锚点/展开；远处锚点按新SourceCursor精确ID窗口读取，不回放旧分页；不匹配筛选保留筛选+独立定位提示。Workspace刷新facts+metadata，预览区域不替换，只显式重载预览才重读/hash。列表刷新当前页，若原位置无效回首页给更新提示。
- [ ] **Step 4: 接入本地资源。** 按[HTMX官方安装与配置文档](https://htmx.org/docs/)核对的发行版锁定HTMX 2.0.11，从上游发行包保留license和真实SHA-256记录于交付证据；不使用CDN运行。`allowEval=false,allowScriptTags=false,includeIndicatorStyles=false,selfRequestsOnly=true,historyCacheSize=0`，页面`hx-history=false`，避免HTML进入localStorage。配置用本地JS且在DOM初始化前生效，CSP不放宽；indicator CSS写入本地样式。时间用Intl显示浏览器时区并保UTC title，无JS为UTC；copy ID与details键盘可用。更新HTMX版本须由计划负责人修订此决定。
- [ ] **Step 5: Ruff/ty后运行Step2命令，预期PASS。** 真实脚本哨兵无副作用/外网请求，localStorage无页面正文，console无因eval/内联样式违反CSP而失效的功能。
- [ ] **Step 6: 提交所属文件。** `git commit -m "feat(cli): preserve dashboard state across refreshes"`。

### Task 22: 独立 Typer CLI、optional extra 与 wheel 兼容

**Files:** Create `src/miniagent/dashboard/cli.py`；Modify `pyproject.toml`、`uv.lock`；Test `tests/dashboard/test_cli.py`、`tests/acceptance/dashboard/test_installation.py`。

**Interfaces:** Produces `app:typer.Typer`、`entry()->None`；`miniagent-dashboard="miniagent.dashboard.cli:entry"`。仅database/artifacts/port/help，标准Typer无completion额外选项，旧miniagent script不变。

- [ ] **Step 1: 写 `test_cli_does_not_read_provider_settings`、`test_missing_extra_explains_installation`、`test_wheel_preserves_existing_task_entry`。** TC-U1-01～06/08/09、TC-U10-22；构建真实wheel，隔离最小和extra两个环境，不在同一环境假装卸载依赖。

  ```python
  assert "uv sync --extra dashboard" in missing_extra.output
  assert "Traceback" not in missing_extra.output
  assert "--host" not in help_result.output
  assert "--workers" not in help_result.output
  assert "--experiments" not in help_result.output
  assert served_run_id == explicit_database_run_id
  ```

  无key/no.env及恶意.env/旧环境变量指另一库两例；原miniagent TASK走原入口，沿用已有tests/cli边界Provider替身或实际协议的本地假Provider服务，不调用真实模型，不只测--help。
- [ ] **Step 2: RED。** `uv run --extra dashboard pytest tests/dashboard/test_cli.py tests/acceptance/dashboard/test_installation.py -q`；预期安装/入口/无凭证启动行为失败。
- [ ] **Step 3: 实现CLI与打包。** cli顶层只导入Typer/config，entry解析参数后延迟import FastAPI/Uvicorn/app；未装extra给安装说明非ImportError栈。Pydantic校验port，固定uvicorn host=127.0.0.1,workers=1,access_log=False；无browser open。启动结构检查失败退出非零且不建目录/库。wheel显式验证包含templates、static、license；uv_build默认包含若已成立不增加多余配置。缺artifacts根视为当前无文件，不创建；只有预览已登记却内容缺失时为问题。
- [ ] **Step 4: Ruff/ty 后 Step2 PASS。** `uv run miniagent --help`和真实旧TASK回归；wheel启动后请求/runs/healthz与全部静态资源，观察单worker/监听地址；最小安装无Web模块仍能import运行原CLI。生产脚本不导入bootstrap/application。
- [ ] **Step 5: 提交所属文件。** `git commit -m "feat(cli): package the optional dashboard command"`。

### Task 23: 真实非普通文件与 FD 替换竞争证据

**Files:** Create `tests/acceptance/dashboard/test_file_boundaries.py`；Modify `tests/dashboard/observation.py`；若证据发现缺陷，仅修改 `artifacts.py` 中所属行为并补最小回归，不改producer。

**Interfaces:** Consumes Reader的公开OpenFd边界；Produces测试支持 `RecordingOpen`：原签名委托os.open并记录真实FD/fstat，仅对选定relative path在open返回后用Pipe握手；不替换内容、hash或系统调用结果。子进程监督15秒，finally仅回收本测试进程/FD/临时路径。

- [ ] **Step 1: 写 `test_fifo_and_socket_are_rejected_without_waiting`、`test_open_content_survives_path_replacement`、`test_open_directory_survives_node_replacement`、`test_actual_oversize_reads_no_content`。** TC-U14-01～03/09，TC-U9-13后盾；manifest/content各FIFO无writer、Unix socket、目录；G-FD已确认open后外部rename并放根外symlink/另一普通文件。

  ```python
  assert product_finished_before_supervisor_kill
  assert replacement_sentinel not in response_text
  assert response_bytes in (verified_original_bytes, None)
  assert oversized_content_read_count == 0
  assert light_request_completed
  ```

- [ ] **Step 2: 运行。** `uv run --extra dashboard pytest tests/acceptance/dashboard/test_file_boundaries.py -q`；真实边界既有正确时可初次PASS，记录特性验证，不制造RED。缺陷必须先记录可定位行为失败，再最小修复。
- [ ] **Step 3: 完成实际OS证据。** 区分manifest/中间目录/content各攻击位置；记录FD身份、替换前后inode/时间线；同FD安全原内容或拒绝均成立，绝不能读替代内容。设备文件只在能安全准备的真实环境执行，否则该variant DEFER；不mock设备。RO仅排除夹具实际改写的确切文件，不排除整个artifact树。
- [ ] **Step 4: 若有Python修复先Ruff/ty，再重跑Step2与 `tests/dashboard/test_artifact_content.py`，预期PASS。** 即使HTTP已返回也确认无FD/线程残留；监督kill不是正确退出证据，记录FAIL后回收。
- [ ] **Step 5: 审查并提交。** 仅测试时 `git commit -m "test: verify dashboard file descriptor boundaries"`；若修产品用 `fix(cli): reject unsafe dashboard artifact reads`，说明实际修复行为。

### Task 24: 真实 SQL 预算、重阶段线程与取消后的名额

**Files:** Create `tests/acceptance/dashboard/test_resource_boundaries.py`；Modify `tests/dashboard/observation.py`；若发现缺陷，最小修改 `queries/connection.py`、`workers.py` 或 `pages.py` 对应归属。

**Interfaces:** Consumes connect/open_fd/monotonic/work_pool公开边界，G-DB/G-SQL/G-WORK/G-STAGE；Produces `RecordingConnection`/`RecordingCursor`委托真实sqlite3操作；测试`RecordingPool(HeavyWorkPool)`只覆盖公开run，调用`super().run(tracked_operation)`，tracked_operation记录真实operation开始/完成并原样执行，取消后完成记录不丢。这个实际operation区间位于名额持有区间内。`StageRecorder`在隔离子进程通过threading.setprofile_all_threads记录实际公开函数call/return与hash.update C调用线程，不替换生产decode/project/redact/hash函数。

- [ ] **Step 1: 写 `test_real_sql_is_interrupted_after_deadline`及`test_real_sql_before_deadline_succeeds`、`test_heavy_stages_run_inside_worker_slot`、`test_cancelled_http_keeps_slot_until_worker_finishes`。** TC-U14-04～12。目标SQL由实际/runs task/model筛选面和大合成数据触发，不新增任意SQL HTTP端点；Connection trace只确认实际语句开始，监督者受控clock越过t0+5秒后放行查询。G-SQL不能以handler触发作为前置。

  ```python
  assert timed_out_response.status_code == 503
  assert sql_interrupted and actual_worker_finished
  assert before_deadline_response.status_code == 200
  assert expected_stages <= recorded_actual_stages
  assert all(stage.thread_id != asgi_thread_id for stage in stages)
  assert all(slot_start <= stage.start <= stage.end <= slot_end for stage in stages)
  assert peak_active_heavy_work <= 2
  ```

- [ ] **Step 2: 运行。** `uv run --extra dashboard pytest tests/acceptance/dashboard/test_resource_boundaries.py -q`；记录每variant实测结果。观测设施先独立验证能辨认请求/实际线程；目标SQL已确认开始但跨截止仍活动到监督回收为产品FAIL，不能转DEFER。
- [ ] **Step 3: 实现/补齐真实观测并修暴露缺陷。** busy用实际rollback-journal模式的冲突锁，不用WAL普通writer锁伪造读阻塞；解锁后重读200。目录/文件/JSON预检/diff循环独立越截止，按本计划Q10状态断言部分或503。G-STAGE预先列预期阶段：Run decode、Snapshot decode、Artifact JSON decode、页面纯投影、hash、完整脱敏；用公开函数code对象+真实C调用采样，不能只记录提交闭包。两实际heavy工作握手暂停后第三503、health可调度；取消ASGI请求但工作仍活动时第三仍503；实际结束后第三200。HTTP coroutine结束不能替代worker结束。
- [ ] **Step 4: 固定环境与验证。** 在测试报告登记OS/SQLite/monotonic精度、独立握手开销和ε，监督15秒；受控clock精确验证deadline±1ms，真实1秒busy计时单列。RO、任务/连接/FD最终回收。Ruff/ty后Step2及受影响Task2/17测试PASS；设施/环境不可用DEFER，不能掩盖已观察到的失败。
- [ ] **Step 5: 审查并提交。** 仅测试时 `git commit -m "test: verify dashboard resource lifetimes and deadlines"`；有产品修复按实际行为用fix(cli)。

### Task 25: 横切 HTTP、只读与泄露治理集合

**Files:** Create `tests/dashboard/test_governed_routes.py`；Modify `tests/dashboard/{builders,observation}.py`；若发现遗漏，最小修改 `app.py`、`presentation.py`或所属投影。

**Interfaces:** Consumes 全部公开HTTP路由/HEAD/错误类；Produces一组参数化治理入口清单，按完整页、HTMX片段、Event来源、Snapshot来源、metadata、preview、health、static、各错误分类记录，不新增产品接口。

- [ ] **Step 1: 写 `test_all_read_routes_preserve_business_data`、`test_each_display_surface_redacts_and_escapes`、`test_each_http_failure_has_its_own_semantics`。** TC-U1-07/08、TC-U13-01～08全出口、TC-U14-10；每展示面独立植入task/output/payload/summary/metadata/diff/log/error/query/诊断合成哨兵，保留可读非敏感对照。

  ```python
  assert after.logical_rows == before.logical_rows
  assert after.schema == before.schema
  assert after.artifacts == before.artifacts
  assert not observed_write_sql
  assert all(s not in html_headers_logs for s in secret_sentinels)
  assert readable_control in response.text
  assert head.content == b""
  ```

- [ ] **Step 2: 运行。** `uv run --extra dashboard pytest tests/dashboard/test_governed_routes.py -q`；初次既有行为全绿记回归，遗漏先记录正确断言失败再修，不用“没有通过标签”代替正确问题类别。
- [ ] **Step 3: 完成真实故障入口。** 启动后替换/损坏本测试DB，读取500+错误ID而非空200或404；schema不支持启动失败、单行坏schema200占位；不存在404、登记冲突409、超限200、busy/budget/heavy503、参数422、同源403、方法405。错误响应与日志无input/ctx、搜索词、URI或宿主路径，诊断用同错误ID关联。验证每读单元无initialize/migration/DDL/DML、不设置journal mode；除受控writer/替换夹具明示变更外RO。HEAD同参数/Host安全，不返回body。
- [ ] **Step 4: Ruff/ty 后 Step2 PASS。** 完整CSP各self/object/frame/base、DENY/nosniff/no-referrer/no-store包括静态/错误；source/preview链接只校验ID+相对路由+签名，不输出任意URI；无Compare/Checkpoint/恢复入口或占位。
- [ ] **Step 5: 审查并提交。** `git commit -m "test: verify dashboard read-only and display boundaries"`（如含产品fix则按实际intent调整）。

### Task 26: 唯一跨页主旅程、无 JS 与交付审查

**Files:** Create `tests/acceptance/dashboard/test_journey.py`；Create `docs/superpowers/reviews/2026-10-19-dashboard-implementation-evidence.md`（执行时记录真实结果）；仅在验证失败时修改所属实现/测试。

**Interfaces:** Consumes真实wheel、实际服务、本地资源、合成持久化R/M_good/C_good/P；Produces D/TA/TC→pytest node/variant→实测结果与残余DEFER的交付记录。主旅程仅一条，不复制所有报告/金额逻辑到浏览器。

- [ ] **Step 1: 写 `test_persisted_evidence_journey`、`test_without_javascript_has_get_navigation`、`test_broken_source_preserves_other_evidence`。** 严格沿TC-U16-01七步：`/`→列表→详情→Tool/Step→Event/Snapshot来源→Workspace→preview下一段→返回列表；原Workspace不存在。A1/E1局部直达页面，不重新跑MAIN全部步骤。

  ```python
  assert selected_run_id == expected_run_id
  assert source_boundary == detail_boundary
  assert workspace_boundary_read_at != detail_boundary_read_at
  assert preview_next_offset > preview_offset
  assert no_external_resource_requests
  assert logical_after == logical_before
  ```

- [ ] **Step 2: 自动验证。** `uv run --extra dashboard pytest tests/acceptance/dashboard/test_journey.py -q`；真实服务响应、包内HTMX/CSS/JS，不mock页面内容；无JS GET分页/来源/下一段可用、UTC与手动刷新可见。缺浏览器/包资源不能计PASS，修接线后重跑受影响案例。
- [ ] **Step 3: 人工审读 TC-U16-03。** 执行者记录实际OS/browser/视口、用键盘完成展开/来源/返回，长diff/窄屏横滚、焦点、状态文字+颜色、本地时区及UTC。审读“最后记录状态”“已记录独立评测通过/完整覆盖无法复核”“未完成校验/证据冲突”能否区分；保存具体发现，不宣称WCAG合规或用户研究。若没有实际浏览器会话，保持人工项未验证并交接，不伪造截图/观察。
- [ ] **Step 4: 完成交付验证。** Ruff/ty（仅变更Python）后执行 `uv run --extra dashboard pytest tests/dashboard tests/acceptance/dashboard -q`；原CLI/Artifact/存储兼容回归 `uv run --extra dashboard pytest tests/cli/test_main.py tests/artifacts/test_file_store.py tests/storage/test_sqlite.py -q`。这是首次整合验收，已完成后无新变化不反复全量重跑。检查wheel最小/extra结果、Tasks23/24真实证据、F1～F5对应测试能识别五类错误；只在隔离副本做定向错误变体，不提交突变。若变体未执行，标未验证，不虚报强度。证据文档逐node标PASS/FAIL/DEFER及命令/环境，D01–D16只有相关证据齐全才完成。
- [ ] **Step 5: 最终审查并提交。** 核对三页面、公开接口、异常分类、版本、无producer改动；所有critical修完且相关检查通过才 `git commit -m "test: verify the dashboard evidence browsing journey"`。交接具体残余DEFER/人工未执行项，不能以绿色组件测试宣称完整DoD。此计划不授权部署、公开发布或push。

## 覆盖索引与验收闭环

完整variant与输入数据仍以TD原行为依据；下表是任务分配，不是测试已执行或审计关闭记录。

| TD 范围 | 所属 Tasks / 对应证据 |
| --- | --- |
| U1 全部 | 2（缺库/结构）、18/25（HTTP/RO）、22（双安装/旧TASK/入口） |
| U2 全部 | 4（真实SQL）、18（模板/筛选URL/空结果）、21（刷新） |
| U3 全部 | 5（G-DB/F0-F1/来源）、19/20（页面接线）、21（整体刷新/保位） |
| U4 全部 | 3（独立签名/参数）、5/19/20（SQL/文件语义后盾）、22（真实重启失效） |
| U5 全部 | 6（失败SQL/Tool）、19（详情） |
| U6 全部 | 7（scope+纯投影），B15/B16可达性单列 |
| U7 全部 | 9（模板/hash/归组）、10（精确内容来源）、19（链接） |
| U8 全部 | 8（数值/事实）、18–20（页面口径） |
| U9 全部 | 1（词法正例）、11（发现）、12（FD）、13（预览）、16（diff）、20/23（消费/真实攻击） |
| U10 B01/B02/B03/B04/B05/B06 | 5为单项/合计正文主测；4补列表/recovery；24补实际SQL返回范围 |
| U10 B07/B08/B09/B10 | 4列表自由文本/标量；11 manifest；12内容/actual size；25各展示出口 |
| U10 B11/B12/B13/B14 | 13预览双约束；11 metadata；3/5 Event页大小与默认 |
| U10 B15/B16/B17/B18 | 7可达性与scope；10压缩链；1深度+13消费 |
| U10 B19/B20、21～28 | 3游标/搜索/页大小/UTC/有效期；22 port；5定位；13 offset/双限制；12读取块 |
| U11 全部 | 16纯决策+11完整发现、20页面接线 |
| U12 全部及严格类型variant | 14适配器、15链判定、20每字段真实消费者后盾 |
| U13 全部 | 1机制；18同源/错误；21真实脚本/CSP；25完整出口集合 |
| U14 全部 | 23真实文件；24真实SQL/线程/取消；25 RO/资源收尾 |
| U15 全部 | 21局部受控浏览器交互，不重复完整MAIN |
| U16 全部 | 26单条MAIN、局部无JS/异常与人工记录 |

| 验收 | Tasks | 验收 | Tasks |
| --- | --- | --- | --- |
| D01 | 3/4/18/21 | D09 | 14/15/20 |
| D02 | 4/8/18–20 | D10 | 18–22/25 |
| D03 | 5/6/19 | D11 | 8/9 |
| D04 | 6/19 | D12 | 8 |
| D05 | 5–7/19 | D13 | 3/5/19–21/24 |
| D06 | 9/10/19 | D14 | 1/12/13/18/21/25 |
| D07 | 16/20/26 | D15 | 1–3/5/7/10–13/17/21/23–25 |
| D08 | 11–13/20/23 | D16 | 2/18/22/24/25 |

F1：14严格字段+20消费；F2：24真实重阶段；F3：12/23零内容read；F4：1/13三份词法正例；F5：2/24真实SQL开始→截止→中断→实际完成。原audit仍保留历史结论；本计划覆盖映射不替代独立复核。

## 计划自审与实施交接

已按D§1–15/A§4–15、D01–D16、TA-01～29与TD U1～U16逐项分配任务；公共签名、Cursor用途、原始reads与脱敏views、schema版本和CLI边界集中固定。Q项的实施建议在本计划明确，不改输入设计的已确认范围。真实环境/人工评价尚无执行结果，不能在计划阶段关闭DEFER；B15/B16不可达情形需按证据记录。

每次任务交接须给出：Task编号、允许文件、依赖接口、完整TC variant、RED行为与GREEN命令/实际结果、critical审查结果、commit、剩余未验证项。若发现schema或producer已变、既定接口不能满足设计、真实观测设施无法实现，执行模型只报告该具体冲突并继续不依赖它的准备工作，不擅改设计或放宽测试。

本轮产物只有本计划；未运行runtime测试、未开始实现、未提交现有设计文件。按用户意图后续交给低阶模型执行，先审阅本计划再实施。建议采用逐Task串行的 Native 执行，每项保留独立审查，再作整分支复核；共享的游标/预算/证据类型较多，避免同时修改共同契约。
