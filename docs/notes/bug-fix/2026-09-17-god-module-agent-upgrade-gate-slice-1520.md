# God-module 垂直切片：agent upgrade-gate 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2523（host heartbeat）之上切 upgrade-gate：把
`POST /hosts/{id}/upgrade-gate` / `.../release` 抽到
`backend/services/agent_upgrade_gate.py`：

| 符号 | 职责 |
|---|---|
| `acquire_agent_upgrade_gate` | begin_host_upgrade + 审计 + HTTP 映射 |
| `release_agent_upgrade_gate` | end_host_upgrade + 审计 |
| `raise_upgrade_gate_http` | 领域异常 → HTTPException |
| `UpgradeGateRequest` / `ReleaseRequest` | 入参 schema |

路由退化为 `ok(acquire/release_agent_upgrade_gate(...))`。

`agent_api.py` **859 → 729**（本刀约 -130；相对 heartbeat tip）。

## Alternatives

- **只迁 _raise、端点留路由**：弃——HTTP 适配整条垂直线应同迁；
- **顺手迁 job_heartbeat**：弃——另一条垂直线。

## Verification

- 服务直测：`test_agent_upgrade_gate`（404/409 映射 / holder / acquire）；
- API：`test_upgrade_gate_api`（13 passed）；
- `ruff` + `check:quick`（10 gates）通过。

## Revisit

- **下一刀**：`job_heartbeat` / `extend_job_lock` / `update_job_step_status`；
- Issue #1520 保持 OPEN；`Refs #1520`。
