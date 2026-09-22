# step_trace 静默回收（#3061）

## Decision

在 `recycler.recycle_once` 增加 Pass **#2c**：`RUNNING` job 若最新 `step_trace` 为终态
（非 `STARTED`）且 `original_ts` 早于 `STEP_TRACE_STALL_SECONDS`（默认 3600s），则 CAS
转 `UNKNOWN` 并写 `step_trace_stall_detected` 审计。补「心跳/coordinator 仍 fresh、但
pipeline 长期无 step 活动」的回收缝——典型于 `timeout_seconds=NULL` 的 watcher 计划。

## Alternatives

- **门禁侧**「活跃 = 最近 N 分钟有活动」：不释放租约，host 仍被 zombie job 占位。
- **计划侧**补 `timeout_seconds`：需逐计划运维，不覆盖已卡死实例。
- **仅 patrol_stall**：依赖 `last_patrol_heartbeat_at`；#3061 实测 12.5h 无 step 仍
  `RUNNING`，heartbeat 路径未覆盖。

## Verification

- `pytest backend/tests/scheduler/test_recycler.py -k step_trace_stall`
- `pytest backend/tests/core/test_job_timeout_config.py`

## Revisit

- 阈值是否应随 `patrol.interval_seconds` 分级（现统一 env）。
- 生产 `.87` job 42219 需 reconciler grace 后释放租约；本 PR 只负责 recycler 检出。
