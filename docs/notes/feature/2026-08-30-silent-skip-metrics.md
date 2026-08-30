# 静默跳过路径指标化（merge skip + 链接健康）

Status: implemented
Class: feature

## Decision

代码里有一批 `return ""` + 一行日志的静默跳过路径，日志刚通但没人盯（2026-08-30 的
两个问题都是主动 grep 才发现的）。导出 4 个计数器到 `backend/core/metrics.py`：

| 计数器 | 语义 | 告警 |
|--------|------|------|
| `stability_merge_skip_tool_not_configured_total` | `STP_BACKEND_DEDUP_SCAN_*` 缺失 → merge 跳过（#518） | **非零即告警**（配置缺陷，稳态不该出现） |
| `stability_merge_skip_no_org_files_total` | 本轮无 org 文件 → 跳过 | 只计数（空产物自然跳过） |
| `stability_merge_skip_failed_plan_run_total` | PlanRun FAILED → 不 merge（ADR-0028 显式门禁） | 只计数（设计行为非故障） |
| `stability_unlinked_fixable_total` | watcher-summary 计算发现 `unlinked_fixable > 0`（#528 阈值） | **非零即告警** |

告警规则两条（`increase(...[15m]) > 0`，形态对齐现有规则）：
`StabilityMergeSkipToolNotConfigured` / `StabilityUnlinkedFixable`，落在
`deploy/prometheus/alerts-stability-platform.yml` 新组 `stability-platform-silent-skips`。

埋点位置：`backend/services/dedup_scan.py:run_merge_sync` 三处跳过分支；
`backend/services/log_observation.py:aggregate_signal_link_stats`（`unlinked_fixable > 0`
时 inc）。

## Alternatives

- **`saq_merge_skip_extract` 也计数**：拒绝。它是 merge 已跳过的派生跳过，双计且无语义
  增益。
- **`no_org_files` / `failed_plan_run` 也非零即告警**：拒绝。前者在轻量轮次可能常见，
  后者是 ADR-0028 的显式设计门禁——非零即告警会制造噪音淹没真告警。
- **unlinked_fixable 接到服务端终态聚合 / reconciler 周期路径**：更干净（无 GET 依赖），
  但当前唯一计算点是 GET watcher-summary 路由，接终端聚合需动 aggregator 链路，列为
  后续。

## Verification

- `backend/tests/services/test_dedup_scan_merge.py`：三条 skip 分支各断言对应 counter
  `inc()` 被调用一次（monkeypatch 成 MagicMock，不依赖 prometheus_client 是否安装）。
- `backend/tests/services/test_log_observation.py`：
  `test_signal_link_stats_splits_unlinked_into_three_buckets` 断言 `inc()` 一次；
  `test_fixable_link_rate_ignores_not_yet_archived` 断言 `inc()` 未被调用。
- 生产验证：`curl -H "X-Agent-Secret: ..." http://127.0.0.1:8000/metrics`（401 白名单）
  确认四个新指标出现；Prometheus UI 确认 scrape 成功（注意：仓库无 prometheus.yml，
  抓取链路不在版本库内）。

## Revisit

- `unlinked_fixable_total` 计数随前端轮询重复自增（GET 触发），**绝对值无意义**，仅用于
  `increase(...)>0` 非零告警；代码注释已写明。若未来要「出现次数」语义，须迁到服务端
  每 run 一次的计算点。
