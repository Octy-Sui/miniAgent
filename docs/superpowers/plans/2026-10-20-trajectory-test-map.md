# Trajectory 执行计划测试映射

此索引从 TD 的100个唯一 TC 规格生成；不是测试执行结果。依据与 oracle 仍为 [TD](../specs/2026-10-20-trajectory-test-design.md) 和 [TA](../specs/2026-10-20-trajectory-test-analysis.md)，执行规则见[主计划](2026-10-20-trajectory.md)。源文件不改写；P2/P3 等可达性裁定在主计划登记。

状态：全部 PLANNED。表中主责 task 的测试函数名固定为 `test_tc_uxx_nn`（例如 TC-U07-02→test_tc_u07_02）；TD 的每个 variant 用 pytest.param id 保留完整 TC ID。一个TC的主测试通过不代表后盾/其他backend通过。主责管理最终覆盖记录，各task完成以本task分配的variant为准；全TC需所有必需variant/后盾齐全。

| TC | 主责 Task | 主测试文件 | 必需后盾/说明 |
| --- | --- | --- | --- |
| TC-U01-01 | 16 | `tests/sit/cli/test_trajectory.py` | 按TD逐variant展开 |
| TC-U01-02 | 16 | `tests/sit/cli/test_trajectory.py` | 按TD逐variant展开 |
| TC-U02-01 | 15 | `tests/sit/cli/test_trajectory_options.py` | 按TD逐variant展开 |
| TC-U02-02 | 15 | `tests/ut/cli/test_trajectory_settings.py` | default/env/.env分别隔离 |
| TC-U02-03 | 15 | `tests/sit/cli/test_trajectory_options.py` | 按TD逐variant展开 |
| TC-U02-04 | 15 | `tests/sit/cli/test_trajectory_options.py` | 按TD逐variant展开 |
| TC-U03-01 | 8 | `tests/it/runtime/test_model_evidence.py` | 按TD逐variant展开 |
| TC-U03-02 | 8 | `tests/it/runtime/test_model_evidence.py` | 按TD逐variant展开 |
| TC-U03-03 | 8 | `tests/it/runtime/test_model_evidence.py` | 按TD逐variant展开 |
| TC-U03-04 | 8 | `tests/it/runtime/test_model_evidence.py` | 按TD逐variant展开 |
| TC-U04-01 | 16 | `tests/sit/resume/test_trajectory.py` | B-request/B-response/B-tx：必须实际窗口证明 |
| TC-U04-02 | 16 | `tests/sit/resume/test_trajectory.py` | B-request/B-response/B-tx：必须实际窗口证明 |
| TC-U04-03 | 16 | `tests/sit/resume/test_trajectory.py` | B-request/B-response/B-tx：必须实际窗口证明 |
| TC-U04-04 | 16 | `tests/sit/resume/test_trajectory.py` | B-request/B-response/B-tx：必须实际窗口证明 |
| TC-U05-01 | 8 | `tests/it/runtime/test_compaction_evidence.py` | P2/P6；Task 16 跨进程后盾 |
| TC-U05-02 | 8 | `tests/it/runtime/test_compaction_evidence.py` | P2/P6；Task 16 跨进程后盾 |
| TC-U05-03 | 8 | `tests/it/runtime/test_compaction_evidence.py` | P2/P6；Task 16 跨进程后盾 |
| TC-U05-04 | 8 | `tests/it/runtime/test_compaction_evidence.py` | P2/P6；Task 16 跨进程后盾 |
| TC-U05-05 | 8 | `tests/it/runtime/test_compaction_evidence.py` | P2/P6；Task 16 跨进程后盾 |
| TC-U06-01 | 7 | `tests/it/providers/test_qwen_response_evidence.py` | 按TD逐variant展开 |
| TC-U06-02 | 8 | `tests/it/runtime/test_model_evidence.py` | Task 7 adapter规范化失败后盾；E4按P3 |
| TC-U06-03 | 8 | `tests/it/runtime/test_model_evidence.py` | Task 7无response外部连接错误后盾 |
| TC-U06-04 | 7 | `tests/it/providers/test_qwen_response_evidence.py` | 按TD逐variant展开 |
| TC-U07-01 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-02 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-03 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-04 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-05 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-06 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-07 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U07-08 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U08-01 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U08-02 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U08-03 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U08-04 | 14 | `tests/it/cli/test_trajectory_finalization.py` | Tasks 9/10 执行身份；含lifecycle/patch/evaluator/diagnostic四分区 |
| TC-U09-01 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U09-02 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U09-03 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U09-04 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U09-05 | 10 | `tests/it/environments/test_docker_output_evidence.py` | tests/sit/environments/test_docker_output_evidence.py真实Docker |
| TC-U09-06 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10: tests/it/environments/test_docker_output_evidence.py + 真实 Docker SIT |
| TC-U10-01 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U10-02 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U10-03 | 11 | `tests/it/runtime/test_tool_evidence.py` | 按TD逐variant展开 |
| TC-U11-01 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10 Docker故障后盾；Task 11 raw故障 |
| TC-U11-02 | 11 | `tests/it/runtime/test_tool_evidence.py` | Task 13 registered_only，真实Event事务失败 |
| TC-U11-03 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10 Docker故障后盾；Task 11 raw故障 |
| TC-U11-04 | 11 | `tests/it/runtime/test_tool_evidence.py` | 有效大ToolResult，存储外部故障 |
| TC-U12-01 | 1 | `tests/it/artifacts/test_stream_writer.py` | Task 14: exported/outcome；Task 16: RunLock/重复信号 |
| TC-U12-02 | 16 | `tests/sit/resume/test_trajectory.py` | Task 1文件发布屏障；Task 14 CLI outcome |
| TC-U12-03 | 1 | `tests/it/artifacts/test_stream_writer.py` | Task 14: exported/outcome；Task 16: RunLock/重复信号 |
| TC-U12-04 | 1 | `tests/it/artifacts/test_stream_writer.py` | Task 14: exported/outcome；Task 16: RunLock/重复信号 |
| TC-U12-05 | 9 | `tests/it/environments/test_local_output_evidence.py` | Task 10 Docker stream后盾 |
| TC-U13-01 | 12 | `tests/it/runtime/test_persistence_dependencies.py` | 按TD逐variant展开 |
| TC-U13-02 | 16 | `tests/sit/cli/test_trajectory.py` | Task 12当前契约，Task 13导出，Task 15 Dashboard分别后盾 |
| TC-U13-03 | 12 | `tests/it/runtime/test_persistence_dependencies.py` | 按TD逐variant展开 |
| TC-U13-04 | 12 | `tests/it/runtime/test_persistence_dependencies.py` | 按TD逐variant展开 |
| TC-U13-05 | 12 | `tests/it/runtime/test_persistence_dependencies.py` | 按TD逐variant展开 |
| TC-U13-06 | 12 | `tests/it/runtime/test_persistence_dependencies.py` | 按TD逐variant展开 |
| TC-U14-01 | 17 | `tests/sit/trajectory/test_history_resources.py` | G1–G8；Tasks 3–6 提供结构/业务基础 |
| TC-U14-02 | 17 | `tests/sit/trajectory/test_history_resources.py` | G1–G8；Tasks 3–6 提供结构/业务基础 |
| TC-U14-03 | 17 | `tests/sit/trajectory/test_history_resources.py` | G1–G8；Tasks 3–6 提供结构/业务基础 |
| TC-U14-04 | 17 | `tests/sit/trajectory/test_history_resources.py` | G1–G8；Tasks 3–6 提供结构/业务基础 |
| TC-U14-05 | 17 | `tests/sit/trajectory/test_history_resources.py` | G1–G8；Tasks 3–6 提供结构/业务基础 |
| TC-U15-01 | 5 | `tests/it/runtime/test_recovery_history_reads.py` | 按TD逐variant展开 |
| TC-U15-02 | 4 | `tests/it/runtime/test_compaction_history_reads.py` | 重复/断链/循环 |
| TC-U15-03 | 4 | `tests/it/runtime/test_compaction_history_reads.py` | Run/N/Step/field-excerpt分别破坏 |
| TC-U15-04 | 4 | `tests/it/runtime/test_compaction_history_reads.py` | 缺Snapshot/hash/version/retained-message |
| TC-U15-05 | 5 | `tests/it/runtime/test_recovery_history_reads.py` | 按TD逐variant展开 |
| TC-U15-06 | 5 | `tests/it/runtime/test_recovery_history_reads.py` | 按TD逐variant展开 |
| TC-U15-07 | 5 | `tests/it/runtime/test_recovery_history_reads.py` | 按TD逐variant展开 |
| TC-U15-08 | 5 | `tests/sit/cli/test_resume_stale_boundary.py` | 另进程合法writer；dry-run不写 |
| TC-U16-01 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U16-02 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U16-03 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U16-04 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U16-05 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U16-06 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U17-01 | 2 | `tests/it/artifacts/test_manifest_listing.py` | Task 13导出不验证正文 |
| TC-U17-02 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U17-03 | 14 | `tests/it/cli/test_trajectory_finalization.py` | 坏candidate patch仍失败 |
| TC-U17-04 | 13 | `tests/it/trajectory/test_export.py` | 按TD逐variant展开 |
| TC-U17-05 | 2 | `tests/it/artifacts/test_manifest_listing.py` | Task 13相邻trajectory排除 |
| TC-U18-01 | 13 | `tests/ut/trajectory/test_encoding.py` | 同 Task tests/it/trajectory/test_export.py 验证真实注册/无成品 |
| TC-U18-02 | 13 | `tests/ut/trajectory/test_encoding.py` | 同 Task tests/it/trajectory/test_export.py 验证真实注册/无成品 |
| TC-U18-03 | 13 | `tests/ut/trajectory/test_encoding.py` | 同 Task tests/it/trajectory/test_export.py 验证真实注册/无成品 |
| TC-U18-04 | 13 | `tests/ut/trajectory/test_encoding.py` | 同 Task tests/it/trajectory/test_export.py 验证真实注册/无成品 |
| TC-U18-05 | 14 | `tests/it/cli/test_trajectory_finalization.py` | Task 13编码检查单次deadline |
| TC-U18-06 | 14 | `tests/it/cli/test_trajectory_finalization.py` | export早/cleanup早/已耗尽 |
| TC-U18-07 | 14 | `tests/it/cli/test_trajectory_finalization.py` | Task 1发布drain；Task 16锁后盾 |
| TC-U19-01 | 14 | `tests/it/cli/test_trajectory_finalization.py` | 按TD逐variant展开 |
| TC-U19-02 | 14 | `tests/it/cli/test_trajectory_finalization.py` | 按TD逐variant展开 |
| TC-U19-03 | 14 | `tests/it/cli/test_trajectory_finalization.py` | 按TD逐variant展开 |
| TC-U19-04 | 16 | `tests/sit/cli/test_trajectory.py` | Workspace销毁后重开，真实命令/manifest |
| TC-U19-05 | 16 | `tests/sit/resume/test_trajectory.py` | 前后Artifact bytes不变 |
| TC-U19-06 | 14 | `tests/it/cli/test_trajectory_finalization.py` | 按TD逐variant展开 |
| TC-U19-07 | 16 | `tests/sit/cli/test_trajectory.py` | 持锁/排空/patch/清理顺序证据 |
| TC-U20-01 | 16 | `tests/sit/cli/test_trajectory.py` | Task 7/8/13更小范围白名单断言；正常/失败逐哨兵 |
| TC-U20-02 | 13 | `tests/it/trajectory/test_export.py` | Task 7 provider白名单后盾 |
| TC-U20-03 | 13 | `tests/it/trajectory/test_export.py` | Task 7 provider白名单后盾 |

