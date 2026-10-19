# Dashboard 实施与验证证据

执行：2026-10-19～20，独立工作树 `feat/dashboard`；基线 `49a8714`。按用户授权以当前 schema v3 / Resume 契约实施，仅提供 Runs、Run 详情、Workspace 三页只读浏览，不增加恢复操作。原工作区配置与输入设计文件未纳入本分支提交；本文件不改写原测试策略审计的历史结论。

## 验证结果与口径

- 最终整合：`UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run --extra dashboard pytest tests/dashboard tests/acceptance/dashboard -q`（增加 `--junitxml` 仅用于下表取证）：**557 PASS，0 FAIL / ERROR / SKIP，49.76s**。
- 兼容回归：`uv run --extra dashboard pytest tests/cli/test_main.py tests/artifacts/test_file_store.py tests/storage/test_sqlite.py -q`：**42 PASS，0.22s**。
- 改动 Python 的 Ruff check/format 与 `uv run --extra dashboard ty check` 通过。初始原仓库基线为 814 PASS / 19 SKIP；该基线不冒充改动后的全仓重跑。
- 之前一次整合为 556 PASS（新增搜索词令牌反例之前）；之后 556 PASS / 1 FAIL 是 busy 总耗时的固定容差不足，已保留记录并改为独立 SQLite 对照计量；最终 557 项均通过，不把失败/监督回收算作通过。
- 本文逐 node 的 PASS 来自实际 JUnit；D/TA/TC 范围按实施计划与 TD §4.1 关联。范围关联不表示某一个 node 证明了范围内全部行为；严格报告类型变体另按实际字段/路径展开。完整 DoD 不因组件全绿自动关闭，下面两项 DEFER 保留。

## 环境、真实边界与残余

macOS 26.7.1 arm64；Python 3.13.14；SQLite 3.53.1；monotonic resolution `4.166666666666666e-08s`；Chromium `156.0.8078.4`。SQL/文件子进程监督 15s；截止对照 4.999 / 5.000 / 5.001s。SQL trace 先确认真实筛选语句开始，再推进共享时钟；测试同时要求 SQLITE_INTERRUPT、503 和实际 worker 完成。F5 定向错误证明仅返回 503 不足以让测试通过。

busy 采用真实 rollback-journal EXCLUSIVE 锁。每个产品连接实际 `PRAGMA busy_timeout=1000`；独立原生 SQLite 的 1s 等待样本 1.118953s，产品 SQL 等待 1.124868s、HTTP 1.155069s、握手最大 0.000063125s；`ε=max(0.05,2×握手,2×独立 SQLite 超调)=0.237906s`。错误页渲染另有真实 profile 计量，解锁重读 200；没有更改产品 timeout。

真实 FIFO（无 writer）、Unix socket、目录，以及 manifest/content/根/中间目录在 os.open 返回后的替换均已执行。Pipe 握手记录 FD/device/inode，内容读取必须来自原 inode 或受控拒绝，不把 hash 不符后的拒绝当成没读替代文件。macOS socket 的 ENOTSUP 曾暴露未处理异常，已 RED→GREEN 修复为 409。超大实际文件通过 profile 证明零内容 read/hash；连接、实际线程、已观测 FD 均回收。

- **DEFER：真实设备节点。** 没有安全的非特权设备节点准备条件；未用 `/dev/null` symlink 冒充设备测试。
- **DEFER：原生 OS 隐藏事件交付。** 有头 Chromium 的标签切换及窗口 minimize/restore 都未改变 `document.hidden`。受控 DOM visibility-event + clock 测试证明状态机显隐分支，但不当成原生窗口隐藏证据。

## 浏览器执行者审读

这是 AI 执行者基于实际 Chromium 会话、真实截图和键盘操作的审读，不是人类用户研究、辅助技术矩阵或 WCAG 认证。1280×900 与 390×844：Enter 展开 payload，键盘进入 Snapshot 来源并返回；3px 焦点描边可见；状态有文字和颜色；时间本地 GMT+8 显示并保留 UTC title。表格在自身容器横滚，页面本身不横向溢出；长路径页原 3433px 已修复为 390px。覆盖限制原在长卡片末端，已移到结论旁并由首屏可见性测试验证。

[桌面 Runs](assets/dashboard-desktop.png) · [窄屏 Runs](assets/dashboard-mobile.png) · [Workspace](assets/dashboard-workspace.png) · [窄屏长补丁](assets/dashboard-mobile-diff.png)

实际审读测量：

```json
{
  "browser": "156.0.8078.4",
  "desktop": [
    1280,
    900
  ],
  "mobile": [
    390,
    844
  ],
  "keyboard_expanded": true,
  "keyboard_return": true,
  "outline_width": "3px",
  "localized_time": "Oct 20, 2026, 12:32:32 AM GMT+8",
  "utc_title": "2026-10-19T16:32:32.674802+00:00 UTC",
  "table": {
    "client": 360,
    "scroll": 799,
    "document": 390,
    "viewport": 390
  },
  "long_diff": {
    "document": 390,
    "viewport": 390
  }
}
```

## wheel、依赖与原入口

构建真实 wheel，最小与 dashboard extra 两个独立环境分别安装。最小环境无 FastAPI，Dashboard 给安装说明；原 `miniagent TASK` 实际连接本机协议假 Provider，完成一次模型响应与 Local Workspace 生命周期，不只验证 help，不连接真实模型。extra 环境分别在无凭证、恶意 `.env`/旧环境变量指向另一库时服务显式数据库；缺 Artifact 根不创建。包内 templates、CSS、JS、license 均实际请求。

追加的已安装 wheel PID 观测（只检查本测试创建的进程）确认：

```json
{
  "installed_wheel": "miniagent-0.1.0-py3-none-any.whl",
  "listening": [
    "127.0.0.1:60920"
  ],
  "child_process_count": 0,
  "health": 200,
  "explicit_run_found": true,
  "missing_artifacts_created": false
}
```

