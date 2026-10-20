# Dashboard Test Design

日期：2026-10-19；文件日期沿用所属需求增量。状态：测试设计评审稿，未实现、未执行。

Review修订：按 [Test Strategy Audit](2026-10-14-dashboard-test-strategy-audit.md) F1～F5补报告类型反例、实际重阶段线程观测、fstat超限零读取、JSON词法正例及SQL截止协议。原TC保留，新增TC续号；audit中的NOT-ADEQUATE保留为修订前快照，本次文档修订不代表重新审计通过。

依据：[功能设计 D](2026-10-14-dashboard-design.md)、[架构设计 A](2026-10-14-dashboard-architecture-design.md)、[Test Analysis TA](2026-10-14-dashboard-test-analysis.md) 的当前工作区版本。保留 D01–D16、A1–A11、TA-01～TA-29；U、TC、EP、规则、转换和路径为本文局部编号。

本轮只设计测试，不实现 Dashboard 或自动化测试，不宣称完成 RED/GREEN。TDD 约束用于后续实现：先写一个能捕获指定错误的行为测试，观察正确的断言失败，再写最小生产代码并回归。缺模块导致的 collection/import error 不是有效 RED；接口建立后必须再次观察行为断言失败。设计未规定私有 API、DOM 布局或完整实现计划。

## 1. 测试设计计划

| Unit | Source | Logic | Technique | Why this technique |
| --- | --- | --- | --- | --- |
| U1 | D10、D16；A§3、5、11.1；TA-01、27 | 启动、包装、只读与旧 CLI | 等价分区 | 安装/数据结构/入口类别有不同结果 |
| U2 | D01；A§6.1；TA-03、04、05 | Run 列表查询 | 等价分区；有序位置另用边界表 | 固定筛选类别与字面匹配，排序具有独立不变量 |
| U3 | D13；A§5.2；TA-02、07、22 | 事务与页面边界 | 用例测试 | writer 与 reader 的交错流程不能仅靠纯投影验证 |
| U4 | D13、D15；A§6.3；TA-29 | 游标与参数 | 等价分区；有效期/大小另用 U10 | 签名正确不代表用途和范围合法 |
| U5 | D03–D05；A§6.2、7.1；TA-07、08 | 失败谓词及工具关联 | 决策表 | 类型、状态、Step/call 共同决定匹配与解释 |
| U6 | D05；A§7.2；TA-09 | Span 事实解释 | 决策表 | 唯一性、父关系、时间与边界状态决定结果 |
| U7 | D06、D11；A§8；TA-10～12 | 摘要与来源、尝试归组 | 决策表 | 结构、范围、内容与加载状态必须分开判定 |
| U8 | D02、D11–D12；A§7.3；TA-04～06 | Run/usage/费用与时间展示 | 等价分区 | 不同事实来源和缺失类别不得被推断合并 |
| U9 | D07–D08、D15；A§9；TA-13～18 | manifest、文件与预览类别 | 等价分区 | 登记、类型、路径、内容完整性有不同拒绝/降级结果 |
| U10 | D01、D05–D06、D08、D13、D15–D16；A§12；TA-03、07、09、11、13、16～18、24、27、29 | 字节、数量、深度、时间与位置 | 边界值分析 | 用精确单位检查限额前后，不以常量断言代替执行 |
| U11 | D07；A§10.1；TA-19 | 默认 patch 选择 | 决策表 | 已关联/唯一/多候选/扫描完整性共同决定选择 |
| U12 | D09；A§10.2；TA-20、21；F1 | 独立评测证据链与报告字段类型 | 决策表；字段类型另列等价分区 | 链条件与严格类型是不同失效面，不能靠false语义例证明拒绝字符串/数字 |
| U13 | D14–D15；A§11.2–11.3；TA-16、18、23、25 | 同源、HTML、脱敏与错误输出 | 等价分区 | 各输入面必须到达共同机制，非法输入逐项隔离 |
| U14 | D08、D15–D16；A§9.2、12；TA-01、15、17、24、25 | 真实竞争、资源与取消 | 用例测试 | 文件描述符/线程/锁的生命周期需要真实边界观测 |
| U15 | D13、D15；D§4；A§11.4；TA-22、26 | 轮询与刷新状态 | 状态转换 | 失败历史、页面可见性和终态改变后续请求行为 |
| U16 | D三页面DoD；D§4；TA-22、28 | 开发者证据浏览旅程 | 用例测试 | 只保留一条跨页主路径，其余交互是局部替代/异常路径 |

规则组合、有序限额、持久化读取边界与浏览器交互分开建模。单用场景列表会漏掉边界值，单用决策表无法证明线程取消或旧响应覆盖的时序。已知多条件规则不以 pairwise 替代；浏览器不重复纯逻辑的全部分支。

## 2. 假设、待落实项与公共协议

### 2.1 假设与问题

| ID | Assumption or question | Why it matters | Affects |
| --- | --- | --- | --- |
| ASM-1 | 每个负向变体只有一个无效条件，除显式标记 robustness 的测试 | 防止坏 hash 掩盖错 Run 等错误 | 所有参数化反例 |
| ASM-2 | 一个表行中的具名变体必须分别执行；完整 ID 为基础 TC 加 `/variant`，不算一次测试覆盖所有变体 | 可定位、可复现；不夸大执行覆盖 | 含变体的 TC |
| ASM-3 | KiB=1024、MiB=1048576；字节限额用 UTF-8 编码长度；时间测试步长 δ=1ms，仅是夹具测量精度 | 避免字符/字节及测量单位混淆 | U10、U14、U15 |
| ASM-4 | 所有数据合成且隔离，生产Store仅在准备阶段创建DB/manifest；被测Dashboard完全只读 | 排除测试准备写入干扰 | 全部 |
| ASM-5 | 初始自动刷新关闭；测试启用轮询后才检查退避，浏览器传输调度仅控制响应时序/失败 | 不把网络stub当真实DB锁证据 | U15；真实后盾U14 |
| ASM-6 | F1/F4的JSON样本在测试准备阶段独立编码、计算hash并重建对应合法manifest/事件引用；只改变指定字段/词法内容，不调用被测adapter计算期望 | 防止hash/引用坏项掩盖类型或词法错误 | TC-U12-13～16、TC-U9-10～12 |
| ASM-7 | F3以M=20971520字节，真实内容hash与路径布局正确，仅manifest.size_bytes虚报；I/O记录从产品请求开始，排除夹具准备的读写/hash | 使零内容读取oracle确实针对产品fstat门禁 | TC-U9-13～14 |
| Q-1 | 游标到期恰等于 expires_at 的判定、未来签发的容忍规则尚未定义 | 改变临界预期 | TC-U4-06、U10-T01 临界子例 DEFER；远离临界仍可设计 |
| Q-2 | 金额最终展示精度/舍入方式未定 | 不能擅定小数位或rounding mode | U8精细Decimal格式子例DEFER；精确内部值/不重算仍可断言 |
| Q-3 | 缺失与明确冲突同时存在时的总标题/优先级未定 | 不得虚构一种覆盖另一种 | TC-U12-09仅断言两项事实/禁通过，总标题DEFER |
| Q-4 | 公共观测接口及OS级工具尚未实现/选定 | 决定能否稳定命中真实窗口；设施缺失与已经观测到的产品失败必须分开 | U3、U14及G-STAGE；仅设施/夹具未准备记DEFER，目标已开始后缺handler/错线程按协议判FAIL |
| Q-5 | 真实宿主计时容差 ε、测试监督上限和环境负责人尚未登记 | 无法凭任意宽超时判断1秒/5秒策略有效 | TC-U14-04～07真实计时结论DEFER，逻辑截止测试可确定 |
| Q-6 | 支持的OS/浏览器/辅助技术具体版本未定 | 执行环境不等同产品兼容承诺 | U14/U16：先记录实际macOS环境，Linux/其他浏览器待支持面确认 |
| Q-7 | Event页大小最小值、Snapshot消息范围参数的最小/空区间语义未明确 | 不能把0/空范围臆断为422或有效 | U4/U10只测试已明确最大值、负偏移和越界；其余DEFER |
| Q-8 | 同时触发不同大小限制/路径错误时的HTTP优先级未定义 | 20MiB超限与登记大小冲突可能同时存在 | U9/U10分离单故障；组合优先级不设预期 |
| Q-9 | 正常Span父类别是有向有限层级，64层及1000 scope在最大200事件批次下可能仅对异常历史可达 | 不能为了边界构造而放宽RunTrace/父类别约束 | U10-B15/B16：使用原始异常envelope；若提前拒绝使限额不可达，记录不可达理由，不声称合法深链通过 |
| Q-10 | A§9.1/§10.1要求扫描/解析不完整时保留问题，A§12又将查询预算耗尽列为503；同类局部中断的顶层状态需明确 | 不能以“200或503均可”作为自动化oracle | TC-U14-06：循环退出与局部标记可确定，顶层HTTP状态DEFER，纯SQL预算503已确定 |

Q项不会使其他场景停止设计。待定项是显式缺口，不是隐含需求选择或风险接受；后续落实只更新受影响测试，不修改两份输入文档。

### 2.2 数据与独立 oracle

- `R`、`R2` 是两个不同合法UUID的Run；`E(n)` 是R的sequence=n事件；`S(v,q)` 是R、version=v、event_sequence=q的Snapshot。外部列与内部值一致的合法样本为基线；错身份样本只改一个字段。
- `F0`：非终态R，as_of_sequence=240、latest_snapshot_version=2、S(2,200)；`F1`：writer在同一真实RunCommit发布终态、E(241)、S(3,241)和不同usage/摘要。F0/F1的可观察值逐项写入fixture，禁止只检查sequence而忽略usage/摘要是否混用。
- `L`：30条Run，创建时间两组，每组UUID末尾为可比较的固定数值；期望顺序用预先列出的ID数组，不使用被测排序/查询函数生成。`task`样本分别为`literal % done`、`literal _ done`、带escape字符、`literal X done`和包含引号的普通文本。
- `P`：最小合法文本patch，`P2`：不同hash/submission的另一patch；`A1/A2`为不同attempt UUID；`I1/I2`为实例字符串；`C_good`为真实生产形状的config、final summary、5.0.2 report与同Run manifest及EvaluationCompleted引用。report自身没有未生产的Run/attempt字段。
- `C_good`中report明确resolved、非空patch、成功apply、无infra_failure，tests_status的两类组分别使用`test_fix`与`test_regression`成功，失败数组为空；这两个名字只是报告记录，不作为目标全集。事件passed=true且Run=succeeded。预期通过由源设计逐项手工核对，不调用被测adapter得出。
- `M_good`使用实际模板v1构造完整摘要，保留完整原始UTF-8及独立SHA-256。EventSource叶文本`verified ok`，对应excerpt/claim均为`verified ok`；field_path指向实际值`3`，规范JSON claim为`3`；SnapshotSource指明固定消息范围。摘要历史值与当前值刻意不同。
- `U_good`：input=100、output=20、total=120、cached input=40、reasoning output=5；持久化总cost为Decimal("0.30")，摘要贡献Decimal("0.10")已包含其中；current max_steps=10，steps_started=9，active_duration_ms=12000，started_at到finished_at差20秒。计费分项按实际Usage模型构造，不虚构存储字段。
- 秘密只用合成哨兵：敏感键值、Authorization/Bearer行、已知凭证前缀、私钥块分别有唯一token；另含数字秘密`{"token":123456789}`与真实usage统计路径中的token计数，检测白名单过宽。宿主路径、URI和query搜索词另放不同哨兵。
- `RO`：无writer并发时，HTTP/启动前后所有逻辑表、schema/user_version、artifact树路径和字节相等；SQL观测无DDL/DML/initialize/migration，不设置journal mode。WAL/SHM协调变化不计业务写入。所有成功/错误/HEAD/取消路径复用RO后置条件。

每个测试记录数据版本、R/attempt/Artifact/hash、原始输入、动作和独立期望。连接与文件是真实依赖；不mock内部queries/projections/codec/hash或FileArtifactStore安全方法。浏览器传输替身的省略语义必须由真实服务场景补足。

