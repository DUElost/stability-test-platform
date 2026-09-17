# God-module 垂直切片：agent archive_status 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2553（job_status）之上切 `GET /{host_id}/archive-status`：抽到
`backend/services/agent_archive_status.py`（读 `Host.extra` 运维概览）。

路由退化为 `ok(await get_agent_archive_status(db, host_id))`。

`agent_api.py` **528 → 515**（本刀约 -13；相对 job_status tip）。

## Alternatives

- **并入 agent_host_heartbeat**：弃——本端点是控制面用户读，非 Agent 写；
- **留在路由**：弃——与 #1520 垂直切片目标一致，且 Host 模型可离开路由 import。

## Verification

- 服务直测：`test_agent_archive_status`（404 / extra 字段 / 非 dict）；
- API：`test_archive_status_unknown_host_404`（4 passed）；
- `ruff` + `check:quick`（11 gates）通过。

## Revisit

- **下一刀**：评估 `agent_api` 残余（鉴权/_version 薄壳 + re-export）是否还值得再切；
  god-files 已远低于 ceiling（~505/1005）；
- Issue #1520 保持 OPEN；`Refs #1520`。
