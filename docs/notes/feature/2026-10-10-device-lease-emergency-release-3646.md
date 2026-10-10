# 设备租约紧急释放走管理端 API

Status: implemented
Class: feature

## Decision

#3646 用管理员接口 `POST /api/v1/devices/{device_id}/leases/{lease_id}/release` 替换 SOP 里的手工 `UPDATE`。服务 `release_terminal_job_lease` 只释放终态（`TERMINAL_JOB_STATUSES`：`COMPLETED` / `FAILED` / `ABORTED`）上的 ACTIVE `JOB` 租约，调用 `release_lease_sync(device_id, job_id, LeaseType.JOB)`，并写 `emergency_release_lease` 审计。不改 job 或 PlanRun。

终态集合从回收器的 `_FINAL_STATUSES` 字面量提升为 `backend/models/enums.py` 的 `TERMINAL_JOB_STATUSES`，回收器改为引用它。设备路由是同步会话，所以调用同步孪生 `release_lease_sync`；它与异步 `release_lease` 的匹配条件相同（`device_id` + `job_id` + `lease_type` + `ACTIVE`）。

加锁顺序是 job → lease：先无锁读出 `job_id`，再锁 job，再锁租约。持有租约行锁后如果 `job_id` 变了，返回 409 `LEASE_JOB_CHANGED`，不再去锁另一条 job。新加锁点登记在 `docs/notes/architecture/2026-09-14-shared-row-lock-table.md` 的 I1 表。

本单元没有设备页按钮。前端只同步了 `types.ts` 和 `devices.releaseLease`，供以后的入口调用。告警不在本单元（#3660）。

## Alternatives

- 在设备路由上改用异步 `release_lease`：测试客户端只覆盖同步 `get_db`，异步会话看不见同一条测试事务。放弃。
- 继续手工 `UPDATE`，或加只写 `device_leases` 两列的数据库角色：Planner 裁决已否决（#3646 评论）。
- 另写一份终态字面量：与「不得另写一份」冲突。放弃。

## Verification

`backend/tests/api/test_device_lease_emergency_release.py`：终态释放并写审计、`RUNNING` / `PENDING` / `UNKNOWN` 为 409、非 `JOB` 为 409、已 `RELEASED` 为 409、设备不符为 404、非管理员为 403、空白 `reason` 为 422、同设备上另一个 job 的新 ACTIVE 租约不被释放。

## Revisit

设备详情页要加「释放租约」按钮时，复用 `devices.releaseLease`，不要再开一条写路径。回收器停转告警走 #3660，不并进本接口。