### 2.3 可观测时序协议

下列名称是测试协议，不要求新增同名产品方法；不得为了测试在生产类添加专用cleanup/故障函数。使用可组合的现有公共连接/读取/工作调度边界或OS观测落地；具体接口未定时保留Q-4。不得monkeypatch私有查询函数、伪造SQLite事务结果或用sleep猜时机。

| 协议 | 必须定位的事实 | 协调方式与独立证据 | 未命中判定 |
| --- | --- | --- | --- |
| G-DB | Dashboard事务已执行首个实际读取，WAL视图已固定，剩余关联读取尚未结束 | 公共连接适配边界委托真实SQLite、记录实际查询与线程身份，外部管道握手后第二连接提交F1；释放读取并重开核对 | 只在BEGIN前握手不证明固定视图；未定位不能算并发证据 |
| G-FD | 根下目标已以安全方式打开，内容读取未结束 | OS文件调用观测或公开Reader资源边界记录真实FD/fstat；外部进程rename原路径并放替代物；释放读取 | 未确认open发生的竞争不算TOCTOU验证 |
| G-WORK | 两个重内容任务已占名额且实际仍在工作 | 公开任务边界委托真实读取/解析；可控资源握手阻断，记录开始/完成；第三请求及HTTP取消与实际完成分别观测 | 只记录HTTP开始/结束不能证明线程存在或名额未早释 |
| G-SQL | 真实目标SQL已进入SQLite执行且仍活动；独立记录读取单元开始t0和5秒预算，尚不要求handler安装/触发或查询结束 | 在公共连接/实际SQL观察边界确认目标语句及活动身份后握手，监督者推进时钟越过t0+5秒并允许SQL继续运算；中断/503/实际任务完成全部为后置结果 | 设施无法建立才DEFER；确认目标开始后未主动截止为产品FAIL，不能等待“handler已触发”才开始验证 |
| G-STAGE | 同一真实请求的实际重阶段入口/退出、请求ID、线程身份及重任务名额区间可观察 | 记录ASGI线程身份；在公开执行/解析边界委托真实解码、页面投影、hash和完整脱敏，采实际阶段执行线程而不只是提交线程；以结果确认场景确实消费该阶段 | Q-4设施缺失才DEFER；实际阶段在ASGI线程或在名额外执行则FAIL；仅executor包装日志/暂停时health成功不算证据 |
| G-UI | 旧请求已发出但尚未到达DOM，新筛选/刷新代次已完成 | 浏览器传输层延迟旧响应，保存真实服务器边界令牌，先交付新响应再旧响应 | 不能只比较请求发出顺序 |

每个协议有独立监督进程/超时与finally回收，不能让FIFO或后台任务挂住测试。监督超时触发kill是测试失败/未验证，不是产品正确停止。时间记录使用monotonic；测试监督上限不替代产品预算。

G-SQL特别区分：目标SQL未开始就被其他错误拒绝，属于夹具未命中，不能算预算通过；目标已确认执行、截止已越过且允许其继续CPU运算，仍持续到监督回收，则记录“SQL未按读取单元预算中断”的产品失败，附活动/时钟证据后再清理。查询在推进时钟前已自然结束需重建夹具，不计预算证据。缺handler不是DEFER理由，也不以任意内核慢IO证明SQLite主动检查失败。

## 3. 分单元测试设计

各单元的Source对下表所有TC生效；行中的EP/R/PATH/BV是Covers，不是另一份产品需求。Scope说明真实SUT与依赖，When列为刺激。每行的“捕获错误”回答TDD中应使测试失败的错误生产变化。

### U1：安装、启动与只读

Scope：真实wheel/隔离环境中的console script、Typer、实际SQLite/文件与ASGI；Source：D10、D16，TA-01/27，A§3/5/11.1/14。

| Partition | 分类及等价规则 | 代表输入 / 期望类别 |
| --- | --- | --- |
| U1-E1 | 无Web extra；独立CLI延迟依赖 | 最小安装：旧CLI可用，dashboard友好安装说明 |
| U1-E2 | 有extra且无Provider配置；只依赖DashboardSettings | 无key/.env：启动成功 |
| U1-E3 | 显式CLI路径；不读取旧环境契约 | 冲突.env/环境路径：使用CLI输入 |
| U1-E4 | DB缺失；禁止初始化 | 缺父目录/DB：启动失败且无新文件 |
| U1-E5 | 结构不支持；启动检查拒绝 | user_version错、缺必需表/列、缺JSON能力分别测试 |
| U1-E6 | 结构支持但单行不支持；隔离而非启动全扫 | 单行schema新版本：服务启动，该行问题视图 |
| U1-E7 | 只读合法请求；GET/HEAD不会写业务 | 已有DB/Artifact：RO |
| U1-E8 | 不提供的操作或选项 | --experiments、--host、--workers、修改路由：拒绝/不存在 |

| ID | Covers | Given / 数据 | When | Then | 捕获错误 |
| --- | --- | --- | --- | --- | --- |
| TC-U1-01 | E1 | 最小wheel安装、原TASK夹具 | 调用原miniagent TASK，外部provider用真实协议stub提供确定响应 | task正确进入原入口且产生原预期结果，不导入Web依赖；另调用dashboard时给extra安装说明而非ImportError堆栈 | 将Web依赖变成强制依赖/子命令破坏TASK |
| TC-U1-02 | E2 | 有extra、真实库、清除Provider配置且无.env | 启动dashboard并请求/runs、/healthz | 页面可用；health不含路径/配置；无Provider/Workspace/Evaluator启动；RO | 复用Settings导致缺key失败 |
| TC-U1-03 | E3 | .env/旧环境变量指向带哨兵DB，CLI指向另一库 | 启动并浏览 | 仅CLI库的R可见；旧配置哨兵不可见；未读取模型limits/pricing；缺省另验证沿用既有路径默认值 | 继承.env/旧配置来源 |
| TC-U1-04 | E4 | 分别缺DB、缺父目录 | 启动 | 失败有可读原因，无空DB或目录被创建 | 读服务隐式initialize |
| TC-U1-05 | E5 | 单个结构缺陷，其余支持 | 启动 | 明确结构不支持且RO；缺JSON能力仅在实际无该能力SQLite环境执行，不mock返回 | 默许不支持结构/启动迁移 |
| TC-U1-06 | E6 | 一个坏行与一个合法Run | 启动并无筛选列举 | 服务不全库拒绝，坏行ID/问题及好行可见，无原始state泄露 | 全库预验证或把坏行当空 |
| TC-U1-07 | E7 | 参数化完整页/片段/来源/预览/静态/health及各错误场景 | GET与HEAD后重开库和文件，收集SQL/调用边界 | RO；HEAD安全/参数规则相同且无body；每个读取单元同工作线程开用关，事务在渲染/文件阶段前结束 | 用写Store生命周期、跨线程共享连接或长事务 |
| TC-U1-08 | E8 | 合法Run；分别POST/PUT/PATCH/DELETE业务路由 | 发请求；另访问/compare及未知执行入口、调用延后选项 | 非GET/HEAD业务方法405；未提供路由404；CLI未知选项失败；无Compare/Checkpoint/恢复入口或空占位卡；RO | 新增改变业务的端点/预建延后能力 |
| TC-U1-09 | E2 | 有extra打包应用、有效端口 | 启动观察监听并读取包内资源 | 固定127.0.0.1、单worker，无自动浏览器；HTMX/CSS/JS可从包读取，无CDN请求 | 错host/worker或漏打包资源 |

有效分区未合并以掩盖安装差异；E5每种缺陷独立保留variant。端口数值边界见U10。

### U2：Run 列表

Scope：真实SQL/HTTP；Source：D01、D02，TA-03～05，A§6.1。

| Partition | 等价规则 | 代表与结果 |
| --- | --- | --- |
| U2-E1 | 精确/多选筛选，各字段有固定源 | UUID、模型、Workspace、多个状态分别得到已知ID集 |
| U2-E2 | task按普通文本包含 | `%`、`_`、escape、引号分别字面匹配 |
| U2-E3 | 缺Workspace来源 | unknown显示并可筛选，不推断evaluator |
| U2-E4 | 无匹配 | 空列表+清除筛选入口，不是失败 |
| U2-E5 | 有序稳定位置 | 同created_at按run_id降序；新插入不挤乱下一页 |
| U2-E6 | 无法解码但envelope可读 | 无筛选异常行；无法判定的筛选不冒充匹配 |
| U2-E7 | 列表没有持久化独立结论 | 详情查看证据，无resolved rate |

| ID | Covers | Given | When | Then | 捕获错误 |
| --- | --- | --- | --- | --- | --- |
| TC-U2-01 | E1 | 合成已知ID集合 | 每个筛选独立请求，再做一组全部合法的组合筛选 | 返回精确交集及同事务总数/状态计数；URL保留规范参数 | 页内过滤/计数不同谓词 |
| TC-U2-02 | E2 | L中字面特殊字符与相似普通文本 | 分别搜索%、_、escape、引号 | 仅含该字面文本的ID出现，无SQL表达式效果；query不进入日志 | 未escape LIKE或拼接SQL |
| TC-U2-03 | E3 | 缺workspace_spec.kind与一个合法local Run | 筛unknown并浏览 | 只出现unknown样本，历史验证类型未记录 | 默认local或默认pass-through |
| TC-U2-04 | E4 | 无匹配关键词 | 请求、清除筛选、返回 | 空结果有入口，清除后恢复ID；浏览器返回仍保原筛选 | 空列表不可恢复/筛选丢失 |
| TC-U2-05 | E5 | L、page_size=10、手写顺序数组 | 页1后插入更前Run，再翻全部余页 | 原30条恰各一次，严格二元位置顺序；首屏新插入只在重载后可见 | 仅按时间游标/offset分页 |
| TC-U2-06 | E5 | 状态筛选、跨请求由writer改变匹配成员 | 翻页并重载 | 每次结果满足该事务谓词；允许成员/计数变化，仍无位置重复承诺违背 | 声称集合冻结或错误保留不匹配行 |
| TC-U2-07 | E6 | 身份/状态/时间内外列不一致、坏JSON各变体 | 无筛选及有模型/Workspace筛选 | 无筛选显示异常身份；不可判定字段不当匹配；其他合法行可见 | 信任未经校验标量或吞坏行 |
| TC-U2-08 | E7 | succeeded local+大量events+summary内容访问哨兵 | 列表读取 | 不逐行扫描Event/打开Artifact/conversation；只显示已证明列和详情入口，无resolved rate | N+1历史/内容扫描或捏造评测结论 |

### U3：事务与页面边界路径

Actor为本机开发者；writer只提供受控持久化更新。Scope：页面读取编排+真实WAL DB/文件；Source：D13，TA-02/07/22，A§5.2。

| Path | 分支 / 条件 | 结束与后置保证 |
| --- | --- | --- |
| U3-P1 | G-DB固定F0后writer提交F1 | 首屏F0完整一致；重载才F1 |
| U3-P2 | F0首屏后提交F1，再事件分页 | 时间线≤240，概览仍F0 |
| U3-P3 | F0来源链接后提交F1 | 原边界来源不越界，不以F1状态覆盖 |
| U3-P4 | 主页面详情→Workspace | Workspace新边界；manifest另有读取时间 |
| U3-P5 | Workspace终态后文件晚发布 | 手动刷新可发现，旧文件区域边界不改变Run |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U3-01 | P1 | F0，G-DB握手；writer提交F1后释放首屏读取 | Run/usage/摘要/上界/version/事件全部F0；第二次整页读全部F1；无F0/F1拼接；记录真实语句/提交时间线 | 多连接或无事务拼首屏 |
| TC-U3-02 | P2 | F0首屏；提交F1；使用旧Event游标翻页并加载scope | 不含E241，概览与scope终态分类沿用F0；整页刷新后才按F1 | 分页重读新Run改旧scope |
| TC-U3-03 | P3 | F0来源边界；请求E240、E241、S(2,200)、S(3,241) | 合法来源可见；超sequence/version拒绝且无正文；另各造仅version越界/仅sequence越界，证明两项都校验；错Run同样拒绝 | 仅检查Snapshot version或Event ID |
| TC-U3-04 | P4 | 详情F0后提交F1，切Workspace | 新页Run/Evaluation引用为F1；manifest_read_at独立；metadata翻页不重新换Run概览 | 跨主页面复用陈旧边界 |
| TC-U3-05 | P5 | 终态Workspace无Artifact，之后发布合法manifest/content | 首次标当前未发现；手动刷新出现；已打开日志保旧内容/read_at，只有显式重载更新并重新hash | 永久缓存缺失或刷新暗换预览 |

