# Trajectory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline, task by task. Steps use checkbox (`- [ ]`) syntax. 后续由低阶模型实施；本次只完善计划，不执行代码任务。

**Goal:** 始终保存完整模型与工具证据，按冻结配置可选导出 trajectory，并保证大 Event 不使压缩、恢复和诊断全量驻留历史正文。

**Architecture:** Event 是事实主线，完整工具流写不可变 Artifact，原 ToolResult 在业务校验替换前捕获。业务响应/判定/计费/状态一次提交，摘要沿用既有 attempt/发布边界；历史消费者固定边界分页折叠或定点查询。CLI 持 RunLock 导出，导出错误不改变原结果。

**Tech Stack:** Python 3.13、Pydantic、asyncio、SQLite、FileArtifactStore、Typer、pytest；不新增依赖。

**Spec:** [架构 A](../specs/2026-10-20-trajectory-architecture-design.md)、[需求 D](../specs/2026-10-20-trajectory-design.md)、[测试分析 TA](../specs/2026-10-20-trajectory-test-analysis.md)、[测试设计 TD](../specs/2026-10-20-trajectory-test-design.md)。A 优先于 D 的实现细节，TD 是测试输入与 oracle；不得从实现倒推 expected。任务覆盖索引：[100 个 TC 到任务/文件映射](2026-10-20-trajectory-test-map.md)。

状态：执行交接稿，待用户 review；代码、测试与 task ledger 均未开始。用户本轮授权以修订 A/TA/TD 完善计划，并指定后续低阶模型执行；这不等于本轮授权开始实现。A 首页旧状态与 R3 PROCEED 的差异在本计划登记，不改写源文档，不把咨询评审当产品验收。

## Global Constraints

- 完整证据始终保存；trajectory 默认不导出，配置只控制最终 JSON Artifact。
- 不保存模型 reasoning/thinking 正文；reasoning token 计数照常保存。
- Event/Snapshot schema_version=3，CLIApplicationDefinition=1，trajectory=1；不升级、不迁移、不删除旧开发期数据，不做多版本兼容。
- JSON kind=miniagent.trajectory；trace_id=run_id.hex；金额为十进制字符串，时间 UTC。生产 schema 不额外增加 identity 嵌套。
- JSON 上限 **67,108,864 UTF-8 bytes**；原 ToolResult 内联阈值 **65,536 UTF-8 JSON bytes（含等值）**；历史完整 Event 页大小最多 **16**。
- Settings.export_trajectory=False；EXPORT_TRAJECTORY；显式 CLI true/false > Settings > false；resume 继承冻结值，dry-run 零写入。
- 不新增 ModelResponseObserved、pending response、本地响应恢复协议；业务响应提交前丢失窗口接受为未知调用，不据 CancelledError 推断回滚。
- 原 ToolResult、有效有界 ToolResult、实际 ToolMessage 分开；外层归属来自当前 attempt，绝不信任被拒绝返回值的 ID。
- 全部 G1–G8 历史消费者适用固定 N、SQL 类型筛选、定点定位和不缓存历史 payload；不得仅优化 exporter。
- 不新增历史 export、JSONL、bundle、脱敏、专用 UI、后台采集、GC、通用 RunTrace、证据表或消息去重系统。
- pytest，优先真实 SQLite/Artifact/进程；故障装置仅在 provider、文件系统、SQLite driver/connection、OS/guest 等外部边界。禁止 patch Runtime 私有方法。
- F/M/C/B/H/S fixture 与屏障含义见 TD§3；C 的 expected 独立手算：每次 Decimal("0.000020")，两次 Decimal("0.000040")，不能调用生产计费函数生成 expected。
- 不修改 README，不触碰已有无关 config/blueprint 变更，不新增依赖、全局格式化或 AI attribution trailer。
- 只有任务列出的测试、全部 variant 与完成证据实际取得后才可 task-done；skip、窗口未命中、环境缺失不算通过。资源数值门槛 DEFER 不等于结构约束可免验。

## Review Focus

1. 大 Event 进入历史后，压缩/恢复/诊断或 Dashboard 全量解码或缓存正文；G1–G8 各自有资源及安全验证（Tasks 3–6、17）。
2. 原返回 ID/错误码被校验替换，完整证据随之丢失或被错归属；先捕获后验证，含大小边界（Task 11）。
3. 取消落在事务/文件发布后段，结果已提交却被当回滚，或锁释放后继续注册；必须证明真实窗口与排空（Tasks 1、8、9、10、14、16）。
4. provider 规范化失败丢正文或泄露 reasoning/headers；业务和摘要两条路径都要验证白名单（Tasks 7、8、13、16）。
5. resume 准备期间提前返回或收尾失败导致漏导出、原退出码改变、补丁校验弱化；覆盖真实入口和冻结值（Tasks 14–16）。

---

## 执行前裁定与证据基线

以下是本计划固定的选择。执行者先读，不自行重新设计；若实测证据推翻，按 executing-plans 在 ledger 写 Ruling 后做最小修订。源测试文档保持不变，不能悄悄改 expected 或把不可达场景标通过。

| 编号 | 已核对事实与处理规则 | 影响/验证 |
| --- | --- | --- |
| P1 / Q-1 | 用户已指定以修订架构对齐本计划，R3 为 PROCEED；使用当前 A 指纹。计划仍需 review 后才执行，不假定源文档旧状态已自动改写 | 不因旧页首停掉计划编写，也不跳过执行授权 |
| P2 / U05-02 | 当前 loop.py 在无效摘要 JSON/来源/预算校验后直接 fail_compaction，hard trigger 失败、soft trigger 回旧请求；并非同次调用自动重试。保留 A§5.2 的既有重试语义 | 将该 TC 的 invalid variants 实现为“软触发失败→业务继续到下一次允许压缩→新 attempt 成功”；先证明 fixture 可达。hard trigger 另验证终止。不得为测试新增 invalid-summary 自动重试；若两次软触发仍不可达，登记该路径未覆盖，保留失败/后续合法独立证据，不虚称 ST4→ST7 已测 |
| P3 / U06 E4 | 当前 Qwen 未知 finish reason 映射为 FinishReason.UNKNOWN，能够构造 ModelResponse | E4 进入 U03 的 MODEL_ABNORMAL_FINISH 业务拒绝；arguments/usage 规范化失败才走白名单失败证据 |
| P4 / U03 R3 | TD 明确缺 usage 拒绝不依赖 pricing；当前代码仅 pricing 配置时拒绝。采用 TD 的显式规则，保留完整 response，计数一次，不执行其中动作 | Task 8 对 pricing 有/无各测一例；这是预期行为修订，不是保留旧分支 |
| P5 / U03 R6 | `PriceConfig.price_for` 对模型缺失抛 ValueError；将“已验证价格配置中无该模型”视为可预期配置失败，响应与失败 disposition 同提交，cost=null、calculation_failed | 直接 Runtime 公开依赖构造此输入；只捕获该已知条件，不把所有 ValueError/程序错误吞成普通拒绝。其他意外错误按 U03-04 传播 |
| P6 / U05-05 | 摘要缺 usage 不凭空新增另一套拒绝/重试规则：完整响应和未确认结算照存，是否发布继续由既有摘要有效性规则决定 | 无 pricing/缺 usage/协议失败/无 response 分开断言；保存响应本身不表示发布 |
| P7 / U12 | TD 未规定 writer 重入 API。只锁定单写者状态：write 仅写入阶段；finish 只启动一次发布；取消后查询已发布结果不发起第二次发布；关闭后继续写抛 RuntimeError | 删除旧计划“重复 finish 必须幂等”的额外测试要求；不扩展无需求 API |
| P8 / Q-2/3/4/6 | Docker/实际屏障/数值性能门槛/人工验收与独立 reviewer 的证据在执行时取得，未取得则标 NOT-RUN/WINDOW-NOT-REACHED/DEFER，不标 PASS | 继续可独立完成的任务，不能将整项完成或发布；低阶 executor 不自行豁免 |

输入 HEAD `02b42a9b24b4f0d8f4fbae27c20e735fba7fb53a` 不能代表未提交输入。初始 SHA-256：

```text
D  5041c49975278986bd114ad1568ff5b14158e79419ca5ddf6b8aeae2c594adaa
A  f4ae74eb64edbd15469a1b04404633c60119e899038c9fb05c6710f2f96a20ce
TA 72afb9d1c463126f4e0c924b13dad6ed8f46752e2ba1d3f075c1ceb82fe2aeb0
TD fd6c93f177a9ee317de272c521afde4e529d0d7048358acfc5a6849e37c3f824
```

