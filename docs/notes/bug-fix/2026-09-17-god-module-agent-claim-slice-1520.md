# God-module 垂直切片：agent claim_jobs 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2460（extend-leases）之上切 claim：把 `POST /jobs/claim` 抽到
`backend/services/agent_claim.py`：

| 符号 | 职责 |
|---|---|
| `claim_agent_jobs` | 版本门禁 → `claim_jobs_for_host` → enrich → PlanRunHost → `JobOut` |
| `claim_jobs_for_host` | 主机锁/退役/维护 → 空闲设备 → SKIP LOCKED 认领 + lease |
| `enrich_job_metadata` | 批量 serial + watcher_policy（admin snapshot 叠加） |
| `ClaimRequest` / `JobOut` | 入参出参 schema |

路由退化为 `ok(await claim_agent_jobs(...))`；私有名 `_claim_jobs_for_host` /
`_enrich_job_metadata` / `_LockAcquireFailed` 经路由 re-export 保既有测试导入。

`agent_api.py` **2309 → 1995**（本刀约 -314；相对 extend tip）。

## Alternatives

- **等 #2460 合入再从 main 切**：弃——claim 依赖面与 extend 无冲突，可叠 PR；
- **JobOut 装配留在路由**：弃——装配依赖 claim 产物与 enrich，整条垂直线应同迁。

## Verification

- 服务直测 **4 passed**（空 enrich / 426 门禁 / 空认领 / JobOut schema）；
- claim 相关 API：watcher + dual_write + retirement + session_lease →
  **115 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：plan_runs devices（#2451 合入后），或 agent 写路径剩余端点；
- Issue #1520 保持 OPEN；`Refs #1520`。