越界请求必须满足A§6.3的422；真实不存在的ID/版本为404。若同时不存在且越界，不以此组合验证优先级。

### U4：游标和HTTP参数分区

Scope：游标契约+实际HTTP/SQL/文件backstop；Source：D13、D15，TA-29，A§4/6.3/12。

| Partition | 等价规则 / 代表 | 期望 |
| --- | --- | --- |
| U4-E1 | 合法签名、用途、Run、规范化筛选及范围 | 只读取绑定边界与位置 |
| U4-E2 | 编码/JSON/版本格式不合法 | 422 |
| U4-E3 | payload或signature被篡改 | 422 |
| U4-E4 | 签名合法但用途/Run/筛选不匹配 | 422 |
| U4-E5 | 签名合法但位置/上下界非法或超已提交上界 | 422 |
| U4-E6 | 明确过期、重启换密钥 | 422+重载入口 |
| U4-E7 | Artifact额外绑定不匹配 | 422，不读另一Artifact或新视图 |
| U4-E8 | 未知参数/排序/事件值、非法UUID、负offset | 422，安全错误不回显原始input/ctx |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U4-01 | E1 | 每个用途一份合法token，另输入相同集合的状态筛选顺序变体 | 合法读取范围/完整位置正确，规范化等价筛选可继续；payload解码不含原始task搜索词/凭证/宿主路径 | 漏位置/上界或原始筛选词入token |
| TC-U4-02 | E2 | invalid-base64、非JSON、未知version各自独立 | 422有界说明，无原始参数值 | 任意decode异常变500/直接透传 |
| TC-U4-03 | E3 | 修改payload一个字段不重签；另只改signature | 均422，返回无越界内容 | 跳过签名校验 |
| TC-U4-04 | E4 | 各用途交叉互换；错Run；实际不同规范化筛选，token签名本身合法 | 分别422；重新加载入口可用 | 把签名当授权或不绑定用途 |
| TC-U4-05 | E5 | 用测试密钥独立签署语义错token，分别倒置范围/未来上界/非法位置 | 422；合法签名不绕过语义校验；RO | 只验HMAC不验内容 |
| TC-U4-06 | E6 | 签发后1799秒/1801秒，另真实服务重启 | 前者可用、后者422，重启后旧token422；恰1800秒按Q-1暂不判 | 忽略时间或使用持久化固定密钥 |
| TC-U4-07 | E7 | Artifact ID/hash/redaction_version/脱敏offset逐一不匹配 | 422且无旧游标读新内容；不混用原始offset | token未绑定脱敏视图 |
| TC-U4-08 | E8 | 每类只一个错误参数，其他合法 | 422、无原始input/ctx/秘密；未知字段不静默忽略 | 宽松过滤/排序或验证错误泄露 |
| TC-U4-09 | E1/E3 | 手工固定JSON bytes/key，以标准库独立计算HMAC-SHA256已知向量 | 生成结果与向量一致、合法可验、改字节拒绝；实现复核使用constant-time比较 | 两端共享错误被round-trip掩盖 |

未枚举全部编码字符串，以EP代表和HMAC向量替代；不从计时侧信道测试宣称constant-time，需复核所用比较API。非法UUID只选择明确非UUID值，不发明短ID支持契约。

### U5：Event失败筛选与工具关联

Scope：真实Event筛选SQL+纯投影；Source：D03–D05，TA-07/08，A§6.2/7.1。

下表是扩展入口决策表，每行一条规则，事件类型与字段决定是否入“仅失败”。不展开类型×不适用字段的无意义笛卡尔积；未知组合不推断失败。

| Rule | 类型 / 条件 | 入失败集 | 展示约束 |
| --- | --- | --- | --- |
| U5-R1 | ModelResponded.error_code非空 | 是 | 模型错误 |
| U5-R2 | ToolEnded/ToolExecutionEnded.status=error/timeout/denied | 是 | denied为拒绝，不能暗示已执行 |
| U5-R3 | StepCompleted.failed=true | 是 | Step失败事实 |
| U5-R4 | EvaluationCompleted.passed=false | 是 | 评测失败事实 |
| U5-R5 | RunFinished.status=failed | 是 | Runtime失败事实 |
| U5-R6 | CompactionFailed | 是 | 压缩失败事实 |
| U5-R7 | ApprovalResolved.outcome=rejected/expired/invalidated | 是 | 原审批结果 |
| U5-R8 | ApprovalInvalidated | 是 | 不作approval_wait结束 |
| U5-R9 | 上述已知类型对应正常值 | 否 | 正常事实仍在无筛选时间线 |
| U5-R10 | PolicyDecided.effect=deny | 否 | 策略事实，非单独执行失败 |
| U5-R11 | 未知类型/未知状态 | 否 | envelope可见，未知不当失败 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U5-01 | R1～R8 | 每条规则独立seed并查询失败筛选；R2展开2事件类型×3状态，R7展开3outcome | 返回精确ID并保持sequence，状态解释正确 | 漏失败谓词或deny声称执行 |
| TC-U5-02 | R9～R11 | 正常值分别用无error_code、success、failed=false、passed=true、succeeded、approved；另policy deny/未知payload | 均不入失败集，无筛选仍可浏览；未知不强解码 | 把所有非success算失败 |
| TC-U5-03 | SQL全局筛选 | 首100条不匹配，E201/220匹配type/Step/tool_name；另外零匹配 | 查询能取到远处匹配；零匹配无继续游标 | 先limit再Python筛选/空页扫描循环 |
| TC-U5-04 | 坏匹配行推进 | SQL字段满足失败谓词但codec其余部分坏，后面还有合法匹配 | 坏行ID/sequence/type问题保留，下一页不重复；其他行出现 | 解码失败吞掉并重读同位置 |
| TC-U5-05 | 顺序/定位 | 时间戳逆序、event_id目标不符合当前筛选 | 按sequence升序；按ID定位绕过筛选且最多前后20，各来源受旧边界约束 | 按timestamp排序或来源套筛选 |
| TC-U5-06 | 关联键 | R Step1/Step2共用call_id，R2也共用 | 每个结果仅归属自己的Run/Step/call；缺参数显示未记录，不反推conversation | 仅call ID连接结果 |
| TC-U5-07 | 冲突 | call_index/span/tool_name分别与调用不符；另缺起止/重复结果 | 问题明确，不任选一条；无假完整调用 | 以最后结果覆盖冲突 |
| TC-U5-08 | 独立完整性 | success且truncated，shell exit0；Step只加载后半 | success和输出不完整同时显示；exit0仅命令状态；Step标部分加载 | 截断改失败、exit0改测试通过 |

### U6：Span决策表

Scope：SpanView纯投影+真实按scope补查；Source：D05，TA-09，A§7.2。

条件包括起止计数、parent/层级、时间、加载状态、边界终态。直接使用互斥的规范样本行；任意多个异常同时存在的组合不穷举，也不擅定问题优先级。

| Rule | 单一条件 / 其他合法 | 结果 |
| --- | --- | --- |
| U6-R1 | 唯一起止、合法parent与时间 | 真实记录间隔 |
| U6-R2 | 有开始无结束，边界非终态 | open、存活未知，无伪duration |
| U6-R3 | 有开始无结束，边界终态 | incomplete，无伪duration |
| U6-R4 | 只有结束 | 缺开始问题，保留事件 |
| U6-R5 | 重复开始或结束 | duplicate，计数不能被limit隐藏 |
| U6-R6 | 应读范围已完成但父节点不存在 | missing_parent |
| U6-R7 | parent不一致或违反既有类别约束 | invalid_hierarchy/归属问题 |
| U6-R8 | 唯一起止但结束早于开始 | invalid_time，不改原时间 |
| U6-R9 | 父链/scope未加载完 | not_loaded，不当missing_parent |
| U6-R10 | 根Span缺失 | 只显示Event时间线 |
| U6-R11 | 同边界RunFinished与Run状态矛盾 | 证据冲突 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U6-01 | R1 | 7类已知起止配对分别构造；开始t=0、结束t=2秒 | 每类间隔2秒、父关系正确，标记录间隔；不称Provider精确延迟 | 漏类别配对/误导延迟 |
| TC-U6-02 | R2/R3 | 同一未结束scope分别用非终态/终态边界；后续DB状态相反 | 仅按所用边界判open或incomplete，不用Run结束时间补闭合 | 分页用最新状态或伪造结束 |
| TC-U6-03 | R4/R5 | 结束独有、重复开始、重复结束分别构造 | 保留所有Event及对应问题，不算合法duration | 选择一个起止掩盖重复 |
| TC-U6-04 | R6/R7/R8 | 真缺父、错parent、非法类别、倒时各自测试 | 问题类型正确且其他事件可读 | 异常统一吞成空Span |
| TC-U6-05 | R9 | U10父链/scope超限样本，仅在实际遍历可达时执行该限额断言 | 标未完成加载，不把未读部分断言为历史缺父；已读非法层级可单独提示；不可达按Q-9记录 | 限额误作历史损坏 |
| TC-U6-06 | R10/R11 | 根缺失/边界矛盾分别测试 | 根缺仍Event可读；矛盾不改Run状态且提示冲突 | 为构造RunTrace修补事实 |
| TC-U6-07 | R1分页不变量 | 起点在前页或被当前筛选排除，结束在当前页；另重复起点在第3页 | 补查仍合法配对或发现重复，筛选不改事实；只查相关scope/父链 | 仅当前页配对或limit隐藏重复 |
| TC-U6-08 | approval边界 | ApprovalRequested、ApprovalResolved、之后ApprovalInvalidated；另只有Requested+Invalidated | 前者等待在Resolved结束；后者不能由Invalidated闭合；压缩不创建新SpanCategory | 错配审批结束或新增压缩Span |

### U7：Task Memory与来源

Scope：真实summary契约的纯投影+精确来源SQL；Source：D06、D11，TA-10～12，A§8。

独立条件是摘要结构/哈希/版本关系、来源归属/范围、来源内容、读取是否完成。全组合至少2^4=16且还含多种故障原因；为保留不同结果，不将所有失败折成一列，采用下列具名规则及单故障反证。