## Variant 清单与防漏规则

- U02-01：`r1/r2/r3/r4/r5/r6` × `direct/run/trigger` 共18个实例；U02-02 的default/env/.env五分区单列，不能以三态UT替代入口装配。
- U03-03：R1、R2、R3[pricing/no-pricing]、R4、R5、R6；另把 STOP带工具/空正文、TOOL_CALL无调用、重复ID、LENGTH/UNKNOWN分别覆盖到对应规则，不以一个异常finish代表全部业务判断。
- U04-03：rollback/commit两种真实事务结局；U05-02：invalid-json/source/budget，按P2解释路径；U05-05：missing-usage/no-pricing/normalization-arguments/normalization-usage/no-response。
- U07-02：wrong-call-id/invalid-error-code × inline/artifact四实例；U07-05～07分别65,535/65,536/65,537最终JSON bytes。
- U08/U09凡TD列Local/Docker均须两端；IT协议验证不能替代真实Docker SIT。U11-01逐write/body-finish/manifest失败，U11-03双故障单独robustness。
- U12按writer/trajectory put及cancel/deadline实际分区；不是任意枚举组合乘积。U14-01为G1–G8×15/16/17/32/33；U14-02/03为每G三档正文/数量；允许紧凑元数据增长与单对象峰值。
- U15每次只破坏一项；第16/17条缺口、旧链非当前active operation、重复resume start不可省。U16-06所有允许定义字段逐项设非默认独立值，benchmark有/无两分区。
- U18最终JSON大小67,108,863/64/65三点，不用payload长度代替；U19-02六出口、U19-03原成功/失败/异常×导出存储失败/超时六实例；U20正常/规范化失败路径逐禁止字段使用不同哨兵。
- 其余TC仍以TD表中全部显式variant为准；此清单突出易漏组合，不缩减TD。统计只基于pytest collection，不用100当参数化实例数或通过率。

## 证据记录格式

每一实际 nodeid 在计划 workspace `evidence.md` 留下：`TC/variant | owning task | implementation SHA | input SHA | nodeid | platform/backend | barrier proof path | result | residual`。结果可为 PASS、FAIL、NOT-RUN、WINDOW-NOT-REACHED、DEFER；不使用“已设计”代替PASS。屏障证据、真实新连接读取的摘要、外部动作日志与原始输出路径都可追溯。未来自动统计读取collection与该记录，不在本轮新增测试插件/CI服务。
