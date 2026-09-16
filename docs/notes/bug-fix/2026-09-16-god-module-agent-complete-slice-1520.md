# God-module 垂直切片：agent complete_job 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 recovery 切片（#2416）之上，把 `agent_api.py` 的 **Job 终态完成**抽到
`backend/services/agent_completion.py`：

| 符号 | 职责 |
|---|---|
| `complete_agent_job` | `/jobs/{id}/complete` 全链路（回放幂等 / UNKNOWN late-complete / 状态机 / watcher / lease 释放 / 聚合 / post_completion 入队） |
| `get_valid_runtime_lease` | Phase 4b fencing 门禁（heartbeat / extend_lock 经路由 re-export） |
| `_RUN_TO_JOB` / `_RunCompleteIn` | 终态映射与入参模型随业务线下沉 |
| `apply_watcher_summary` / `bridge_reconciler_metrics` | complete 专用副作用 |

`resume_expired_lease_for_recovery` **复用** `agent_recovery`（晚到完成 UNKNOWN→RUNNING）。

相对 recovery tip：`agent_api.py` **3042 → 2616**（本刀约 -426）。
相对拆分前 main（3486）：两刀合计约 **-870**。

## Alternatives

- **独立于 #2416 从 main 抽取**：弃——会与 recovery 大面积冲突，且晚到完成依赖
  已下沉的 resume helper；
- **把 `get_valid_runtime_lease` 留在路由**：弃——complete 是主消费者，heartbeat/
  extend 经 re-export 成本更低；
- **连 claim / extend_leases_batch 一并下沉**：弃——本刀只切 complete 垂直线。

## Verification

- 新增服务直测 **7 passed**（映射别名、lease 过期/错 token/非 RUNNING、非法终态 400×2）；
- API 回归：`complete_job*` / abort·watcher complete（指标 patch → `agent_completion`）
  + 既有 recovery 直测 → 合计见 PR 实测；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`extend_leases_batch` / claim，或 `plan_runs` timeline/events；
- Issue #1520 保持 OPEN；本 PR 叠在 #2416 之上（`--base` recovery 分支），
  `Refs #1520`。