## 执行协议：低阶模型必须遵循

实施启动时加载 executing-plans、using-git-worktrees 和 TDD；确认隔离 checkout 含上述未提交输入的确切内容。不得直接 `git stash` 全工作区或用 main HEAD 代替批准输入。无需在本次计划编辑时建 worktree/ledger。

1. 用 skill 的 `sdd-workspace PLAN_FILE` 取得专属目录，ledger 首行为 `# SDD ledger — plan: docs/superpowers/plans/2026-10-20-trajectory.md`。完整阅读本计划 Global Constraints、裁定、接口表以及 A/TA/TD；task-start 只抽取任务正文，不会自动附带全局约束。
2. 每任务 `task-start PLAN_FILE N`，记录 BASE 并读 brief。以下 `TEST` 段是该任务 **task-done 的精确命令**；`RED` 段是首个行为红灯命令。不可用 `-k smoke` 或缩减 variant 替代。
3. 每个映射 TC 创建 `test_tc_uxx_nn`；参数 id 用 `TC-Uxx-nn[variant]`，variant 展开 TD 表内各分区（尤其 U02 的18实例、G1–G8）。同一 TC 多文件验证允许，但必须有一个指定主责任务；新增环境后盾在映射表列出，不按单实例通过算全 TC。
4. F 真数据库/文件 fixture 复用 `tests/support`；新增 helpers 只放 `tests/support/trajectory/`，按 fixtures、barriers、history、worker 职责拆分，不写通用 scenario DSL。测试中的局部 result/record/expected 均由该 TC Given 和实际动作产生；下文断言片段不是可跳过 setup 的伪测试。
5. RED 必须因目标行为缺失失败，而不是拼错 import/环境不可用。已有行为测试原本通过可登记 BASELINE，不人为破坏代码造红灯；每次生产行为修订仍至少有相应 RED→GREEN。
6. 绿灯前执行 changed-files ruff check --fix/format；按 Files 中本次实际 Python 文件显式列路径。然后运行 TEST，检查 `failed=0,error=0` 且所需 TC/variant 无 skip。最后关键问题 review、显式 git add 本任务文件并按 commit message 提交；不 add 整个工作区。
7. 运行 `task-done PLAN_FILE N BASE -- uv --cache-dir /private/tmp/miniagent-uv-cache run pytest <TEST 中的相同路径> -q`。它记录测试命令，不替代前述无 skip/屏障证明检查。将 TC→pytest nodeid/实现 revision/窗口证据/结果追加到本 workspace 的 `evidence.md`；尚缺必要真实后盾的 task 不写 complete。
8. 每个偏离写 `Ruling: 决定 — 依据 — 错误代价`。compaction 后先读 ledger 和 git log，从首个未完成 task 继续。测试失败最多一次诊断重跑，不重跑到绿；保存最初输出。
9. Tasks 1–16 完成后 Task 17 收集横切证据；最后按 executing-plans 用最强可用模型做一次 fresh whole-branch review，Critical/Important 修复须 RED→GREEN，Minor 记账。低阶模型不把自己的 self-review 当独立审查，不自行 merge/push。

## 共享接口与文件边界

精确字段/函数在生产任务 Interfaces 定义；后续任务引用，不改名重造。初始接口 pre-flight 将每行对照结果记 ledger；读到旧计划号或旧 signature 不沿用。

| 生产 → 消费 | 接口/依赖 | 不允许的替代 |
| --- | --- | --- |
| 1 → 2、9–14 | ArtifactWriter、ArtifactPersistenceError、deadline-aware put | 全量 stdout bytes 或锁外后台 publish |
| 2 → 13、14 | list_records_for_run / ArtifactRecordPage | list_for_run 后过滤大正文 |
| 3 → 4–6、8、13 | 固定 N 的 iter_run_events、SQL types、get_event | list_events 无 limit 或分页后 tuple 全历史 |
| 4 → 8、12、16 | 紧凑压缩来源索引与逐 Snapshot 验证 | 新 pending response/摘要恢复机制 |
| 5 → 9、10、14、16 | collect_history_facts、原 planner 结果 | 丢旧未知执行链、仅验当前 active_operation |
| 6 → 11、12、13、17 | 有界有效 ToolResult 查询、Dashboard metadata | 解码 raw_result 为取有效结果，页面累计全正文 |
| 7 → 8、13、16 | ProviderResponseEvidence、ModelResponseNormalizationError | SDK dump、无效 usage 入账 |
| 8 → 9、11–16 | request/response/compaction 扩展事件，一次业务提交 | 新 ModelResponseObserved / pending |
| 9 → 10、11、12、16 | CommandEvidence、StreamEvidence、OutputCapture、observer 扩展 | helper 控制帧作为 stdout |
| 11 → 12、13、16 | CapturedToolReturn、raw_result/validation/observation | 在 `_execute_prepared` 返回合成错误后才捕获 |
| 12 → 13–16 | 必需 run_store/artifact_store、CLI 冻结定义 | 生产静默回退内存 |
| 13 → 14、15、16 | TrajectoryDefinition、encode_trajectory | 引入 CLI 依赖到 trajectory 包 |
| 14 → 15、16 | TrajectoryExportOutcome、try_export_trajectory | finalization_errors 改原成功退出码 |
| 15 → 16、17 | CLI 三态/冻结继承/诊断 | resume 临时切换、dry-run 写入 |

以下任务按 1→17 顺序执行。历史读取任务先于放大 Event，避免中间可运行版本突然全历史驻留。Tasks 3–6 的历史 fixture 先使用当前 schema 合法的大正文；Tasks 8/11 接入新证据时扩展这些 fixture 并重跑对应历史读取回归，不能提前依赖尚未定义的字段。任务独立交付可 review，不表示每项已经完成整个产品验收。

### Task 1: 流式 Artifact 与可收敛发布

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U12-01、TC-U12-03、TC-U12-04。U12-01/03/04 的 writer 与 put 阶段全部测；增加磁盘 write/body/manifest 故障基础，CLI exported 映射由14/16补齐。锁竞争的最终证明由16负责。

**Files:** 修改 `src/miniagent/artifacts/{contracts,file,__init__}.py`；新增 `src/miniagent/artifacts/writer.py`、`tests/it/artifacts/test_stream_writer.py`；回归 `tests/it/artifacts/test_file_store.py`。

**Interfaces:**
- `ArtifactStore.open_writer(request: ArtifactWrite, *, deadline: float | None = None) -> Awaitable[ArtifactWriter]`。
- `ArtifactWriter.write(chunk: bytes) -> Awaitable[None]`、`finish() -> Awaitable[ArtifactRef]`、`abort() -> Awaitable[None]`。finish 后关闭该 writer；完成后 write 拒绝；abort 清理未发布暂存，不能删除已发布内容。
- `ArtifactPersistenceError(RuntimeError)` 定义于 contracts.py，翻译正文写入/发布故障并保留 cause。`put(request: ArtifactWrite, content: bytes, *, deadline: float | None = None) -> Awaitable[ArtifactRef]` 保持旧调用兼容，复用同一写入与发布纪律；deadline 在 writer 内检查，不能仅依赖外层 asyncio.timeout。

- [ ] **Step 1 — 写失败测试。** 临时 FileArtifactStore，按 64 KiB chunk 写入 8 MiB，重开 store 读取并比对 hash/内容。空正文也注册；abort 不产生 manifest。关键断言：
  ```python
  # test_stream_writer_preserves_bytes_after_reopen
  assert ref.size_bytes == len(expected)
  assert ref.sha256 == hashlib.sha256(expected).hexdigest()
  assert await reopened.read(ref) == expected
  # test_abort_does_not_publish
  assert await store.list_for_run(run_id) == []
  ```
  `test_cancelled_publish_settles_before_return` 在文件 I/O 外部边界设置可控 barrier，覆盖准备阶段取消、manifest 发布已开始、重复取消；释放 barrier 后验证可读 ref 或无 manifest，且任务返回后目录不再变化。不得 patch Runtime 私有方法。
