# God-module 垂直切片：agent coordinator_heartbeat 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2502（patrol_heartbeat）之上切 `POST /coordinator-heartbeat`：抽到
`backend/services/agent_coordinator_heartbeat.py`：

| 符号 | 职责 |
|---|---|
| `record_agent_coordinator_heartbeat` | instance fencing → job 先于 PRH（#1980）→ epoch/phase |
| `_CoordinatorHeartbeat*` | 入参出参 schema |
| `_VALID_COORDINATOR_PHASES` | phase 白名单 |

`_VALID_EXECUTION_STATES` / `_parse_progress_ts` 仍从 `agent_lease_extend` 引用。
路由退化为 `ok(await record_agent_coordinator_heartbeat(...))`。

`agent_api.py` **1196 → 1063**（本刀约 -133；相对 patrol tip）。

## Alternatives

- **顺手迁 agent_heartbeat / artifacts**：弃——另一条垂直线；
- **等栈底合入再从 main 切**：弃——可叠 PR。

## Verification

- 服务直测 **3 passed**（phases / 空批 / instance stale）；
- API：fencing_contract + lock_order_1980 → **10 passed**；
- 合计 **13 passed**；`ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`ingest_artifact` 或 `agent_heartbeat`；
- Issue #1520 保持 OPEN；`Refs #1520`。
