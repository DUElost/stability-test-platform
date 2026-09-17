# God-module 垂直切片：agent extend_leases_batch 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#2451（plan_runs events）仍 OPEN 时，从 `main` 并行切 agent 写路径：把
`POST /leases/extend-batch` 抽到 `backend/services/agent_lease_extend.py`：

| 符号 | 职责 |
|---|---|
| `extend_agent_leases_batch` | 预检分类 → Job→Lease 锁序 → CAS renew → execution_state/progress 回写 |
| `_cas_renew_leases` | 所有权元组 CAS（host/token/ACTIVE/未过期） |
| `_ExtendBatch*` / `_LEASE_EXTEND_BATCH_MAX` | 入参出参与批量上限 |
| `_VALID_EXECUTION_STATES` / `_parse_progress_ts` | 与 coordinator_heartbeat 共用（路由 re-export） |

路由退化为 `ok(await extend_agent_leases_batch(...))`。

`agent_api.py` **2616 → 2309**（本刀约 -307）。

## Alternatives

- **等 #2451 合完再切 plan_runs devices**：可并行；本刀选 agent 避免与
  events PR 抢 `plan_runs.py`；
- **把 VALID_EXECUTION_STATES 单独成模块**：弃——目前仅 extend + coordinator
  两处，经 re-export 足够。

## Verification

- 服务直测 **5 passed**（parse_progress / 空批 / 超限 413 / 状态集合）；
- API + 锁序：agent_routes extend/cas + step5a extend + reconciler×extend
  不死锁 → **18 passed**；
- 测试补丁点改到 `agent_lease_extend`（`_LEASE_EXTEND_BATCH_MAX` /
  `_cas_renew_leases`）；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`claim_jobs` / `_claim_jobs_for_host`，或 #2451 合入后
  plan_runs devices；
- Issue #1520 保持 OPEN；`Refs #1520`。
