# God-module 垂直切片：plan_run devices 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#2451（events）已合入后，把 `GET /plan-runs/{id}/devices` 整条垂直线抽到
`backend/services/plan_run_devices.py`：

| 符号 | 职责 |
|---|---|
| `build_plan_run_devices` | Job×Device×Host×lease 联表 → 矩阵行 + facets + 过滤 |
| `ui_status_for_job` / `job_exec_status_for_job` | UI / 执行正交状态 |
| `running_heartbeat_deadline` 等 | 与 recycler 对齐的存活/认领截止派生 |
| `derive_busy_reason` | busy_reason / busy_lease_job_id |

时间辅助复用 `plan_run_read_common.aware` / `iso`。路由退化为计时包装 +
`_require_plan_run` + `ok(build_plan_run_devices(...))`；私有名经路由 re-export。

`plan_runs.py` **2636 → 2303**（本刀约 -333）。

## Alternatives

- **只迁联表、状态机留路由**：弃——状态派生是矩阵主体，拆开会让路由仍臃肿；
- **顺手迁 watcher-summary**：弃——另一条业务线，下一刀再切。

## Verification

- 服务直测 **6 passed**（exec/ui/stage/pending deadline）；
- stuck 对齐 + aggregation（含 `/devices`）→ **78 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀（plan_runs）**：watcher-summary 或 log-events；
- **agent 侧**：等 #2464 claim 合入后继续写路径；
- Issue #1520 保持 OPEN；`Refs #1520`。