- [ ] **Step 2 — 验证红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/artifacts/test_stream_writer.py -q`；预期因新接口缺失失败。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现 writer。** 64 KiB 写入块、增量 sha256/size、Artifact root 下暂存、flush/fsync、内容发布后 manifest 原子发布；每次 write 等待完成，禁止无限队列/read-all。所有已开始的工作线程写入都要 drain；deadline 后不启动新发布。发布已完成时 finish 返回 ref，取消不制造“未发布”的假结论。磁盘 syscall 阻塞不声称硬实时终止。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/artifacts/test_stream_writer.py tests/it/artifacts/test_file_store.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交本 task 文件。** 检查关键异常/接口调用者；`git commit -m "feat(artifacts): persist streamed evidence with settled publication"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 2: 只读 manifest 分页

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U17-01、TC-U17-05。U17-01/05：不得读取普通正文；跨页排除trajectory的导出行为由13完成。

**Files:** 修改 `src/miniagent/artifacts/{contracts,file,__init__}.py`；新增 `tests/it/artifacts/test_manifest_listing.py`。

**Interfaces:** `ArtifactRecordPage`（frozen BaseModel，records: tuple[ArtifactRecord, ...]，next_cursor: str | None）；`ArtifactStore.list_records_for_run(run_id: UUID, *, cursor: str | None = None, limit: int = 100) -> Awaitable[ArtifactRecordPage]`。cursor 为 store 返回的 opaque token，按 manifest 名排序；limit 必须为正。现有 list_for_run 校验语义不变。

- [ ] **Step 1 — 写失败测试。** 建立三条记录，limit=2 分页；删除其中正文后仍可列 metadata，read/get_record/list_for_run 仍拒绝损坏正文；跨 Run/非法 cursor 拒绝。断言：
  ```python
  # test_manifest_pages_do_not_read_content
  assert {r.artifact_id for r in (*first.records, *second.records)} == expected_ids
  assert second.next_cursor is None
  # test_validating_query_still_rejects_missing_body
  with pytest.raises(ArtifactNotFoundError, match="Artifact not found"):
      await store.list_for_run(run_id)
  ```
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/artifacts/test_manifest_listing.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现分页。** 仅读取当前页 manifest，验证归属/字段；可遍历文件名排序但不得加载全部 manifest/body。写入互斥由调用者的 RunLock 保证，不构建新索引服务。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/artifacts/test_manifest_listing.py tests/it/artifacts/test_file_store.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(artifacts): list registered metadata without reading bodies"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 3: 固定边界分页、SQL 类型筛选与单事件定位

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** U14/U15 的公共读取基础（完整TC主责见映射表）。按G范围执行对应结构与安全variant，资源后盾由17汇总。

**Files:** 修改 `src/miniagent/runtime/{store_contracts,events}.py`、`src/miniagent/storage/{sqlite.py,queries/events.py}`；新增 `src/miniagent/runtime/history.py`、`tests/it/storage/test_event_boundary.py`。

**Interfaces:**
- Consumes：现有 RunEvent/RunEventType、RuntimeStore/RunEventStore；不改 schema。
- Produces：两个 store protocol 和 SQLite 实现统一 `async def list_events(self, run_id: UUID, *, after_sequence: int = 0, through_sequence: int | None = None, types: tuple[RunEventType, ...] | None = None, limit: int | None = None) -> list[RunEvent]`。None 类型表示全部，空 tuple 表示零匹配；负下界、负上界、非正 limit 拒绝；上界小于游标返回空。
- `async def get_event(self, run_id: UUID, event_id: UUID, *, through_sequence: int) -> RunEvent | None`；Run/上界限定，查不到返回 None，坏当前格式抛 CorruptStoredDataError。
- history.py：`async def iter_run_events(store: RuntimeStore, run_id: UUID, *, through_sequence: int, after_sequence: int = 0, types: tuple[RunEventType, ...] | None = None) -> AsyncIterator[RunEvent]`，固定 page size=16，无全历史缓存。无筛选时验证连续性至 N，有筛选时只验严格递增/归属/上界，不要求相邻 sequence 连续。

- [ ] **Step 1 — 写失败测试。** TC-U14-01 的分页基础、U14-04 SQL 过滤、U15-01 缺口基础：真实 SQLite，边界15/16/17/32/33；N 后存在数据；相邻页交界缺口；混合类型查询。SQL trace/EXPLAIN 证明 WHERE 筛选先于 Python decode；不能用 mock list_events 证明实际查询。
  ```python
  # test_filtered_events_keep_original_sequence
  assert [e.sequence for e in selected] == [2, 17, 32]
  assert await store.get_event(other_run, selected[0].event_id, through_sequence=32) is None
  # test_unfiltered_scan_rejects_page_boundary_gap
  with pytest.raises(CorruptStoredDataError, match="sequence"):
      async for event in iter_run_events(store, run_id, through_sequence=33):
          pass
  ```
- [ ] **Step 2 — RED。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/storage/test_event_boundary.py -q`。Expected: FAIL，接口缺失或现有无上界/类型行为不符，非 fixture 语法错误。
- [ ] **Step 3 — 实现查询。** SQL 参数化加入 sequence/type 谓词和 LIMIT，按 sequence 排序；get_event 复用解码和 Run 检查。iterator 每页释放后再加载下一页；不能在调用者 list/tuple comprehension 重新聚合。同步更新公开 store fake 的签名与当前查询回归。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/storage/test_event_boundary.py tests/it/storage/test_resume_transactions.py -q`。Expected: PASS，页/边界/归属全满足；changed-files ruff。
- [ ] **Step 5 — 提交。** `git commit -m "feat(storage): bound and target run event queries"`；Expected: 仅本 task 文件进入提交，随后同 TEST task-done。

### Task 4: 压缩来源与恢复链按需读取（G1/G2）

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U15-02、TC-U15-03、TC-U15-04。按G范围执行对应结构与安全variant，资源后盾由17汇总。

**Files:** 修改 `src/miniagent/runtime/context.py`；新增 `tests/it/runtime/test_compaction_history_reads.py`；复用 `tests/support/runtime/{context,compaction,compaction_resume}.py`。

**Interfaces:**
- Consumes：Task 3 `iter_run_events/get_event`，现有 `load_version(run_id, version)`。
- Produces：context.py 内部 `CompactionSourceFacts` frozen dataclass，只保留 current/previous version、event_id/sequence、source/new snapshot id/version、source event sequence、covered_messages/steps、retained_first_step、summary_sha256；禁止含 RunEvent 或整个 payload。
- `async def load_compaction_sources(store: RuntimeStore, run_id: UUID, *, through_sequence: int) -> dict[int, CompactionSourceFacts]`；SQL 仅筛 compaction_completed，重复版本即拒绝，不覆盖 dict 键。
- `_source_ranges` 与 `_verify_evidence` 共享同次校验的紧凑索引；公开 validate_summary/validate_compaction_record 签名不变。

- [ ] **Step 1 — 写失败测试。** G1/G2 用合法多压缩历史及多引用；逐个破坏重复版本/断链/循环、EventSource Run/Step/N/字段、Snapshot/hash/保留消息。原 fixture 可直接构造 current schema，但坏来源只能破坏一个条件。
  ```python
  # test_tc_u15_03 [beyond_boundary]
  with pytest.raises(InvalidSummaryError, match="source"):
      await validate_summary(response, plan, store)
  # test_tc_u15_02 [duplicate_version]
  with pytest.raises(ValueError, match="duplicat"):
      await validate_compaction_record(record, store)
  ```
  真实 DB trace 证明多 EventSource 按 ID 定点查询；结构审查不缓存整个 Event/Snapshot 链，资源趋势交 Task 17。
- [ ] **Step 2 — RED。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_compaction_history_reads.py -q`。Expected: 新的定点/分页约束失败；已有坏来源拒绝可登记 BASELINE。
- [ ] **Step 3 — 实现折叠。** 固定 plan.source_event_sequence/record.last_event_sequence；每次 validate_summary 建一次来源索引，Snapshot 按当前引用读取并释放；恢复逐版本仅持 source/new 两份。保留所有原身份、hash、版本、范围检查；叶子文本用 iterator，不累计全部字符串副本。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_compaction_history_reads.py tests/it/runtime/test_context.py tests/it/runtime/test_compaction_resume.py -q`。Expected: PASS；changed-files ruff。
- [ ] **Step 5 — 提交。** `git commit -m "ref(runtime): validate compaction without retaining history payloads"`；Expected: 本 task 提交，随后同 TEST task-done。

### Task 5: 恢复、执行确认和诊断分页折叠（G3–G6）

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U15-01、TC-U15-05、TC-U15-06、TC-U15-07、TC-U15-08。按G范围执行对应结构与安全variant，资源后盾由17汇总。

