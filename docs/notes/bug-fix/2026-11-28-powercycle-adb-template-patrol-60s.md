# 调整 `powercycle_adb.json` 模板默认巡检间隔至 60s（规避 `patrol_stall_detected` 误杀）

Status: implemented
Class: bug-fix

## Decision

在 PlanRun `1765` / `1768` / `1769`（10 台 `INFINIX_X6728` 7 天宿主侧 ADB 开关机压测）实跑取证中，默认 `patrol.interval_seconds = 15` 触发了控制面 `patrol_stall_detected` 误判（判定阈值 = `interval_seconds × 3 = 45s`）：当单次 `powercycle_adb_patrol` 恰好叠加瞬态 `adb` 超时（~30s）、内联等待（15s）与主机 permit 排队时，两次心跳间隔达到 `49–72s > 45s`，导致健康作业被翻成 `UNKNOWN`，继而引发 `patrol_heartbeat` `409 JOB_NOT_RUNNING` → `recovery_resume` 重跑 `init` 撞死 `init_return_check:not_executed`。

本次调整内置模板 `backend/schemas/pipeline_templates/powercycle_adb.json` 的 `patrol` 默认配置：

- `patrol.interval_seconds`：`15` → **`60`**（控制面 `patrol_stall_detected` 阈值由 `45s` 抬升至 **`180s`**，远大于单步最大墙钟预算，彻底消除误杀）；
- `powercycle_adb_patrol` 步骤 `timeout_seconds`：`90` → **`120`**；
- `powercycle_adb_patrol` 步骤 `params.inline_wait_max_seconds`：`35`（默认 `wait_seconds=30` 时，开机解锁至主界面后仍在当步内精确等满 30s 触发下一次 `adb reboot`，间隔精度不受影响，同时 60s 巡检间隔与典型 60–80s 开机窗口自然错峰）。

## Alternatives

- **放宽控制面全局 `patrol_stall_detected` 乘数（`multiplier=3`）**：影响所有专项的真卡死告警灵敏度，改动面大且无必要；调大本模板自身的 `patrol.interval_seconds` 即可在既有契约内彻底解决。
- **保留 `interval_seconds=15` 并缩小单步内部 `adb` 超时**：主机多设备并发下 permit 排队仍可能超过 45s，无法结构性杜绝。

## Verification

- `scripts/run_pytest.py tests/test_pipeline_template_script_pins_2865.py tests/test_template_step_timeout_vs_script_budget.py backend/tests/api/test_pipeline_templates_stages.py -q`
- `scripts/run_gates.py check:quick`

## Revisit

- 合入 `main` 后随下一次控制面部署生效（生产存量 Plan `259` / `260` 已同步在线调整至相同参数）。