| Rule | 条件 | 输出 |
| --- | --- | --- |
| U7-R1 | 无持久化压缩 | 尚未生成持久化Task Memory，无模型调用 |
| U7-R2 | 模板/summary解析/hash或版本关系不合法 | 证据不一致；可安全解析内容可脱敏查看 |
| U7-R3 | 来源确认缺失/范围不合法/断链 | source_missing/范围等问题，不能声称证据成立 |
| U7-R4 | 来源存在但excerpt/claim/field_path不支持 | 证据不一致 |
| U7-R5 | 来源未加载或读取达限 | 未完成校验，不能判通过或损坏 |
| U7-R6 | 结构、范围、内容均合法且已加载 | 已加载来源校验可成立，精确链接只给VerifiedEvidence |
| U7-R7 | attempt同时出现在Recorded和结束列表 | Recorded优先，列表只补缺，不重复计费 |
| U7-R8 | 尝试冲突或组起止不明确 | 保留问题/未关联，不猜组 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U7-01 | R1 | Given：无压缩，有普通assistant JSON；When：打开详情 | 无摘要提示；不把任意JSON当Task Memory，不发模型请求 | regex搜索JSON补造摘要 |
| TC-U7-02 | R2 | Given：M_good逐一改变固定前缀/template_version/消息类型/summary字段；When：读取当前摘要 | 各自证据不一致；不接受非契约文本 | 宽松解析未知模板 |
| TC-U7-03 | R2 | Given：登记hash分别改成JSON后缀hash、脱敏后hash、错误hash；When：读取 | 均不通过；完整原文hash的合法对照通过 | hash对象选错 |
| TC-U7-04 | R2 | Given：分别错current/previous version、新旧Snapshot ID、event_sequence、covered_messages/steps、retained_first_step；When：校验 | 每一反例被对应规则发现，不改数据 | 只验summary JSON不验上下文关系 |
| TC-U7-05 | R3 | Given：来源分别错Run/sequence范围/Snapshot版本/消息范围/祖先压缩范围/真不存在/断链；When：读取来源 | 已确定问题可见，无假精确链接或成功标记；仍能看其他合法来源 | 只验证链接存在 |
| TC-U7-06 | R4 | Given：source叶文`verified ok`但excerpt另值；claim≠excerpt；field_path不存在；指到值4但claim3分别测试；When：校验 | 内容不支持均证据不一致；真实值3/claim3对照成立 | 漏内容比较 |
| TC-U7-07 | R5 | Given：合法来源尚未读、单项超大小、版本链超限分别测试；When：查看 | 未完成校验，与真缺失明确区分 | not_loaded当source_missing |
| TC-U7-08 | R6 | Given：M_good当前/历史值不同，含VerifiedEvidence及普通字段/uncertainties；When：打开当前及指定new Snapshot历史 | 当前/历史各自正确；VerifiedEvidence指准确Event/Snapshot，其他字段只指承载Snapshot；保留不确定项 | 所有字段伪造逐项来源/历史读当前 |
| TC-U7-09 | R7 | Given：同组attempt1有Recorded成本0.10及结束重复；attempt2只在结束有0.20；When：展示尝试与费用 | 组内总0.30，不是0.40；Run已包含贡献不再次增加 | 双计尝试或双计Run费用 |
| TC-U7-10 | R8 | Given：同Run scope两次Requested(attempt1)分别sequence20/80，均有attempt2；另起止缺失/重复/金额冲突；When：分组 | 两组按首次sequence区分；异常记录不串组、不任选成本；问题保留 | 只按span+attempt join |

### U8：Run、费用和时间

Scope：纯展示投影+一次实际模板/RunRecord集成；Source：D02、D11–D12，TA-04～06，A§7.3。

| Partition | 规则 / 代表 | 预期 |
| --- | --- | --- |
| U8-E1 | 终态/非终态事实 | 既有RunStatus各值，非终态最后记录状态 |
| U8-E2 | 验证类型未记录 | Local/unknown不能推断独立通过 |
| U8-E3 | 已记录usage/Decimal | 持久化值、无重复子集/摘要 |
| U8-E4 | 缺数据/未配置上限 | 不变零、不画百分比 |
| U8-E5 | 合法/负/非终态时间 | wall与active分开，倒时不可算 |
| U8-E6 | 上限达限/超额 | 仅真实配置项百分比，不钳制超额实际值 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U8-01 | E1 | Given：各既有状态，candidate与final文本不同，error含长文本；When：渲染 | status/reason/error code对应原值，candidate/final独立折叠、有界错误；非终态即使陈旧也不擅改failed/interrupted | 混合输出/凭更新时间判死 |
| TC-U8-02 | E2 | Given：Local succeeded、unknown succeeded、task似I1；When：浏览列表/详情/Workspace | 默认装配说明与该Run类型未记录分开；无SWE-bench独立卡/无instance猜测；Runtime状态仍succeeded | Workspace kind推断evaluator |
| TC-U8-03 | E3 | Given：U_good；When：读取 | token总120不是165，cost0.30不是0.40；请求/工具数取RunUsage，不从当前Event页估算；标题模型成本USD及完整性未证明 | 累加subset/摘要或页内估总 |
| TC-U8-04 | E3 | Given：持久化费用不变，外部当前价格任意改变/定价文件不可读；priced_separately真假各例；When：读取 | 不读当前价格重算，cost不变；缓存分项按持久化标志；精细Decimal值在投影保真 | float/当前价格重算 |
| TC-U8-05 | E4 | Given：无时间/token/cost硬上限，配置只含已有Step限制；部分历史字段缺失；When：渲染 | 未配置上限无百分比，不从TerminationReason猜上限；缺字段非零/成功 | 编造预算和零值 |
| TC-U8-06 | E5 | Given：U_good、倒时、非终态read_at比start晚20秒分别测试；When：展示时间 | wall20秒/active12秒分开；负值不可算；非终态是截至read_at经过时间并提示最后状态 | wall当活动时长或进程存活 |
| TC-U8-07 | E6 | Given：steps_started=9/10/11，max_steps=10独立运行；连续失败项同法；When：展示 | 9/10未超；10/10可100%；11/10保留11及超额标记；不钳成10 | off-by-one/钳制actual |
| TC-U8-08 | E3/E4 | Given：context_window/output reserve来自definition；CompactionCompleted估计时点早于当前；When：展示 | 容量/预留与累计Token分开，估计有来源时点，不称当前上下文占用率 | 累计token除容量冒充占用 |

### U9：Artifact和内容类别

Scope：受限Reader+真实manifest/content+HTTP；Source：D07–D09、D14–D15，TA-13～18，A§9。

| Partition | 等价规则 / 代表 | 结果 |
| --- | --- | --- |
| U9-E1 | 已登记、身份/大小/hash均合法的已知文本 | 校验后脱敏预览 |
| U9-E2 | 缺manifest | 404 |
| U9-E3 | manifest语法/版本/ID/Run不合法 | 单项问题；身份等登记冲突预览409，不阻断其他项 |
| U9-E4 | 内容大小/hash/引用不符或已登记内容不可用 | 单项证据不可用；冲突预览409 |
| U9-E5 | 非本地/authority非空/布局不符/逃逸路径 | 拒绝读取；非法登记预览409 |
| U9-E6 | 根以下任何symlink、非普通末级/非目录中间节点 | 单项不可用，不能读取/阻塞；真实竞争见U14 |
| U9-E7 | 未知/二进制/非法UTF-8 | 200元信息，不提供正文 |
| U9-E8 | 合法未知JSON形状 | 脱敏预览，不参与通过推断 |
| U9-E9 | 语法错/重复键/深度超限JSON | 200元信息/问题，不原文回退 |
| U9-E10 | diff格式类别 | 支持结构解释；不支持有界原文，不当空patch |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U9-01 | E1 | Given：真Store产生文本/JSON/diff各一项；When：metadata页后按ID预览 | metadata不批量校验内容，预览才按允许路径校验；同Run条目及来源正确；无原始下载 | 直接用list_for_run或任意URI读取 |
| TC-U9-02 | E2/E3/E4 | Given：逐项单故障：缺manifest、坏JSON、新schema、文件名ID错、目录Run错、改hash、缺内容、登记大小与实际不符；When：访问列表/目标预览/另一合法项 | 各类别正确降级/拒绝；合法项仍可读；冲突不冒充未登记404 | 一个坏项阻断全Workspace |
| TC-U9-03 | E5 | Given：file://host/path、https URI、根外路径、同根但非content/hash布局各变体；metadata.files指向根外哨兵；When：预览 | 不下载、不读根外/metadata路径，违规项拒绝；安全文件名/相对路径可展示 | 仅resolve检查或信任URI |
| TC-U9-04 | E6 | Given：manifest/content末级及中间目录各造指向根内/根外symlink；末级目录、中间普通文件；When：请求 | 每种拒绝；其他正常项可见；不因指向根内放行 | 只拒越界symlink |
| TC-U9-05 | E7/E8/E9 | Given：各类型独立样本，未知JSON含secret；重复键分别顶层/嵌套；When：预览 | 未知合法JSON脱敏可读但不判成功；其他类别无正文，尤其坏JSON不回退原文 | 宽松last-key-wins/绕过结构化脱敏 |
| TC-U9-06 | E1分页 | Given：先构建独立期望脱敏文本，秘密跨256KiB或2000行切点，含中文emoji/超长单行；When：逐段请求直到结束 | 拼接等于固定脱敏视图，无秘密；offset严格前进且为脱敏后UTF-8字节、字符不切坏、行内续段标记 | 先分页后脱敏/原文offset |
| TC-U9-07 | E1再验证 | Given：第一次预览后替换实际内容，不更新旧manifest；When：重载或下一段请求 | 重新hash而拒绝不匹配，不从跨请求缓存继续旧正文 | 永久内容cache |
| TC-U9-08 | E10 | Given：新增/删除/rename/带空格与引用路径/binary/不支持格式各样本；When：浏览diff | 合法各有结构/标记；binary说明；不支持保留有界原文；不运行Git/应用patch | 解析失败当空补丁/路径交shell |
| TC-U9-09 | E3逐项发现 | Given：>100项，首批坏manifest，页外有Event明确引用的合法config/patch；When：metadata翻页并展示评测 | UUID顺序无重复；只首批常规metadata但引用可精确补查；不读所有日志内容 | 引用只能在当前页找 |
| TC-U9-10 | E8；F4字符串括号正例 | Given：深度仅为一个顶层对象，message值为65个`[`接65个`]`，另有secret哨兵；When：按ASM-6登记并预览 | 200且正文可见，message内容保留，无depth超限问题，secret遮盖；不能只验200 | 字符串括号错误入深度栈 |
| TC-U9-11 | E8；F4转义正例 | Given：单层对象message叶文本包含一个双引号、一个反斜杠及65对方括号，以标准JSON编码产生有效转义，另有secret；When：按ASM-6预览 | 叶文本按普通文本保留、秘密遮盖、无深度/语法错误；真实结构仍浅层 | 转义引号/反斜杠导致词法状态提前出字符串 |
| TC-U9-12 | E8；F4对象键正例 | Given：单层对象的普通键包含65对花括号、引号和反斜杠，值为`visible-ok`，另有secret；When：按ASM-6预览 | 普通键值可读且secret遮盖，无depth超限问题；真正深层反例仍由B18拒正文 | 只对值处理引号而错数对象键括号 |
| TC-U9-13 | E4；F3实际超限；robustness交叉条件 | Given：M=20971520，实际普通文本文件M+1字节、登记M-1字节；Run/Artifact/URI/hash布局均合法，仅size虚报；When：按ID预览并采本请求I/O | 可以open/fstat；不得发生首次content read、mmap或内容hash输入，其他合法项可读；200/409优先级按Q-8暂DEFER，零内容读取已确定 | 只检查登记size，读完整文件后才发现大小冲突 |
| TC-U9-14 | E4；F3限额内不符对照 | Given：实际12字节、登记11字节，真实hash与路径布局一致；When：预览 | 大小冲突409，不冒充超20MiB；B10合法等大小对照仍可预览，防止拒绝所有文件假通过 | 把任何size不符当阅读超限或全部拒绝 |

E6真实FIFO/socket和TOCTOU用U14补足；本单元不因构造一个symlink就声称全部文件安全成立。

F3/F4的确认场景为TC-U9-13和TC-U9-10，邻近/正向对照为TC-U9-11/12/14与B10/B18。三个浅层JSON例各自独立执行，编码器只用于生成输入，结构深度和预期叶值由测试设计独立确定，不使用产品深度检查器生成期望。

Test level(s): Reader/HTTP集成是F3与F4的最小真实边界：实际fstat和内容read证明早拒绝，合法JSON到脱敏预览证明词法误判未发生；浏览器不重复这些分区。

### U10：边界值清单

Scope按字段使用真实SQL/Reader/HTTP/纯投影最小边界；Source：A§12及相关D/TA。每次只改变被测量，其余结构、JSON、签名、hash和身份全部合法。编码游标若无法构造合法结构达到指定长度，该长度只验证参数入口大小拒绝，不能把格式错误计作大小边界通过。

上限型分区为`[可达最小值,M]`与`[M+1,∞)`；采用3-value BVA，同时覆盖合法最大M和非法最小M+1的邻居，去重后为`M-1,M,M+1,M+2`。为检查下侧接受分支另保留`M-2`，因此统一五值表；多字节UTF-8通过ASCII padding填满准确字节，不破坏JSON。不存在的负长度不生成。

