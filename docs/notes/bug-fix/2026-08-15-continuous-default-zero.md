# CONTINUOUS 默认值回归 0（过滤模型）

Status: implemented
Class: bug-fix

## Decision

ADR-0028 方案 A 规定 `STP_EVENT_UPLOADER_CONTINUOUS` 默认 0
（过滤模型，仅拉 `UPLOAD_PENDING`），1 是逃生阀（全量上送）。
但 `backend/agent/event_uploader.py` 与 `backend/services/device_log_event.py`
两处 `os.getenv` 默认写成了 "1" —— Agent/控制面 `.env` 一旦缺该键，
会静默翻转为全量连续上送。

- 两处代码默认改回 "0"；`count_pending_upload_events` docstring 同步
  （此前误写「连续上送模式（默认）」）。
- `backend/.env.example` 与 `backend/agent/.env.example` 补上该键说明（默认 0）。
- 回归测试：`_event_uploader_continuous` 默认 False；
  `count_pending_upload_events` 默认过滤模型（LOCAL 不计入 pending）。

## Alternatives

- 保持默认 1 但强制 hot-update 下发：依赖部署纪律，缺键即回退全量，不成立。
- 未设开关即抛错 fail-fast：开关语义要求「缺键 = 默认 0」，抛错会打断
  无 PlanRun 纯采集的逃生场景。

## Verification

- `backend/agent/tests/test_event_uploader.py`（8 用例）
- `backend/tests/services/test_device_log_event_prune_extract.py`（2 用例）
- `ruff check` 通过

## Revisit

#217 PRUNE/Spill 验收依赖过滤模型底座；上线后观察 `upload_task` 标记率
与 LOCAL 存量，确认全量上送未隐性开启。
