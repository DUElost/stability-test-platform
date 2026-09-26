# recovery/sync 与 step_trace 的 job 行锁死锁：锁降级为 FOR NO KEY UPDATE（#3426）

Status: implemented
Class: bug-fix

关联：[#3426](https://github.com/DUElost/stability-test-platform/issues/3426)、
[#3370](https://github.com/DUElost/stability-test-platform/issues/3370)（验收 ④ 唯一偏差项）、
[#2015](https://github.com/DUElost/stability-test-platform/issues/2015)（I1 共享行加锁全序）、
[#2871](https://github.com/DUElost/stability-test-platform/issues/2871)（I2 集合内全序）、
ADR-0003（锁序）、ADR-0026 §6。

## Decision

`sync_agent_recovery` 循环内的 `job_instance` 行锁由 **FOR UPDATE** 降为
**FOR NO KEY UPDATE**（SQLAlchemy `with_for_update(key_share=True)`，见
`backend/services/agent_recovery.py`），其余不动（锁序仍是 job → lease → device、
集合内按 job_id 升序、末尾单次提交）。

依据（2026-09-26 18:45:46 PG 服务端日志实证，T0+5s 中止回流窗口）：

- 环的两侧：`SELECT job_instance … FOR UPDATE`（recovery/sync，循环内持多把到末尾提交）
  ↔ `INSERT INTO step_trace … ON CONFLICT …`（FK 对同一 job 行取 **KEY SHARE**）；
- PG 行锁矩阵：**FOR NO KEY UPDATE 与 KEY SHARE 兼容**，FOR UPDATE 与之冲突 ⇒ 降级即拆环；
- 本函数**不写 job 的任何键列**（device_id/host_id/plan_run_id 仅作比较，见
  ownership 校验），降级语义等价；写者互斥不受影响（FOR NO KEY UPDATE 仍与
  FOR NO KEY UPDATE / FOR SHARE / FOR UPDATE 冲突）；
- 先例：`plan_run_abort` / `plan_run_finalization` / `plan_dispatcher_sync` 等处
  已在用同一手法（#1473；`key_share=True` → FOR NO KEY UPDATE）。

## Alternatives

- **只把 step_trace 插入按 job_id 排序**：FK KEY SHARE 的取得顺序由插入顺序决定，
  跨请求交织时仍可成环；属概率缓解而非拆环，不作为主修（可后续加固）。
- **缩短 recovery/sync 事务（逐 job 提交）**：破坏 #2015 的「一次请求持全 tuple 锁、
  末尾提交」原子语义与 ownership 校验窗口，不采纳。
- **应用层捕获死锁并重试**：掩盖根因，且当前目标是消除 500（本单即该 500 的来源），
  不采纳。
- **device / lease 行锁一并降级**：本次实证环只在 job 行；未见 device FK KEY SHARE
  的同类形态，按最小面不动（见 Revisit）。

## Verification

- 新增 `backend/tests/services/test_recovery_step_trace_deadlock_3426.py`（3 例）：
  2 个静态锚点（job 行锁必须 `key_share=True`、不得回退裸 `with_for_update()`）+
  1 个真实 PG 并发复现：A = 真实 `sync_agent_recovery`（boot_mismatch 分支、升序持锁、
  首个 job 的 `on_job_terminal` 处用事件暂停），B = 真实 `reconcile_step_traces`
  （trace 顺序 j2 → j1，取 FK KEY SHARE），在 B 插入 j2 后放行 A。
- **反向验证**：源码退回 FOR UPDATE → **3 failed**，并发用例复现
  `asyncpg.exceptions.DeadlockDetectedError`（`Process 85 waits for ShareLock on
  transaction 747 … blocked by process 84` 互等）；修复后 **3 passed**。
- 相关存量面（recovery 服务/两条锁序回归/patrol heartbeat/锁序判据两文件）
  → **45 passed**。
- `python scripts/run_gates.py check:quick` → 见 PR 记录。
- 生产侧：18:45:46 事件后未再复现（PG 计数保持 20、20:00 排程 run 582 无死锁）；
  本修复先入 main，待部署后在中止回流窗复核。

## Revisit

- 历史同族但**形态不同**的环（如 2026-09-20 三进程环含 `plan_run` 行锁）未被本单覆盖：
  若复发含 job 的 FK KEY SHARE 成分，同一处理；若为纯 plan_run 锁环，另裁。
- `device` / `device_leases` 行锁的同类降级（FOR NO KEY UPDATE）未做——若后续观察到
  以它们为交点的 FK KEY SHARE 环，按本单同一判据重评。
- `reconcile_step_traces` 未做插入排序；若未来出现「任意顺序都无环」的要求，
  在该函数内按 job_id 排序作为加固（不改变本单结论）。
