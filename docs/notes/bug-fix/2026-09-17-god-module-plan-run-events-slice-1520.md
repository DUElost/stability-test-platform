# God-module 垂直切片：plan_run events 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

接 timeline 切片后，把 `GET /plan-runs/{id}/events` 抽到
`backend/services/plan_run_event_feed.py`，并收口共享时间辅助到
`backend/services/plan_run_read_common.py`：

| 模块 | 内容 |
|---|---|
| `plan_run_event_feed` | `build_plan_run_events`、`build_synthetic_stage_events`、log_signal 标题/严重度 |
| `plan_run_read_common` | `aware` / `iso` / `duration_seconds` / `LIVE_PATROL_*` / `TERMINAL_PR_*` / min·max |
| `plan_run_timeline` | 改引用 common（去掉本地副本） |

路由退化为 `_require_plan_run` + `ok(build_plan_run_events(...))`。

`plan_runs.py` **3017 → 2636**（本刀约 -381）。

## Alternatives

- **只搬 synthetic、events 主函数留路由**：弃——主函数才是行数主体，合成是
  其子例程；
- **新建 common 与 events 分两个 PR**：弃——Revisit 已点名收口 `_aware`/
  `_LIVE_PATROL_*`，同刀完成避免短暂三份副本；
- **顺手下沉 devices UI 状态机**：弃——另一条业务线，下一刀再切。

## Verification

- 服务直测：**5**（event_feed）+ **5**（timeline 回归）passed；
- API：`TestEventsEndpoint` + `TestTimelineEndpoint` → 合计 **27 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀（plan_runs）**：devices 矩阵（`_get_plan_run_devices_impl` /
  ui_status 派生）或 watcher summary；
- **agent 侧**：`extend_leases_batch`（~216）/ claim；
- 路由内仍留 `_aware`/`_iso` 供 devices 等使用——可再迁到 common；
- Issue #1520 保持 OPEN；`Refs #1520`。
