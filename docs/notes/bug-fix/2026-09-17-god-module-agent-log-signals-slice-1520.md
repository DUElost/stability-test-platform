# God-module 垂直切片：agent log_signals 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2487（DLE ingest）之上切 `POST /log-signals`：抽到
`backend/services/agent_log_signals.py`：

| 符号 | 职责 |
|---|---|
| `ingest_agent_log_signals` | 契约校验 → lease fencing → 部分接受 → PG 幂等入库 → 计数/关联/广播 |
| `require_job_bound_upload_lease` | 历史上传 fencing（artifacts 经路由 re-export） |
| `LogSignalIn` / `LogSignalBatchIn` | 入参 schema |

路由退化为 `ok(await ingest_agent_log_signals(...))`。

`agent_api.py` **1587 → 1371**（本刀约 -216；相对 DLE tip）。

## Alternatives

- **lease helper 单独成模块**：弃——目前仅 log_signals + artifacts 两处，随本刀迁出即可；
- **等 #2487 合入再从 main 切**：弃——可叠 PR。

## Verification

- 服务直测 **2 passed**（TERMINAL 集合 / 空批）；
- watcher API（含 log_signals）→ **21 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`patrol_heartbeat` / `coordinator_heartbeat`，或 artifacts；
- Issue #1520 保持 OPEN；`Refs #1520`。
