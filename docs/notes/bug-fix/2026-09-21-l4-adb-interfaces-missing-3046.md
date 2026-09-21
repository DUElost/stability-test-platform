# L4 观测面：USB 有设备但无 ADB 接口 → DEGRADED（#3046）

Status: implemented
Class: bug-fix

## Decision

只做 issue 方向①**观测面**（告警/自愈另单）：

1. `capacity_reporter` 合取 `usb_device_count > 0 ∧ adb_interface_count == 0`
   → reason `adb_interfaces_missing`，warning 级 DEGRADED，不打闸。
2. 控制面 `_HEALTH_REASONS` + 前端 `REASON_LABELS` 同步登记（守卫
   `test_host_health_reason_surface` 已覆盖）。
3. None（采集失败）不报警，与空树判据同形。

刻意不做：连续 ≥2 拍去抖（告警面）、adb kill-server / rebind 自愈（恢复面）。

## Alternatives

- **等选型三件套一起做**：静默还会再拖数周；观测面零风险先合入。
- **复用 `adb_low_healthy_devices`**：它要求 `total_devices > 0`，本态恒假。

## Verification

- `python -m pytest backend/agent/tests/test_capacity_reporter.py tests/test_host_health_reason_surface.py -q`（49 passed）

## Revisit

告警规则与 #2972 类恢复动作另开切片；连续拍去抖若告警需要再进 heartbeat 状态机。
