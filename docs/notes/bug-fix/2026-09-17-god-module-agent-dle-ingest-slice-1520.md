# God-module 垂直切片：agent device_log_events 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#2464（claim）已合入后，从 `main` 并行切 agent DLE 写路径（不碰仍 OPEN
的 plan_runs devices #2481）：把 `POST/GET /device-log-events` 抽到
`backend/services/agent_device_log_events.py`：

| 符号 | 职责 |
|---|---|
| `ingest_agent_device_log_events` | 状态机 / 身份不变式 / 路径校验 / 幂等 upsert + signal 关联 |
| `list_agent_device_log_events` | host 待处理恢复拉取 |
| `_ALLOWED_TRANSITIONS` 等 | DLE 迁移表与 extractable 终态 |
| `_validated_remote_path` / `_parse_iso_dt` | 路径与时间校验 |

路由退化为 `ok(await …)`；常量与 schema 经路由 re-export 保既有测试。

`agent_api.py` **1995 → 1587**（本刀约 -408）。

## Alternatives

- **只迁 ingest、list 留路由**：弃——共享 `_VALID_EVENT_STATES`，同刀迁完；
- **叠在 devices PR 上切 watcher-summary**：可并行；本刀选 agent 避免抢
  `plan_runs.py`。

## Verification

- 服务直测 **4 passed**（parse_iso / PULL_FAILED 派生 / 空批）；
- API：`test_agent_device_log_events` → **14 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`ingest_log_signals` / `patrol_heartbeat`，或 #2481 合入后
  watcher-summary；
- Issue #1520 保持 OPEN；`Refs #1520`。
