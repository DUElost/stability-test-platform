# God-module 垂直切片：agent host heartbeat 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2509（artifacts）之上切 `POST /api/v1/agent/heartbeat`：抽到
`backend/services/agent_host_heartbeat.py`：

| 符号 | 职责 |
|---|---|
| `record_agent_host_heartbeat` | host upsert / catalog / 退役告警 / capacity / 建议值 |
| `suggested_heartbeat_interval` / `suggested_log_rate_limit` | ADR-0026 backpressure 建议 |
| `HeartbeatRequest` / `Response` / `BackpressureInfo` | schema |

权威 `/api/v1/heartbeat` 改为从本模块 import 建议函数（避免 services→routes）。
路由退化为 `ok(await record_agent_host_heartbeat(...))`。

`agent_api.py` **957 → ~873**（本刀约 -84；相对 artifacts tip）。

## Alternatives

- **建议函数仍留 heartbeat 路由、agent service 反向 import**：弃——违反
  #1519 services↛routes；
- **顺手迁 upgrade-gate**：弃——另一条垂直线。

## Verification

- 服务直测（interval/log_rate / 新建 host）；
- API：`test_heartbeat_backpressure`；
- `ruff` + `check:quick`。

## Revisit

- **下一刀**：upgrade-gate 或 job_heartbeat / extend_job_lock；
- Issue #1520 保持 OPEN；`Refs #1520`。
