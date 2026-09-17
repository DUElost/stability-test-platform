# God-module 垂直切片：agent job_status 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

栈底 #2537 已合入 main；本刀从 main 切 `POST /jobs/{id}/status`：抽到
`backend/services/agent_job_status.py`：

| 符号 | 职责 |
|---|---|
| `update_agent_job_status` | fencing + 仅 RUNNING 保活 no-op；禁终态 |
| `JobStatusUpdate` | 入参 schema |

路由退化为 `ok(await update_agent_job_status(...))`。

`agent_api.py` **565 → 528**（本刀约 -37）。

## Alternatives

- **并入 agent_job_heartbeat**：弃——兼容 status 与 heartbeat/extend_lock 语义不同；
- **顺手迁 archive-status**：弃——用户鉴权读端点，另一条垂直线。

## Verification

- 服务直测：`test_agent_job_status`（404/400/终态/INVALID/RUNNING no-op）；
- API：`test_agent_routes` / dual_write / fencing（114 passed）；
- `ruff` + `check:quick`（10 gates）通过。

## Revisit

- **下一刀**：`get_archive_status`；或评估 `agent_api` 残余（鉴权/_version 薄壳）是否值得再切；
- Issue #1520 保持 OPEN；`Refs #1520`。
