# 2026-09-26 下午 PR 审计后续：/metrics 读失败回滚与设备面意图清除审计

Status: implemented
Class: bug-fix

关联：#3416（表增长指标）、#3406（心跳 tick 分桶）、#3421（设备面意图位 A 期）、
#3102（共享 session 读失败须 rollback 的先例）。

## Decision

- `backend/services/db_growth_metrics.py` 与 `backend/services/heartbeat_timing_metrics.py`
  的读失败分支补 `db.rollback()`。`/metrics` 各刷新组共享同一 session，PostgreSQL 上一次
  失败的读会让事务进入 aborted，同一次 scrape 中其后的 `_sweep_push_host_gauge_children`
  会因此静默跳过（与 `_refresh_script_presence_gauges` 的 #3102 注释同一形态）。
- `clear_device_intent` 服务新增可选 `reason`，并入清除动作本体的那条审计
  （`details.clear_reason`）；`DELETE /hosts/{id}/device-intent` 路由不再另写审计。
  原实现对无意图 host 的幂等清除也会写一条「清除」审计，真实清除则写两条。

## Alternatives

- 在 `metrics()` 路由里每组之间统一 rollback：改动面更大且与既有逐组 rollback 惯例不一致，未采纳。
- 保留路由层补写、仅在「确实清除」时补写：仍是一次动作两条审计，复盘时需要拼接，未采纳。

## Verification

- `python -m pytest backend/tests/api/test_db_growth_metrics_3327.py backend/tests/api/test_heartbeat_timing_metrics_3219.py`：
  新增两例在修复前失败、修复后通过（隔离临时 PostgreSQL，非生产库）。
- `python -m pytest backend/tests/api/test_host_device_intent_3159.py`：新增
  `test_clear_reason_rides_single_audit_row_and_noop_writes_none` 修复前失败、修复后通过。
- `python scripts/run_gates.py check:quick`。

## Revisit

- 若 `/metrics` 刷新组继续增多，考虑把「失败即 rollback」收敛为一个包装器，避免新组再漏。
