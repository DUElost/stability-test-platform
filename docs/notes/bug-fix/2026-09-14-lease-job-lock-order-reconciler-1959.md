# 租约回收器统一 Job→Lease 锁序，消除与批量续租的死锁环（#1959）

Status: implemented
Class: bug-fix

## Decision

`_reconcile_expired_leases`（`backend/scheduler/device_lease_reconciler.py`）改为与
`#992` 约定的 **Job → Lease** 同序：对每个过期租约候选，先用
`SELECT JobInstance … FOR UPDATE`（`populate_existing=True`）锁住候选 job 行，
再 `SELECT DeviceLease … FOR UPDATE` 复读并校验租约；候选整体按 `job_id` 升序处理
（`job_id` 为 NULL 的孤儿租约最后），与 `extend_leases_batch` 的
`WHERE id IN (…) ORDER BY id FOR UPDATE`（`backend/api/routes/agent_api.py:1562` 起）
处于同一全序。

### 为什么是这条路径

同一条 (job, lease) 两行被两条并发路径以相反顺序加锁：

| 路径 | 顺序 |
|---|---|
| `extend_leases_batch` → `_cas_renew_leases` | Job（`ORDER BY id`） → Lease（CAS `UPDATE … FROM job_instance`） |
| `_reconcile_expired_leases`（改前） | **Lease → Job** |

交错是真实可发生的：续租的 prelim 分类读的是**快照**，租约在那个快照里仍有效
（判为 renewable，于是先锁 Job）；等 CAS 落地时租约已过期，回收器刚好接手同一行，
双方互等成环。PostgreSQL 观测（2026-09-13/14）：83 次死锁，48 次卡在
`job_instance` 元组。

`#992` 已经修了 complete × extend-batch 那一半，并在
`docs/notes/bug-fix/2026-09-08-lease-lock-order-992.md:35` 的 Revisit 里预告了
「其它路径对同 Job 先碰 Lease 再碰 Job（例如某些 reclaim）」——本单就是那一半。
同文件 `_reconcile_stale_unknown_jobs`（Job → Lease）方向本来就是对的，所以不是
整个回收器都错，是其中一个 check 错。

### 顺带查实的两件事（不改行为，登记事实）

1. **死锁是被回收器自己吞掉的**。旧实现里 `DeadlockDetectedError` 被
   `_reconcile_expired_leases` 逐候选的 `except Exception` 收走，日志记成
   ``reconciler_job_load_failed job=N`` ——与真实原因完全不符；回收器随后照常
   commit，`reconciler_runs{outcome}` 记的是 `success`。这解释了
   「为什么这个缺陷能持续复发而无人察觉」，检测面缺口另立 #1958。
2. **`extend_leases_batch` 的 `expires_at > now` 用的是请求起点的 `now` 快照**
   （`_cas_renew_leases` 的 `now` 由路由入口一次性捕获）。因此在「prelim 通过 →
   CAS 前租约到期」的窗口里，续租仍可能判 `renewed`。这是 `#992` 起的既有语义，
   本单不改，仅登记（见 Revisit）。

### 涉及

- `backend/scheduler/device_lease_reconciler.py`（`_reconcile_expired_leases`）
- `backend/tests/scheduler/test_reconciler_renew_lock_order.py`（新增回归，需 PostgreSQL）

### 未覆盖

同一家族的另外两类环本单**不动**，仍需按同一「以共享行为单位枚举加锁点」的方法收口：
`job_heartbeat` / `extend_job_lock` 的事务后半段 `UPDATE job_instance SET updated_at`
（约占观测 20/83），以及终态化聚合的 `plan_run → plan_run_host → job_instance`
（约占 9/83）。它们的环形态与本单已证实的二方逆序不同，需要各自证据后再改。

## Alternatives

- **改成 Lease → Job（让 complete/extend-batch 反过来）**：放弃。`complete_job`
  本就先锁 Job（终态路径已持 Job 锁），把三条路径一起改成 Lease 开头改动面大得多，
  且与 `#992` 已合入的约定冲突。
- **整批候选先一次性 `FOR UPDATE` 锁全部 Job**：放弃。锁footprint 与持锁时长随过期
  候选数线性增长，与 `#989`/`#987` 明确记录的「不整批长持锁」取舍相悖；逐候选加锁
  已足以给出全序。
- **不排序候选**：放弃。回收器单实例（`_reconcile_lock` + 单进程调度），跨候选逆序
  未必立刻成环，但排序成本为零，且避免与批量续租的全序出现分叉。
- **只依赖 PG 死锁检测 + Agent 重试**：放弃。不消除额外失败、回收延迟与错误日志洪峰。

## Verification

- 新增 `backend/tests/scheduler/test_reconciler_renew_lock_order.py`：
  - `test_reconciler_locks_job_before_lease`：另一会话占住 Job 行时，回收器必须
    **没有**持有 `device_leases` 行锁（用第三会话 `FOR UPDATE NOWAIT` 直接探测）。
  - `test_reconciler_and_extend_batch_same_job_no_deadlock`：显式编排
    「续租锁 Job → 租约过期 → 回收器排队 → 放行 CAS」的生产交错；断言
    `pg_stat_database.deadlocks` 不增、且回收器未走失败分支（不能只看异常——修复前
    异常被吞）。
- **负向对照（必须失败才证明测试有鉴别力）**：把
  `device_lease_reconciler.py` 换回 `origin/main` 版本后，上面两个用例**均失败**
  （前者报 `LockNotAvailableError`：回收器持 Lease 等 Job；后者日志出现
  `reconciler_job_load_failed` + `reconciler_expired_lease_failed` 且 PG 报
  `deadlock detected`）。换回修复版后均通过。
- 实跑命令（testcontainers `postgres:16`；未设 `TEST_DATABASE_URL`）：

  ```text
  TESTING 由 conftest 设置；JWT_SECRET_KEY=test-secret
  python -m pytest backend/tests/scheduler/ -q
  → 95 passed（含新增 2 例）
  python -m pytest backend/tests/services/test_aggregator_deadlock_regression.py \
      backend/tests/services/test_execution_state_signals_step5a.py \
      backend/tests/api/test_agent_dual_write.py -q
  → 110 passed
  ```

- 回归有效性与「旧实现必须失败」的负向对照两轮，均在本机 testcontainers 环境实跑；
  未对生产库做任何读写。

## Revisit

- **新增任何 Lease/Job 触碰路径时**（尤其 reclaim / recovery / watchdog 类），按
  「以共享行（`job_instance`、`device_leases`、`plan_run`、`plan_run_host`）为单位
  枚举全部加锁点并断言全序一致」核对，而不是只看本条路径自洽（本次漏检的方法层
  原因，见 #1960）。
- **剩余两类环**（`updated_at` touch 家族、`plan_run` 三方环）逐类取证后决定是否
  同序化；在它们收敛前，`job_instance` 上的偶发死锁仍可能出现，只是主因已消除。
- **`extend_leases_batch` 的请求起点 `now`**：若后续观察到「已过期租约被续期」产生
  实际危害，应把 CAS 的时间谓词改为写时求值，或显式接受该窗口并写进契约。
- 上线后以 PG 日志 `deadlock detected` 频次回落作为效果观测；该观测面本身目前缺失
  （无指标无告警，见 #1958），在 #1958 落地前只能靠人工读服务端日志。
