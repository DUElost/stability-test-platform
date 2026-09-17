# God-module 垂直切片：watcher-summary / AEE dashboard 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

把 plan_runs.py 里最大的一块内聚域——**watcher-summary + AEE dashboard +
crash-details**——整族抽到 `backend/services/plan_run_watcher_summary.py`
（叠在 #2481 devices 切片的同一模式上）：

| 符号 | 职责 |
|---|---|
| `build_plan_run_watcher_summary` | 窗口解析 → AEE dashboard 双段 → aee_breakdown → platform buckets → capability → run-log archive |
| `build_plan_run_crash_details` | 复用同一条去重链（`_load_deduped_aee_events`）按包名给事件明细 |
| `_resolve_watcher_summary_window` / `_window_minutes_to_scope_label` | watcher 窗口口径（#1962 grace 语义在此） |
| `_aee_event_dedup_key` / `_uniview_dedup_key` 等去重/归一族 | #2285 契约钉死的纯函数 |
| `_aggregate_watcher_platform_buckets` / `_aggregate_run_log_archive` / `_aggregate_watcher_capability` | 聚合桶/归档态/能力位 |

两条路由退化为 `_require_plan_run` + `ok(build_*(...))` 薄壳；全部被移动的私有名
在路由模块经 `noqa: F401` re-export，保既有测试导入（devices 切片先例）。

`plan_runs.py` **2303 → 1301**（本刀约 **-1002**，含 4 个常量、18 个私有函数、
2 个路由体）。#1520 记录在案的 3328 行自此不到一半。

边界（有意不动）：`log_observation` / `device_log_event` 两个服务只被 import、
未被修改——#2494 在窗风险面（results/metrics/status-badge）零触碰；`_iso` 按
`plan_run_export.py` 先例在服务内自带小副本，不回导路由模块（避免 service→route
反向依赖，platform-audit H 项点过的那个）。

## Alternatives

- **拆成 watcher-summary 与 crash-details 两刀**：弃——两者共享整条去重/helper 链
  （`_load_deduped_aee_events` 及 15 个 helper），拆两刀要先造一个中间公共模块，
  反而多一层；
- **只搬 crash-details helper 簇（1628-2146）**：弃——留下 watcher 路由跨模块反向
  import 服务的私有名，切片不完整，drift 面上更糟；
- **把 `_require_plan_run`/`_iso` 一并挪进新 util**：弃——非本刀必需（S「不顺手
  重构」），`_iso` 副本先例已在，`_require_plan_run` 仍被 8 条路由共用。

## Verification

- `pytest test_plan_run_dedup_key_2285 + test_watcher_summary_uniview_1956 +
  test_plan_run_window_grace_1962 + test_plan_run_log_events +
  test_plan_run_stuck_alignment + test_plan_runs_api + test_read_api_auth +
  test_agent_api_watcher + test_dedup_scan_endpoints +
  test_signal_link_reconciler` → **159 passed**；
- `pytest backend/tests/api/test_plan_run_aggregation_endpoints.py` → **66 passed**
  （watcher-summary/crash-details 的 HTTP 面回归全在此）；
- `#2285` 去重键守卫与 `#1956` 「单源 ANOMALY_SIGNAL_CATEGORIES」源码守卫改随
  所有权指向服务文件（**不是放宽**：同两条断言，只是读对了现在的 owner 文件）；
- `ruff check`（F/E9 全规则面）两文件 All checks passed；`run_gates.py check:quick`
  结果见 PR。

## Revisit

- 剩余体量集中在 500-1015 行的触发/归档/手动重试簇与 344-500 的列表/详情装配——
  与 #1805（claim/scan/WS 收口，未认领）同文件不同域，动工前先查其在窗状态；
- `_iso` 第二副本记在案：若 services 里出现第三份，应抽 `backend/core/timefmt.py`
  一类的公共位，独立小单做。
