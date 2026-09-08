# R06-F04 落地：回收器锁复读强制刷新 identity map（#989）

Status: implemented
Class: bug-fix

## Decision

`backend/scheduler/device_lease_reconciler.py` 四个 check 的写路径共享同一
缺陷模式：**先无锁加载候选进 Session identity map，再按 id
`SELECT ... FOR UPDATE` 复读**。SQLAlchemy 对已加载对象默认不刷新属性，
锁后读到的是缓存的旧状态——并发 `/complete`（或 abort ACK）在扫描与加锁
之间提交的终态被遮蔽，reaper 按旧 RUNNING 视图把 ABORTED/COMPLETED
写回 UNKNOWN，Job 与 PlanRun/租约事实不一致。

修复（与 #987 同族，同一「锁后以数据库为准」纪律）：

1. 全部四处「初查加载 + 按 id 锁复读」的锁查询统一加
   `execution_options(populate_existing=True)`：abort reaper（Check 0）、
   expired-lease 的 lease 复读与 job 复读（Check 1）、stale-UNKNOWN job
   复读（Check 2）。锁后属性即锁后行为，后续状态判断天然成为条件守卫；
2. abort reaper 循环体抽为 `_abort_reaper_recheck_job(db, job_id, now)`：
   锁复读 → 刷新后 status 仍为 RUNNING 才 transition UNKNOWN。函数化让
   「扫描后并发 complete、再加锁」的交错可以确定性测试（同 #987 先例）；
3. 非终态语义不变：UNKNOWN 仍是 grace 后由标准路径 FAILED + release。

## Alternatives

- **全部锁复读改为单语句条件 UPDATE（原生 SQL CAS）**——放弃：JSONB
  `run_context` 字段更新与状态机校验难以在单条 UPDATE 表达，且
  populate_existing 是 SQLAlchemy 一等语义，一行改动、模式内聚；
- **初查即带 FOR UPDATE 消除窗口**——放弃：整批候选长持锁放大
  `/complete` 等待；锁复读的单行窗口只有一条 SELECT 距离（同 #987
  裁决）；
- **只修 abort reaper（验收主路径）**——放弃：Check 1/2 的 lease/job
  复读同样依赖初查加载对象做状态判断（如「lease.status 仍 ACTIVE」），
  同类风险一体收口（验收 2「同类加锁复查路径统一刷新」）。

## Verification

- **反例实证**：回退实现文件保留测试 → 新用例 **2 failed**（函数缺失 =
  旧路径无刷新语义）；修复版全绿；
- 新增用例（`backend/tests/scheduler/test_abort_reaper.py`）：
  - identity map 已加载 RUNNING → 另一连接提交 COMPLETED →
    `_abort_reaper_recheck_job` skip，终态与 `status_reason`/`ended_at`
    原样保留（验收 1/3）；
  - 同场景无并发提交 → 正常转 UNKNOWN（populate_existing 不改正常路径）；
- `backend/tests/scheduler/` 全套 **52 passed**（abort reaper 4 既有 +
  device lease reconciler 全套回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- Check 3（terminal-job lingering lease）只读租约行 + `release_lease` 幂等
  UPDATE，无状态判断依赖初查对象，未加 populate_existing（无覆盖面）；
- `_reconcile_expired_leases`/`_reconcile_stale_unknown_jobs` 的锁复读
  目前由既有顺序测试回归；若未来补并发交错测试，可仿
  `_abort_reaper_recheck_job` 拆函数化；
- 与 #987 同族但独立根因（#987 = 无锁复读直写快照；#989 = 有锁但刷新
  缺失），各自修复互不替代。
