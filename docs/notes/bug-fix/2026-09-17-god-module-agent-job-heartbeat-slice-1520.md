# God-module 垂直切片：agent job_heartbeat / extend_lock 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2529（upgrade-gate）之上切 Job 保活线：把
`POST /jobs/{id}/heartbeat` 与 `POST /jobs/{id}/extend_lock` 抽到
`backend/services/agent_job_heartbeat.py`：

| 符号 | 职责 |
|---|---|
| `record_agent_job_heartbeat` | RUNNING 保活 + fencing；禁终态 |
| `extend_agent_job_lock` | Job→Lease 全序续期（#1980） |
| `JobHeartbeatIn` / `ExtendLockIn` | 入参 schema（路由 re-export `_JobHeartbeatIn` 等） |

路由退化为 `ok(await record/extend_...(db, job_id, payload))`。

`agent_api.py` **729 → 669**（本刀约 -60；相对 upgrade-gate tip）。

## Alternatives

- **只迁 heartbeat、extend_lock 留路由**：弃——同属 mid-run 保活垂直线；
- **顺手迁 step status**：弃——走 reconciler，另一条垂直线。

## Verification

- 服务直测：`test_agent_job_heartbeat`（404/409/终态/续期冲突）；
- API：`test_agent_routes` / `test_agent_dual_write` / `test_shared_row_lock_order_1980` / fencing（118 passed）；
- `ruff` + `check:quick`（10 gates）通过。

## Revisit

- **下一刀**：`update_job_step_status` / `upload_step_traces` / `update_job_status`；
- Issue #1520 保持 OPEN；`Refs #1520`。