**Files:** 修改 `src/miniagent/runtime/{resume,loop,execution}.py`、`src/miniagent/cli/{resume,diagnostic_finalization}.py`；新增 `tests/it/runtime/test_recovery_history_reads.py`、`tests/sit/cli/test_resume_stale_boundary.py`。

**Interfaces:**
- Consumes：Task 3 迭代器；现有 ResumeHistoryFacts/ExecutionChainFact/planner。
- Produces：`async def collect_history_facts(record: RunRecord, store: RuntimeStore, *, tool_results: tuple[ToolResultFact, ...] = (), approvals: tuple[StoredApproval, ...] = ()) -> ResumeHistoryFacts`（runtime/resume.py）。现有同步 history_facts 改接 Iterable[RunEvent] 并复用私有 `_HistoryFactsBuilder.feed(event)/finish()`；不改变输出语义。
- builder 只保存原 planner 需要的身份/结束/权限/中断等紧凑事实；全序列连续性由 expected_sequence 检查，禁止 range/list 全历史；不需要把 planner 改 async。

- [ ] **Step 1 — 写失败测试。** U15-01/-05/-06/-07/-08，覆盖旧页未知链、当前 operation 之外的诊断链、错结束身份、重复 ResumeStarted、压缩选择冲突、dry-run stale。外部命令副作用日志证明拒绝后 NO-NEXT。
  ```python
  # test_tc_u15_05 [diagnostic]
  assert result.error.code == "DIAGNOSTIC_EXECUTION_UNCONFIRMED"
  assert command_log.read_text() == before
  # test_tc_u15_07 [duplicate_resume_start]
  with pytest.raises(ValueError, match="unique start"):
      await runtime.resume(record)
  ```
- [ ] **Step 2 — RED。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_recovery_history_reads.py tests/sit/cli/test_resume_stale_boundary.py -q`。Expected: 全历史驻留/边界检查缺失失败，安全拒绝已有行为允许 BASELINE。
- [ ] **Step 3 — 替换全部对应读取。** CLI inspect/diagnostic 共用 collect_history_facts；loop.resume 用 N 尾事件及筛选开始事件增量计数，保留 **全 Run ResumeStarted 总数=resume_attempts** 和匹配唯一性的两项原校验；prepare_request 仅保留原选择字段并逐项比对。execution 从操作开始到 N 折叠，不读取模型正文；旧未知链不删除。dry-run 结束重读 record，变化则 RESUME_VIEW_STALE，不写入；持锁执行路径不降低锁范围。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_recovery_history_reads.py tests/sit/cli/test_resume_stale_boundary.py tests/ut/runtime/test_resume_planner.py tests/it/runtime/test_request_resume.py tests/sit/cli/test_diagnostic_resume.py -q`。Expected: PASS；changed-files ruff。
- [ ] **Step 5 — 提交。** `git commit -m "ref(runtime): fold recovery facts within a fixed event boundary"`；Expected: 本 task 提交，随后同 TEST task-done。

### Task 6: 有界工具结果查询和 Dashboard 消费者（G7/G8）

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** U14/U15 的公共读取基础（完整TC主责见映射表）。按G范围执行对应结构与安全variant，资源后盾由17汇总。

**Files:** 修改 `src/miniagent/storage/{sqlite.py,queries/events.py}`、`src/miniagent/runtime/store_contracts.py`、`src/miniagent/dashboard/queries/events.py` 及需适配的 `reads.py`/`projections/{events,spans}.py`；新增 `tests/it/storage/test_tool_result_projection.py`、`tests/it/dashboard/test_history_payload_bounds.py`。

**Interfaces:**
- Consumes：Task 3 固定边界、现有 ToolResultFact、Dashboard PageBoundary/ReadIssue。
- Produces：`list_tool_results(run_id: UUID, *, step_number: int, through_sequence: int | None = None) -> Awaitable[list[ToolResultFact]]`；None 使用该只读事务取得的 record 边界，显式 N 不得拿更晚 State 定义工具组。原批次 call_index/ID/name/连续唯一前缀校验不变。
- SQL 仅取当前 Step 的 tool_end 及有效 result 子对象、scope/sequence 字段，不向 Python 读完整 raw_result/model payload；坏 JSON/缺必需字段明确报 CorruptStoredDataError。

- [ ] **Step 1 — 写失败测试。** 先用当前 schema 可合法构造的大 ToolEnded.result 等正文，混入不属于目标 Step 的事件，用数据库边界/trace 证明读取投影；Task 8/11 扩展 schema 后在同一测试补入完整模型正文/raw_result，重跑本任务回归。不得在前置任务伪造尚不合法的新字段；当前工具前缀、重复结果、错 ID 仍拒绝。Dashboard 元数据/Span 查询对超过现有1 MiB展示预算的 Event 不误称“事件不存在”，正文受现有访问/大小限制并有 ReadIssue。
  ```python
  # test_tool_query_returns_only_effective_result
  assert facts[0].result == effective_result
  assert facts[0].event_sequence == expected_sequence
  # test_dashboard_keeps_metadata_for_large_event
  assert view.event_id == original_id
  assert view.type == original_event_type
  ```
- [ ] **Step 2 — RED。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/storage/test_tool_result_projection.py tests/it/dashboard/test_history_payload_bounds.py -q`。Expected: 全 payload 查询/元数据丢失约束失败。
- [ ] **Step 3 — 实现投影与审计。** 保留 schema 验证与查询安全语义；SQL 局部 JSON 提取不等于 Python 完整 decode。Dashboard 禁止用 `_PAYLOAD` 的整对象大小阈值把必要元数据静默换成空事实；单正文查看仍可明确 not_loaded。审计 src 下 list_events/select_events/fetchall 与等价读取，记录 G1–G8 消费者→调用点→N/limit/投影/保留状态到 workspace `consumer-audit.md`，新消费者必须加入。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/storage/test_tool_result_projection.py tests/it/dashboard/test_history_payload_bounds.py tests/it/runtime/test_tool_batch_resume.py tests/it/dashboard/test_events.py -q`。Expected: PASS；changed-files ruff。
- [ ] **Step 5 — 提交。** `git commit -m "ref(storage): read bounded tool and dashboard facts without raw history"`；Expected: 本 task 提交，随后同 TEST task-done。

### Task 7: provider 规范化失败仍保留最终内容

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U06-01、TC-U06-04。只处理provider公开适配。U06-02/03 的Runtime落盘主责8；U20正常/失败哨兵在此提供细粒度后盾。未知finish走P3。

**Files:** 修改 `src/miniagent/models/protocol.py`、`src/miniagent/providers/qwen.py`；新增 `tests/it/providers/test_qwen_response_evidence.py`；回归 `tests/it/providers/test_qwen.py`。

**Interfaces:**
- protocol.py 新增 `ProviderResponseEvidence`，字段为 content: str | None、tool_calls: tuple[ProviderToolCallEvidence, ...]、finish_reason: str | None、provider_request_id: str | None、usage: dict[str, int | None] | None；tool call 保存 id/name/arguments 原文本，允许缺失字段。
- `ModelResponseNormalizationError(ModelProviderError)` 携带 `evidence: ProviderResponseEvidence` 和规范化错误 code；不含 SDK 对象、headers、reasoning。usage 只允许已知 token 计数字段，未经验证不能进入账单。

- [ ] **Step 1 — 写失败测试。** 用 provider HTTP 边界的可控响应，分别返回非法 arguments JSON、非 object arguments、无效 token 总计以及 reasoning 字段。断言：
  ```python
  # test_malformed_arguments_preserve_other_final_content
  assert exc.value.evidence.content == "final explanation"
  assert exc.value.evidence.tool_calls[0].arguments == "{broken"
  assert "secret-thinking" not in exc.value.evidence.model_dump_json()
  assert "secret-header" not in str(exc.value)
  ```
  正常最终 content 含普通解释应逐字保留；连接失败仍是无响应异常。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/providers/test_qwen_response_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现白名单错误映射。** SDK 返回后先提取允许字段，再做现有 ModelResponse 规范化；仅翻译已收到响应的规范化错误。不得整体 dump response，也不得把原始异常文本直接当安全诊断。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/providers/test_qwen_response_evidence.py tests/it/providers/test_qwen.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "fix(providers): retain final response evidence when normalization fails"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 8: 业务响应一次提交，摘要扩展既有 attempt

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U03-01、TC-U03-02、TC-U03-03、TC-U03-04、TC-U05-01、TC-U05-02、TC-U05-03、TC-U05-04、TC-U05-05、TC-U06-02、TC-U06-03。实现U03/U05全部表项及U06-02/03落盘；P2/P4/P5/P6逐条遵守。B-request/B-tx 屏障在 tests/support/trajectory/barriers.py 于SQLite driver/connection边界提供，留实际trace/握手证据；B-response不patch判定函数。U04跨进程主责16。

