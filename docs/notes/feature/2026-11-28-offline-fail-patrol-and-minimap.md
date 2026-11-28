# 休眠唤醒与 Monkey 长时间 ADB 离线判 fail（脚本层 patrol_fail + Minimap 专项区分标红）

Status: implemented
Class: feature

## Decision

稳定性专项按「离线是否属于测试过程的一部分」存在本质差异：
- **预期离线专项**（开关机 `power-cycle` / `powercycle_*`、Aging DDR `aging_ddr_*`、刷机 `flash_*` / `vbus_flash*`）：
  设备在测试过程中反复重启或进入 DST / 下载态，ADB 离线是正常过程；
- **非预期离线专项**（休眠唤醒 `sleep`、Monkey `monkey`、MTBF、GPU 等）：
  短暂重启/USB 抖动应容错，但连续长时间 ADB 离线（如睡死、Kernel Panic、进入 UNISOC Full Sysdump）
  意味着设备已异常中断。

此前 `sleep_check@1.0.6` 对离线无限豁免、`monkey_check@2.0.3` 离线不发 `patrol_fail=true`，且 Minimap
方块仅按 `job_exec_status` 着色，导致 8 台已离线 6–43 小时的休眠设备在 Minimap 上仍全部显示黄色「运行中」。

采用**双层联动**最小方案落地：

1. **Agent 巡检脚本层（新 PlanRun 真实判 `FAILED`）**：
   - `sleep_check@1.0.7` 与 `monkey_check@2.0.4` 新增 `max_offline_seconds`（默认 `1800` 秒 / 30 分钟，
     支持 step params / env 覆盖，设为 `0` 关闭），按 `STP_JOB_ID` 隔离记录首次连续离线时间戳 `offline_since`；
   - 离线未达阈值仍保持原有瞬时容错与重试；恢复在线自动清零计时；
   - 连续离线达到阈值返回 `success=false` 且附带 `patrol_fail=true`，由 `pipeline_engine` 干净结束巡检并落 `FAILED`；
   - 同步更新 `sleep.json`、`monkey.json`、`monkey_watcher_patrol.json` 模板钉扎与默认参数。
2. **控制面策略投影 + Minimap 展示层（存量在跑 PlanRun 即时生效）**：
   - 新增 `backend/services/plan_run_offline_policy.py`，按 PlanRun 快照步骤与 `specialty_key` 判定
     `offline_expected` 与阈值（优先读取快照 patrol 步骤 `max_offline_seconds`，缺省 `1800s`）；
   - `PlanRunDevicesMinimapOut` 保持单设备 7 字段轻量契约不变（契约 §5.1），在顶层新增
     `offline_failed_job_ids` 与 `offline_expected`（与 `stuck_job_ids` 同构），并同步 `frontend/src/utils/api/types.ts`；
   - `DeviceOverview.tsx` 对 `offline_failed_job_ids` 中的在跑格子以 `destructive` 红色渲染（`data-offline-failed="true"`）
     并在 Minimap 上方显示可一键筛选连接异常的告警条；Aging DDR / 开关机等预期离线专项的 `offline_failed_job_ids` 恒为空。

## Alternatives

- 仅改巡检脚本层：已在跑的 7 天长稳任务锁定旧 `plan_snapshot`，7 天内 Minimap 仍无红态提示，否决。
- 仅改前端或仅按 `device_link_status != online` 一刀切标红：会误伤开关机与 Aging DDR 的正常离线阶段，且新任务离线后仍会长期占住 Agent 槽位，否决。
- 在 Minimap 单设备对象里新增第 8 字段：违反 `2026-11-24-list-pagination-and-facets-contract.md` §5.1 严格 7 字段约定，改用顶层 `offline_failed_job_ids` 与 `stuck_job_ids` 对齐。

## Verification

- 脚本层：`backend/agent/tests/test_offline_fail_patrol.py` + 全量既有 `*sleep*.py`、`test_monkey_watchdog_chain_809.py`、`test_pipeline_template_script_pins_2865.py`、`test_agent_clock_stub_guard_3202.py`；
- 控制面：`backend/tests/services/test_plan_run_offline_policy.py`、`backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py`、`tests/test_api_response_shape_contract.py`；
- 前端：`frontend/src/components/plan-run/DeviceOverview.test.tsx`（覆盖休眠/Monkey 异常离线标红与一键筛选，以及开关机/Aging DDR 保持运行态不误标红）；
- 门禁：`check_script_packages.py`、`check_tool_manifest.py --base origin/main`、`scripts/run_gates.py check:quick`。

## Revisit

- 后续如需将同样的 `max_offline_seconds` 离线判死能力扩展到 `mtbf_check` / `gpu_check`，可直接复用 `sleep_check@1.0.7` 与 `monkey_check@2.0.4` 的 `job_id` 隔离计时模式。
- 随 #4196 / 本 PR 后续部署后，在跑的存量 `#1747` / `#1740` 无需重启即可在 Minimap 立即看到标红提示。