HTMX 按[官方安装文档](https://htmx.org/docs/)和[配置 API](https://htmx.org/api/)锁定 2.0.11，仅加载包内资源。上游 `https://raw.githubusercontent.com/bigskysoftware/htmx/v2.0.11/`：

| 文件 | SHA-256 |
| --- | --- |
| `dist/htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `LICENSE` | `d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38` |

## F1–F5 定向错误实验

仅在临时完整 src/tests/config 副本中修改；各副本有独立环境并核对实际 import 路径。副本均已回收，工作树产品代码没有保留突变。下列 5/5 是五个指定错误被相应断言检测，不是全面 mutation score。

| 家族 | 植入的错误 | 实际 node | 结果/独立反证 |
| --- | --- | --- | --- |
| F1 | StrictBool 改宽松 bool | <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path9-true]</code> | FAIL：resolved 字符串被转为通过，页面出现禁止的已记录独立评测通过；exit=1 |
| F2 | 重操作移回 ASGI Task 内直接执行 | <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_heavy_stages_run_inside_worker_slot</code> | FAIL：实际阶段 thread_id 等于 ASGI thread_id；exit=1 |
| F3 | 超大 gate 返回前先 os.read 完内容 | <code>tests/acceptance/dashboard/test_file_boundaries.py::test_actual_oversize_reads_no_content</code> | FAIL：捕获 322 次内容 read，零读取断言失败；exit=1 |
| F4 | JSON 词法扫描忽略字符串状态 | <code>tests/dashboard/test_previews.py::test_shallow_json_lexical_variants_reach_the_preview</code> | FAIL：合法浅层预览不再可读；三份词法正例有消费后盾；exit=1 |
| F5 | progress handler 固定返回 0 | <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_real_sql_is_interrupted_after_deadline[5.001]</code> | FAIL：HTTP 可为503但真实 interrupted=false，主动中断断言失败；exit=1 |

## D01–D16 证据索引

| 验收 | 实际证据入口 | 当前判定 |
| --- | --- | --- |
| D01 | run_list / parameters / cursors / list_page / refresh | PASS：已执行分页、排序、字面筛选与乱序响应分区 |
| D02 | run_projection / run_list / detail_pages / evaluation_projection | PASS：记录状态、历史未知字段与 Local 口径 |
| D03 | events / detail_pages / journey | PASS：sequence、跨 Step 调用身份 |
| D04 | events / journey | PASS：工具 success 与 truncated 同时保留 |
| D05 | spans / events / page_reads / sources | PASS：已执行缺失、冲突、跨页与限额；有效 Span 层级最多4，62～66层是异常输入健壮性而非正常链 |
| D06 | compaction_projection / compaction_sources / sources / journey | PASS：当前/历史、hash、证据支持与承载 Snapshot 链接 |
| D07 | workspace_projection / workspace_pages / journey | PASS：选择、rename/binary/未知结构、销毁工作目录、长路径 |
| D08 | manifests / artifact_content / previews / file_boundaries | PASS（已执行文件分区）；真实设备节点 DEFER |
| D09 | evaluation_adapter / evaluation_projection / workspace_pages / journey | PASS：链判定、类型变体、坏单项与覆盖口径 |
| D10 | CLI / installation / governed_routes | PASS：仅三个只读页面与指定 CLI，未接恢复/Compare/Checkpoint |
| D11 | run_projection / compaction_projection | PASS：持久化 usage、未配置上限与摘要计费去重 |
| D12 | run_projection / list_page | PASS：Decimal 与完整性口径，非终态不推断存活 |
| D13 | page_reads / detail_pages / workspace_pages / refresh / cursors | PASS（已执行一致性、保位与代次）；原生 OS 隐藏交付 DEFER |
| D14 | content / previews / governed_routes / content_execution / detail source privacy | PASS：已执行所有展示面与真实脚本 backstop |
| D15 | limits / scopes / source chains / workers / resource_boundaries / refresh | PASS（已执行资源分区）；原生 OS 隐藏交付 DEFER；不承诺内核慢 I/O 硬截止 |
| D16 | connection / governed_routes / installation / compatibility | PASS：只读差分、SQL 观测、真实 wheel 与旧 TASK |

## 逐 node 执行记录

来源族范围见计划覆盖索引；严格类型变体下列路径是实际单一改动，不用 hash 错误掩盖类型错误。PASS 仅表示该 node 的具体断言通过。

| pytest node / variant | D | TA | TC 关联 | 结果 |
| --- | --- | --- | --- | --- |
| <code>tests/dashboard/test_artifact_content.py::test_actual_oversize_reads_no_content</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_identity_and_layout</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[content-False]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[content-True]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[prefix-False]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[prefix-True]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[directory-False]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_internal_symlinks_are_always_rejected[directory-True]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_uri_query_and_fragment_are_rejected[?]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_uri_query_and_fragment_are_rejected[#]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_uri_query_and_fragment_are_rejected[?query=secret]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_uri_query_and_fragment_are_rejected[#fragment]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[file://host/content]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[https://example.invalid/never-fetch]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[file:///outside]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[file:///bad%ZZ]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[file:///bad%FF]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_uri_cannot_select_an_arbitrary_path[file:///bad%00]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_size_comparison_for_bounded_content[11]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_size_comparison_for_bounded_content[12]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_reference_missing_fields_are_unknown_but_recorded_conflicts_fail</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_faults_do_not_become_verified_bytes[hash]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_faults_do_not_become_verified_bytes[missing]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_faults_do_not_become_verified_bytes[directory]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_content_faults_do_not_become_verified_bytes[middle-file]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_actual_file_size_boundary[20971518]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_actual_file_size_boundary[20971519]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_actual_file_size_boundary[20971520]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_actual_file_size_boundary[20971521]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_artifact_content.py::test_actual_file_size_boundary[20971522]</code> | D08,D15,D16 | TA-14,TA-15,TA-17 | TC-U9-02～04,13～14;TC-U14-01～03,09 | PASS |
| <code>tests/dashboard/test_cli.py::test_cli_does_not_read_provider_settings</code> | D10,D16 | TA-27 | TC-U1-01～06,08～09;TC-U10-22 | PASS |
| <code>tests/dashboard/test_cli.py::test_invalid_port_exits_without_creating_files</code> | D10,D16 | TA-27 | TC-U1-01～06,08～09;TC-U10-22 | PASS |
| <code>tests/dashboard/test_cli.py::test_missing_database_does_not_initialize_storage</code> | D10,D16 | TA-27 | TC-U1-01～06,08～09;TC-U10-22 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_summary_hashes_full_rendered_message</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_compaction_attempts_do_not_cross_groups</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_current_and_historical_summary_are_distinct</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_unloaded_attempt_cannot_prove_group_total</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_conflicting_attempt_prices_remain_unknown</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_projection.py::test_explicit_no_summary_does_not_override_completed_fact</code> | D06,D11 | TA-10,TA-12 | TC-U7-01～04,08～10 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_existing_source_must_support_claim[first evidence-first evidence-verified]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_existing_source_must_support_claim[invented conclusion-first evidence-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_existing_source_must_support_claim[content-content-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_existing_source_must_support_claim[absent leaf-absent leaf-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_unloaded_evidence_is_not_missing</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_source_identity_and_ancestor_range_are_required[change0]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_source_identity_and_ancestor_range_are_required[change1]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_source_identity_and_ancestor_range_are_required[change2]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_exact_compaction_sources_use_immutable_rows</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_coerced_event_field_cannot_be_claimed_as_original_bool</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_unstored_snapshot_default_cannot_support_a_field_proof</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_compaction_source_chain_is_bounded[30]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_compaction_source_chain_is_bounded[31]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_compaction_source_chain_is_bounded[32]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_compaction_source_chain_is_bounded[33]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_compaction_source_chain_is_bounded[34]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_field_proof_checks_actual_type_and_canonical_claim[first evidence-&quot;first evidence&quot;-verified]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_field_proof_checks_actual_type_and_canonical_claim[True-true-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_field_proof_checks_actual_type_and_canonical_claim[1-1-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_field_proof_checks_actual_type_and_canonical_claim[first evidence-first evidence-inconsistent]</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_history_lookup_does_not_read_current_run_body</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_compaction_sources.py::test_visible_events_cannot_extend_the_source_boundary</code> | D06,D15 | TA-11,TA-12 | TC-U7-05～10;TC-U10-B17 | PASS |
| <code>tests/dashboard/test_config.py::test_config_does_not_read_provider_environment</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_config.py::test_port_boundaries[0-False]</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_config.py::test_port_boundaries[1-True]</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_config.py::test_port_boundaries[65535-True]</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_config.py::test_port_boundaries[65536-False]</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_config.py::test_unknown_configuration_is_rejected</code> | D10,D16 | TA-27 | TC-U1-01～03;TC-U10-22 | PASS |
| <code>tests/dashboard/test_connection.py::test_missing_database_is_never_created</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_read_transaction_is_read_only</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_deadline_interrupts_real_sql</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_budget_reservation_does_not_consume_rejected_bytes</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_deadline_boundary[4.999-False]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_deadline_boundary[5.0-True]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_deadline_boundary[5.001-True]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_encoded_database_path_is_readable</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_startup_rejects_unsupported_structure[version]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_startup_rejects_unsupported_structure[table]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_connection.py::test_startup_rejects_unsupported_structure[column]</code> | D15,D16 | TA-01,TA-24,TA-25 | TC-U1-04～06;TC-U14-04～06 | PASS |
| <code>tests/dashboard/test_content.py::test_json_strings_do_not_increase_depth[value0]</code> | D14,D15 | TA-18,TA-23 | TC-U9-10 | PASS |
| <code>tests/dashboard/test_content.py::test_json_strings_do_not_increase_depth[value1]</code> | D14,D15 | TA-18,TA-23 | TC-U9-11 | PASS |
| <code>tests/dashboard/test_content.py::test_json_strings_do_not_increase_depth[value2]</code> | D14,D15 | TA-18,TA-23 | TC-U9-12 | PASS |
| <code>tests/dashboard/test_content.py::test_json_limits_and_duplicates[62]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_limits_and_duplicates[63]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_limits_and_duplicates[64]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_limits_and_duplicates[65]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_limits_and_duplicates[66]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[{&quot;a&quot;:1,&quot;a&quot;:2}-json_duplicate_key]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[{&quot;a&quot;:{&quot;x&quot;:1,&quot;x&quot;:2}}-json_duplicate_key]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[NaN-json_nonfinite]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[Infinity-json_nonfinite]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[-Infinity-json_nonfinite]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[1e9999-json_nonfinite]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[[}-json_invalid]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_json_rejects_ambiguous_content[&quot;\xff&quot;-json_invalid]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[token]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[TOKEN]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[api_key]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[API-KEY]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[ApiKey]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[password]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[Secret]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[authorization]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[access_token]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[input_tokens]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[nested-secret]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_redaction_preserves_only_usage_counts[auth_token]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_event_usage_allowlist_is_path_scoped[value0-expected0]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_event_usage_allowlist_is_path_scoped[value1-expected1]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_event_usage_allowlist_is_path_scoped[value2-expected2]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_event_usage_allowlist_is_path_scoped[value3-expected3]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[sk-proj-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[ghp_cccccccccccccccccccccccccccccccccccc]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[github_pat_dddddddddddddddddddddddddddddddddddddddd]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[AKIAEEEEEEEEEEEEEEEE]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[Bearer private-bearer-value]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[Authorization: Basic private-basic-value]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[api_key=&quot;private-assignment-value&quot;]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[password=private-password-value]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[-----BEGIN PRIVATE KEY-----\nPRIVATE-MATERIAL\n-----END PRIVATE KEY-----]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_text_credentials_are_redacted_before_any_pagination[-----BEGIN RSA PRIVATE KEY-----\nRSA-MATERIAL\n-----END RSA PRIVATE KEY-----]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_local_file_uris_are_not_displayed_in_plain_logs</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_connection_values_are_hidden_in_json_and_text[endpoint]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_connection_values_are_hidden_in_json_and_text[base_url]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_connection_values_are_hidden_in_json_and_text[openai_base_url]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_connection_values_are_hidden_in_json_and_text[connection-string]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_content.py::test_connection_values_are_hidden_in_json_and_text[dsn]</code> | D14,D15 | TA-18,TA-23 | TC-U9-05,10～12;TC-U10-B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_cursors.py::test_cursor_matches_independent_hmac_vector</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change0]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change1]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change2]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change3]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change4]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_cursor_rejects_semantic_misuse[change5]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_cursor_time_boundaries[now0-True]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_cursor_time_boundaries[now1-True]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_cursor_time_boundaries[now2-False]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_boundary_rejects_invalid_identity[boundary_change0]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_boundary_rejects_invalid_identity[boundary_change1]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_signed_boundary_rejects_invalid_identity[boundary_change2]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_invalid_cursor_encoding[AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_invalid_cursor_encoding[a.b.c]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_invalid_cursor_encoding[\u975e\u6cd5.\u7b7e\u540d]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_invalid_cursor_encoding[a=.b]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_all_cursor_purposes_roundtrip[events-fields0]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_all_cursor_purposes_roundtrip[runs-fields1]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_all_cursor_purposes_roundtrip[manifests-fields2]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_all_cursor_purposes_roundtrip[preview-fields3]</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_event_position_cannot_exceed_boundary</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_cursor_key_restart_and_tampering_are_rejected</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_cursors.py::test_filter_digest_contains_no_literal_search_text</code> | D13,D15 | TA-29 | TC-U4-01～09;TC-U10-B19,25～26 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_detail_refresh_changes_all_database_facts</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_event_pagination_keeps_original_overview</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_source_links_keep_boundary_and_filters</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_detail_native_filter_form</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_snapshot_projection_keeps_absent_message_fields_absent[content]</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_snapshot_projection_keeps_absent_message_fields_absent[tool_calls]</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_detail_pages.py::test_source_tokens_do_not_store_raw_search_terms</code> | D02,D03,D06,D13 | TA-07,TA-10,TA-22,TA-29 | TC-U3-01～03;TC-U5-05;TC-U7-08;TC-U8-01～02;TC-U16-02;TC-U4-07 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_native_booleans_and_optional_infra</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_exists-true]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_exists-1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_exists-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_is_None-true]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_is_None-1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_is_None-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_successfully_applied-true]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_successfully_applied-1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[patch_successfully_applied-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[resolved-true]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[resolved-1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[resolved-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[infra_failure-true]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[infra_failure-1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_each_invalid_field_type[infra_failure-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_required_report_boolean_cannot_default[patch_exists]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_required_report_boolean_cannot_default[patch_is_None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_required_report_boolean_cannot_default[patch_successfully_applied]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_required_report_boolean_cannot_default[resolved]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path0-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path1-value1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path2-None]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path3-value3]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path4-not a list]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path5-value5]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path6-not a list]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path7-value7]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path8-not a list]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path9-value9]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path10-not a list]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_container_shapes_are_strict[path11-value11]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_adapter_only_uses_a_supported_recorded_harness</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path0]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path2]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path3]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path4]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path5]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_each_required_report_container_is_present[path6]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_exists-True]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_exists-False]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_is_None-True]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_is_None-False]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_successfully_applied-True]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[patch_successfully_applied-False]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[resolved-True]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[resolved-False]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[infra_failure-True]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_native_bool_values_retain_their_recorded_meaning[infra_failure-False]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_internal_test_set_conflicts[success0-failure0]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_internal_test_set_conflicts[success1-failure1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_rejects_internal_test_set_conflicts[success2-failure2]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_same_test_in_different_groups_is_not_a_duplicate</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_has_exactly_the_expected_instance[document0]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_adapter.py::test_report_has_exactly_the_expected_instance[document1]</code> | D09,D14 | TA-20,TA-21 | TC-U12-04～05,10～11,13～16 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_recorded_pass_requires_the_whole_evidence_chain</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_each_necessary_artifact_is_incomplete[103]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_each_necessary_artifact_is_incomplete[104]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_each_necessary_artifact_is_incomplete[105]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_each_necessary_artifact_is_incomplete[106]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_logs_do_not_replace_verdict[107]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_logs_do_not_replace_verdict[108]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_logs_do_not_replace_verdict[109]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_logs_do_not_replace_verdict[110]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[104-run_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[104-attempt_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[104-instance_id-other-instance]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[105-run_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[105-attempt_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[105-instance_id-other-instance]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[104-patch_sha256-0000000000000000000000000000000000000000000000000000000000000000]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[105-patch_sha256-0000000000000000000000000000000000000000000000000000000000000000]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[104-submission_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_explicit_identity_mismatches_are_conflicts[105-submission_id-00000000-0000-0000-0000-0000000003e7]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_and_conflicting_facts_both_remain_visible</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_missing_event_cannot_be_replaced_by_report_or_logs</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_unverified_patch_content_is_not_pass_evidence</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_local_workspace_does_not_get_a_swebench_card</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_each_report_negative_fact_blocks_recorded_pass[resolved-False]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_each_report_negative_fact_blocks_recorded_pass[patch_exists-False]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_each_report_negative_fact_blocks_recorded_pass[patch_is_None-True]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_each_report_negative_fact_blocks_recorded_pass[patch_successfully_applied-False]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_each_report_negative_fact_blocks_recorded_pass[infra_failure-True]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_runtime_failure_is_independent_of_the_report</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_v3_result_cannot_disagree_with_event_passed</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_unreferenced_unknown_summary_does_not_replace_the_named_final</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_invalid_metadata_attempt_is_never_filled_from_content[None]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_invalid_metadata_attempt_is_never_filled_from_content[invalid-uuid]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_event_passed_false_is_a_separate_negative_fact</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_reference_hash_missing_differs_from_recorded_mismatch[None]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_reference_hash_missing_differs_from_recorded_mismatch[0000000000000000000000000000000000000000000000000000000000000000]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_summary_verdict_is_not_implicitly_final[collecting-incomplete]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_summary_verdict_is_not_implicitly_final[failed-conflict]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_summary_verdict_is_not_implicitly_final[cancelled-conflict]</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_successful_shell_and_pass_log_do_not_replace_a_report</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_two_complete_attempts_keep_their_own_evidence</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_evaluation_projection.py::test_a_report_from_another_attempt_is_not_spliced_into_the_chain</code> | D02,D09 | TA-20,TA-21 | TC-U12-01～12 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[model_responded-payload0-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[model_responded-payload1-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[tool_end-payload2-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[tool_end-payload3-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[tool_execution_end-payload4-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[tool_end-payload5-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[tool_end-payload6-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[step_completed-payload7-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[step_completed-payload8-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[evaluation_completed-payload9-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[evaluation_completed-payload10-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[run_finished-payload11-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[run_finished-payload12-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[compaction_failed-payload13-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[approval_invalidated-payload14-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[approval_resolved-payload15-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[approval_resolved-payload16-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[approval_resolved-payload17-True]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[approval_resolved-payload18-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[policy_decided-payload19-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_failure_filter_matches_exact_known_facts[unknown-payload20-False]</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_tool_ids_do_not_cross_steps</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_success_and_truncation_are_independent</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_events.py::test_sparse_matches_are_filtered_before_limit</code> | D03,D04,D05 | TA-07,TA-08 | TC-U5-01～08;TC-U3-03 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_all_read_routes_preserve_business_data</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[list]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[detail]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[timeline]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[snapshot]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[memory]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[metadata]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[diff]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[log]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[json]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_display_surface_redacts_and_escapes[fragment]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs/not-a-uuid-GET-headers0-422]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs?bad=sk-query-secret-sentinel-GET-headers1-422]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs?cursor=sk-query-secret-sentinel-HEAD-headers2-422]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs/00000000-0000-0000-0000-000000009999-GET-headers3-404]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs-POST-headers4-405]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs-GET-headers5-403]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/runs-HEAD-headers6-403]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_each_http_failure_has_its_own_semantics[/static/missing.css-GET-headers7-404]</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_corrupt_database_returns_error_id_without_diagnostic_leaks</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_registered_missing_and_oversized_content_have_distinct_statuses</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_unknown_stored_schema_is_a_readable_placeholder</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_unsupported_database_schema_is_not_initialized</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_heavy_busy_is_503_while_health_remains_readable</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_expired_read_budget_is_503</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_governed_routes.py::test_all_supported_status_filters_are_selectable</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01～08;TC-U14-10 | PASS |
| <code>tests/dashboard/test_http.py::test_host_origin_and_methods</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01,06～08 | PASS |
| <code>tests/dashboard/test_http.py::test_error_responses_never_echo_input</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01,06～08 | PASS |
| <code>tests/dashboard/test_http.py::test_generation_is_bounded_and_reflected</code> | D10,D14,D15,D16 | TA-01,TA-23,TA-25 | TC-U1-07～08;TC-U13-01,06～08 | PASS |
| <code>tests/dashboard/test_list_page.py::test_list_page_shows_only_recorded_facts</code> | D01,D02,D13 | TA-03,TA-04,TA-26 | TC-U2-04,08;TC-U1-07～08 | PASS |
| <code>tests/dashboard/test_list_page.py::test_native_form_empty_filters_and_dates</code> | D01,D02,D13 | TA-03,TA-04,TA-26 | TC-U2-04,08;TC-U1-07～08 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_page_does_not_validate_all_contents</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_corrupt_manifest_does_not_hide_other_items</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_partial_discovery_cannot_prove_unique_patch</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_junk_directory_entry_is_an_issue_without_inventing_an_artifact</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_byte_gate[-2]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_byte_gate[-1]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_byte_gate[0]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_byte_gate[1]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_byte_gate[2]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_symlinks_are_rejected_even_inside_root[manifest]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_manifest_symlinks_are_rejected_even_inside_root[directory]</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_manifests.py::test_missing_manifest_directory_is_an_empty_current_discovery</code> | D07,D08,D15 | TA-13,TA-14 | TC-U9-02～04,09;TC-U11-02,05;TC-U10-B09,B13 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_first_page_uses_one_committed_view</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_oversized_run_preserves_envelope</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_wal_writer_cannot_mix_first_page_facts</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_missing_persisted_usage_is_marked_before_codec_defaults</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_last_evaluation_is_read_independently_of_first_batch</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_state_utf8_length_gate[-2]</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_state_utf8_length_gate[-1]</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_state_utf8_length_gate[0]</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_state_utf8_length_gate[1]</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_state_utf8_length_gate[2]</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_recovery_error_does_not_discard_valid_state</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_page_reads.py::test_actual_returned_structured_bytes_never_exceed_request_budget</code> | D02,D05,D13,D15 | TA-02,TA-05,TA-07,TA-17 | TC-U3-01～03;TC-U5-03～05;TC-U10-B01～06 | PASS |
| <code>tests/dashboard/test_parameters.py::test_run_filters_normalize_without_changing_literal_task</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_run_filters_reject_invalid_ranges[values0]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_run_filters_reject_invalid_ranges[values1]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_run_filters_reject_invalid_ranges[values2]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_run_filters_reject_invalid_ranges[values3]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[EventFilters-values0]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[EventFilters-values1]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[EventParams-values2]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[EventParams-values3]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[EventParams-values4]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[SnapshotParams-values5]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[SnapshotParams-values6]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[ArtifactParams-values7]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_parameter_ranges[RunListParams-values8]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_parameters_preserve_multiselect_and_literal_search</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_parameters_reject_ambiguous_single_values[pairs0]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_parameters_reject_ambiguous_single_values[pairs1]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_parameters_reject_ambiguous_single_values[pairs2]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_parameters_reject_ambiguous_single_values[pairs3]</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_parameters.py::test_http_event_parameters_validate_selection</code> | D01,D13,D15 | TA-03,TA-29 | TC-U2-03;TC-U4-01～09;TC-U10-B19～B20,21～26 | PASS |
| <code>tests/dashboard/test_previews.py::test_redacted_preview_chunks_reassemble_exactly</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_full_redaction_precedes_line_pagination</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_json_never_falls_back_to_raw_text[{bad]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_json_never_falls_back_to_raw_text[{&quot;token&quot;:&quot;one&quot;,&quot;token&quot;:&quot;two&quot;}]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_json_never_falls_back_to_raw_text[NaN]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_json_never_falls_back_to_raw_text[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_json_never_falls_back_to_raw_text[{&quot;message&quot;:&quot;\\ud800&quot;}]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_byte_limit[262142]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_byte_limit[262143]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_byte_limit[262144]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_byte_limit[262145]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_byte_limit[262146]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_both_limits[1998]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_both_limits[1999]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_both_limits[2000]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_both_limits[2001]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_preview_respects_both_limits[2002]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_multibyte_and_inline_offsets_are_preserved</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_is_redacted_and_serialized_as_plain_text</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_shallow_json_lexical_variants_reach_the_preview[value0]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-10 | PASS |
| <code>tests/dashboard/test_previews.py::test_shallow_json_lexical_variants_reach_the_preview[value1]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-11 | PASS |
| <code>tests/dashboard/test_previews.py::test_shallow_json_lexical_variants_reach_the_preview[value2]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-12 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_depth_gate_at_the_preview_consumer[62]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_depth_gate_at_the_preview_consumer[63]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_depth_gate_at_the_preview_consumer[64]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_depth_gate_at_the_preview_consumer[65]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_depth_gate_at_the_preview_consumer[66]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_logical_newlines_preserve_the_line_limit[\r\n-6000]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_logical_newlines_preserve_the_line_limit[\u2028-8000]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_or_control_text_is_metadata_only[\xff]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_or_control_text_is_metadata_only[visible\x00tail]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_invalid_or_control_text_is_metadata_only[visible\xc2\x85tail]</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_previews.py::test_json_artifact_reference_uri_is_hidden</code> | D08,D14,D15 | TA-16,TA-18,TA-23 | TC-U9-05～07,10～12;TC-U10-B10～B12,B18;TC-U13-04～05 | PASS |
| <code>tests/dashboard/test_run_list.py::test_list_keyset_survives_new_insert</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_literal_task_filters_and_counts</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_oversized_run_preserves_envelope</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_inconsistent_identity_is_not_a_model_match[identity.run_id-00000000-0000-0000-0000-000000000099]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_inconsistent_identity_is_not_a_model_match[lifecycle.status-running]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_inconsistent_identity_is_not_a_model_match[identity.created_at-2025-01-01T00:00:00Z]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_task_byte_gate[131070]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_task_byte_gate[131071]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_task_byte_gate[131072]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_task_byte_gate[131073]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_task_byte_gate[131074]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_filters_and_scalar_sql_preserve_business_data</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_missing_usage_is_not_zero</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_model_scalar_gate[4094]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_model_scalar_gate[4095]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_model_scalar_gate[4096]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_model_scalar_gate[4097]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_model_scalar_gate[4098]</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_workspace_and_decimal_use_persisted_values</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_list.py::test_oversized_recovery_is_unknown_not_missing_run</code> | D01,D02,D12 | TA-03,TA-04,TA-05 | TC-U2-01～08;TC-U10-B05,B07,B20,21,23 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_usage_uses_persisted_totals</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_currency_rounding[value0-0.000000]</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_currency_rounding[value1-0.000002]</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_unknown_evaluator_is_not_inferred</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_wall_duration_and_step_limits[9-90%]</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_wall_duration_and_step_limits[10-100%]</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_wall_duration_and_step_limits[11-110%]</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_missing_fields_override_pydantic_defaults</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_run_projection.py::test_oversized_run_preserves_envelope</code> | D02,D11,D12 | TA-04,TA-05,TA-06 | TC-U8-01～08 | PASS |
| <code>tests/dashboard/test_sources.py::test_old_boundary_never_loads_future_sources</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_sources.py::test_event_pages_freeze_at_signed_sequence</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_sources.py::test_snapshot_only_returns_requested_messages</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_sources.py::test_locator_returns_at_most_twenty_neighbors_each_side</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_sources.py::test_future_event_source_is_outside_boundary</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_sources.py::test_empty_snapshot_and_independent_bounds</code> | D05,D06,D13,D15 | TA-02,TA-07,TA-29 | TC-U3-03;TC-U4-04～09;TC-U7-08;TC-U10-24 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[run]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[step]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[model]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[tool]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[tool_execution]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[evaluation]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_pairs_survive_filters_and_pages[approval_wait]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_span_issues_preserve_events</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_unloaded_parent_is_not_missing</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_boundary_status_alone_controls_open_scope</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_abnormal_parent_chain_is_bounded[62]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_abnormal_parent_chain_is_bounded[63]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_abnormal_parent_chain_is_bounded[64]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_abnormal_parent_chain_is_bounded[65]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_abnormal_parent_chain_is_bounded[66]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_count_is_reachable_through_two_hundred_visible_ids[998]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_count_is_reachable_through_two_hundred_visible_ids[999]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_count_is_reachable_through_two_hundred_visible_ids[1000]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_count_is_reachable_through_two_hundred_visible_ids[1001]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_spans.py::test_scope_count_is_reachable_through_two_hundred_visible_ids[1002]</code> | D05,D15 | TA-09 | TC-U6-01～08;TC-U10-B15～B16 | PASS |
| <code>tests/dashboard/test_workers.py::test_third_heavy_work_is_rejected</code> | D15 | TA-24 | TC-U14-07～08,11 | PASS |
| <code>tests/dashboard/test_workers.py::test_http_cancellation_keeps_actual_work_slot</code> | D15 | TA-24 | TC-U14-07～08,11 | PASS |
| <code>tests/dashboard/test_workers.py::test_close_waits_for_actual_workers</code> | D15 | TA-24 | TC-U14-07～08,11 | PASS |
| <code>tests/dashboard/test_workers.py::test_exception_propagates_and_releases_slot</code> | D15 | TA-24 | TC-U14-07～08,11 | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_workspace_survives_deleted_active_directory</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-01;TC-U12-15/explicit-false;TC-U11-01 | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path0-true]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_exists-string | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path1-1]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_exists-number | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path2-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_exists-null | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path3-true]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_is_None-string | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path4-1]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_is_None-number | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path5-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_is_None-null | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path6-true]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_successfully_applied-string | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path7-1]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_successfully_applied-number | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path8-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/patch_successfully_applied-null | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path9-true]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/resolved-string | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path10-1]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/resolved-number | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path11-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/resolved-null | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path12-true]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/infra_failure-string | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path13-1]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/infra_failure-number | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path14-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-13/infra_failure-null | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path15-not a list]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.success-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path16-value16]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.success-nonstring | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path17-not a list]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.failure-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path18-value18]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.failure-nonstring | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path19-not a list]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.success-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path20-value20]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.success-nonstring | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path21-not a list]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.failure-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path22-value22]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.failure-nonstring | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path23-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path24-value24]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path25-None]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path26-value26]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path27-value27]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-14/patch_exists-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path28-value28]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-14/patch_is_None-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path29-value29]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-14/patch_successfully_applied-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path30-value30]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-14/resolved-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path31-value31]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path32-value32]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path33-value33]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.success-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path34-value34]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.failure-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path35-value35]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.success-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path36-value36]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.failure-missing | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path37-value37]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path38-value38]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path39-value39]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.FAIL_TO_PASS.failure-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_report_type_variants_reach_workspace[path40-value40]</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-16/tests_status.PASS_TO_PASS.failure-wrong-container | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_workspace_partial_requests_preserve_database_boundary</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U3-04～05;TC-U9-07,09;TC-U12-01～16;TC-U4-07 | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_preview_rechecks_content_and_cursor</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U3-04～05;TC-U9-07,09;TC-U12-01～16;TC-U4-07 | PASS |
| <code>tests/dashboard/test_workspace_pages.py::test_optional_infra_failure_can_be_absent</code> | D07,D08,D09,D13 | TA-13,TA-20,TA-21,TA-22,TA-29 | TC-U12-15/absent | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_patch_selection_uses_explicit_evidence</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_partial_scan_never_selects_a_default</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_diff_paths_and_incomplete_parse[diff --git a/a b/a\nnew file mode 100644\n--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+hello\n-None-a-add]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_diff_paths_and_incomplete_parse[diff --git a/a b/a\ndeleted file mode 100644\n--- a/a\n+++ /dev/null\n@@ -1 +0,0 @@\n-hello\n-a-None-delete]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_diff_paths_and_incomplete_parse[diff --git a/old name b/new name\nsimilarity index 100%\nrename from old name\nrename to new name\n-old name-new name-rename]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_diff_paths_and_incomplete_parse[diff --git &quot;a/\\344\\270\\255 x&quot; &quot;b/\\344\\270\\255 x&quot;\nBinary files &quot;a/\\344\\270\\255 x&quot; and &quot;b/\\344\\270\\255 x&quot; differ\n-\u4e2d x-\u4e2d x-binary]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_unknown_or_truncated_diff_remains_incomplete[unexpected secret_token=abc123]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_unknown_or_truncated_diff_remains_incomplete[diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n+new\n]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_unknown_or_truncated_diff_remains_incomplete[diff --git a/a b/a\nunknown format\n]</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_deadline_preserves_partial_diff</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/dashboard/test_workspace_projection.py::test_diff_compares_original_paths_before_redaction</code> | D07 | TA-19 | TC-U9-08;TC-U11-01～07 | PASS |
| <code>tests/acceptance/dashboard/test_content_execution.py::test_content_never_executes_and_assets_are_local</code> | D14 | TA-23 | TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[manifest-fifo]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[manifest-socket]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[manifest-directory]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[content-fifo]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[content-socket]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_fifo_and_socket_are_rejected_without_waiting[content-directory]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_content_survives_path_replacement[manifest-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_content_survives_path_replacement[manifest-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_content_survives_path_replacement[content-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_content_survives_path_replacement[content-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[root-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[root-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[manifests-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[manifests-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[run-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[run-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[content-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[content-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[prefix-False]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_open_directory_survives_node_replacement[prefix-True]</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_file_boundaries.py::test_actual_oversize_reads_no_content</code> | D08,D15,D16 | TA-15,TA-17 | TC-U14-01～03,09;TC-U9-13 | PASS |
| <code>tests/acceptance/dashboard/test_installation.py::test_missing_extra_explains_installation</code> | D10,D16 | TA-27 | TC-U1-01～09;TC-U10-22 | PASS |
| <code>tests/acceptance/dashboard/test_installation.py::test_wheel_serves_explicit_database_without_provider_settings[False]</code> | D10,D16 | TA-27 | TC-U1-01～09;TC-U10-22 | PASS |
| <code>tests/acceptance/dashboard/test_installation.py::test_wheel_serves_explicit_database_without_provider_settings[True]</code> | D10,D16 | TA-27 | TC-U1-01～09;TC-U10-22 | PASS |
| <code>tests/acceptance/dashboard/test_installation.py::test_wheel_preserves_existing_task_entry</code> | D10,D16 | TA-27 | TC-U1-01～09;TC-U10-22 | PASS |
| <code>tests/acceptance/dashboard/test_journey.py::test_persisted_evidence_journey</code> | D02,D04,D06,D07,D09,D10,D13,D14,D16 | TA-28 | TC-U16-01～04 | PASS |
| <code>tests/acceptance/dashboard/test_journey.py::test_without_javascript_has_get_navigation</code> | D02,D04,D06,D07,D09,D10,D13,D14,D16 | TA-28 | TC-U16-01～04 | PASS |
| <code>tests/acceptance/dashboard/test_journey.py::test_broken_source_preserves_other_evidence</code> | D02,D04,D06,D07,D09,D10,D13,D14,D16 | TA-28 | TC-U16-01～04 | PASS |
| <code>tests/acceptance/dashboard/test_journey.py::test_status_text_has_distinct_color_cues</code> | D02,D04,D06,D07,D09,D10,D13,D14,D16 | TA-28 | TC-U16-01～04 | PASS |
| <code>tests/acceptance/dashboard/test_journey.py::test_long_patch_paths_wrap_on_narrow_viewport</code> | D02,D04,D06,D07,D09,D10,D13,D14,D16 | TA-28 | TC-U16-01～04 | PASS |
| <code>tests/acceptance/dashboard/test_refresh.py::test_poll_backoff_and_visibility</code> | D13,D14,D15 | TA-22,TA-26 | TC-U15-01～08;TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_refresh.py::test_old_response_cannot_replace_new_filter</code> | D13,D14,D15 | TA-22,TA-26 | TC-U15-01～08;TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_refresh.py::test_refresh_restores_far_anchor</code> | D13,D14,D15 | TA-22,TA-26 | TC-U15-01～08;TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_refresh.py::test_workspace_preview_survives_metadata_refresh</code> | D13,D14,D15 | TA-22,TA-26 | TC-U15-01～08;TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_refresh.py::test_terminal_stops_automatic_but_manual_refresh_remains</code> | D13,D14,D15 | TA-22,TA-26 | TC-U15-01～08;TC-U13-02,07 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_real_sql_is_interrupted_after_deadline[5.0]</code> | D15,D16 | TA-24,TA-25 | TC-U14-05/deadline=5.0 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_real_sql_is_interrupted_after_deadline[5.001]</code> | D15,D16 | TA-24,TA-25 | TC-U14-05/deadline=5.001 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_real_sql_before_deadline_succeeds</code> | D15,D16 | TA-24,TA-25 | TC-U14-12/deadline-1ms | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_heavy_stages_run_inside_worker_slot</code> | D15,D16 | TA-24,TA-25 | TC-U14-11 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_cancelled_http_keeps_slot_until_worker_finishes</code> | D15,D16 | TA-24,TA-25 | TC-U14-07;TC-U14-08 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_real_busy_lock_times_out_and_recovers</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[4.999-directory]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[4.999-file]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[4.999-json]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[4.999-diff]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.0-directory]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.0-file]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.0-json]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.0-diff]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.001-directory]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.001-file]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.001-json]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/acceptance/dashboard/test_resource_boundaries.py::test_each_heavy_loop_observes_deadline[5.001-diff]</code> | D15,D16 | TA-24,TA-25 | TC-U14-04～12 | PASS |
| <code>tests/cli/test_main.py::test_first_signal_requests_graceful_cancellation</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_second_signal_forces_task_cancellation</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_signal_exit_code_follows_shell_convention[2-130]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_signal_exit_code_follows_shell_convention[15-143]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_successful_run_prints_cost_after_output</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_separate_cache_pricing_prints_cache_cost</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_successful_run_prints_persisted_patch_location</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_successful_run_with_finalization_error_exits_nonzero</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_cli_forwards_docker_workspace_options</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_cli_model_override_preserves_generation_options</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/cli/test_main.py::test_unknown_requests_are_not_rendered_as_zero_cost</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_records_survive_reopening_and_preserve_metadata</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_same_content_has_independent_records</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_changed_content_is_rejected</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_missing_content_is_rejected_by_run_query</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_artifact_path_fields_must_be_safe_segments</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/artifacts/test_file_store.py::test_multi_log_collection_rejects_any_corrupted_content</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_event_sequences_are_contiguous_and_scoped_to_run</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_tool_end_round_trips_full_result_after_reopen</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_concurrent_append_with_same_expected_sequence_has_one_winner</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_commit_atomically_updates_state_context_events_and_snapshot</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_snapshot_requires_current_semantic_state</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_approval_resolution_is_atomic_and_idempotent</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_conflicting_approval_resolution_rolls_back_event</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_store_reopens_existing_database</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_event_trace_scope_round_trips</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_approval_resolution_and_invalidation_are_scoped_to_run</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_expired_approval_cannot_be_backdated</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_idempotent_resolution_ignores_retry_timestamp</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_recovery_context_never_serializes_environment_values</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_persisted_workspace_reuses_normalized_runtime_options</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_commit_freezes_nested_input_before_dispatch</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_concurrent_initialization_serializes_migration</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_unsupported_database_schema_is_rejected[2]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_unsupported_database_schema_is_rejected[4]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_snapshot_change_does_not_require_a_metadata_event</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_newer_record_versions_are_reported_as_unsupported</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_legacy_recovery_context_is_rejected_without_loading_values</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_newer_related_record_versions_are_reported_as_unsupported[event]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_newer_related_record_versions_are_reported_as_unsupported[snapshot]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_newer_related_record_versions_are_reported_as_unsupported[approval]</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |
| <code>tests/storage/test_sqlite.py::test_historical_snapshot_preserves_request</code> | D16 | TA-01,TA-27 | 既有兼容回归（无新增TC编号） | PASS |

## 实施裁决记录

以下按实际决定顺序保留原始裁决与判断错误的代价。

- Ruling: user-authorized v3 baseline adaptation — current producer has Resume and schema v3; retain dashboard-only scope — cost if wrong: revise fixtures and evidence interpretation, no producer or DB writes.
- Task 1: Ruling: parser budget annotation uses a TYPE_CHECKING check-only Protocol until Task 2 defines ReadBudget — plan imports nonexistent Task 2 module while requiring ty green for Task 1 — cost if wrong: replace one type-only annotation/import in Task 2, no runtime change.
- Task 7: Ruling: parent depth counts requested scope as level 1, maximum 64 loaded nodes along a chain — fixed inclusive JSON-style depth convention resolves unspecified ancestor counting — cost if wrong: one fewer ancestor than a 64-parents interpretation; users can navigate another bounded scope.
- Task 9: Ruling: introduce project_historical_memory in Task 9 rather than Task 10 — Task 9 explicitly requires current/historical distinction test, and shared pure implementation avoids a fabricated current RunRecord — cost if wrong: move one function between commits; public signature remains the planned Task 10 contract.
- Task 10: Ruling: move the existing field-presence checker to reads and shared boundary SQL check to queries/connection, and separate bounded immutable Snapshot loading from message-window slicing — events→snapshots→runs/events otherwise cycles when loading exact compaction sources — cost if wrong: three small prior-task files are touched; public contracts and read semantics stay unchanged.
- Task 11: Ruling: add defaulted issues: tuple[ReadIssue,...]=() to ManifestPage — the plan requires reporting non-UUID directory entries but its page contract has no place for a directory issue; inventing a navigable Artifact ID would violate evidence identity — cost if wrong: downstream rendering reads one additional optional field; existing positional constructors remain compatible.
- Task 12: Ruling: optional expected ArtifactRef fields set to None remain unrecorded rather than conflicting with manifest facts — the global missing-versus-conflict rule applies; all supplied fields, mandatory ID and URI are matched — cost if wrong: stricter callers may prefer rejecting partial refs; evaluation still must require its necessary chain fields.
- Task 14: Ruling: fix existing JSON/text preview local URI disclosure before adding the evaluation adapter — current producer embeds patch_ref.uri, violating the already-required display boundary — cost if wrong: file URI text and location-key values are now hidden, reducing diagnostic detail; verified identities/hashes remain raw in reads. Regression tests JSON ArtifactRef URI and plain-log file URI each RED→GREEN; content/preview suite 77 passed.
- Task 19: Ruling: SourceCursor adds defaulted return_filters and return_grouped; source_href accepts optional keyword context — SnapshotParams deliberately has only range/boundary, so signing normalized filter context in the source token preserves the required return navigation without arbitrary return URLs — cost if wrong: source tokens grow and prior signed shape changes within this unreleased process-local protocol.
- Task 20: Ruling: parse_diff accepts optional recorded_files, comparing unredacted parsed new paths (old path for deletion) before building display views — matches the producer's git name-only file list and avoids comparing redaction-collapsed labels; partial parses never assert a mismatch — cost if wrong: rename metadata using both source and destination names will receive a conservative mismatch notice.
- Task 26: Ruling: amend Task 19 return-context decision: SourceCursor rejects raw tool_name; normalized non-text filters/grouping remain signed, tool_name travels as an ordinary typed GET value (SnapshotParams gains optional tool_name) for same-Run return navigation — TA-29 forbids original search terms inside tokens; no arbitrary return URL is accepted — cost if wrong: copying a source token alone no longer restores its tool-name filter without the corresponding URL parameter.

## 独立整分支审查

2026-10-20 按 executing-plans 派发唯一一次全分支独立审查，reviewer 为 gpt-6-astra / high，范围 `49a8714..9fa4427`。审查者只读源码、设计、计划、11 条原有裁决与证据，并用真实 SQLite／FileArtifactStore／ASGI 复现；未修改工作树、索引或提交，未再派子代理、未重复全套测试。

结论：0 Critical、8 Important、3 类 Minor；建议修复后合并。执行者按实际用户影响复核，8 项 Important 均成立。一次 TDD 修复批次如下，所有修复限制在 Dashboard 及其测试中：

| 编号 | 独立审查发现／根因 | 修复和消费路径回归 |
| --- | --- | --- |
| R1 | v3 ProviderIdentityChanged 的两个 endpoint identity 不在共享脱敏表 | 把已知连接字段纳入共享规则；真实持久化 Event→详情响应验证两个地址均不泄露 |
| R2 | ToolResult.output 及子字段缺失被 decoder 默认值补成 complete | 按 Pydantic 字段存在性判断完整度，payload exclude_unset；删除整个 output／单一 false 标记均显示未记录且不补造 payload |
| R3 | 默认 patch 在 config/summary 消费前选择，已知 hash 冲突仍声称明确关联 | 先解析有界结构化证据并投影关联冲突，再默认选择；合法 hash 的冲突 config 不预选 patch |
| R4 | 原生 patch form 带 boundary，只返回 diff，无法更新多 attempt 评测卡 | 显式选择走完整 Workspace GET，创建新页面边界；每次仍只读一个 patch；两 attempt 原生表单选择后仅所选组通过，其余明确尚未校验 |
| R5 | JSON MIME 参数／大小写及按业务类型解析绕过正文预算 | Reader 对规范化 application/json 与 evaluation_config/summary/report 均先预留实际大小；5 个变体在不足预算下均不读内容 |
| R6 | Python 5,000 位整数限制抛普通 ValueError，未转换 ContentError | 保留已有固定 ContentError，其他 JSON ValueError 转 json_invalid；真实 Artifact 预览返回200元信息＋解析问题，无正文回退 |
| R7 | codec 降级后 envelope 又裸解析时间，坏一行毒化整页 | 坏时间设 None＋行 issue，保留 ID/sequence/status；详情邻居仍可见，Run 的三个时间字段分别隔离，坏创建时间分页可继续 |
| R8 | project_run.latest_compaction 从未接入实际页面 | 从同一边界已读取的最近 CompactionCompleted 接入估计，展示 Event序号并可定位；过滤掉压缩事件的详情仍显示两个估计 |

修复前 16 个变体实际失败。Tool output 与 Event 时间的首轮夹具 UPDATE 被不可变触发器拦截，不能当作产品 RED：仅在损坏测试数据库删除对应触发器，恢复 output 投影到原 HEAD，然后重新观察完整性误报及详情500的真实 RED。修复后16个变体通过；另外增加坏 created_at 的游标分页回归，共17个通过。Ruff（仅修改 Python 文件）与全仓 ty check 通过。没有执行第二轮 reviewer。

### 暂缓的次要问题

- Runs 行没有展示 item.issues 的具体原因，只显示未加载／未记录。
- Event 行没有展示时间和 span ID；Runs 的状态选择器没有原生多选，GET 仍支持多个状态。
- 部分既有测试／builder 存在中段 imports，按仓库规则不阻塞提交。

### 最终裁决（按发生顺序）

- Final: Ruling: real device node behavior remains DEFER — no safe nonprivileged fixture; FIFO/socket evidence does not establish device behavior — cost if wrong: unsupported node behavior could differ until tested with a dedicated fixture.
- Final: Ruling: native OS hidden-event delivery remains DEFER — controlled visibility tests prove refresh logic only, headed window switching did not change document.hidden — cost if wrong: native window hiding may not pause refresh on some platforms.
- Final: Ruling: human usability/assistive technology/WCAG certification not claimed — AI screenshots and keyboard checks establish only the exercised paths — cost if wrong: human or assistive technology issues remain undiscovered.
- Final: Ruling: non-macOS file boundary compatibility not claimed — actual descriptor and race evidence is macOS only — cost if wrong: another OS may reject supported reads or handle unsafe nodes differently.
- Final: Ruling: malformed created_at uses a nullable typed timestamp and a signed Run ID cursor anchor resolved inside the next short read transaction — no fabricated date and no corrupt raw timestamp in signed tokens, pagination still reaches adjacent valid records — cost if wrong: deleting the malformed anchor between pages returns controlled409 and requires refreshing the list.

### 修复批次最终验证

最终命令：`UV_CACHE_DIR=/private/tmp/miniagent-uv-cache uv run --extra dashboard pytest -q --junitxml=.superpowers/sdd/2026-10-19-dashboard/final-suite.xml`。真实浏览器、文件描述符、套接字与进程用例在允许这些 OS 操作的执行环境中运行。

结果：**1388 passed, 19 skipped, 0 failure/error，298.52 秒，exit 0**。其中 Dashboard 574 个通过（原557＋最终回归17）；19个 skip 没有改记 PASS。修改文件的 Ruff check／format 与全仓 `uv run --extra dashboard ty check` 均通过。以上 Task 26 原有599行证据是修复前基线，最终结果以本节为准；原有5项 mutation 证据来自审查前版本，本次未重复 mutation 或独立审查。

新增最终回归的实际 JUnit 节点如下（R编号对应上表，所有状态均从最终全仓 XML提取）：

| 实际测试节点 | 状态 |
| --- | --- |


| <code>tests/dashboard/test_final_review.py::test_provider_identity_change_hides_both_connections</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_unrecorded_tool_output_is_not_complete[output]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_unrecorded_tool_output_is_not_complete[stdout_truncated]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_every_structured_artifact_reserves_body_budget[application/json; charset=utf-8-evaluation_report]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_every_structured_artifact_reserves_body_budget[Application/JSON-evaluation_report]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_every_structured_artifact_reserves_body_budget[text/plain-evaluation_config]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_every_structured_artifact_reserves_body_budget[text/plain-evaluation_summary]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_every_structured_artifact_reserves_body_budget[text/plain-evaluation_report]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_large_json_integer_degrades_only_the_preview</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_bad_event_time_keeps_neighbour_and_identity</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_bad_run_time_is_a_row_issue[created_at]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_bad_run_time_is_a_row_issue[updated_at]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_bad_run_time_is_a_row_issue[finished_at]</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_corrupt_creation_time_cursor_keeps_other_runs_reachable</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_recent_compaction_estimates_reach_filtered_detail</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_patch_selector_rebuilds_evaluation_cards</code> | PASS |
| <code>tests/dashboard/test_final_review.py::test_conflicting_config_prevents_default_patch</code> | PASS |

全仓跳过节点（保留 skip 原意）：

- `tests.acceptance.resume.test_docker::test_same_container_layer_survives_resume[running]`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_docker::test_same_container_layer_survives_resume[exited]`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_docker::test_deleted_or_replaced_container_is_rejected[False]`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_docker::test_deleted_or_replaced_container_is_rejected[True]`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_docker_connections::test_workspace_and_evaluator_use_their_own_daemons`：DEFER: three independently prepared daemon connections unavailable
- `tests.acceptance.resume.test_docker_connections::test_frozen_tls_connection_survives_resume`：DEFER: independently prepared verified TLS daemon/image unavailable
- `tests.acceptance.resume.test_docker_faults::test_real_helper_and_guest_birth_identity_is_recorded`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_docker_faults::test_dead_supervisor_with_live_group_is_busy`：DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull
- `tests.acceptance.resume.test_swebench::test_swebench_resume_preserves_patch_and_expected_unresolved`：DEFER: no independent prior unresolved manifest; user confirmed unavailable
- `tests.environments.test_docker::test_docker_workspace_with_task_image`：MINIAGENT_DOCKER_IMAGE is not configured
- `tests.environments.test_docker_attach::test_real_running_and_exited_container_retains_original_layer`：DEFER: requires an explicitly prepared Docker image; no pull
- `tests.environments.test_docker_supervisor::test_supervisor_separates_control_frames_from_command_output`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.environments.test_docker_supervisor::test_supervisor_exit_does_not_hide_live_group`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.environments.test_docker_supervisor::test_supervisor_streams_large_stdin_and_binary_output`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.environments.test_docker_supervisor::test_killed_supervisor_leaves_no_completed_evidence`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.environments.test_docker_supervisor::test_real_sdk_chain_preserves_binary_io_and_end_evidence`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.environments.test_docker_supervisor::test_guest_identity_commit_failure_never_releases_command`：DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image
- `tests.integration.test_swebench_evaluation::test_real_ready_development_patch_is_verified`：Real SWE-bench requires explicit cached-image opt-in
- `tests.integration.test_swebench_evaluation::test_real_invalid_patch_fails`：Real SWE-bench requires explicit cached-image opt-in

本计划的临时 ledger、brief、diff 和原始运行日志在最终修复提交后删除；全部裁决、暂缓项、执行结果与可复核测试源码保留在本证据和 Git 历史中。