每个BV行生成`TC-U10-<BV>-m2/m1/eq/p1/p2`，对应M−2/M−1/M/M+1/M+2；这五个是独立场景。表内动作及上下侧结果就是每个场景的Given/When/Then，覆盖项为该值。分页容量超出的数据转下一页，不作为无效输入。

| BV | 对象 / Trace | M与步长 | 构造与动作 | ≤M / >M 的独立期望 |
| --- | --- | --- | --- | --- |
| B01 | state_json；TA-17 | 20971520字节；1字节 | 持久化合法正文，读取详情 | 允许按预算读取 / envelope+超限，SQL不取原文 |
| B02 | snapshot_json；TA-17 | 20971520字节；1字节 | 读取指定合法范围Snapshot | 允许按范围读取 / 问题视图无正文，不称损坏 |
| B03 | Event payload_json；TA-17 | 1048576字节；1字节 | 固定envelope合法事件 | 合法解析 / envelope保留、正文不取 |
| B04 | Event artifacts JSON；TA-17 | 1048576字节；1字节 | 扩充合法引用列表 | 合法解析 / 超限正文不取 |
| B05 | recovery_context_json；TA-05/17 | 1048576字节；1字节 | 独立字段正文，Run主state合法 | kind按真实值 / 未完成读取，不猜kind |
| B06 | 合计结构化正文；TA-17 | 67108864字节；1字节 | 每项低于单项限额，跨多项精确合计 | 只取预算内 / 超额项未完成，不先fetchall再检查 |
| B07 | 列表自由文本字段；TA-03/17 | 131072字节；1字节 | 带完整秘密末段的task | 脱敏后摘要 / 超限提示，不泄未脱敏前缀 |
| B08 | 其他展示标量；TA-17 | 4096字节；1字节 | 一个合法可变长度标量 | 有界脱敏值 / 超限提示；不影响其他字段 |
| B09 | manifest；TA-13 | 1048576字节；1字节 | 合法manifest+可扩展metadata | 允许解析 / 不解析只问题 |
| B10 | Artifact内容；TA-16/17 | 20971520字节；1字节 | 登记与实际大小一致且hash合法；虚报小size的交叉条件由TC-U9-13单独承接 | 有界hash后预览 / 200元信息、未校验、无内容读取；不以此族替代fstat分支 |
| B11 | 预览字节；TA-16 | 262144字节；1字节 | 脱敏后视图长度，行数很少 | 单段 / 分段，每段≤M且next offset正确 |
| B12 | 预览行数；TA-16 | 2000行；1行 | 每行短且字节远低B11 | 单段 / 下一段剩余行；每段≤2000 |
| B13 | metadata数量；TA-13 | 100项；1项 | 合法UUID清单 | 一页 / 下一页余项，无重复遗漏 |
| B14 | Event请求页大小；TA-07 | 200条；1条 | 参数198/199/200/201/202 | 有效大小 / 422；默认另验100 |
| B15 | Span父链；TA-09 | 64层；1层 | 原始Event envelope构造完整可定位但类别层级异常的祖先链，不构造合法RunTrace；见Q-9 | 若遍历到限额，限额外not_loaded，不能宣称missing_parent；已发现非法层级仍可报告；若提前拒绝则限额边界不可达，单独记录 |
| B16 | scope数量；TA-09 | 1000个；1个 | Reader公开scope读取边界或异常历史可达的关联集合，保持每页≤200；见Q-9 | 可达时最多加载M，剩余not_loaded；若正常路径不足以达阈值，记录不可达，不把1001事件一页作为合法请求 |
| B17 | 压缩版本链；TA-11 | 32版；1版 | 合法previous_version链 | 已读部分可校验 / 限制外未完成，允许按版本再看 |
| B18 | JSON深度；TA-18 | 64层；1层 | 最外对象算1的测试计数约定，具体与实现契约复核；字符串/键内括号与转义浅层正例由TC-U9-10～12承接 | 支持深度 / 元信息无原文；若生产深度计数不同先定规则，不能改期望凑绿 |
| B19 | 编码游标；TA-29 | 4096字节；1字节 | 合法可填充结构或独立长度入口 | 长度校验可过且再验语义 / 422不decode正文 |
| B20 | task查询；TA-03 | 200字符；1字符 | 含中文的字符长度198～202 | 合法查询 / 422；与B07字节限额分离 |

额外下限/区间和双约束边界独立列出，不复用上表M公式。

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U10-21 | Run页大小集合 | 分别不传及传10/25/50/100、11/0/101 | 默认25；四枚举有效，其余422 | 把枚举当10–100连续区间 |
| TC-U10-22 | port下/上边界 | 分别0/1/2/65534/65535/65536；仅解析合法端口避免占用系统端口 | 1～65535参数有效，0/65536拒绝；实际监听用可用非特权端口另验 | off-by-one或仅测常量 |
| TC-U10-23 | [from,to) | t0=00:00Z、t1=00:01Z，各边界±1ms/恰等于，各Run其余合法 | t0包含、t1排除；不同明确时区的同瞬间结果相同；from=to/from>to独立422 | to误含/from拒等/UTC编码错 |
| TC-U10-24 | 定位窗口 | 目标前后分别19/20/21条的独立输入 | 每侧最多20，加目标；边界Run首尾不补虚构事件 | 一侧off-by-one或无界定位 |
| TC-U10-25 | 脱敏offset/字符 | 合法段尾、负offset、超范围offset、中文字符中间位置分别 | 合法下一位置不丢重；负/非法范围422；不生成损坏UTF-8 | 原始offset/中间字节切断；字符中间输入具体拒绝/规范化按接口定稿 |
| TC-U10-26 | T01游标有效期 | 受控时钟expires_at−δ/expires_at/expires_at+δ | 前有效、后422；恰等于Q-1 DEFER，不隐含任选 | 过期漏验/提前过期 |
| TC-U10-27 | 64KiB读取块 | 内容65535/65536/65537字节，登记及hash合法 | 完整结果正确且单次内容read≤65536，不全量read_bytes；最末块正确 | 块边界丢字节/全量读取 |
| TC-U10-28 | 两种预览限制交叉 | A先达字节上限但未2000行；B先达2000行但未字节上限，各含UTF-8 | 每次同时满足两个上限，按先达到的限制分段 | 只执行一个限制 |

TC-U10-25的中间UTF-8偏移若允许用户直接提交，422还是安全对齐需接口澄清；生成的next offset必须始终有效，这部分oracle已确定。B18计数约定不是新增产品深度政策，实施前需明确计数点。

### U11：默认Patch选择规则

Scope：纯选择投影+真实metadata发现backstop；Source：D07，TA-13/19，A§9.1/10.1。

条件：明确一致关联`A`；扫描完整`S`；合法patch数`N=0/1/>1`；关联冲突`C`。名义组合2×2×3×2=24；不输出不可能自相矛盾组合，直接保留下列业务列。不为多个异常定义隐含优先级。

| Rule | A | S | N | C | 结果 |
| --- | --- | --- | --- | --- | --- |
| U11-R1 | 是 | 完整 | ≥1 | 否 | 默认明确引用patch，不能按UUID择新 |
| U11-R2 | 否 | 完整 | 1 | 否 | 默认唯一合法patch |
| U11-R3 | 否 | 完整 | >1 | 否 | 用户选择，无默认 |
| U11-R4 | 否 | 完整 | 0 | 否 | 当前未发现patch |
| U11-R5 | 任意 | 不完整 | 任意 | 否 | 不猜默认，提示部分发现/选择 |
| U11-R6 | 任意 | 任意 | ≥1 | 是 | 展示关联问题，用户选择 |

R5采用A§10.1“扫描不完整要求选择”的明确表述；R6仅断言冲突和无默认，不决定与部分发现并存时总标题。24格未声称穷举；保留规则列逐列验证。

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U11-01 | R1 | P与P2均合法，P2 UUID更大；Event/config/summary一致引用P | 默认P并显示明确来源，不选P2 | UUID当latest |
| TC-U11-02 | R2 | 完整发现只有P合法；有无效非patch条目 | 默认P，唯一判断有完整发现证据 | 按当前页唯一猜选 |
| TC-U11-03 | R3 | P/P2均无关联 | 要求选择，无预选；每项可单独浏览 | 随机/首个/最后patch默认 |
| TC-U11-04 | R4 | 完整扫描无patch | 显示未发现，不称空patch或验证通过 | 无Artifact当无修改成功 |
| TC-U11-05 | R5 | 目录扫描预算到期；当前看到一个P，另变体关联P但发现未完成 | 部分发现，不默认；不得声称唯一 | 部分扫描冒充完整 |
| TC-U11-06 | R6 | A1指P、A2指P2；另同attempt config/summary错关联 | 关联问题并要求选择；不改Run/Evaluation原事实 | 多attempt合成同一最新候选 |
| TC-U11-07 | 文件清单关系 | metadata.files与完整diff解析不同；另解析中途达预算 | 完整解析才显示不一致；未完成只说解析不完整，两份清单各自可见 | 不完整推导误报文件冲突 |

### U12：独立评测链决策表

Scope：纯evaluation投影、真实JSON/manifest/DB引用集成；Source：D09，TA-18/20/21，A§10.2。

条件：Run succeeded `R`，passed=true且引用明确的Event `E`，config/summary身份/最终状态 `C`，report支持且语义通过 `H`，patch身份/hash/submission及内容验证 `P`，必要项齐全 `M`。名义2^6=64组合，采用全部合法基线+每个必要条件单独破坏；不称穷举64组合。未知形状与缺失、明确冲突分别保留。

| Rule | 条件 | 动作 / 问题 |
| --- | --- | --- |
| U12-R1 | R/E/C/H/P/M全成立 | 已记录独立评测通过；目标全集无法复核说明必显 |
| U12-R2 | R/E有明确否定且其他证据声称通过 | 不显示独立通过，保留Runtime/Evaluation并提示冲突 |
| U12-R3 | C中身份/最终状态或引用明确矛盾 | 证据冲突，不挑最新summary |
| U12-R4 | H已知但resolved/patch/apply/infra/tests规则不成立 | 不能独立通过，保留已记录失败/问题 |
| U12-R5 | H或C形状/版本未知但合法JSON | 仅脱敏预览，不推断结论 |
| U12-R6 | P身份/hash/submission不一致或内容验证失败 | 证据冲突/不可用，不通过 |
| U12-R7 | 必要证据缺失，其他无冲突 | 证据不完整，不通过 |
| U12-R8 | 非必要日志缺失，R1链仍完整 | 可保留已记录通过，单独提示缺日志 |
| U12-R9 | 明确冲突与缺失同时存在 | 不通过、两项事实可见；总标题Q-3 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U12-01 | R1 | Given：C_good，报告自身无Run/attempt字段；When：浏览Workspace | 已记录独立通过及目标清单未持久化/无法复核完整覆盖；测试名称为报告记录的测试；不要求未生产字段/目标全集 | 自造目标清单或过严要求report身份字段 |
| TC-U12-02 | R2 | Given：单独Run≠succeeded、单独passed=false、单独Event引用不符；When：浏览 | 每个反例不通过，原Runtime状态/Evaluation值/文件可用性独立保留 | 只看summary verdict |
| TC-U12-03 | R3 | Given：config/summary内容与manifest逐一错Run/attempt/instance；summary为collecting/cancelled/failed；同attempt多个summary但Event引用较小UUID的final；When：浏览 | 错身份/非final阻止通过；正常多summary只用明确引用的final，其余解释差异 | 文件名当最新/跨attempt拼链 |
| TC-U12-04 | R4 | Given：report分别resolved=false、空patch、apply=false、infra=true、缺tests_status、组内重复、同组成功失败交集、resolved含失败；When：浏览 | 每例不能通过；保留结构问题与已有结论，不靠日志PASS反转 | 忽略报告必要规则 |
| TC-U12-05 | R5 | Given：harness未知、config形状未知、summary形状未知各例，合法JSON含秘密；When：浏览 | 仅脱敏预览，不能套5.0.2规则；不发明config/summary schema_version | 宽松未知版本判成功 |
| TC-U12-06 | R6 | Given：patch ID/hash/submission与config/summary/Event各处单一不一致，或实际bytes篡改；When：浏览 | 不通过并显示正确关联/校验问题；不能另选P2补齐 | 校验manifest不校验内容或重选patch |
| TC-U12-07 | R7 | Given：分别去除Event/config/summary/report/patch及必要身份字段；When：浏览 | 证据不完整或明确字段问题，无独立通过；其他材料可看 | 缺证据默认真 |
| TC-U12-08 | R8 | Given：通过链完整，仅stdout/stderr/test output/instance log分别缺失；When：浏览 | 保留已记录通过与目标覆盖说明，缺失按项提示 | 缺任意日志都推翻verdict |
| TC-U12-09 | R9；robustness | Given：config错attempt且report缺失；When：浏览 | 不通过，冲突与缺失都可见；总标题暂DEFER | 一种问题掩盖另一种事实 |
| TC-U12-10 | report身份/组规则 | Given：唯一instance key错I2/额外顶层instance；组内重复与跨组同名独立样本；When：浏览 | 错key/额外实例阻止通过；每组无重复/无自身交集时跨组同名不因跨组重名拒绝 | 忘唯一实例/把组间同名当组内冲突 |
| TC-U12-11 | 无补造/执行 | Given：合法链，无task.json/宿主准备目录；shell exit0、日志写ALL PASSED但缺必要report另例；When：浏览 | 合法链仍按已记录通过口径；不读宿主目标或运行评测，exit0/日志不能代替链 | 根据日志或自造目标判成功 |
| TC-U12-12 | attempt分组 | Given：manifest分别有合法A1/A2、缺attempt、非法attempt，内容合规；When：浏览 | 两合法组分开；缺/坏身份为未关联证据，不偷偷分进当前组 | 按文件名前缀或默认attempt归组 |

