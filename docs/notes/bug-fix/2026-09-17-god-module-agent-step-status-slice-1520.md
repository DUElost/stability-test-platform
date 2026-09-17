# God-module 垂直切片：agent step_status / traces 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2533（job_heartbeat）之上切 StepTrace 线：把
`POST /steps` 与 `POST /jobs/{id}/steps/{step_id}/status` 抽到
`backend/services/agent_step_status.py`：

| 符号 | 职责 |
|---|---|
| `upload_agent_step_traces` | 批量幂等 upsert + 广播 |
| `update_agent_job_step_status` | 单步 status（派生稳定 event id） |
| `require_valid_runtime_lease` | fencing 门禁（路由 `update_job_status` re-export） |
| `StepTraceIn` / `StepStatusIn` | 入参 schema |

路由退化为 `ok(await upload/update_...(db, ...))`。
fencing 契约测试的 patch 点迁到 service 模块。

`agent_api.py` **669 → 565**（本刀约 -104；相对 job_heartbeat tip）。

## Alternatives

- **只迁单步 status、/steps 留路由**：弃——同属 reconciler 垂直线；
- **顺手迁 update_job_status**：弃——兼容心跳端点，另一条线。

## Verification

- 服务直测：`test_agent_step_status`（404/409/稳定 event id）；
- API：`test_agent_routes` / `test_agent_dual_write` / fencing（113 passed）；
- `ruff` + `check:quick`（10 gates）通过。

## Revisit

- **下一刀**：`update_job_status` / `get_archive_status`；
- Issue #1520 保持 OPEN；`Refs #1520`。
