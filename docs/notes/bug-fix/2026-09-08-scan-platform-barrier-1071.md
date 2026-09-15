# 双平台 scan 完备性按 host×platform；仅 UNISOC 可执行（#1071）

Status: implemented
Class: bug-fix

## Decision

1. **控制面**：`count_hosts_with_scan_artifacts(..., require_platforms=)` 在
   `saq_tasks` 传入 `DEDUP_PLATFORMS` 时，host 须对 mtk 与 unisoc 各有 ≥1 条
   本轮产物才计数；避免 MTK 先到即开 merge、UNISOC 漏入本轮。

   > **2026-09-15 部分取代**：本条的「每个 host 双平台齐」已被
   > [`2026-09-15-scan-completeness-per-host-platform.md`](./2026-09-15-scan-completeness-per-host-platform.md)
   > 取代为「按 (host, platform) 期望集判定」——原语义让纯 MTK / 纯 UNISOC host 永远
   > 判不齐。被取代的是**判据粒度**，本条要解决的「MTK 先到不得提前开 merge」仍然保留。
   > 第 2 条（Agent 侧）不受影响，仍然有效。

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

**2026-09-15 更新：本条已触发。** 上述「预期」被判定不可接受——纯平台 host 在生产
fleet 中占多数，双平台要求使它们每轮烧满轮询预算并误报 `saq_scan_partial_artifacts`，
真实缺口被假警报掩盖。控制面判据已按 ADR-0032 B1 改为 **(host, platform) 期望集**：
见 [`2026-09-15-scan-completeness-per-host-platform.md`](./2026-09-15-scan-completeness-per-host-platform.md)。
「Agent 能力声明」仍是未采纳的后续方向，在该 note 的 Revisit 中登记了残留风险与触发条件。

另：本文件 §Verification 记录的 `test_count_hosts_require_platforms_waits_for_unisoc`
已随 API 变更改名/改写为
`test_scan_completeness_mixed_host_waits_for_both_platforms` 等用例（语义保留）。