F1补充技术：字段等价分区，不把非bool混进resolved=false语义规则。严格类型依据当前InstanceReport/TestGroup契约；只取消费者使用的字段。有效分区E1由C_good同时覆盖所有合法布尔/列表路径，E6单独验证可选字段缺省；负向每次仅一个路径变为一个无效代表，不采用pairwise缩减字段分支。

| Input | Partition | Validity | 等价规则 | 代表 / 对照 |
| --- | --- | --- | --- | --- |
| 五个报告布尔字段 | U12-E1 | 有效 | 原生JSON bool按实际语义解释，无强制转换 | C_good的true/false；TC-U12-01与15 |
| 同上 | U12-E2 | 无效 | 字符串不是bool，即使词义像bool | 每字段把原值换为同义`"true"`或`"false"` |
| 同上 | U12-E3 | 无效 | 数字0/1不是bool | true字段换1、false字段换0 |
| 同上 | U12-E4 | 无效 | null不能替代字段类型，infra_failure缺省不等于null | 每字段换null |
| 四个必需bool字段 | U12-E5 | 无效 | required字段不能省略 | resolved/patch_exists/patch_is_None/patch_successfully_applied分别缺失 |
| infra_failure | U12-E6 | 有效 | 唯一此bool字段可缺省为false | 仅去除infra_failure，其他同C_good |
| tests_status/组/列表容器 | U12-E7 | 无效 | 已知字段必须匹配对象或list[str]容器形状，不是未知业务版本 | tests_status=[]；组=[]；success字符串；failure对象，逐路径独立 |
| 已知组及success/failure | U12-E8 | 无效 | 组/列表字段是必需结构，不能省略 | FAIL_TO_PASS/PASS_TO_PASS组及各自success/failure分别缺失 |
| success/failure列表元素 | U12-E9 | 无效 | list[str]不把数字元素强制转字符串 | 各组success=[1]、failure=[1]，逐路径独立 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U12-13 | E2/E3/E4；F1确认 | 在C_good的resolved、patch_exists、patch_is_None、patch_successfully_applied、infra_failure中每次只选一个字段，分别换同义字符串、0/1或null；按ASM-6重建合法完整性后经消费者入口读取 | 每个变体均不显示独立通过且有已知字段结构问题；不是hash/归属失败；合法bool对照仍通过；必要时安全脱敏预览 | StrictBool被宽松bool转换替代 |
| TC-U12-14 | E5；F1必需字段 | 每次删除四必需bool中的一个，其他链完整且hash/ref正确；读取Workspace | 结构/必要字段问题，不能把缺失补成成功默认值；其他证据可读 | 必需bool使用宽松默认值 |
| TC-U12-15 | E1/E6；F1有效对照 | C_good原生bool且infra_failure=false为一例；仅省略infra_failure为另一例，分别重新登记后读取 | 两例均保留已记录独立评测通过及完整覆盖不可复核说明；不发明必需infra字段 | 拒绝全部报告或把可选字段误当必需 |
| TC-U12-16 | E7/E8/E9；F1形状 | 按分区表逐路径替换容器/删除字段/植入数字元素；仅一处非法，其余C_good，ASM-6保证合法bytes/hash/ref；读取adapter及Workspace | 有可定位已知结构问题，不通过；failure=[1]也必须是字段类型问题，不能只以“有失败测试”通过负向断言；不把可读未知形状当成功；C_good形状对照仍可读 | 组或列表强制转换、忽略缺组/缺列表、数字元素转字符串 |

TC-U12-13的完整variant为`/<field>-string`、`/<field>-number`、`/<field>-null`，五字段共15个；TC-U12-14有4个missing-field；TC-U12-15有explicit-false/absent两个有效变体。TC-U12-16以`/<json-path>-<wrong-container/missing/nonstring>`命名，表内每个具体路径分别运行，分区计数不等于执行次数。确认场景首先为resolved-string；已有false语义例只作其相邻语义对照，不能代替严格类型证据。

Test level(s): adapter组件契约是字段类型错误的最低可见范围；每个类型/形状反例再经真实JSON/manifest/引用的Workspace读取验证消费者实际接线。既有生产者parser测试仅提供契约依据，不替代消费入口结果。

### U13：内容安全、同源和错误分区

Scope：真实HTTP/Jinja/日志与少量浏览器执行验证；Source：D14–D15，TA-16/18/23/25，A§11–12。

| Partition | 等价规则 / 代表 | 结果 |
| --- | --- | --- |
| U13-E1 | 精确合法Host配置端口、无Origin直接导航 | 允许 |
| U13-E2 | Host尾缀/错端口/外部Host | 403 |
| U13-E3 | 出现Origin但不是当前服务origin | 403 |
| U13-E4 | Fetch Metadata明确cross-site | 403 |
| U13-E5 | HTML/script/Markdown内嵌HTML/URI脚本 | 普通文本/受控链接，不执行 |
| U13-E6 | 结构化敏感键/已知文本秘密 | 遮盖值，未知JSON形状也适用 |
| U13-E7 | 统计token明确白名单路径 | 保留统计；同名未知路径不白名单 |
| U13-E8 | HTTP/codec/Pydantic错误、query及服务诊断 | 有界脱敏错误与错误ID，无input/ctx/path/searchword泄露 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U13-01 | E1～E4 | Given：精确127.0.0.1/localhost配置端口为合法基线；每次改变一项Host/Origin/cross-site；When：请求完整页和HTMX片段 | 合法可读，非法403；无Origin导航不误拒绝；无CORS允许跨站 | 尾缀allowlist/只验证HTMX |
| TC-U13-02 | E5 | Given：在task/output/payload/summary/metadata/log/diff/错误放script、引号属性闭合、javascript URI、Markdown内嵌HTML各变体；When：渲染并在真实浏览器展示代表脚本例 | 文本转义、无注入DOM/脚本副作用或外部请求，metadata URI不成为不受控链接 | safe标记/Markdown直出/原URI href |
| TC-U13-03 | E6 | Given：每个展示入口独立植入结构化键、Bearer/Authorization/赋值/凭证前缀/私钥块；When：取完整页、片段、属性、错误、预览及日志 | 每个哨兵均不在输出；可读非敏感对照保留，防止全删内容假通过 | 漏入口或过度脱敏空页 |
| TC-U13-04 | E7 | Given：数字token秘密与合法RunUsage统计并存；When：渲染 | 秘密数字遮盖；白名单统计保留具体值；未知路径token键不因数字放行 | 仅按值类型判断敏感 |
| TC-U13-05 | E6分页 | Given：私钥/凭证跨摘要截短或Artifact分段边界；When：请求各段 | 所有段及省略提示均无秘密片段；对照不敏感内容存在 | 先截断再脱敏 |
| TC-U13-06 | E8 | Given：secret/search/path哨兵分别进入HTTP参数验证input/ctx、codec值、服务IO错误；When：触发对应错误，采响应和访问/诊断日志 | 返回状态正确、无原始值/宿主路径；500错误ID可关联脱敏诊断，access log无原始query | 默认Pydantic/异常回显 |
| TC-U13-07 | 安全响应契约 | Given：包含可交互页面与片段；When：获取响应/本地脚本并交互 | CSP各self与object/frame/base限制、nosniff/no-referrer/no-store生效；无eval型HTMX/内联事件依赖；iframe嵌入被禁止 | CSP漏配或JS依赖eval |
| TC-U13-08 | HTTP错误类别 | Given：404实体缺失、409内容冲突、200未知payload/超限、503资源、500物理坏DB分别用真实故障样本；When：GET与HEAD对应入口 | 各状态符合A§12，有导航/重试提示/错误ID适用项，无空200成功或body；RO | 捕获所有异常统一成功/404 |

TC-U13-08中的物理坏DB用已启动服务面对后续真实损坏/替换库的读取路径；若启动时已坏，仅证明启动失败，不能计500页面证据。损坏操作只针对本测试临时库。

### U14：真实文件竞争、工作任务与资源路径

Scope：真实OS/SQLite/工作线程/已组装服务；Source：D08、D15–D16，TA-01/15/17/24/25，A§9.2/12。

