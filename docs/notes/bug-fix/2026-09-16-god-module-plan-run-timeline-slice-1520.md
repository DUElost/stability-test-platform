# God-module 垂直切片：plan_run timeline 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#2416/#2426（agent recovery/complete）仍叠在合入队列时，从 `main` 并行推进
`plan_runs` 读侧：把 `GET /plan-runs/{id}/timeline` 抽到
`backend/services/plan_run_timeline.py`：

| 符号 | 职责 |
|---|---|
| `build_plan_run_timeline` | step_trace 分桶、stage 设备级成败、patrol 心跳活跃度、current_stage、aborted 重数 |
| `_stage_status_from_steps` | stage 整体状态派生（随本业务线下沉） |

路由退化为 `_require_plan_run` + `ok(build_...)`。允许 import `api.schemas`
（#1519 只禁 services→routes）。`_aware` / `_LIVE_PATROL_*` 仍留路由供 events
共用；timeline 内保留同口径副本，避免 timeline↔events 反向耦合。

`plan_runs.py` **3283 → 2997**（本刀约 -286）。

## Alternatives

- **连 `/events` + `_build_synthetic_stage_events` 一并下沉**：弃——共享时间辅助
  多、面更大；本刀先锚定 timeline API 回归（`TestTimelineEndpoint` 5 例）；
- **等 agent PR 合完再动 plan_runs**：弃——与 agent 文件无交集，可并行减
  God-module 总行数；
- **服务只返回 dict、路由再装 Out**：弃——装配即业务口径的一部分，schema
  进服务不违反分层门禁。

## Verification

- 新增服务直测 **5 passed**（`_stage_status_from_steps` 终态/运行中分支）；
- API：`TestTimelineEndpoint` **5 passed**（聚合 / 404 / skipped 双层 / 不计
  succeeded / COMPLETED 计数）；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀（plan_runs）**：`/events` + `_build_synthetic_stage_events`（可把
  `_aware`/`_LIVE_PATROL_*` 收成共享小模块）；
- **agent 侧**：等 #2416/#2426 落地后继续 `extend_leases_batch` / claim；
- Issue #1520 保持 OPEN；本 PR `Refs #1520`，直推 `main`（不叠 agent 栈）。
