# God-module 垂直切片：agent recovery_sync 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 切回主战场后的一刀：把 `agent_api.py` 的 **crash-recovery 业务线**抽到
`backend/services/agent_recovery.py`：

| 符号 | 职责 |
|---|---|
| `sync_agent_recovery` | `/recovery/sync` 全链路（outbox 推导、退役早返回、Job→Lease 锁序、RESUME/CLEANUP/ABORT_LOCAL） |
| `build_recovery_job_payload` | RESUME 用的 claim 形 payload（含 execution_state / PRH） |
| `resume_expired_lease_for_recovery` | UNKNOWN grace 内刷新过期 ACTIVE lease（**complete_job 晚到完成共用**） |
| `rotate_recovery_lease_token` | 换代 fencing token |

路由 `recovery_sync` 退化为 `return ok(await sync_agent_recovery(...))`；Pydantic
入参模型随业务线下沉，路由 **re-export** 保持既有测试导入路径不变。

`agent_api.py` **3486 → 3042 行**（本刀约 -444）。

## Alternatives

- **连 `complete_job`（326 行）一并下沉**：弃——与 recovery 共享的只有 grace
  刷新；complete 另有 fencing 回放 / 审计 / 聚合面，拆成下一刀更清晰；
- **新建 schema 模块、服务只收 dict**：弃——入参模型本就只服务本业务线，随
  服务下沉减少跨文件跳转；路由 re-export 保住测试面；
- **只搬三个 helper、留 `recovery_sync` 在路由**：弃——Issue 提名的是
  complete/recovery **段**；helper  alone 对 God-module 行数收益不足。

## Verification

- 新增服务直测 **5 passed**（`test_agent_recovery.py`：grace 内/外、非 UNKNOWN、
  非 ACTIVE、缺 device 409）；
- 锁序：`test_recovery_sync_lock_order_2015.py` **1 passed**；
- 行为回归：`test_agent_dual_write.py -k recovery_sync` + watcher recovery
  payload → **19 passed**；
- `ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`complete_job`（~326 行）——可复用本模块的
  `resume_expired_lease_for_recovery`；或转 `plan_runs` 读侧大块
  （timeline/events）；
- **`agent_api.py` 仍 ~3042 行**：claim / heartbeat / ingest 仍是热点；
- Issue #1520 保持 OPEN（ledger），本 PR 只 `Refs #1520`。