**Files:** 修改 `src/miniagent/runtime/{events,loop,execution,transitions}.py`；新增 `tests/it/runtime/test_model_evidence.py`、`tests/it/runtime/test_compaction_evidence.py`；必要更新 `tests/support/runtime/{runtime,compaction,request_resume,compaction_resume}.py` 的当前契约构造数据。

**Interfaces:**
- `ModelRequested.request: ModelRequest`；attempt_id 对真实开始事件必须提供。`CompactionRequested` 同样携带 request、attempt_id。
- `ModelResponded` 扩展 response: ModelResponse | None、normalization_failure: ProviderResponseEvidence | None、request_cost: RequestCost | None、cost_status: Literal["calculated", "not_configured", "usage_missing", "invalid_usage", "calculation_failed"]、disposition: ResponseDisposition | None、error: RunError | None。响应与规范化失败分支互斥；沿用明确无响应的既有错误事件路径。
- `CompactionAttemptRecorded` 外层保存完整 response 或 normalization_failure 及 attempt_id；`CompactionAttempt` 只保存现有小型 usage/cost 摘要并补 attempt_id，避免后续 CompactionFailed/Completed 再重复所有响应正文。
- `RunExecution.begin_step(*, provider: str, model: str, request: ModelRequest)`；`begin_model_retry(*, request: ModelRequest, context_retry: bool = False)`；`begin_compaction_attempt(plan, attempt, *, request: ModelRequest, branch=...)`；现有 `record_model_response` 保留单 journal commit。

- [ ] **Step 1 — 写失败测试。** 真实 SQLite + 可控 provider，测试缺 usage、STOP 空正文、重复 tool ID、正常 tool response、异常 finish、规范化失败；pricing 有/无两侧缺 usage 都拒绝且仍保存完整响应（P4）：
  ```python
  # test_missing_usage_commits_response_and_failure_together
  assert response_event.payload.response.message.content == supplied_text
  assert record.state.execution.active_operation is None
  assert record.state.usage.unconfirmed_model_requests == 1
  assert response_event.payload.cost_status == "usage_missing"
  ```
  对数据库事务外部边界注入提交失败，确认不能只保存 response 而不保存 disposition/usage；恢复用实际提交边界，不新增 pending 字段。摘要测试依 P2 分开构造无效 JSON、证据不匹配、超预算及后续合法压缩，均可读取完整 response、唯一 attempt_id；发布失败不抹掉已记录费用。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/runtime/test_model_evidence.py tests/it/runtime/test_compaction_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现事件与调用路径。** 把缺 usage 分支改为构造失败 disposition 后统一提交；provider 白名单错误走失败证据分支，不能添加有效 assistant 消息/工具调用。请求 event 与 provider.generate 使用同一份最终 request。摘要在既有 record_compaction_attempt 保存正文，后续 publish/fail 仍使用原逻辑。保留计费、取消和错误状态不变量，不吞意外程序错误。价格缺少模型按 P5 精确映射 MODEL_PRICING_UNAVAILABLE；费用已知的业务拒绝仍结算 C 一次。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_model_evidence.py tests/it/runtime/test_compaction_evidence.py tests/it/runtime/test_request_resume.py tests/it/runtime/test_compaction_resume.py tests/ut/runtime/test_resume_planner.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(runtime): persist complete model evidence with response decisions"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 9: Local 命令证据与流式完整输出

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U08-01、TC-U08-02、TC-U08-03、TC-U09-01、TC-U09-02、TC-U09-03、TC-U09-04、TC-U09-06、TC-U11-01、TC-U11-03、TC-U12-05。Local端完整执行U08-01～03、U09-01～04/-06、U11-01/-03、U12-05；Docker对应variant由10接手。本task完成不宣称整个跨backend TC完成。

**Files:** 新增 `src/miniagent/environments/output_capture.py`、`src/miniagent/environments/evidence.py`、`tests/it/environments/test_local_output_evidence.py`；修改 `environments/{contracts,managed_execution,process,local}.py`、`runtime/{events,execution}.py`、`cli/diagnostic_finalization.py`；更新 `tests/support/environments/docker_exec_helper.py` 等 ExecutionObserver 实现。

**Interfaces:**
- `CommandEvidence` 放在 environments/evidence.py：argv: tuple[str, ...]、cwd: str、timeout_seconds: float | None；不含 environment、stdin 或授权材料。
- 扩展 `ExecutionObserver.identified(identity, *, permission=..., command: CommandEvidence | None = None)`；工作命令对应 execution_id 首次放行前必须存 command；Docker helper/create_exec 身份可无 command，guest permit_command 前必须补齐，后续身份观察可不重复；更新 Runtime、Diagnostic、Memory observer 及所有实现，不能使用 hasattr 静默忽略。
- `StreamEvidence`：ref: ArtifactRef | None、received_bytes: int、status: Literal["complete", "partial", "missing"]、eof_observed: bool、reason: str | None。`CommandOutputEvidence`：attempt_id、execution_id、stdout、stderr。定义于 environments/evidence.py。
- `ExecutionObserver.output_recorded(output: CommandOutputEvidence) -> Awaitable[None]` 与新的 `OperationOutputRecorded` Event；挂在现有 observer，不建另一条日志总线。为避免 contracts.py→attachment→managed_execution 导致循环，CommandEvidence/StreamEvidence/CommandOutputEvidence 统一放在新 `environments/evidence.py`，该文件只依赖 ArtifactRef/Pydantic/UUID；其他文件导入它。
- `ExecutionResult.output_evidence: CommandOutputEvidence | None`。`OutputCapture`（output_capture.py）`@classmethod async def open(cls, *, store: ArtifactStore, run_id: UUID, attempt_id: UUID, deadline: float | None) -> OutputCapture` 创建两个 writer；`async def write(self, stream: Literal["stdout", "stderr"], chunk: bytes) -> None`、`async def finish(self, *, execution_id: UUID, stdout_eof: bool, stderr_eof: bool, reason: str | None) -> CommandOutputEvidence`、`async def abort(self) -> None`。第二个 writer 创建失败时清理第一个。

- [ ] **Step 1 — 写失败测试。** 真实 LocalWorkspace 和临时 ArtifactStore，命令输出超过 max_output_bytes 的二进制与 UTF-8。读取 refs 比对原 bytes；普通 stdout 预览保持上限/截断标记。验证空流、timeout、取消、一个工具内两次 execute；命令描述在首次许可前可从 SQLite 读取。
  ```python
  # test_local_full_output_survives_preview_truncation
  assert await store.read(output.stdout.ref) == expected_bytes
  assert result.stdout_truncated
  assert output.stdout.status == "complete"
  # test_cancelled_drain_is_not_complete
  assert output.stdout.status == "partial"
  assert not output.stdout.eof_observed
  ```
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/environments/test_local_output_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现接收时持久化。** LocalWorkspace.execute 显式建立 capture，传入 backend._run/read_stream；不使初始化 git 命令误归属 Agent Tool Call。read_stream 在预览裁剪前 await writer.write；双 pipe 并发排空且有背压。创建 writer 后才放行命令，身份事件携带有效 cwd/argv。异常 cleanup 后预算允许时注册前缀并提交 output fact，无法注册则 missing；保持现有进程结束确认与 Workspace BROKEN 行为。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/environments/test_local_output_evidence.py tests/it/environments/test_local.py tests/it/environments/test_managed_process.py tests/it/runtime/test_execution_observer.py tests/sit/cli/test_diagnostic_resume.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(env): preserve local command output before preview truncation"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 10: Docker 复用完整输出捕获

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U09-05。承接9所列全部Docker variants（不只是U09-05）；IT使用guest协议边界，SIT真实Docker。writer创建失败禁止放行、F1/F2/F3/F5、timeout/partial均有Docker后盾。

**Files:** 修改 `src/miniagent/environments/{docker_exec,docker}.py`；新增 `tests/it/environments/test_docker_output_evidence.py`、`tests/sit/environments/test_docker_output_evidence.py`；回归 helper/supervisor 测试。

