# Aging DDR 专项脚本族、模板与巡检提前终态支持

Status: implemented
Class: feature

## Decision

1. **新增 Aging DDR 四族平台脚本（ADR-0051 Phase 3 单族源码树模型）**：
   - `backend/agent/scripts/aging_ddr_setup/`（`1.0.0`）：面向 ROM 预装 `com.ape.aging`（FacAging）的纯 ADB 无 UI 启动器——前置校验（`adb root`、APK 安装/版本、电量）、`WAKE` + `kill -9` 干净清缓存防 Android 16 重复 `BOOT_COMPLETED`、写入 4 份 Jetpack DataStore `.preferences_pb`（含 `DDR:PENDING` 会话）、定向广播 `UnifiedBootReceiver` 并核验 logcat 启动与 DST 断连标记。
   - `backend/agent/scripts/aging_ddr_check/`（`1.0.0`）：面向 SPRD DST 无 USB/ADB 枚举特征的巡检器——按 `STP_JOB_ID` 隔离本地离线计时状态，离线窗口在 `max_offline_seconds`（默认 2400s）内放行；在线后读取 DataStore，命中 `DONE_PASS` 返回 `{"success": true, "patrol_complete": true}`，命中 `DONE_FAIL` 返回 `{"success": false, "patrol_fail": true}`，运行中跟踪在线进度停滞与非预期退出宽限。
   - `backend/agent/scripts/aging_ddr_disarm/`（`1.0.0`）：等待设备从 DST/重启恢复上线后停止 `DdrTestService`、清除 `active`/`running` 续跑足迹并回读校验；对已终态（`COMPLETED`/`FAILED`）会话保持原终态字段不被覆写。
   - `backend/agent/scripts/aging_ddr_finish/`（`1.0.0`）：拉取 `ddr_report_*.csv`、4 份 DataStore 快照与 `/cache/ddr_test_flag`，写出结构化摘要 JSON 与 CSV 副本至 `{STP_AEE_NFS_ROOT}/aging-ddr/{project}/results/`，挂 SIGTERM 处理器并在 `finally` 回收本地临时目录。
2. **新增独立模板 `backend/schemas/pipeline_templates/aging_ddr.json`**：保留旧 `ddr.json`（memtester）不动；四族脚本经 `check_script_packages.py --register <family> 1.0.0` 登记进 `tool_manifest.json`，并在 `tests/test_pipeline_template_script_pins_2865.py` 纳入 `PINNED_SCRIPTS`。
3. **在 `backend/agent/pipeline_engine.py` 支持巡检步骤显式提前终态**：
   - 当 patrol 步骤 stdout JSON 显式返回 `"patrol_complete": true`（且 `success=true`）时，本轮心跳上报后立即以 `termination_reason="completed"` 结束巡检并进入 `teardown`，最终 Job 终态为 `COMPLETED`；
   - 当 patrol 步骤显式返回 `"patrol_fail": true`（`success=false`，无论退出码为 0 还是非 0）时，立即以 `termination_reason="patrol_failure"` 结束巡检并进入 `teardown` 收取失败证据，最终 Job 终态为 `FAILED`；
   - 未声明上述标记的既有专项脚本保持 ADR-0022 巡检退避循环语义不变。

## Alternatives

- **复用旧 `ddr.json` 模板**：拒绝——旧模板面向内核态 memtester，与 FacAging DDR（含 SPRD DST 硬件重启周期）前置条件、参数与产物结构完全不同，覆盖会破坏历史 Plan 兼容。
- **通过 UI 自动化 / `app_process` 绑定 `AgingControlService` 启动**：在 Android 16 实测失败（`ActiveServices.bindServiceLocked` 拒绝非托管进程绑定，UI 方式受锁屏/分辨率干扰），而 DataStore + `UnifiedBootReceiver` 纯 ADB 链路已在 525 平台 94/94 台实机验证通过。
- **不改 `pipeline_engine.py`，等 7 天 `timeout_seconds` 自然结束**：拒绝——DDR 专项按固定 `aging_module_pass_count`（如 7 轮）运行，完成后必须及时释放设备租约并汇报 PASS/FAIL 终态，且失败时应立即宣告 Job FAILED 并收取报告。

## Verification

- 单元测试与门禁结果见本 PR 实际执行输出（`scripts/run_pytest.py`、`check_script_packages.py`、`check_tool_manifest.py`、`scripts/run_gates.py check:quick`）。
- 纯 ADB 启动链路已在 5 台生产 Agent 主机（94/94 台 525 设备）通过实机验证。

## Revisit

- 后续若需要将 `aging-ddr` 纳入 ADR-0061 `device_return.ONBOARDED` 跨作业残留探针闭环，可在真机复测 `aging_ddr_disarm` `probe`/`disarm` 流程并登记 `verified_device_classes` 后单独推进。
- 若后续 ROM 升级 FacAging 版本导致 DataStore key 或 `sys.boot.reason.last` 校验正则变更，通过 `aging_ddr_*` 新版本升级（`1.0.1`+），遵守 ADR-0051 已发布版本不可原地修改原则。
