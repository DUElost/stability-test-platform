# watcher-summary：无信号平台按 RUNNING 设备展示分桶

Status: preview
Class: feature

## Decision

「按平台分桶」原先仅统计时间窗内 `job_log_signal`，展锐等设备在 UNIVIEW watcher 未联调前信号恒为 0，整桶缺失（如 PlanRun #327 仅见 MTK）。

扩展 `_aggregate_watcher_platform_buckets`：信号平台 ∪ RUNNING Job ∪ 本 run 参与 Job 的 `device.platform` 并集；桶增加 `running_device_count` / `participating_device_count`。前端无异常时展示「运行中 N」或终态「参与 N」。

## Alternatives rejected

- 用 archive dedup 产物反推平台：与 watcher 语义不同，且 RUNNING 时 dedup 可能尚未产出。
- 前端用 `/devices` 自行拼桶：重复业务逻辑，与 watcher-summary 口径分叉。

## Verification

```bash
JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/api/test_plan_run_aggregation_endpoints.py::TestWatcherSummaryEndpoint::test_watcher_summary_platform_bucket_includes_running_unisoc_without_signals \
  backend/tests/api/test_plan_run_aggregation_endpoints.py::TestWatcherSummaryEndpoint::test_watcher_summary_platform_bucket_includes_terminal_unisoc_without_signals -q
```

合入后：`GET /plan-runs/327/watcher-summary` 应含 `UNISOC` 且 `total=0`、`participating_device_count=224`（约）。

## Revisit

UNIVIEW reconciler 上线后 UNISOC 会有信号；`running_device_count` / `participating_device_count` 仍保留，便于区分运行态与终态参与规模。