**Interfaces:** Task 9 的 CommandEvidence、OutputCapture、CommandOutputEvidence；`run_docker_exec_output(..., output_capture: OutputCapture | None = None)` 保留其他参数与原结果；工作命令执行路径必须提供 capture。没有 Run 工具归属的底层探测不伪造工具证据。

- [ ] **Step 1 — 写失败测试。** helper 协议边界提供多个合法 guest data frame，验证超过预览限额、跨 frame UTF-8、stderr、空流、未完整 completion、重复/错误 scope；真实 Docker SIT 使用本地可用测试镜像，不隐式拉取或付费调用。
  ```python
  # test_docker_capture_contains_only_business_bytes
  assert await store.read(output.stdout.ref) == guest_stdout
  assert b"permit_command" not in await store.read(output.stdout.ref)
  assert output.execution_id == remote.execution_id
  ```
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/environments/test_docker_output_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现解码后、截断前捕获。** 仅保存已校验 guest 业务 data；实际 argv/cwd 在 permit_command 前随身份事实提交。取消/helper 断开时区分完整结束和前缀；不得扩大 MAX_FRAME_BYTES 或改用全量 bytearray。不得降低已有 Docker 执行权限握手和未知执行拒绝规则。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/environments/test_docker_output_evidence.py tests/sit/environments/test_docker_output_evidence.py tests/it/environments/test_docker_exec_helper.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(env): preserve docker business output as complete evidence"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 11: 保存原始 ToolResult 与精确 ToolMessage

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U07-01、TC-U07-02、TC-U07-03、TC-U07-04、TC-U07-05、TC-U07-06、TC-U07-07、TC-U07-08、TC-U10-01、TC-U10-02、TC-U10-03、TC-U11-02、TC-U11-04。U07全部分区与三个等值边界；U10全部观察/事务失败/未产生观察，U11-02/-04。对错误ID/错误码先捕获；renderer不能产生观察时提交明确missing reason，不事后重渲染。

**Files:** 修改 `src/miniagent/tools/{contracts,execution_result}.py`、`src/miniagent/runtime/{events,execution,tool_runner,loop}.py`；新增 `tests/it/runtime/test_tool_evidence.py`；必要更新 storage/tool result 查询消费者、`cli/application.py` 装配和受影响的 `tests/support/runtime/` ArtifactStore 注入。

**Interfaces:**
- `CapturedToolReturn`（内部 frozen dataclass，放在 tool_runner.py）含 `raw_result: ToolResultEvidence | None`、`effective_result: ToolResult`、`validation: ToolReturnValidation`；`ToolReturnValidation`（tools/contracts.py Pydantic）含 disposition: accepted/rejected/not_returned、reason: str | None。`_execute_prepared` 返回此容器；execute/complete_tool 同步更新，不使用全局变量缓存 raw。
- `ToolResultEvidence` 放在 tools/contracts.py：kind 为 inline/artifact 的判别联合，分别含 result: ToolResult 或 ref: ArtifactRef。`ToolEnded.raw_result: ToolResultEvidence | None`、`return_validation: ToolReturnValidation`、`observation: ToolMessage | None`、`observation_missing_reason: str | None`；现有 result 保存模型可见的有界 ToolResult，供已有恢复/查询使用；直接解析本次已生成的 ToolMessage.content 得到，不二次渲染。完整 result 仅在 raw_result 存一份（内联或 Artifact），避免新增引用后仍把巨型正文重复塞入旧 result 字段。RunExecution 显式接收 artifact_store；AgentRuntime 临时可选 artifact_store 在 Task 12 收紧为必需，执行完整证据路径时缺失必须 fail-fast。

- [ ] **Step 1 — 写失败测试。** 一个真实 journal Run，分别返回 65,535、65,536、65,537 bytes 的完整 ToolResult JSON，验证阈值；最终一次工具观察无需下一次 provider 调用即可读取。denied/invalid args 不产生执行正文。断言：
  ```python
  # test_last_tool_observation_matches_conversation_exactly
  assert ended.observation == record.state.conversation.messages[-1]
  assert ended.observation.content == rendered_at_execution
  # test_large_raw_result_is_referenced_without_loss
  assert ToolResult.model_validate_json(await artifacts.read(ended.raw_result.ref)) == original
  ```
  writer 故障只在 ArtifactStore 外部接口注入：后续 provider/tool 调用次数不增加；不是普通 TOOL_EXECUTION_EXCEPTION。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/runtime/test_tool_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现提交。** 在 `prepared.invoke` 返回后、调用 ID/错误码校验及 `_invalid_tool_result` 替换前复制可序列化 ToolResult，并内联或 Artifact 保存；再生成有效结果及 validation。错误 ID 只在 raw 中保留，外层/目录归属取当前 attempt；非 ToolResult 不 repr/pickle，只给 unsupported_result_type。工具自带完整输出 ref 传播到 Event.artifacts。实际 ToolMessage 只渲染一次并与 conversation/ToolEnded 同 commit；若渲染失败，在安全失败提交中保留 raw_result 并明确没有 observation。持久化异常单独传播/翻译，不被 ToolRunner 的通用 catch 吞掉。同步给 Application 和本任务受影响测试注入 ArtifactStore，确保本任务独立可验证；Task 12 再收紧全体调用者的必需契约。取消强杀无法提交时沿用未知结果，不补执行。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime/test_tool_evidence.py tests/it/runtime/test_tool_batch_resume.py tests/ut/tools/test_contracts.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(runtime): retain tool evidence and exact model observations"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 12: 收紧持久化依赖并统一当前契约消费者

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U13-01、TC-U13-03、TC-U13-04、TC-U13-05、TC-U13-06。U13-01/-03/-04/-05/-06 在此完成；U13-02整链由16负责。新依赖变更同步所有调用者，旧开发fixture只读不改写。不要因trajectory包尚未接入而伪造schema=1验证。

**Files:** 修改 `src/miniagent/runtime/loop.py`（AgentRuntime.__init__）、`src/miniagent/cli/{application,bootstrap}.py`、当前 schema codec/消费者；修改现有 `tests/support/runtime/`、`tests/support/context_compaction/live.py`、其他 AgentRuntime 调用处；新增 `tests/it/runtime/test_persistence_dependencies.py`。

**Interfaces:** `AgentRuntime(*, provider: ModelProvider, tools: ToolRegistry, run_store: RuntimeStore, artifact_store: ArtifactStore, ...既有其他可选参数)`；两项无默认且拒绝 None。不自动创建 store，不改为全局单例。RunExecution 接收同一 ArtifactStore；CLI Workspace 与 Runtime 注入同一实例。

