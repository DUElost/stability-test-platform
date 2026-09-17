# God-module 垂直切片：agent patrol_heartbeat 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2499（log_signals）之上切 `POST /jobs/{id}/patrol-heartbeat`：抽到
`backend/services/agent_patrol_heartbeat.py`：

| 符号 | 职责 |
|---|---|
| `record_agent_patrol_heartbeat` | RUNNING 门禁 → lease → GREATEST cycle → CAS 写计数 |
| `PatrolHeartbeatIn` / `Out` | 入参出参 schema |

路由退化为 `ok(await record_agent_patrol_heartbeat(...))`。

`agent_api.py` **1371 → 1196**（本刀约 -175；相对 log_signals tip）。

## Alternatives

- **顺手迁 coordinator_heartbeat**：弃——另一条垂直线，下一刀再切；
- **等栈底合入再从 main 切**：弃——可叠 PR。

## Verification

- 服务直测 **3 passed**（404 / JOB_NOT_RUNNING / 负 delta）；
- API：`test_patrol_heartbeat_api` → **15 passed**（含 CAS race；补丁点改到
  `agent_patrol_heartbeat._get_valid_runtime_lease`）；
- 合计 **18 passed**；`ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`coordinator_heartbeat` 或 `ingest_artifact`；
- Issue #1520 保持 OPEN；`Refs #1520`。