| Path | 触发条件 | 后置保证 |
| --- | --- | --- |
| U14-P1 | manifest/content末级非普通文件 | 不阻塞、不读正文，其他请求可用 |
| U14-P2 | G-FD后替换内容路径 | 安全已开内容或拒绝，绝不读取替代目标 |
| U14-P3 | 真实SQLite锁竞争 | busy策略后503，无无限重试，解锁可再读 |
| U14-P4 | 真实SQL/目录/解析循环超过预算 | 主动截止并最终工作完成，不只HTTP timeout |
| U14-P5 | 两个重任务仍活动，第三个请求 | 快速503，轻请求可调度 |
| U14-P6 | P5期间取消一个HTTP协程 | 工作未完成不释放名额；完成后允许新重任务 |
| U14-P7 | 资源门禁/超限前后 | 无无界读取、RO、资源回收 |
| U14-P8 | 合法请求实际进入重解码/投影/hash/完整脱敏阶段 | 每个实际阶段不在ASGI线程，且属于同一受限重任务名额区间 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U14-01 | P1 | Given：manifest/content分别是真FIFO无writer、Unix socket、目录；设备文件只在可安全准备的真实环境；When：监督进程请求并同时读合法轻页面 | 单项不可用，FIFO open不等待writer；轻页能完成；无监督kill才结束的假通过 | 先阻塞open后fstat |
| TC-U14-02 | P2 | Given：合法P安全打开达G-FD，外部rename原路径并换成根外symlink/另一普通内容；When：释放并收集FD/hash/响应 | 返回完整已打开且已验证P或明确拒绝；替代内容哨兵绝不出现；RO对受控替换前后按测试外部写入范围核对 | resolve后重新open/另FD read |
| TC-U14-03 | P2目录变体 | Given：已开根/中间目录后外部替换目录节点，替代目标含哨兵；When：按协议继续读取 | 相对已开安全目录的内容或拒绝，无跟随替代节点；协议不可用则DEFER | 用字符串绝对路径重开 |
| TC-U14-04 | P3 | Given：真实锁能够阻塞reader的SQLite模式；单独连接持有实际冲突锁；When：发送读取，按monotonic记录；释放锁再读 | 1秒busy策略后503、无无限重试；释放后200；记录ε；不能用WAL普通writer锁假装read阻塞 | 仅mock OperationalError或无限重试 |
| TC-U14-05 | P4 SQL；F5确认 | Given：G-SQL仅确认真实目标SQL已开始且活动，外部已记t0与5秒预算；When：在查询结束前推进到t0+5秒+δ并放行SQL运算，收集中断/任务结果，再读轻页 | 503、真实SQL主动中断且实际工作完成，之后轻查询可用；不以前置handler触发为条件。已确认目标跨deadline仍执行到监督回收为产品FAIL；设施缺失才DEFER | 未安装progress handler被误记未命中，或仅外层async timeout |
| TC-U14-06 | P4目录/文件/解析 | Given：目录循环、64KiB文件循环、JSON深度预检、diff解析各通过真实边界受控推进时钟跨5秒；When：继续一次截止检查 | 各循环主动退出；可保留结果时有未完成标记，不能继续无界读取/计算或标完整；RO。局部超限的顶层HTTP状态按Q-10暂DEFER，不以多个状态任取作为通过 | 只在SQL实现预算或解析不检查 |
| TC-U14-07 | P5 | Given：两个真实重任务达G-WORK并暂停，分别覆盖Run/Snapshot正文和Artifact读取；When：第三个重请求及health轻请求 | 第三个503可重试且不开始内容工作；暂停期间轻页可调度。该结果只证明门禁/等待接线，实际重计算线程归属由TC-U14-11证明 | 并发门禁遗漏或等待名额阻塞轻请求 |
| TC-U14-08 | P6 | Given：P5条件，取消其中HTTP请求但不释放底层工作；When：再发第三重请求，之后释放工作并重试 | 工作未完成时仍503；完成后200；记录实际任务开始/结束与占名额峰值≤2 | finally在协程取消时提前release |
| TC-U14-09 | P7；F3后盾 | Given：单项/合计超限，调用SQL/I/O只读观测；When：请求U10大正文样本及TC-U9-13虚报小size样本 | 超限正文未从SQL返回、超大Artifact未read/hash/mmap，虚报小size也不能产生首次内容读取；每块≤64KiB，不以最终响应小或409代替读取证据 | fetchall/read_bytes后检查，或仅信登记size |
| TC-U14-10 | P7 | Given：完整/错误/取消路径各一次，关闭服务；When：重开DB/artifacts并核对任务/FD | RO，任务真正回收且连接/FD无跨测试残留；只清理本测试资源 | read服务意外写入/后台泄露 |
| TC-U14-11 | P8；F2确认 | Given：合法有界大Run/Snapshot/Artifact样本分别确实需要正文解码、页面纯投影、内容hash、完整文本脱敏，ASGI线程身份已独立记录；When：真实请求从头执行，G-STAGE采每个实际阶段入口/退出及名额记录，不在计算前暂停后只测health | 每个预期阶段都有本请求真实执行证据且thread_id≠ASGI thread_id，开始/结束均处在同一有效重任务名额持有区间；输出正确。任何阶段回ASGI、越过名额或请求完成却缺阶段证据均不能计覆盖 | 仅I/O在线程，解析/投影/脱敏移回ASGI或脱离并发限制 |
| TC-U14-12 | P4；F5截止前对照 | Given：真实查询已开始，独立时钟仍在t0+5秒−δ前，其他预算合法；When：让该查询正常结束并记录实际工作完成 | 正常页面成功返回，不误报预算503；本例完成后再独立运行TC-U14-05跨deadline例，不能只验证统一拒绝 | 截止判断提前或所有SQL一律503 |

P2有外部测试写入，RO比较排除明确由夹具替换的文件，并保留完整操作日志；不能将测试攻击本身误判产品写入，也不能把整个Artifact树排除。Q-4/5未落实前，真实时序/时间项不得以stub、宽超时或sleep通过替代。

Test level(s): F2需要真实ASGI/工作执行边界，纯投影单测和暂停期间的health不能证明线程归属；F5需要实际SQLite查询的开始、运算、中断与工作完成，mock超时异常不能证明主动截止。

F2观测按阶段参数化`decode/projection/hash/redaction`，其中decode分别保留Run/Snapshot/Artifact JSON输入，避免只验证文件解析。Observer委托真实公共操作并记录实际执行线程，不替换计算函数，不只记录executor提交/包装函数。阶段清单来自本场景应消费的输入和输出，不由“执行过什么就算什么”的运行日志反向生成；若公共边界不足以采实际阶段，保持Q-4 DEFER，不以快速health代替。

F5正向边界对照：真实查询在deadline−δ前完成时正常返回，不误报503；目标跨deadline则按TC-U14-05中断。具体SQL由已有查询面和合成数据触发，不给产品临时新增任意SQL入口。缺handler的错误变体即使最终由监督者回收，也必须保留为产品FAIL及活动证据，不能改成观测设施DEFER。

### U15：轮询与刷新状态模型

Scope：实际本地JS/HTMX+浏览器传输调度，真服务接线；Source：D13、D15及D§4，TA-22/26，A§11.4。

状态：`M`默认手动；`A(d)`可见非终态且自动刷新启用，d为5/10/20/30秒；`H(d)`隐藏暂停；`T`终态停止自动刷新。M为起始态；T只对自动轮询终止，手动刷新仍合法。状态是可观测页面行为，不是新增产品RunStatus。

| State / Event | 启用轮询 | 自动到点 | 请求成功且非终态 | 请求失败 | 隐藏 | 显示 | 读到终态 | 手动刷新 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M | A(5) | 无请求 | 保持M/更新内容 | 保持M/保旧 | 暂停自动请求（本无轮询） | 保持M | T | 更新或保旧 |
| A(d) | ? | 发一个请求 | A(5)/整体新边界 | A(next(d))/保旧 | H(d) | 保持A(d) | T/停止 | 更新或保旧；成功恢复5 |
| H(d) | ? | 无请求 | 在途响应交付语义? | 在途失败语义? | 保持H(d) | 恢复可见轮询，首次延迟? | T | 手动入口可用，隐藏页操作不扩展 |
| T | 不产生后续自动请求 | 无请求 | 手动成功仍终态 | 保旧 | 保持T | 保持T | 保持T | 允许，能发现晚发布Artifact |

`next(5)=10,next(10)=20,next(20)=30,next(30)=30`。`?`是未定义状态转移，不按无效拒绝设计。隐藏时已有请求是否取消、重新显示后立即或等待多久，不由“停止轮询”推导；这里只验证隐藏期间不发新请求与恢复可见后可继续，精确首次时刻待接口语义定稿。

| Transition | From / Event | To / 可观察动作 |
| --- | --- | --- |
| U15-T1 | M启用 | A(5)，按5秒请求 |
| U15-T2 | A(5)失败 | A(10)，保内容/read_at |
| U15-T3 | A(10)失败 | A(20)，保旧 |
| U15-T4 | A(20)失败 | A(30)，保旧 |
| U15-T5 | A(30)失败 | A(30)，封顶 |
| U15-T6 | A(d)成功且新边界非终态 | A(5)，新边界；若终态则T9 |
| U15-T7 | A(d)隐藏 | H(d)，无新自动请求 |
| U15-T8 | H(d)显示 | 恢复可见自动刷新；首延迟未规定 |
| U15-T9 | 读到终态 | T，无后续自动请求 |
| U15-T10 | T手动刷新 | T，新metadata可见 |

| Path | 顺序 | 对应TC |
| --- | --- | --- |
| U15-P1 | M→启用→四次失败→成功 | TC-U15-01，覆盖T1～T6 |
| U15-P2 | M→启用→隐藏→显示→成功 | TC-U15-02，覆盖T1/T7/T8/T6 |
| U15-P3 | M→启用→终态→手动刷新 | TC-U15-03，覆盖T1/T9/T10 |

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U15-01 | P1 | 默认页先推进受控时钟确认无轮询；启用后5秒请求，连续503四次，再成功 | 每一步断言下一间隔10/20/30/30/5，失败DOM/read_at不变、成功切新边界；不靠真实长sleep | 默认自动开/失败清页/退避错 |
| TC-U15-02 | P2 | 启用后隐藏，推进多个原周期，再显示 | 隐藏时零新请求；显示后可恢复，未定义首延迟不判；不把隐藏等同终态 | hidden继续轮询 |
| TC-U15-03 | P3 | 自动请求读到终态，随后发布Artifact | 后续自动请求为零，手动刷新发现新文件；保已打开日志到显式重载 | 终态继续poll或禁手动 |
| TC-U15-04 | G-UI旧响应 | 旧筛选请求延迟，新筛选请求先成功，再交付旧响应 | URL/筛选/DOM/游标/read_at保持新代次；无旧内容覆盖 | 仅按到达顺序替换 |
| TC-U15-05 | 刷新保位 | 展开E220且锚点不在首批，F1提交后整体刷新 | 身份/usage/摘要/时间线同新边界；恢复展开/锚点，按ID窗口≤41，不倒读全部历史；旧pending/游标不并入 | 只刷新timeline或全量回放 |
| TC-U15-06 | 锚点筛选冲突 | Given：当前锚点不满足保留的筛选；When：刷新 | 保筛选并提供独立定位提示，不静默清筛选或伪称目标消失 | 保位时丢筛选 |
| TC-U15-07 | Workspace预览 | Given：已开日志L0/read_at0；文件改为合法L1，Run/manifest更新；When：整页刷新，再显式重载日志 | 前一步仅更新事实/metadata，预览仍L0/time0且不重新hash该日志；后一步校验显示L1/new time | 每轮询重复hash所有预览 |
| TC-U15-08 | 列表刷新 | Given：当前非第一页、数据更新；When：轮询更新 | 当前页结果符合新读取；若回第一页则有数据更新提示，不无提示换位置 | 列表静默跳页 |

U15的10个明确转换有路径设计；不是覆盖所有状态表格，更不声称所有1-switch组合。真实服务冒烟验证定时器/事件绑定，精确间隔用受控时钟；传输故障不是底层DB预算证据。

### U16：唯一跨页主旅程及局部替代路径

Actor：本机项目开发者/面试演示者，同一权限。Trigger：打开`/`。前置：持久化R具有合法Event/压缩来源/P/评测链，原Workspace目录不存在。成功后置：能从来源解释Run状态、工具/摘要、补丁、已记录评测与成本，数据RO。

| Path | 分支与条件 | 结束 / 保证 |
| --- | --- | --- |
| U16-MAIN | 列表→详情→Event/Snapshot来源→Workspace→预览 | 全程同R，来源边界正确，主页面独立新边界 |
| U16-A1 | 禁用JS，普通GET替代局部刷新 | 局部导航/来源/分页可用，时间UTC，手动刷新 |
| U16-A2 | 浏览长diff/窄屏/键盘 | 局部控件可达且可横滚，无独立移动流程要求 |
| U16-E1 | 局部来源404/预览409 | 错项可退回上级，其他证据仍可看 |

仅MAIN是一条完整自动跨页旅程；A1/A2/E1直接进入各自页面做局部测试，不复制MAIN所有步骤。

| ID | Covers | Given / When | Then | 捕获错误 |
| --- | --- | --- | --- | --- |
| TC-U16-01 | MAIN | 打包应用/浏览器真实请求；依次执行下面步骤 | 每步验证来源与结果，最后RO | 接线/模板/路由/资源组合断裂 |
| TC-U16-02 | A1 | 禁JS直接GET详情/来源/Workspace及下一段链接 | 可普通导航，明确UTC/手动刷新；无JS不隐藏关键证据 | 全依赖HTMX而无GET |
| TC-U16-03 | A2 | 人工键盘操作详情和长diff；窄屏表格 | Tab焦点、展开/来源/返回可达；状态文字+颜色；可横滚；记录实际浏览器/视口与发现 | 控件不可键盘访问/溢出遮住事实 |
| TC-U16-04 | E1 | 已有页中某个来源不存在/某文件被篡改分别测试 | 错项有适用404/409与返回入口；合法材料仍可访问，Run/Evaluation状态不被页面错误改写 | 单坏链接导致整页丢失 |

TC-U16-01分步oracle：