- [ ] **Step 1 — 写失败测试。** 构造缺任一依赖立即失败，provider 调用数为 0；提供临时持久化依赖后 Run 的证据可跨 store reopen 读取。当前 Event/Snapshot/application 版本断言为 3/3/1，冻结定义被篡改仍拒绝。
  ```python
  # test_runtime_requires_persistence_before_execution
  with pytest.raises(TypeError, match="run_store"):
      AgentRuntime(provider=provider, tools=tools, artifact_store=artifacts)
  ```
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/runtime/test_persistence_dependencies.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 更新所有调用者。** 用 `rg -n 'AgentRuntime\(' src tests` 枚举并逐一装配真实临时 store，异步 fixture 完成 SQLite initialize。更新当前格式测试数据，不加入旧版本迁移或宽松 defaults 来掩盖缺证据；保留应用 hash、Run/Workspace 身份约束。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/runtime tests/it/storage/test_resume_format.py tests/sit/cli/test_resume_bootstrap.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff；另运行 `uv run ty check`，Expected: 无错误。
- [ ] **Step 5 — 评审并提交。** `git commit -m "ref(runtime): require persistence for complete run evidence"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 13: 固定边界投影与有界 JSON 编码

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U16-01、TC-U16-02、TC-U16-03、TC-U16-04、TC-U16-05、TC-U16-06、TC-U17-02、TC-U17-04、TC-U18-01、TC-U18-02、TC-U18-03、TC-U18-04、TC-U20-02、TC-U20-03。完整实现U16-01～06（特别冻结定义正向字段）、U17-02/-04、U18-01～04、U20-02/-03；U17-01/-05目录后盾来自2。手算费用与正反哨兵，不只测not-null。

**Files:** 新增 `src/miniagent/trajectory/{__init__,contracts,export}.py`、`tests/it/trajectory/test_export.py`、`tests/ut/trajectory/test_encoding.py`；消费 Task 3 已实现的读取接口，不重复定义查询契约。

**Interfaces:**
- 消费 Task 3 的 `iter_run_events`/`get_event`，通过 Task 2 manifest-only 目录，不自行绕过分页。
- contracts.py：`TrajectoryDefinition` 为 spec §8.3 的白名单模型；`TrajectoryBoundary`、`CoverageIssue`、`TrajectoryHeader`（不含 events/artifacts 列表）、`TrajectoryExportError`（code/message）。不导入 CLIApplicationDefinition。
- `encode_trajectory(*, record: RunRecord, definition: TrajectoryDefinition, store: RuntimeStore, artifacts: ArtifactStore, exported_at: datetime, producer_version: str, deadline: float) -> Awaitable[bytes]`，实现位于 export.py。默认固定 MAX_JSON_BYTES=67_108_864、EVENT_PAGE_SIZE=16。
- `iter_json_bytes(value: JsonValue) -> Iterator[bytes]`（export.py）：增量编码，decimal 已由投影转换为字符串；局部函数仍检查最终所有字节。

- [ ] **Step 1 — 写失败测试。** 真实 journal 与 manifests，固定 record 到 N 后检查只读取 <=N；序列缺口/错误 Run/未知 payload 导出失败；合法缺失输出为 partial。请求 messages/ToolMessage 逐字段相同；金额字符串；旧 trajectory 排除；registered_only 明确标记。
  ```python
  # test_export_uses_record_boundary_and_faithful_evidence
  assert document["boundary"]["through_sequence"] == record.last_event_sequence
  assert [e["sequence"] for e in document["events"]] == list(range(1, record.last_event_sequence + 1))
  assert document["kind"] == "miniagent.trajectory"
  assert document["schema_version"] == 1
  assert all(a["type"] != "run_trajectory" for a in document["artifacts"])
  ```
  `test_encoded_size_limit_counts_escaping_and_metadata` 测试实际编码总长恰好 67,108,864 与多 1 byte；不是只给正文计算长度。存储边界测试分页每批不超过 16，拒绝通过无限读取实现。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/trajectory/test_export.py tests/ut/trajectory/test_encoding.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现查询与投影。** 以 frozen record 作 summary 来源；逐页校验连续性/attempt 引用，不加载全部历史；Event payload 显式白名单分派，对所有当前事件有映射，无默认任意 model_dump fallback。附件只读 Task 2 接口；coverage 放在 JSON 后部也可，扫描完再决定；不因字段顺序复制全部 Event。单 Event 大小允许成为峰值，禁止全历史对象树。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/trajectory/test_export.py tests/ut/trajectory/test_encoding.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(runtime): project committed evidence into bounded trajectories"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 14: 所有收尾路径集成独立导出结果

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U08-04、TC-U17-03、TC-U18-05、TC-U18-06、TC-U18-07、TC-U19-01、TC-U19-02、TC-U19-03、TC-U19-06。U19-01/02/03/06、U17-03、U18-05/06/07、U08-04；U12已发布优先映射也在此验证。额外覆盖resume准备阶段直接返回suspended（TD未单列，源A§9/TA风险），不以普通Application.resume代替。

**Files:** 新增 `src/miniagent/cli/trajectory.py`、`tests/it/cli/test_trajectory_finalization.py`；修改 `cli/{application,bootstrap,resume,rendering,main}.py`。

**Interfaces:**
- cli/trajectory.py：`TrajectoryExportOutcome` 判别联合 disabled/exported(ref)/failed(code,message)/skipped(reason)；`build_trajectory_definition(record: RunRecord, application: CLIApplicationDefinition) -> TrajectoryDefinition` 仅选白名单。
- `try_export_trajectory(*, enabled: bool, run_id: UUID, run_store: RuntimeStore, artifact_store: ArtifactStore, definition: CLIApplicationDefinition, timeout_seconds: float, cleanup_deadline: float | None) -> TrajectoryExportOutcome`；调用者持锁且保证无未完成 writer。函数自己固定 record 边界、构建定义、编码并 put 注册 run_trajectory。
- `ApplicationResult.trajectory_export` 默认 disabled；`ApplicationDependencies.export_trajectory: bool = False` 与冻结 application 定义的装配一致。正常结果唯一通过 exported ref 展示 URI。

- [ ] **Step 1 — 写失败测试。** 成功/失败/suspension/Runtime 异常/patch interruption 分别开启导出；超限、存储失败、deadline 耗尽都保留原 Run 结果及 Workspace 清理条件。异常路径不制造 ApplicationResult，不覆盖原异常。
  ```python
  # test_export_failure_does_not_change_success_or_cleanup
  assert result.run_result.status is RunStatus.SUCCEEDED
  assert result.trajectory_export.status == "failed"
  assert result.finalization_errors == ()
  assert workspace_destroyed
  ```
  `test_resume_preparation_suspension_exports_frozen_boundary` 覆盖 resume.py 中直接返回 suspended 的路径；`test_metadata_collection_keeps_patch_validation` 验证大正文不读取而损坏 candidate patch 仍触发现有 finalization error。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/it/cli/test_trajectory_finalization.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现收尾协调。** 在适用的 patch/diagnostic 完成后、destroy 前尝试一次。异常路径只读现有事实，绝不为了 trajectory 新跑诊断命令。resume 尚未装配 Application 时，从已验证的 frozen definition/store 调用同一 helper；dry-run/Run 未创建不调用。附件收集改 manifest 分页，candidate patch 单独保留 get_record 校验。ArtifactPersistenceError 在必需证据路径按持久化失败传播，在 exporter 内才变成非致命 outcome。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/it/cli/test_trajectory_finalization.py tests/it/cli/test_application.py tests/sit/cli/test_diagnostic_resume.py tests/it/cli/test_evaluation_delivery.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(cli): export trajectories without changing run outcomes"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 15: 配置、入口、冻结定义与 Dashboard 兼容

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U02-01、TC-U02-02、TC-U02-03、TC-U02-04。U02-01按6规则×3入口18实例，default/env/.env来源、冻结false/true反转、dry-run及非法switch；Dashboard当前schema与读取边界只做必要接线，不新页面。

**Files:** 修改 `src/miniagent/cli/{settings,main,bootstrap,run_definition,rendering}.py`、`trigger`；修改 `dashboard/projections/events.py` 和现有正文预览/访问边界所需的最小消费者；新增 `tests/ut/cli/test_trajectory_settings.py`、`tests/sit/cli/test_trajectory_options.py`、`tests/it/dashboard/test_trajectory_evidence.py`；更新 `tests/sit/scripts/test_trigger.py`。

**Interfaces:** Settings/CLIApplicationDefinition.export_trajectory: bool=False；CLI `trajectory: bool | None` 的 `--trajectory/--no-trajectory`；parse_config/main/trigger 透传该可选值。ApplicationDependencies 使用最终值，resume 仅从原冻结值取值。

- [ ] **Step 1 — 写失败测试。** 参数组合：无设置=false，env=true，CLI=false 覆盖 true，CLI=true 覆盖 false；直接入口/run/trigger 同行为；resume 不接受覆盖开关且忽略当前 env 对原值的改变；dry-run 不新增 manifests/Event。
  ```python
  # test_explicit_false_overrides_settings
  assert frozen.export_trajectory is False
  assert frozen.schema_version == 1
  # test_exported_uri_is_shown_only_after_registration
  assert f"Trajectory: {ref.uri}" in stdout
  ```
  Dashboard 读取新增证据不报错、不把完整敏感正文绕过既有内容展示边界；不添加下载按钮。
- [ ] **Step 2 — 红灯。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/ut/cli/test_trajectory_settings.py tests/sit/cli/test_trajectory_options.py tests/it/dashboard/test_trajectory_evidence.py -q`。 Expected: 新行为的目标断言FAIL；已存在规则登记BASELINE；环境/屏障未就绪不能计作红灯。
- [ ] **Step 3 — 实现装配与兼容。** .env/env 沿用 Settings；freeze/parse/hash 含新值，resume 仍核对原定义。failure/skipped stderr 使用明确 TRAJECTORY_EXPORT_FAILED / TRAJECTORY_EXPORT_SKIPPED，disabled 静默。更新当前契约 fixtures；不加旧格式兼容或 schema bump。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/ut/cli/test_trajectory_settings.py tests/sit/cli/test_trajectory_options.py tests/it/dashboard/test_trajectory_evidence.py tests/sit/scripts/test_trigger.py tests/ut/cli/test_run_definition.py tests/sit/cli/test_resume_dry_run.py tests/it/dashboard/test_events.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交。** `git commit -m "feat(cli): configure and report optional trajectory exports"`。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 16: 跨进程中断、恢复与端到端验收

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U01-01、TC-U01-02、TC-U04-01、TC-U04-02、TC-U04-03、TC-U04-04、TC-U12-02、TC-U13-02、TC-U19-04、TC-U19-05、TC-U19-07、TC-U20-01。落实U01、U04、U12-02、U13-02、U19-04/05/07、U20-01整链；原task的示例不是测试全集。真实命令/DB/文件，provider stub仅在外部边界；所有屏障必须有证据，未知执行拒绝不变。

