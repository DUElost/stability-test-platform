# 双平台 scan 完备性按 host×platform；仅 UNISOC 可执行（#1071）

Status: implemented
Class: bug-fix

## Decision

1. **控制面**：`count_hosts_with_scan_artifacts(..., require_platforms=)` 在
   `saq_tasks` 传入 `DEDUP_PLATFORMS` 时，host 须对 mtk 与 unisoc 各有 ≥1 条
   本轮产物才计数；避免 MTK 先到即开 merge、UNISOC 漏入本轮。

2. **Agent**：队列 defer 改为「MTK 与 UNISOC 都未配置」；`_execute_job` 按各自
   `is_configured()` 分支执行，仅配 UNISOC 时可跑。

涉及：`dedup_scan.py`、`saq_tasks.py`、`scan_runner.py`；测试见
`test_dedup_scan_merge.py`、`test_scan_runner.py`。

## Alternatives

- 按平台各自完备后分别 merge：与现行 `run_merge_all_platforms_sync` 一次链
  路不一致，改动面更大；本轮先统一屏障。
- 默认改 host-only 计数语义：破坏既有单测/兼容调用；用显式 kwarg。

## Verification

- `test_count_hosts_require_platforms_waits_for_unisoc`
- `test_queue_runs_when_only_unisoc_configured`
- `test_execute_job_skips_mtk_runs_unisoc_when_mtk_missing`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_dedup_scan_merge.py
  backend/agent/tests/test_scan_runner.py
  backend/agent/tests/test_saq_scan_pipeline.py -q`

## Revisit

仅配 MTK 的 host 在双平台要求下会一直 partial 至超时（预期）；若需按
Agent 能力声明动态期望平台，另开 issue。
