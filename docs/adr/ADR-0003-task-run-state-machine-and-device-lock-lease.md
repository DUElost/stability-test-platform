# ADR-0003: 任务状态机与设备锁租约机制
- 状态：Accepted
- 日期：2026-02-18（2026-03-16 更新）
- 决策者：平台研发组
- 标签：状态机, 并发控制, 设备锁, 回收, 会话看门狗

## 背景

任务执行存在并发争抢设备、Agent 异常退出、长任务中断等风险。系统需要保证"一个设备同一时间只被一个 run 占用"，并能自动回收异常状态。

## 决策

采用"任务状态机 + 设备锁租约 + 会话看门狗"的组合策略：

### 状态机

Workflow JobInstance 状态转换（M2 主路径）：

```
PENDING → RUNNING → COMPLETED
                  → FAILED
                  → ABORTED
                  → UNKNOWN → RUNNING    (Agent 恢复)
                            → COMPLETED  (Agent 补报)
                            → FAILED     (宽限期超时)

PENDING_TOOL → PENDING  (工具就绪后回到待认领队列)
```

Legacy TaskRun 状态转换（保留兼容）：
- `Task`: `PENDING -> QUEUED -> RUNNING -> COMPLETED/FAILED/CANCELED`
- `TaskRun`: `QUEUED -> DISPATCHED -> RUNNING -> FINISHED/FAILED/CANCELED`

### 统一设备锁服务

`backend/services/device_lock.py` 提供 acquire / extend / release 三个原子操作（async + sync 双版本），所有锁操作收敛到此服务：

- **acquire_lock**：CAS 语义 — 设备空闲、租约过期、或同 job 重入时获取锁
  - WHERE 条件：`lock_run_id IS NULL OR lock_expires_at IS NULL OR lock_expires_at < :now OR lock_run_id = :job_id`
- **extend_lock**：仅 `lock_run_id == job_id` 时续租
- **release_lock**：仅 `lock_run_id == job_id` 时释放，BUSY → ONLINE

### 锁获取时机

锁的 owner 统一为 `JobInstance.id`（非 WorkflowRun.id）。获取时机：

1. **Claim 端点**（硬保护）：Agent 拉取待执行任务时，在 savepoint 中原子完成 `PENDING → RUNNING` + `acquire_lock`。失败时 savepoint 自动回滚，job 保持 PENDING。
2. **Agent 本地守卫**：`_active_device_ids` 集合防止同一设备被提交到线程池两次。

> **注意**：Workflow dispatcher 不再执行设备预锁。预锁使用 WorkflowRun.id 作为 owner，与 claim/complete 使用 JobInstance.id 存在语义冲突，已于 2026-03-16 移除。

### 异常补偿：会话看门狗

`backend/tasks/session_watchdog.py` 替代 recycler 中重叠的检查逻辑，提供三层保护：

1. **Host 心跳超时**（默认 120s）→ Host 置 OFFLINE，RUNNING job → UNKNOWN
2. **设备锁过期**（租约到期）→ 释放锁，RUNNING job → UNKNOWN
3. **UNKNOWN 宽限期**（默认 300s）→ UNKNOWN job → FAILED

通过 `USE_SESSION_WATCHDOG` 环境变量控制（默认 `true`）。启用时：
- `session_watchdog` 运行，`heartbeat_monitor` 不启动（互斥）
- recycler 跳过 host 心跳超时和设备锁过期检查（避免冲突）

### Pipeline 锁验证

PipelineEngine 在两个层面验证锁：
1. **启动前**：调用 `extend_lock` 端点验证锁仍被当前 job 持有
2. **步间检查**：通过注入的 `is_aborted` 回调检测 LockRenewalManager 是否因 409 移除了本 run

## 备选方案与权衡

- 方案 A：仅靠设备状态字段（无租约、无过期）。
  - 优点：实现简单。
  - 缺点：Agent 异常后容易出现死锁设备。
- 方案 B：租约 + Recycler 单路径回收（原始方案）。
  - 优点：容错能力较强。
  - 缺点：recycler 与 heartbeat_monitor 检查逻辑重叠，超时阈值冲突。
- 方案 C（当前决策）：统一锁服务 + 会话看门狗。
  - 优点：锁操作收敛、检查逻辑去重、超时阈值统一、支持 UNKNOWN 宽限恢复。
  - 缺点：状态转换路径增多，需要完善测试覆盖。

## 影响

- 正向影响：并发调度安全性显著提升，降低设备"永久 BUSY"概率。UNKNOWN 宽限期允许 Agent 短暂断联后恢复。
- 代价：代码路径复杂，需要完善测试覆盖状态转换与超时场景。

## 落地与后续动作

- ✅ 统一设备锁服务 `device_lock.py`（async + sync）
- ✅ 会话看门狗 `session_watchdog.py`（Host 超时 + 锁过期 + UNKNOWN 宽限）
- ✅ Claim 端点 savepoint + 锁获取原子操作
- ✅ Complete 端点自动释放锁
- ✅ Agent per-device 并发守卫 `_active_device_ids`
- ✅ PipelineEngine 锁验证（启动前 + 步间 `is_aborted` 回调）
- ✅ 状态机支持 `UNKNOWN → FAILED` 转换
- ✅ 状态机支持 `PENDING_TOOL → PENDING` 转换（工具依赖就绪后重新排队）
- ✅ heartbeat_monitor 与 session_watchdog 互斥运行
- ⏳ 将 claim 端点的锁过滤下推到 SQL 层（避免 LIMIT 前置导致的饥饿）
- ⏳ 统一 pipeline 锁丢失时的终态映射（ABORTED vs FAILED）

## 关联实现/文档

- `backend/services/device_lock.py` — 统一设备锁服务
- `backend/tasks/session_watchdog.py` — 会话看门狗
- `backend/services/state_machine.py` — JobInstance 状态机
- `backend/api/routes/agent_api.py` — claim / complete / extend_lock 端点
- `backend/agent/main.py` — Agent 主循环与 per-device 守卫
- `backend/agent/pipeline_engine.py` — Pipeline 锁验证
- `backend/scheduler/recycler.py` — 回收器（Host 心跳超时与设备锁过期由 session_watchdog 独占处理，recycler 不包含这两类检查）
- `backend/services/dispatcher.py` — Workflow 派发（不再预锁）
- [`openspec/specs/device-concurrency-guard/spec.md`](../../openspec/specs/device-concurrency-guard/spec.md) — 设备并发守卫规范
- [`openspec/specs/session-lifecycle/spec.md`](../../openspec/specs/session-lifecycle/spec.md) — 会话生命周期规范
- `backend/tests/services/test_device_lock.py` — 锁服务测试
- `backend/tests/tasks/test_session_watchdog.py` — 看门狗测试
- `backend/tests/services/test_session_lease.py` — 会话租约集成测试