**Files:** 新增 `tests/sit/cli/test_trajectory.py`、`tests/sit/resume/test_trajectory.py`；复用或按需扩展 `tests/support/resume/worker.py`、`tests/support/cli/resume_signal_driver.py`；新增测试以 pytest collection 为准；`tests/inventory.json` 是既有数量快照，本任务不手工改写历史统计，也不顺带开发生成器。不新增生产功能。

**Interfaces:** 前序任务的公开 CLI、SQLite、ArtifactStore；provider 仅外部可控 fake，不调用真实计费模型。测试 driver 只提供明确暂停点与进程间通知，不 monkeypatch 内部状态转换。

- [ ] **Step 1 — 编写验收测试。** 同一 task 的导出开/关均有完整证据；开启时 JSON 注册 hash/size 匹配。第一次 suspension 导出后 resume，Run ID 不变、序列增加、旧 JSON bytes 不变；业务响应提交前/期间/后的实际边界恢复正确；未知 shell 仍拒绝不安全 replay。
  ```python
  # test_resume_creates_new_trajectory_without_rewriting_old
  assert first["run_id"] == second["run_id"]
  assert first["boundary"]["through_sequence"] < second["boundary"]["through_sequence"]
  assert await store.read(first_ref) == original_first_bytes
  assert first_ref.artifact_id != second_ref.artifact_id
  ```
  重复 SIGINT 覆盖编码和 Artifact 发布，锁未释放前写入收敛；退出后目录不继续变化。JSON 不含凭据/reasoning，最后观察与 final/evaluation 可追溯，Workspace 销毁后仍可读取主线和工具 Artifact。
- [ ] **Step 2 — 运行验收暴露缺口。** `UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run pytest tests/sit/cli/test_trajectory.py tests/sit/resume/test_trajectory.py -q`。若已有实现通过，不人为制造红灯；发现缺口按 owning task 修正并补针对性测试。 Expected: 全部场景PASS；已有实现通过不造红灯，缺口回所属任务RED→GREEN。
- [ ] **Step 3 — 完成资源与故障证据。** 复用 Task 9/10 大流实验，确认 64 MiB 导出上限不限制完整输出保存；正文保存故障停止业务、导出故障不改变原退出码。记录 Docker 实测状态与不能完成的环境条件，不能把 skip 计入通过。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/sit/cli/test_trajectory.py tests/sit/resume/test_trajectory.py -q`。Expected: PASS，映射中本任务的全部TC/variant及回归无失败/错误；所需实例无skip。执行changed-files ruff。
- [ ] **Step 5 — 评审并提交验收变更。** `git commit -m "test: verify trajectory evidence across interruption and resume"`。提交前核对没有 README、无关配置、旧开发期数据迁移或 AI attribution trailer。汇报测试证据与残余限制，全分支独立 review 与执行交接移至 Task 17 后。 Expected: 仅本任务文件进入提交；随后使用上列TEST的相同路径运行task-done。


### Task 17: 全消费者资源证据、覆盖闭合与最终交接

**Read first:** 全局约束/裁定、A相关节、TD对应单元；`task-start`不会自动带入这些全局章节。

**Acceptance:** TC-U14-01、TC-U14-02、TC-U14-03、TC-U14-04、TC-U14-05。按G范围执行对应结构与安全variant，资源后盾由17汇总。

**Files:** 新增 `tests/sit/trajectory/test_history_resources.py`、`tests/support/trajectory/history.py`、`tests/support/trajectory/resource_worker.py`；必要扩展前序测试，不新增生产功能。资源原始输出、人工记录与覆盖 nodeid 表写本计划 workspace，禁止提交真实凭据或巨大日志。

**Interfaces:** 消费 Tasks 3–6 的 G1–G8 查询路径、Task 16 真实 Run 产物；`resource_worker` 仅测试程序，用已准备 DB 的路径和 consumer ID 启动独立测量进程，stdout 输出 JSON 测量记录。不新增运行时监控 API 或固定 RSS SLO。

- [ ] **Step 1 — 建立 TD U14 全集实验。** 每个 G 消费者独立：页边界15/16/17/32/33；固定128合法 Event，正文16/64/256 KiB；固定正文，数量128/512/2048；单个巨大对象；多来源引用。给 H 配置足够真实模型上下文预算，不能为了达到数量破坏 Run 合法性。准备、消费测量、附件/hash验证分进程；采集8/64 MiB shell流复用 Task 9/10。
  ```python
  # test_tc_u14_01 [g3-n17]
  assert observed_page_max <= 16
  assert actual_resume_decision == expected_resume_decision
  # test_tc_u14_04 [g1]
  assert all(item["through_sequence"] == frozen_n for item in query_evidence)
  ```
  上述结构证据由真实查询日志和对象保留检查给出，不替代实际资源测量；不要仅计 mock 调用。
- [ ] **Step 2 — 首轮实验。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/sit/trajectory/test_history_resources.py -q`。Expected: 所有结构/业务断言 PASS；已有行为无需故意造红灯。发现全历史缓存则 FAIL，回 owning task 作 RED→GREEN 修复。量化 RSS/墙钟仅记录，未定义门槛仍为 DEFER。
- [ ] **Step 3 — 证据闭合。** 为100个TC及全部 variant 生成 nodeid/状态/实现 SHA/输入指纹/环境/屏障证据索引，映射表无空主责；未命中窗口标 WINDOW-NOT-REACHED，Docker缺失标 NOT-RUN，不能删用例或 skip 后宣称完整。按 TD§6 用成功、拒绝、摘要失败后后续压缩、suspension/resume 四份真实产物做离线复盘，记录 event/attempt 定位和无法解释项；人工负责人未落实则记录未验收，不由低阶模型替代用户接受。
- [ ] **Step 4 — TEST。** `uv --cache-dir /private/tmp/miniagent-uv-cache run pytest tests/sit/trajectory/test_history_resources.py tests/it/runtime/test_compaction_history_reads.py tests/it/runtime/test_recovery_history_reads.py tests/it/storage/test_tool_result_projection.py tests/it/dashboard/test_history_payload_bounds.py -q`。Expected: PASS，无所需实例 skip。另运行 `uv run ty check`（Expected: 无错误）、changed-files ruff（Expected: clean）和 `git diff --check`（Expected: exit 0）。完整代码验收文件集合见映射表；逐任务 TEST 记录覆盖该集合，最终若无后续代码改动不无故重复全套。最终 reviewer 修复影响相关契约时按 skill 执行修复后全套。
- [ ] **Step 5 — 提交测试并交接 review。** `git commit -m "test: verify trajectory history resource bounds"`；Expected: 仅测试/必要修正进入提交。全部强制自动证据到齐才 task-done；量化门槛 DEFER 与人工验收状态单列，不报告产品已验收。随后执行 executing-plans 的 fresh whole-branch review、一次重要问题修复流程与 finishing-a-development-branch，禁止低阶模型自行省略最终审查或直接合并。

## 计划自检与执行交接

本次改写没有运行task-start/task-done、pytest、ruff或类型检查，没有创建实施ledger或提交。执行步骤全部未勾选；映射表的100个TC全部为PLANNED，不以文档检查替代运行验证。

架构新增B1由Tasks 3–6及17覆盖，S1由Task 11覆盖；响应一次提交由Task 8保持。其他A§4–10的证据、取消、定义、目录、收尾义务分别落实到表中TC，TA的T01–T20及AC1–AC13通过TD原映射保持可追溯。每个TC有主责、文件及必要后盾；不把“一个file通过”当作全部variants通过。

后续执行方式按用户指定采用 **executing-plans inline**，由低阶模型逐任务完成，不再要求重新选择执行方式。执行者先review本计划并核对输入指纹；源文档变化时写Ruling再推进。最终独立review使用最强可用模型，报告完整Rulings/Deferred minors、未取得证据和量化DEFER项；用户决定分支集成与人工验收，不以低阶模型自评替代。