| Step | Action | Expected result |
| --- | --- | --- |
| 1 | 打开`/`并选择R | 跳转/runs；列、短ID/完整复制值、模型/状态文字可用；本地资源无外网依赖 |
| 2 | 进入详情 | R task全文、Runtime状态/reason、read_at/sequence/version、模型成本及完整性说明可见；无未实现功能入口 |
| 3 | 展开Tool、切Step并打开Event来源 | success/truncated双事实、sequence顺序及正确Step归属；来源沿用边界 |
| 4 | 查看Task Memory与VerifiedEvidence，打开Snapshot来源再返回 | 当前摘要与历史来源区分；Snapshot仅请求范围；URL/筛选保留，无恢复按钮 |
| 5 | 切Workspace | 原Workspace已不存在仍能查看P、清单和评测链；新页边界/read_at可见，manifest时间单列 |
| 6 | 打开patch/log预览下一段 | 已校验脱敏有界内容和next offset正确，无原文下载；评测标题与目标覆盖免责声明可见 |
| 7 | 查看本地时区/UTC提示并返回列表 | 有JS显示浏览器明确时区并可看UTC；返回筛选正确，RO |

人工审读另记录：使用者能否区分“最后记录状态”和进程存活、“已记录独立评测通过”和目标覆盖、“未完成校验”和证据冲突。缺UX/无障碍专项标准时只记录可观察发现，不宣称WCAG合规或用户研究结论。

## 4. 覆盖映射与剩余风险

### 4.1 TA到场景入口

| TA | 测试入口 | TA | 测试入口 |
| --- | --- | --- | --- |
| TA-01 | TC-U1-04～08、TC-U14-10 | TA-16 | TC-U9-06～07、TC-U10-B10/B11/B12、TC-U13-05 |
| TA-02 | TC-U3-01～02 | TA-17 | TC-U10-B01～10、TC-U9-13～14、TC-U14-09 |
| TA-03 | TC-U2-01～08、TC-U10-B07/B20、TC-U10-21/23 | TA-18 | TC-U9-05、10～12、TC-U10-B18 |
| TA-04 | TC-U8-01～02、TC-U2-08 | TA-19 | TC-U9-08、TC-U11-01～07 |
| TA-05 | TC-U2-03、TC-U8-02、TC-U10-B05 | TA-20 | TC-U12-01～03、06～09、12 |
| TA-06 | TC-U8-03～08 | TA-21 | TC-U12-04～05、10～11、13～16 |
| TA-07 | TC-U5-03～05、TC-U3-03、TC-U10-B14/24 | TA-22 | TC-U3-04～05、TC-U15-04～08 |
| TA-08 | TC-U5-01～02、06～08 | TA-23 | TC-U13-01～07 |
| TA-09 | TC-U6-01～08、TC-U10-B15/B16（按Q-9核对可达性） | TA-24 | TC-U14-04～08、11～12 |
| TA-10 | TC-U7-01～04、08 | TA-25 | TC-U1-04～06、TC-U13-06/08、TC-U14-04～06 |
| TA-11 | TC-U7-05～08、TC-U10-B17 | TA-26 | TC-U15-01～03 |
| TA-12 | TC-U7-09～10 | TA-27 | TC-U1-01～03、08～09、TC-U10-22 |
| TA-13 | TC-U9-02/09、TC-U10-B09/B13、TC-U11-02/05 | TA-28 | TC-U16-01～04 |
| TA-14 | TC-U9-02～04 | TA-29 | TC-U4-01～09、TC-U10-B19、TC-U10-25/26 |
| TA-15 | TC-U9-04、TC-U14-01～03 | — | — |

表中的短尾号引用沿用同一Unit前缀，例如`TC-U12-01～03、06～09、12`；B编号是场景族，实施追踪使用完整`TC-U10-B20/<suffix>`。这些是设计ID，不是已有pytest node；每个variant需独立结果。

### 4.2 D01–D16入口

| D | Units | D | Units |
| --- | --- | --- | --- |
| D01 | U2、U10 | D09 | U9、U12 |
| D02 | U8、U2 | D10 | U1、U16 |
| D03 | U5 | D11 | U7、U8 |
| D04 | U5 | D12 | U8 |
| D05 | U5、U6、U10 | D13 | U3、U4、U15、U16 |
| D06 | U7、U10 | D14 | U9、U13 |
| D07 | U9、U11、U16 | D15 | U4、U9、U10、U13～U15 |
| D08 | U9、U10、U14 | D16 | U1、U14 |

### 4.3 各技术覆盖口径

以下是**已设计**覆盖入口；实际执行覆盖为未执行，不能报100%通过。表中列数是局部模型规模，不等于所有输入组合或测试执行次数。

| Unit | Technique / 覆盖项 | 设计承接 | 未覆盖 / 残余 |
| --- | --- | --- | --- |
| U1 | EP，E1～E8 | 8分区均有入口 | 实际安装/JSON能力环境待执行；无Web外部能力要求 |
| U2 | EP，E1～E7 | 7分区；有序位置另验证不变量 | 不冻结跨请求筛选集合，不提供成本排序 |
| U3 | Use case，P1～P5 | 5条路径 | G-DB未落实前P1并发证据DEFER |
| U4 | EP，E1～E8及独立签名向量 | 8分区 | Q-1/Q-7、可达合法最大长度构造需落实 |
| U5 | Decision，R1～R11 | 11规则族，各枚举变体展开 | 不声称未知payload归类；无效组合不穷举 |
| U6 | Decision，R1～R11 | 11规则族+分页/审批后盾 | 任意多异常的优先级不制造预期；不验证整RunTrace |
| U7 | Decision，R1～R8 | 8规则族+来源/组变体 | 限额外来源不判真伪；未持久化内容不补造 |
| U8 | EP，E1～E6 | 6分区+独立数值算例 | Q-2舍入；不测试未实现的硬预算引擎 |
| U9 | EP，E1～E10 | 10分区；增加3个词法浅层正例及虚报size/限额内不符两个场景 | F3的零内容read可确定，HTTP优先级仍Q-8；真实文件竞争由U14 |
| U10 | BVA，B01～B20五值族及21～28附加边界 | 每个明确上限有邻界设计，执行前核对可达性 | Q-1/Q-7/Q-9、B18深度计数、offset中间字节语义需落实；无统一资源SLA |
| U11 | Decision，R1～R6 | 6保留列 | 非24组合穷举；关联与扫描异常并存总标题未定 |
| U12 | Decision，R1～R9；EP，E1～E9 | 9保留规则列与9字段类型分区；TC-U12-13～16按字段/路径独立展开 | 非64组合穷举，Q-3；严格类型由消费入口证明，完整目标覆盖不在首版保证 |
| U13 | EP，E1～E8 | 8分区+响应头/真实脚本后盾 | 未知秘密与完整安全认证不保证 |
| U14 | Use case，P1～P8 | 8路径；新增实际阶段线程/名额证据、截止前对照并修订G-SQL | Q-4/5/6/10；暂停health非线程归属证据，已观测产品超时失败不能记DEFER |
| U15 | State，T1～T10与P1～P3 | 10明确转换，另局部乱序/保位 | 状态表?、隐藏在途/恢复首延迟未定义；无全1-switch宣称 |
| U16 | Use case，MAIN/A1/A2/E1 | 4路径，只有1条完整自动主旅程 | Q-6及人工评价负责人/时间盒待登记；无WCAG认证 |

横切机制的治理集合为：RO覆盖启动/每个读取入口/HEAD/错误/取消；脱敏覆盖task/output/payload/summary/metadata/diff/log/error/query与诊断；工作线程覆盖SQL/目录/解码/纯投影/hash/完整脱敏。用参数化代表输入证明每个入口确实到达机制，再用机制自身边界反例验证强度。不能仅测redaction函数或只比较DB主文件字节后声称全部入口满足约束。

### 4.4 Review修订追踪

下面仅记录文档对F1～F5的响应，不是独立复核结论，也不是测试执行证据。

| Finding | 原漏检 / 观测缺口 | 确认场景与相邻对照 | 修订后的独立oracle | 仍需落实 |
| --- | --- | --- | --- | --- |
| F1 | bool强制转换、缺字段/坏列表在消费者端未明确 | TC-U12-13为确认，14～16补必需字段/可选infra/容器与元素；U12-E1～E9 | 合法bytes/hash/ref的单一类型坏项产生结构问题，不独立通过；原生bool及infra缺省对照仍通过 | 按字段/路径展开并接真实消费入口；不拿已有producer测试冒充 |
| F2 | 暂停时health不证明实际计算线程 | TC-U14-11与G-STAGE；TC-U14-07只保留门禁断言 | 实际decode/projection/hash/redaction执行线程不是ASGI，且在同一名额区间；预期阶段必须确实执行 | Q-4实际阶段观测接口；不统计executor提交日志作为计算证据 |
| F3 | 仅登记size超限，未独立证明fstat早拒绝 | TC-U9-13确认，14及B10对照；TC-U14-09后盾 | 实际M+1、登记M-1时零content read/mmap/hash输入；无论HTTP优先级如何均成立 | 实际I/O观测；Q-8仅顶层状态，不能延期零读取oracle |
| F4 | 深度计数未验证字符串/转义/对象键 | TC-U9-10确认，11/12邻近正例；B18真深层反例 | 浅层字符串/键内括号正常脱敏预览，无depth问题；真深层无原文 | 词法样本精确bytes及B18深度计数定义 |
| F5 | 等handler已触发才入测试，缺handler可能被记DEFER | 修订G-SQL与TC-U14-05；TC-U14-12截止前对照 | 查询开始是前置，中断/503/实际完成是后置；已确认跨deadline持续到监督回收是产品FAIL | Q-4设施、Q-5计时；设施缺失与产品错误分别记结果 |

## 5. 后续TDD接续与证据记录

每次从本文选一个TC或一个具名variant，先写清它捕获的生产错误。第一批优先只读/启动、G-DB一致性、文件类型/FD、评测链单故障反证和取消后的并发名额；优先级不意味着本轮已授权实现。

RED必须是预期行为断言失败，例如“首屏usage来自F1而其上界为F0”“FIFO读取未在正常产品出口返回”“错误attempt仍出现独立通过”“取消HTTP后第三个重任务错误开始”。不得以ImportError、准备数据校验失败、网络缺依赖、监督者先kill或测试自身超时代替正确RED。对既有兼容行为，测试可能一开始就绿，应作为回归/特性刻画保留；不能为了制造RED破坏已有生产行为，也不声称该行为完成了新的TDD循环。

GREEN只实现当前行为，不提前补下一个规则。运行对应定向测试与受影响公开契约回归；Python变更按仓库要求ruff check --fix/format changed files，并做相关类型检查。测试命令与额外工具依赖在实施时确定；本文未写入不存在的pytest node或假测试输出。仓库要求运行时变更优先定向测试；本轮是文档变更，跳过运行时测试。

每个已实现TC保留：源D/TA、variant、fixture版本、OS/SQLite/browser、RED断言与失败原因、GREEN命令与实际结果、真实边界观测及残余DEFER。重试前后结果分开，skip/隔离/环境缺失不得算验证通过。若无可证伪独立oracle或缺公开观测边界，维持DEFER并说明，不能默默改为内部mock。

本设计状态为 **PARTIAL（修订稿作者自评，待复核）**：已对F1～F5补足或改写场景，并同步TA映射；不撤销audit快照的NOT-ADEQUATE。D01–D16与TA-01～29有测试设计入口，Q项、真实环境与实际执行尚未落实；下一步针对§4.4受影响项重新review。完整目标覆盖、请求级模型历史、Compare/Checkpoint/Recoverable仍保持首版范围限制，不生成伪证据补齐。CI触发/门禁/保存策略、实现状态与执行结果追踪、完整安全/无障碍评价分别移交对应流程，不由本文承诺。

本轮Verification：核对review与现有报告模型/资源契约；检查原TC保留、新TC续号、F1～F5响应、TA/D映射、文档链接、表格结构及空白；功能/架构与audit快照保持原文。未实现测试、未执行RED/GREEN或运行时测试。
